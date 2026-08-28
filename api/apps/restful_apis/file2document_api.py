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

import logging
from pathlib import Path

from api.apps import current_user, login_required
from api.common.check_team_permission import check_file_team_permission, check_kb_team_permission
from api.db import FileType, TeamMemberState, TenantPermission
from api.db.db_models import DB, Document, File, File2Document, Knowledgebase, Task, TeamMember
from api.db.services.document_service import DocumentService
from api.db.services.file2document_service import File2DocumentService
from api.db.services.file_service import FileService
from api.db.services.knowledgebase_service import KnowledgebaseService
from api.db.services.team_service import TeamService, select_for_update
from api.utils.api_utils import get_data_error_result, get_json_result, get_request_json, server_error_response, validate_request
from common.constants import StatusEnum
from common.misc_utils import get_uuid, thread_pool_exec

logger = logging.getLogger(__name__)
CROSS_OWNER_ERROR = "The source file and target dataset must have the same owner."


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


def _ensure_same_owner(files, target_kbs):
    if any(file.tenant_id != kb.tenant_id for file in files for kb in target_kbs):
        raise PermissionError(CROSS_OWNER_ERROR)


def _can_access_locked_kb(kb, actor_id, active_team_ids, owned_team_ids):
    if kb.status != StatusEnum.VALID.value:
        return False
    if kb.tenant_id == actor_id:
        return True
    return (
        kb.permission == TenantPermission.TEAM.value
        and kb.team_id in active_team_ids
        and kb.team_id in owned_team_ids
    )


def _authorize_locked_conversion(
    files,
    knowledge_bases,
    links,
    documents,
    actor_id,
    active_team_ids,
    owned_team_ids,
):
    accessible_kb_ids = {
        kb.id
        for kb in knowledge_bases
        if _can_access_locked_kb(kb, actor_id, active_team_ids, owned_team_ids)
    }
    if len(accessible_kb_ids) != len(knowledge_bases):
        raise PermissionError("No authorization.")

    documents_by_id = {document.id: document for document in documents}
    accessible_file_ids = {
        link.file_id
        for link in links
        if link.document_id in documents_by_id and documents_by_id[link.document_id].kb_id in accessible_kb_ids
    }
    if any(file.tenant_id != actor_id and file.id not in accessible_file_ids for file in files):
        raise PermissionError("No authorization.")


def _load_conversion_state(file_ids, kb_ids, actor_id):
    files = []
    for file_id in list(dict.fromkeys(file_ids)):
        found, file = FileService.get_by_id(file_id)
        if not found or not file:
            raise LookupError("File not found!")
        if not check_file_team_permission(file, actor_id):
            raise PermissionError("No authorization.")
        files.append(file)

    target_kbs = []
    for kb_id in list(dict.fromkeys(kb_ids)):
        found, kb = KnowledgebaseService.get_by_id(kb_id)
        if not found:
            raise LookupError("Can't find this dataset!")
        if not check_kb_team_permission(kb, actor_id):
            raise PermissionError("No authorization.")
        target_kbs.append(kb)

    _ensure_same_owner(files, target_kbs)
    return files, target_kbs, _authorize_existing_documents([file.id for file in files], actor_id)


def _stage_document_replacements(files, target_kbs, actor_id):
    staged = []
    for file in files:
        for kb in target_kbs:
            document = {
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
                "size": file.size,
            }
            staged.append((file.id, document))
    return staged


def _insert_staged_document(file_id, document):
    inserted = DocumentService.insert_in_transaction(document)
    File2DocumentService.insert_in_transaction(
        {
            "id": get_uuid(),
            "file_id": file_id,
            "document_id": inserted.id,
        }
    )
    return inserted


def _delete_original_document(document):
    if File2DocumentService.delete_by_document_id_in_transaction(document.id) < 1:
        raise RuntimeError("File/document relationship changed concurrently.")
    if not DocumentService.delete_in_transaction(document):
        raise RuntimeError("Document changed concurrently.")


