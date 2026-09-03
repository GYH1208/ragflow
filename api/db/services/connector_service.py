#
#  Copyright 2024 The InfiniFlow Authors. All Rights Reserved.
#
#  Licensed under the Apache License, Version 2.0 (the "License");
#  you may not use this file except in compliance with the License.
#  You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
#  Unless required by applicable law or agreed to in writing, software
#  distributed under the License is distributed on an "AS IS" BASIS,
#  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#  See the License for the specific language governing permissions and
#  limitations under the License.
#
import logging
import os
from datetime import datetime
from typing import List, Optional, Tuple

from anthropic import BaseModel
from peewee import SQL, fn

from api.db import InputType
from api.db.db_models import DB, Connector, Connector2Kb, Knowledgebase, SyncLogs
from api.db.services.common_service import CommonService
from api.db.services.document_service import DocMetadataService, DocumentService
from api.db.services.knowledgebase_service import KnowledgebaseService
from api.utils.common import hash128
from common.constants import ConnectorTaskType, TaskStatus
from common.misc_utils import get_uuid
from common.settings import TIMEZONE
from common.time_utils import current_timestamp, timestamp_to_date

LOGGER = logging.getLogger(__name__)


class ConnectorService(CommonService):
    model = Connector

    @classmethod
    def cancel_tasks(cls, connector_id):
        e, conn = cls.get_by_id(connector_id)
        if not e:
            return

        logging.info(
            "[Connector] stop connector=%s(%s)",
            conn.name,
            connector_id,
        )
        for c2k in Connector2KbService.query(connector_id=connector_id):
            SyncLogsService.filter_update(
                [
                    SyncLogs.connector_id == connector_id,
                    SyncLogs.kb_id == c2k.kb_id,
                    SyncLogs.status.in_([TaskStatus.SCHEDULE, TaskStatus.RUNNING]),
                ],
                {"status": TaskStatus.CANCEL},
            )
        ConnectorService.update_by_id(connector_id, {"status": TaskStatus.CANCEL})
        logging.info(
            "[Connector] connector=%s status updated to %s",
            connector_id,
            TaskStatus.CANCEL,
        )

    @classmethod
    @DB.connection_context()
    def accessible(cls, connector_id: str, user_id: str) -> bool:
        """Return whether the user can access the connector's tenant."""
        e, connector = cls.get_by_id(connector_id)
        if not e:
            LOGGER.warning("connector access denied: connector not found connector_id=%s user_id=%s", connector_id, user_id)
            return False

        if connector.tenant_id == user_id:
            return True

        from api.db.services.user_service import TenantService

        joined_tenants = TenantService.get_joined_tenants_by_user_id(user_id)
        has_access = any(tenant["tenant_id"] == connector.tenant_id for tenant in joined_tenants)
        if not has_access:
            LOGGER.warning(
                "connector access denied: tenant mismatch connector_id=%s user_id=%s tenant_id=%s",
                connector_id,
                user_id,
                connector.tenant_id,
            )
        return has_access

    @classmethod
    def schedule_tasks(cls, connector_id):
        e, conn = cls.get_by_id(connector_id)
        if not e:
            return

        logging.info("[Connector] schedule connector=%s(%s)", conn.name, connector_id)
        prune_enabled = bool((conn.config or {}).get("sync_deleted_files"))
        for c2k in Connector2KbService.query(connector_id=connector_id):
            sync_task = SyncLogsService.get_latest_task(
                connector_id,
                c2k.kb_id,
                ConnectorTaskType.SYNC,
            )
            poll_range_start = None
            total_docs_indexed = 0
            if sync_task and sync_task.status == TaskStatus.DONE:
                poll_range_start = sync_task.poll_range_end
                total_docs_indexed = sync_task.total_docs_indexed

            SyncLogsService.schedule(
                connector_id,
                c2k.kb_id,
                poll_range_start,
                total_docs_indexed=total_docs_indexed,
                task_type=ConnectorTaskType.SYNC,
            )

            if prune_enabled:
                SyncLogsService.schedule(
                    connector_id,
                    c2k.kb_id,
                    task_type=ConnectorTaskType.PRUNE,
                )

    @classmethod
    def list(cls, tenant_id):
        fields = [
            cls.model.id,
            cls.model.name,
            cls.model.source,
            cls.model.status
        ]
        return list(cls.model.select(*fields).where(
            cls.model.tenant_id == tenant_id
        ).dicts())

    @classmethod
    def rebuild(cls, kb_id:str, connector_id: str, tenant_id:str):
        from api.db.services.file_service import FileService
        e, kb = KnowledgebaseService.get_by_id(kb_id)
        if not e:
            return "Dataset not found."
        e, conn = cls.get_by_id(connector_id)
        if not e:
            return "Connector not found."
        if str(conn.tenant_id) != str(kb.tenant_id):
            return "Connector owner does not match the dataset owner."
        SyncLogsService.filter_delete([SyncLogs.connector_id==connector_id, SyncLogs.kb_id==kb_id])
        docs = DocumentService.query(source_type=f"{conn.source}/{conn.id}", kb_id=kb_id)
        err = FileService.delete_docs([d.id for d in docs], kb.tenant_id)
        SyncLogsService.schedule(connector_id, kb_id, reindex=True, task_type=ConnectorTaskType.SYNC)
        if (conn.config or {}).get("sync_deleted_files"):
            SyncLogsService.schedule(connector_id, kb_id, task_type=ConnectorTaskType.PRUNE)
        return err

    @classmethod
    def cleanup_stale_documents_for_task(
        cls,
        task_id: str,
        connector_id: str,
        kb_id: str,
        tenant_id: str,
        file_list,
        delete_batch_size: int = 100,
        snapshot_revision: str | None = None,
        confirmation_scans: int = 1,
    ):
        from api.db.services.file_service import FileService

        links = Connector2KbService.query(connector_id=connector_id, kb_id=kb_id)
        if not links:
            return 0, []
        link = links[0]

        e, conn = cls.get_by_id(connector_id)
        if not e:
            return 0, []

        e, kb = KnowledgebaseService.get_by_id(kb_id)
        if not e:
            return 0, ["Dataset not found."]
        if str(conn.tenant_id) != str(kb.tenant_id):
            return 0, ["Connector owner does not match the dataset owner."]

        source_type = f"{conn.source}/{conn.id}"
        retain_doc_ids = {doc_id for file in file_list for doc_id in (hash128(f"{connector_id}:{file.id}"), hash128(f"{kb_id}:{connector_id}:{file.id}"))}
        existing_docs = DocumentService.list_doc_headers_by_kb_and_source_type(
            kb_id,
            source_type,
        )
        stale_doc_ids = [
            doc["id"] for doc in existing_docs if doc["id"] not in retain_doc_ids
        ]

        state = dict(getattr(link, "sync_state", None) or {})
        previous_missing_counts = dict(state.get("missing_counts") or {})
        missing_counts = {
            doc_id: int(previous_missing_counts.get(doc_id, 0)) + 1
            for doc_id in stale_doc_ids
        }
        confirmation_scans = max(1, int(confirmation_scans))
        confirmed_stale_ids = [
            doc_id
            for doc_id in stale_doc_ids
            if missing_counts[doc_id] >= confirmation_scans
        ]

        state["missing_counts"] = missing_counts
        if snapshot_revision is not None:
            state["last_successful_revision"] = str(snapshot_revision)

        def persist_sync_state():
            link_id = getattr(link, "id", None)
            if link_id:
                Connector2KbService.update_by_id(link_id, {"sync_state": state})

        if not confirmed_stale_ids:
            persist_sync_state()
            return 0, []

        errors = []
        for offset in range(0, len(confirmed_stale_ids), delete_batch_size):
            err = FileService.delete_docs(
                confirmed_stale_ids[offset : offset + delete_batch_size],
                kb.tenant_id,
            )
            if err:
                errors.append(err)

        confirmed_stale_id_set = set(confirmed_stale_ids)
        remaining_doc_ids = {
            doc["id"]
            for doc in DocumentService.list_doc_headers_by_kb_and_source_type(
                kb_id,
                source_type,
            )
            if doc["id"] in confirmed_stale_id_set
        }
        removed_doc_ids = confirmed_stale_id_set - remaining_doc_ids
        for doc_id in removed_doc_ids:
            missing_counts.pop(doc_id, None)
        state["missing_counts"] = missing_counts

        managed_folder_ids = set(state.get("managed_folder_ids") or [])
        if managed_folder_ids:
            kb_root = FileService.get_or_create_kb_root(kb.id, kb.tenant_id)
            remaining_folder_ids = FileService.remove_empty_managed_folders(
                managed_folder_ids,
                kb_root["id"],
                kb.tenant_id,
            )
            state["managed_folder_ids"] = sorted(remaining_folder_ids)
        persist_sync_state()

        removed_count = len(removed_doc_ids)
        SyncLogsService.increase_removed_docs(
            task_id,
            removed_count,
            "\n".join(errors),
            len(errors),
        )
        return removed_count, errors


