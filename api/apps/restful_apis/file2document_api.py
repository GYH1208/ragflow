#
#  Copyright 2026 The InfiniFlow Authors. All Rights Reserved.
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
#  limitations under the License
#

import asyncio
import logging
from pathlib import Path

from api.apps import current_user, login_required
from api.common.check_team_permission import check_file_team_permission, check_kb_team_permission
from api.db import FileType
from api.db.services.document_service import DocumentService
from api.db.services.file2document_service import File2DocumentService
from api.db.services.file_service import FileService
from api.db.services.knowledgebase_service import KnowledgebaseService
from api.utils.api_utils import get_data_error_result, get_json_result, get_request_json, server_error_response, validate_request
from common.misc_utils import get_uuid

logger = logging.getLogger(__name__)


def _authorize_existing_documents(file_ids, actor_id):
    authorized_documents = {}
    knowledge_bases = {}
    for file_id in file_ids:
        authorized_documents[file_id] = []
        for link in File2DocumentService.get_by_file_id(file_id):
            found, document = DocumentService.get_by_id(link.document_id)
            if not found:
                raise LookupError("Cannot find a document associated with this file.")
            kb = knowledge_bases.get(document.kb_id)
            if kb is None:
                found, kb = KnowledgebaseService.get_by_id(document.kb_id)
                if not found:
                    raise LookupError("Cannot find a dataset associated with this file.")
                knowledge_bases[document.kb_id] = kb
            if not check_kb_team_permission(kb, actor_id):
                raise PermissionError("No authorization.")
            authorized_documents[file_id].append((document, kb.tenant_id))
    return authorized_documents


def _convert_files(file_ids, existing_documents_by_file, target_kbs, actor_id):
    """Synchronous worker: delete old docs and insert new ones for the given file/kb pairs."""
    file_ids = list(dict.fromkeys(file_ids))
    for id in file_ids:
        for document, owner_tenant_id in existing_documents_by_file.get(id, []):
            DocumentService.remove_document(document, owner_tenant_id)
            File2DocumentService.delete_by_document_id(document.id)

        e, file = FileService.get_by_id(id)
        if not e:
            continue

        for kb in target_kbs:
            owner_tenant_id = kb.tenant_id
            if not owner_tenant_id:
                logging.warning("owner tenant not found for kb_id=%s, skipping document conversion", kb.id)
                continue
            doc = DocumentService.insert({
                "id": get_uuid(),
                "kb_id": kb.id,
                "parser_id": FileService.get_parser(file.type, file.name, kb.parser_id),
                "pipeline_id": kb.pipeline_id,
                "parser_config": kb.parser_config,
                "created_by": actor_id,
                "type": file.type,
                "name": file.name,
                "suffix": Path(file.name).suffix.lstrip("."),
                "location": file.location,
                "size": file.size
            })
            File2DocumentService.insert({
                "id": get_uuid(),
                "file_id": id,
                "document_id": doc.id,
            })


@manager.route('/files/link-to-datasets', methods=['POST'])  # noqa: F821
@login_required
@validate_request("file_ids", "kb_ids")
async def convert():
    req = await get_request_json()
    kb_ids = req["kb_ids"]
    file_ids = req["file_ids"]

    try:
        files = FileService.get_by_ids(file_ids)
        files_set = {file.id: file for file in files}

        # Validate all files exist before starting any work
        for file_id in file_ids:
            if not files_set.get(file_id):
                logger.warning(
                    "user_id=%s resource_type=file resource_id=%s action=validate_file_lookup result=not_found file_ids=%s kb_ids=%s",
                    current_user.id,
                    file_id,
                    file_ids,
                    kb_ids,
                )
                return get_data_error_result(message="File not found!")

        # Validate all kb_ids exist before scheduling background work
        kb_map = {}
        for kb_id in kb_ids:
            e, kb = KnowledgebaseService.get_by_id(kb_id)
            if not e:
                logger.warning(
                    "user_id=%s resource_type=dataset resource_id=%s action=validate_dataset_lookup result=not_found file_ids=%s kb_ids=%s",
                    current_user.id,
                    kb_id,
                    file_ids,
                    kb_ids,
                )
                return get_data_error_result(message="Can't find this dataset!")
            kb_map[kb_id] = kb

        # Expand folders to their innermost file IDs
        all_file_ids = []
        for file_id in file_ids:
            file = files_set[file_id]
            if file.type == FileType.FOLDER.value:
                all_file_ids.extend(FileService.get_all_innermost_file_ids(file_id, []))
            else:
                all_file_ids.append(file_id)
        all_file_ids = list(dict.fromkeys(all_file_ids))

        user_id = current_user.id
        for file_id in all_file_ids:
            e, file = FileService.get_by_id(file_id)
            if not e or not file:
                logger.warning(
                    "user_id=%s resource_type=file resource_id=%s action=validate_expanded_file_lookup result=not_found file_ids=%s kb_ids=%s",
                    user_id,
                    file_id,
                    file_ids,
                    kb_ids,
                )
                return get_data_error_result(message="File not found!")
            if not check_file_team_permission(file, user_id):
                logger.warning(
                    "user_id=%s resource_type=file resource_id=%s action=authorize_file result=denied file_ids=%s kb_ids=%s",
                    user_id,
                    file_id,
                    file_ids,
                    kb_ids,
                )
                return get_data_error_result(message="No authorization.")

        for kb_id, kb in kb_map.items():
            if not check_kb_team_permission(kb, user_id):
                logger.warning(
                    "user_id=%s resource_type=dataset resource_id=%s action=authorize_dataset result=denied file_ids=%s kb_ids=%s",
                    user_id,
                    kb_id,
                    file_ids,
                    kb_ids,
                )
                return get_data_error_result(message="No authorization.")

        try:
            existing_documents_by_file = _authorize_existing_documents(all_file_ids, user_id)
        except PermissionError:
            logger.warning(
                "user_id=%s resource_type=file action=authorize_existing_documents result=denied file_ids=%s kb_ids=%s",
                user_id,
                all_file_ids,
                kb_ids,
            )
            return get_data_error_result(message="No authorization.")
        except LookupError as exc:
            logger.warning(
                "user_id=%s resource_type=file action=validate_existing_documents result=invalid file_ids=%s error=%s",
                user_id,
                all_file_ids,
                exc,
            )
            return get_data_error_result(message=str(exc))

        # Run the blocking DB work in a thread so the event loop is not blocked.
        # For large folders this prevents 504 Gateway Timeout by returning as
        # soon as the background task is scheduled.
        loop = asyncio.get_running_loop()
        future = loop.run_in_executor(
            None,
            _convert_files,
            all_file_ids,
            existing_documents_by_file,
            list(kb_map.values()),
            user_id,
        )
        future.add_done_callback(
            lambda f: logging.error("_convert_files failed: %s", f.exception()) if f.exception() else None
        )
        logger.info(
            "user_id=%s resource_type=file_to_dataset_link resource_id=batch action=schedule_convert result=scheduled file_ids=%s kb_ids=%s",
            user_id,
            all_file_ids,
            kb_ids,
        )
        return get_json_result(data=True)
    except Exception as e:
        return server_error_response(e)