def _replace_document_rows(files, target_kbs, existing_documents_by_file, actor_id):
    """Atomically stage replacements, then delete originals and clean external state."""
    file_ids = [file.id for file in files]
    target_kb_ids = [kb.id for kb in target_kbs]
    old_documents = {
        document.id: document
        for file_id in file_ids
        for document, _owner_tenant_id in existing_documents_by_file.get(file_id, [])
    }
    old_kb_ids = {document.kb_id for document in old_documents.values()}
    preliminary_kbs = {kb.id: kb for kb in target_kbs}
    for kb_id in old_kb_ids:
        found, kb = KnowledgebaseService.get_by_id(kb_id)
        if not found:
            raise LookupError("Cannot find a dataset associated with this file.")
        preliminary_kbs[kb_id] = kb

    cleanup = []
    with DB.atomic():
        team_owners = sorted(
            {
                (kb.team_id, kb.tenant_id)
                for kb in preliminary_kbs.values()
                if getattr(kb, "permission", None) == TenantPermission.TEAM.value and getattr(kb, "team_id", None)
            }
        )
        owned_team_ids = set()
        for team_id, owner_id in team_owners:
            if TeamService.get_owned_team_for_update(team_id, owner_id) is None:
                raise PermissionError("No authorization.")
            owned_team_ids.add(team_id)

        locked_files = list(
            select_for_update(File.select().where(File.id.in_(file_ids))).order_by(File.id)
        )
        all_kb_ids = set(target_kb_ids) | old_kb_ids
        locked_kbs = list(
            select_for_update(
                Knowledgebase.select().where(
                    Knowledgebase.id.in_(all_kb_ids),
                    Knowledgebase.status == StatusEnum.VALID.value,
                )
            ).order_by(Knowledgebase.id)
        )
        if {file.id for file in locked_files} != set(file_ids) or {kb.id for kb in locked_kbs} != all_kb_ids:
            raise RuntimeError("Conversion inputs changed concurrently.")

        files_by_id = {file.id: file for file in locked_files}
        kbs_by_id = {kb.id: kb for kb in locked_kbs}
        if any(
            (kbs_by_id[kb_id].tenant_id, kbs_by_id[kb_id].permission, kbs_by_id[kb_id].team_id)
            != (kb.tenant_id, kb.permission, kb.team_id)
            for kb_id, kb in preliminary_kbs.items()
        ):
            raise RuntimeError("Dataset assignments changed concurrently.")
        ordered_files = [files_by_id[file_id] for file_id in file_ids]
        ordered_targets = [kbs_by_id[kb_id] for kb_id in target_kb_ids]
        _ensure_same_owner(ordered_files, ordered_targets)

        team_ids = sorted({team_id for team_id, _owner_id in team_owners})
        if team_ids:
            locked_memberships = list(
                select_for_update(
                    TeamMember.select().where(
                        TeamMember.team_id.in_(team_ids),
                        TeamMember.user_id == actor_id,
                        TeamMember.state == TeamMemberState.ACTIVE.value,
                        TeamMember.status == StatusEnum.VALID.value,
                    )
                ).order_by(TeamMember.id)
            )
        else:
            locked_memberships = []
        active_team_ids = {membership.team_id for membership in locked_memberships}

        selected_links = list(
            select_for_update(File2Document.select().where(File2Document.file_id.in_(file_ids))).order_by(
                File2Document.id
            )
        )
        current_old_document_ids = {link.document_id for link in selected_links}
        if current_old_document_ids != set(old_documents):
            raise RuntimeError("File/document relationships changed concurrently.")

        if current_old_document_ids:
            list(
                select_for_update(
                    File2Document.select().where(File2Document.document_id.in_(current_old_document_ids))
                ).order_by(File2Document.id)
            )
            locked_documents = list(
                select_for_update(Document.select().where(Document.id.in_(current_old_document_ids))).order_by(Document.id)
            )
        else:
            locked_documents = []
        if {document.id for document in locked_documents} != current_old_document_ids:
            raise RuntimeError("Documents changed concurrently.")

        _authorize_locked_conversion(
            ordered_files,
            locked_kbs,
            selected_links,
            locked_documents,
            actor_id,
            active_team_ids,
            owned_team_ids,
        )

        staged = _stage_document_replacements(ordered_files, ordered_targets, actor_id)
        for file_id, document in staged:
            _insert_staged_document(file_id, document)

        if current_old_document_ids:
            Task.delete().where(Task.doc_id.in_(current_old_document_ids)).execute()
        for document in locked_documents:
            _delete_original_document(document)
            cleanup.append((document, kbs_by_id[document.kb_id].tenant_id))

    for document, owner_tenant_id in cleanup:
        DocumentService.cleanup_document_resources(document, owner_tenant_id)


def _convert_files(file_ids, existing_documents_by_file, target_kbs, actor_id):
    """Revalidate current state and complete the replacement before returning."""
    files, fresh_target_kbs, fresh_existing_documents = _load_conversion_state(
        file_ids,
        [kb.id for kb in target_kbs],
        actor_id,
    )
    expected_document_ids = {
        file_id: {document.id for document, _owner_id in documents}
        for file_id, documents in existing_documents_by_file.items()
    }
    fresh_document_ids = {
        file_id: {document.id for document, _owner_id in documents}
        for file_id, documents in fresh_existing_documents.items()
    }
    if expected_document_ids != fresh_document_ids:
        raise RuntimeError("File/document relationships changed concurrently.")
    _replace_document_rows(files, fresh_target_kbs, fresh_existing_documents, actor_id)


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

        # Validate all kb_ids exist before conversion work
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
        expanded_files = []
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
            expanded_files.append(file)

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
            _ensure_same_owner(expanded_files, list(kb_map.values()))
        except PermissionError as exc:
            logger.warning(
                "user_id=%s resource_type=file_to_dataset_link resource_id=batch action=validate_owner result=denied file_ids=%s kb_ids=%s",
                user_id,
                all_file_ids,
                kb_ids,
            )
            return get_data_error_result(message=str(exc))

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

        # Keep the event loop responsive, but await the destructive conversion
        # so the success response truthfully means the replacement committed.
        await thread_pool_exec(
            _convert_files,
            all_file_ids,
            existing_documents_by_file,
            list(kb_map.values()),
            user_id,
        )
        logger.info(
            "user_id=%s resource_type=file_to_dataset_link resource_id=batch action=convert result=completed file_ids=%s kb_ids=%s",
            user_id,
            all_file_ids,
            kb_ids,
        )
        return get_json_result(data=True)
    except Exception as e:
        return server_error_response(e)