class SyncLogsService(CommonService):
    model = SyncLogs

    
    @classmethod
    def list_sync_tasks(cls, connector_id=None, page_number=None, items_per_page=15) -> Tuple[List[dict], int]:
        fields = [
            cls.model.id,
            cls.model.connector_id,
            cls.model.task_type,
            cls.model.kb_id,
            cls.model.update_date,
            cls.model.new_docs_indexed,
            cls.model.total_docs_indexed,
            cls.model.docs_removed_from_index,
            cls.model.error_msg,
            cls.model.error_count,
            cls.model.time_started.alias("time_started"),
            Connector.refresh_freq.alias("refresh_freq"),
            Connector.prune_freq.alias("prune_freq"),
            Knowledgebase.name.alias("kb_name"),
            cls.model.status,
        ]
        if not connector_id:
            fields.append(Connector.config)
            
        query = cls.model.select(*fields)\
            .join(Connector, on=(cls.model.connector_id==Connector.id))\
            .join(Connector2Kb, on=(cls.model.kb_id==Connector2Kb.kb_id))\
            .join(Knowledgebase, on=(cls.model.kb_id==Knowledgebase.id))

        if connector_id:
            query = query.where(cls.model.connector_id == connector_id)
        else:
            expr = cls._due_task_cutoff_sql("refresh_freq")
            query = query.where(
                Connector.input_type == InputType.POLL,
                Connector.status == TaskStatus.SCHEDULE,
                cls.model.status == TaskStatus.SCHEDULE,
                cls.model.update_date < expr
            )

        query = query.distinct().order_by(cls.model.update_time.desc())
        total = query.count()
        if page_number:
            query = query.paginate(page_number, items_per_page)

        return list(query.dicts()), total

    @classmethod
    def list_due_sync_tasks(cls) -> List[dict]:
        return cls._list_due_tasks_for_freq(
            ConnectorTaskType.SYNC,
            "refresh_freq",
        )

    @classmethod
    def list_due_prune_tasks(cls) -> List[dict]:
        tasks = cls._list_due_tasks_for_freq(
            ConnectorTaskType.PRUNE,
            "prune_freq",
        )
        return [
            task for task in tasks
            # Prune is opt-in at the connector config level; keep the scheduler
            # blind to prune_freq until the flag is enabled.
            if bool((task.get("config") or {}).get("sync_deleted_files"))
            and int(task.get("prune_freq") or 0) > 0
        ]

    @classmethod
    def _due_task_cutoff_sql(cls, freq_field: str):
        if freq_field not in {"refresh_freq", "prune_freq"}:
            raise ValueError(f"Unsupported connector frequency field: {freq_field}")
        database_type = os.getenv("DB_TYPE", "mysql")
        if "postgres" in database_type.lower():
            return SQL(
                f"NOW() AT TIME ZONE '{TIMEZONE}' - make_interval(mins => t2.{freq_field})"
            )
        return SQL(f"UTC_TIMESTAMP() - INTERVAL `t2`.`{freq_field}` MINUTE")

    @classmethod
    def _list_due_tasks_for_freq(cls, task_type: str, freq_field: str) -> List[dict]:
        fields = [
            cls.model.id,
            cls.model.connector_id,
            cls.model.task_type,
            cls.model.kb_id,
            cls.model.update_date,
            cls.model.poll_range_start,
            cls.model.poll_range_end,
            cls.model.new_docs_indexed,
            cls.model.total_docs_indexed,
            cls.model.error_msg,
            cls.model.full_exception_trace,
            cls.model.error_count,
            Connector.name,
            Connector.source,
            Connector.tenant_id,
            Connector.timeout_secs,
            Connector.config,
            Connector.refresh_freq,
            Connector.prune_freq,
            Knowledgebase.name.alias("kb_name"),
            Knowledgebase.avatar.alias("kb_avatar"),
            Connector2Kb.auto_parse,
            cls.model.from_beginning.alias("reindex"),
            cls.model.status,
            cls.model.update_time,
        ]

        query = cls.model.select(*fields)\
            .join(Connector, on=(cls.model.connector_id==Connector.id))\
            .join(Connector2Kb, on=(cls.model.kb_id==Connector2Kb.kb_id))\
            .join(Knowledgebase, on=(cls.model.kb_id==Knowledgebase.id))

        query = query.where(
            Connector.input_type == InputType.POLL,
            Connector.status == TaskStatus.SCHEDULE,
            cls.model.status == TaskStatus.SCHEDULE,
            cls.model.task_type == task_type,
        )

        expr = cls._due_task_cutoff_sql(freq_field)
        query = query.where(cls.model.update_date < expr)

        return list(query.distinct().order_by(cls.model.update_time.desc()).dicts())

    @classmethod
    def start(cls, id, connector_id):
        cls.update_by_id(id, {"status": TaskStatus.RUNNING, "time_started": datetime.now().strftime('%Y-%m-%d %H:%M:%S') })
        ConnectorService.update_by_id(connector_id, {"status": TaskStatus.RUNNING})

    @classmethod
    def done(cls, id, connector_id):
        cls.update_by_id(id, {"status": TaskStatus.DONE})
        ConnectorService.update_by_id(connector_id, {"status": TaskStatus.DONE})

    @classmethod
    def schedule(
        cls,
        connector_id,
        kb_id,
        poll_range_start=None,
        reindex=False,
        total_docs_indexed=0,
        task_type=ConnectorTaskType.SYNC,
    ):
        try:
            if cls.model.select().where(cls.model.kb_id == kb_id, cls.model.connector_id == connector_id).count() > 100:
                rm_ids = [m.id for m in cls.model.select(cls.model.id).where(cls.model.kb_id == kb_id, cls.model.connector_id == connector_id).order_by(cls.model.update_time.asc()).limit(70)]
                deleted = cls.model.delete().where(cls.model.id.in_(rm_ids)).execute()
                logging.info(f"[SyncLogService] Cleaned {deleted} old logs.")
        except Exception as e:
            logging.exception(e)

        try:
            e = cls.query(
                kb_id=kb_id,
                connector_id=connector_id,
                status=TaskStatus.SCHEDULE,
                task_type=task_type,
            )
            if e:
                logging.warning(
                    "%s--%s already has a scheduled %s task.",
                    kb_id,
                    connector_id,
                    task_type,
                )
                return None
            reindex = "1" if reindex else "0"
            ConnectorService.update_by_id(connector_id, {"status": TaskStatus.SCHEDULE})
            return cls.save(**{
                "id": get_uuid(),
                "kb_id": kb_id, "status": TaskStatus.SCHEDULE, "connector_id": connector_id,
                "task_type": task_type,
                "poll_range_start": poll_range_start, "from_beginning": reindex,
                "total_docs_indexed": total_docs_indexed,
                "time_started": datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            })
        except Exception as e:
            logging.exception(e)
            task = cls.get_latest_task(connector_id, kb_id, task_type)
            if task:
                cls.model.update(status=TaskStatus.SCHEDULE,
                                 poll_range_start=poll_range_start,
                                 error_msg=cls.model.error_msg + str(e),
                                 full_exception_trace=cls.model.full_exception_trace + str(e)
                                 ) \
                .where(cls.model.id == task.id).execute()
                ConnectorService.update_by_id(connector_id, {"status": TaskStatus.SCHEDULE})

    @classmethod
    def increase_docs(cls, id, max_update, doc_num, err_msg="", error_count=0):
        # Keep sync monotonic.
        cls.model.update(new_docs_indexed=cls.model.new_docs_indexed + doc_num,
                         total_docs_indexed=cls.model.total_docs_indexed + doc_num,
                         poll_range_start=fn.COALESCE(fn.GREATEST(cls.model.poll_range_start, max_update), max_update),
                         poll_range_end=fn.COALESCE(fn.GREATEST(cls.model.poll_range_end, max_update), max_update),
                         error_msg=cls.model.error_msg + err_msg,
                         error_count=cls.model.error_count + error_count,
                         update_time=current_timestamp(),
                         update_date=timestamp_to_date(current_timestamp())
                         )\
            .where(cls.model.id == id).execute()

    @classmethod
    def increase_removed_docs(cls, id, removed_count, err_msg="", error_count=0):
        cls.model.update(
            docs_removed_from_index=cls.model.docs_removed_from_index + removed_count,
            error_msg=cls.model.error_msg + err_msg,
            error_count=cls.model.error_count + error_count,
            update_time=current_timestamp(),
            update_date=timestamp_to_date(current_timestamp()),
        ).where(cls.model.id == id).execute()

    @classmethod
    def duplicate_and_parse(cls, kb, docs, tenant_id, src, auto_parse=True):
        from api.db.services.file_service import FileService
        if not docs:
            return None

        class FileObj(BaseModel):
            id: str
            filename: str
            blob: bytes
            fingerprint: Optional[str] = None

            def read(self) -> bytes:
                return self.blob

        errs = []
        files = []
        relative_paths = []
        for d in docs:
            filename = d["semantic_identifier"] + (
                f"{d['extension']}" if d["semantic_identifier"][::-1].find(d["extension"][::-1]) < 0 else ""
            )
            files.append(
                FileObj(
                    id=d["id"],
                    filename=filename,
                    blob=d["blob"],
                    fingerprint=d.get("fingerprint"),
                )
            )
            relative_paths.append(d.get("relative_path"))

        upload_kwargs = {"relative_paths": relative_paths} if all(relative_paths) else {}
        managed_folder_ids = None
        if src.startswith("svn/"):
            managed_folder_ids = set()
            upload_kwargs["managed_folder_ids"] = managed_folder_ids
        doc_ids = []
        err, doc_blob_pairs = FileService.upload_document(
            kb,
            files,
            kb.tenant_id,
            created_by=tenant_id,
            src=src,
            **upload_kwargs,
        )
        errs.extend(err)
        if managed_folder_ids:
            try:
                Connector2KbService.add_managed_folder_ids(
                    docs[0]["connector_id"],
                    kb.id,
                    managed_folder_ids,
                )
            except Exception as exc:
                msg = f"Failed to record SVN managed folders: {exc}"
                LOGGER.exception(msg)
                errs.append(msg)

        # Create a mapping from filename to metadata for later use
        metadata_map = {}
        for d in docs:
            if d.get("metadata"):
                filename = d["semantic_identifier"]+(f"{d['extension']}" if d["semantic_identifier"][::-1].find(d['extension'][::-1])<0 else "")
                metadata_map[filename] = d["metadata"]

        kb_table_num_map = {}
        for doc, _ in doc_blob_pairs:
            doc_ids.append(doc["id"])
            
            # Set metadata if available for this document
            if doc["name"] in metadata_map:
                DocMetadataService.update_document_metadata(doc["id"], metadata_map[doc["name"]])
            
            if not auto_parse or auto_parse == "0":
                continue
            if src.startswith("svn/"):
                DocumentService.run(
                    kb.tenant_id,
                    doc,
                    kb_table_num_map,
                    content_version=doc.get("content_hash"),
                )
            else:
                DocumentService.run(kb.tenant_id, doc, kb_table_num_map)

        return errs, doc_ids

    @classmethod
    def get_latest_task(cls, connector_id, kb_id, task_type=None):
        query = cls.model.select().where(
            cls.model.connector_id==connector_id,
            cls.model.kb_id == kb_id
        )
        if task_type is not None:
            query = query.where(cls.model.task_type == task_type)
        return query.order_by(cls.model.update_time.desc()).first()


class Connector2KbService(CommonService):
    model = Connector2Kb

    @classmethod
    @DB.connection_context()
    def add_managed_folder_ids(cls, connector_id: str, kb_id: str, folder_ids: set[str]):
        if not folder_ids:
            return
        with DB.atomic():
            link = (
                cls.model.select()
                .where(
                    cls.model.connector_id == connector_id,
                    cls.model.kb_id == kb_id,
                )
                .for_update()
                .first()
            )
            if link is None:
                return
            state = dict(link.sync_state or {})
            managed_ids = set(state.get("managed_folder_ids") or [])
            managed_ids.update(folder_ids)
            state["managed_folder_ids"] = sorted(managed_ids)
            cls.update_by_id_in_transaction(link.id, {"sync_state": state})

    @classmethod
    def link_connectors(cls, kb_id:str, connectors: list[dict], tenant_id:str):
        found, kb = KnowledgebaseService.get_by_id(kb_id)
        if not found:
            return "Dataset not found."
        if str(kb.tenant_id) != str(tenant_id):
            return "Dataset owner context does not match the requested tenant."

        resolved_connectors = {}
        for connector in connectors:
            connector_id = connector["id"]
            found, full_connector = ConnectorService.get_by_id(connector_id)
            if not found:
                return f"Connector '{connector_id}' not found."
            if str(full_connector.tenant_id) != str(kb.tenant_id):
                return f"Connector '{connector_id}' owner does not match the dataset owner."
            resolved_connectors[connector_id] = full_connector

        arr = cls.query(kb_id=kb_id)
        old_conn_ids = [a.connector_id for a in arr]
        connector_ids = []
        for conn in connectors:
            conn_id = conn["id"]
            connector_ids.append(conn_id)
            if conn_id in old_conn_ids:
                cls.filter_update([cls.model.connector_id==conn_id, cls.model.kb_id==kb_id], {"auto_parse": conn.get("auto_parse", "1")})
                continue
            cls.save(**{
                "id": get_uuid(),
                "connector_id": conn_id,
                "kb_id": kb_id,
                "auto_parse": conn.get("auto_parse", "1")
            })
            SyncLogsService.schedule(conn_id, kb_id, reindex=True, task_type=ConnectorTaskType.SYNC)
            full_conn = resolved_connectors[conn_id]
            if (full_conn.config or {}).get("sync_deleted_files"):
                SyncLogsService.schedule(conn_id, kb_id, task_type=ConnectorTaskType.PRUNE)

        errs = []
        for conn_id in old_conn_ids:
            if conn_id in connector_ids:
                continue
            cls.filter_delete([cls.model.kb_id==kb_id, cls.model.connector_id==conn_id])
            e, conn = ConnectorService.get_by_id(conn_id)
            if not e:
                continue
            #SyncLogsService.filter_delete([SyncLogs.connector_id==conn_id, SyncLogs.kb_id==kb_id])
            # Do not delete docs while unlinking.
            SyncLogsService.filter_update([SyncLogs.connector_id==conn_id, SyncLogs.kb_id==kb_id, SyncLogs.status.in_([TaskStatus.SCHEDULE, TaskStatus.RUNNING])], {"status": TaskStatus.CANCEL})
            #docs = DocumentService.query(source_type=f"{conn.source}/{conn.id}")
            #err = FileService.delete_docs([d.id for d in docs], tenant_id)
            #if err:
            #    errs.append(err)
        return "\n".join(errs)

    @classmethod
    def list_connectors(cls, kb_id):
        fields = [
            Connector.id,
            Connector.source,
            Connector.name,
            cls.model.auto_parse,
            Connector.status
        ]
        return list(cls.model.select(*fields)\
                    .join(Connector, on=(cls.model.connector_id==Connector.id))\
                    .where(
                        cls.model.kb_id==kb_id
                    ).dicts()
        )
