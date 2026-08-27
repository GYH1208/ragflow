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
#  limitations under the License.
#
from quart import request

from api.apps import login_required
from api.apps.services.knowledge_file_service import KnowledgeFileService
from api.common.check_team_permission import check_kb_team_permission
from api.db.services.knowledgebase_service import KnowledgebaseService
from api.utils.api_utils import (
    add_tenant_id_to_kwargs,
    get_error_argument_result,
    get_error_data_result,
    get_error_permission_result,
    get_json_result,
    get_request_json,
    get_result,
    server_error_response,
)
from common.constants import RetCode


def _get_authorized_kb(dataset_id, tenant_id):
    found, kb = KnowledgebaseService.get_by_id(dataset_id)
    if not found:
        raise LookupError(f"Knowledge base {dataset_id} does not exist.")
    if not check_kb_team_permission(kb, tenant_id):
        raise PermissionError("You do not have permission to manage this knowledge base.")
    return kb


def _parse_positive_int(name, default, maximum=None):
    raw_value = request.args.get(name, default)
    try:
        value = int(raw_value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be an integer.") from exc
    if value < 1 or (maximum is not None and value > maximum):
        range_message = f" between 1 and {maximum}" if maximum is not None else " greater than zero"
        raise ValueError(f"{name} must be{range_message}.")
    return value


def _parse_list_arg(*names):
    for name in names:
        if hasattr(request.args, "getlist"):
            values = request.args.getlist(name)
        else:
            raw_value = request.args.get(name)
            values = [] if raw_value is None else [raw_value]
        if values:
            return [item for value in values for item in str(value).split(",") if item]
    return []


def _error_response(exc):
    if isinstance(exc, PermissionError):
        return get_error_permission_result(str(exc))
    if isinstance(exc, (ValueError, TypeError)):
        return get_error_argument_result(str(exc))
    if isinstance(exc, LookupError):
        return get_error_data_result(str(exc))
    return server_error_response(exc)


@manager.route("/datasets/<dataset_id>/entries", methods=["GET"])  # noqa: F821
@login_required
@add_tenant_id_to_kwargs
async def list_entries(dataset_id, tenant_id):
    try:
        kb = _get_authorized_kb(dataset_id, tenant_id)
        page = _parse_positive_int("page", 1)
        page_size = _parse_positive_int("page_size", 20, maximum=100)
        desc = str(request.args.get("desc", "true")).lower() not in {"false", "0", "no"}
        result = KnowledgeFileService.list_entries(
            kb,
            kb.tenant_id,
            parent_id=request.args.get("parent_id", ""),
            page=page,
            page_size=page_size,
            orderby=request.args.get("orderby", "create_time"),
            desc=desc,
            keywords=request.args.get("keywords", "").strip(),
            filters={
                "run_status": _parse_list_arg("run_status"),
                "types": _parse_list_arg("types", "type"),
                "suffix": _parse_list_arg("suffix"),
            },
        )
        return get_result(data=result)
    except Exception as exc:
        return _error_response(exc)


@manager.route("/datasets/<dataset_id>/folders/<folder_id>/ancestors", methods=["GET"])  # noqa: F821
@login_required
@add_tenant_id_to_kwargs
async def get_ancestors(dataset_id, folder_id, tenant_id):
    try:
        kb = _get_authorized_kb(dataset_id, tenant_id)
        return get_result(data=KnowledgeFileService.get_ancestors(kb, kb.tenant_id, folder_id))
    except Exception as exc:
        return _error_response(exc)


@manager.route("/datasets/<dataset_id>/folders", methods=["POST"])  # noqa: F821
@login_required
@add_tenant_id_to_kwargs
async def create_folder(dataset_id, tenant_id):
    try:
        kb = _get_authorized_kb(dataset_id, tenant_id)
        payload = await get_request_json()
        parent_id = payload.get("parent_id")
        name = payload.get("name")
        if not parent_id or name is None:
            raise ValueError("parent_id and name are required.")
        return get_result(
            data=KnowledgeFileService.create_folder(
                kb,
                kb.tenant_id,
                parent_id,
                name,
                created_by=tenant_id,
            )
        )
    except Exception as exc:
        return _error_response(exc)


@manager.route("/datasets/<dataset_id>/entries/<entry_id>", methods=["PATCH"])  # noqa: F821
@login_required
@add_tenant_id_to_kwargs
async def rename_entry(dataset_id, entry_id, tenant_id):
    try:
        kb = _get_authorized_kb(dataset_id, tenant_id)
        payload = await get_request_json()
        if "name" not in payload:
            raise ValueError("name is required.")
        return get_result(data=KnowledgeFileService.rename_entry(kb, kb.tenant_id, entry_id, payload["name"]))
    except Exception as exc:
        return _error_response(exc)


@manager.route("/datasets/<dataset_id>/entries/move", methods=["POST"])  # noqa: F821
@login_required
@add_tenant_id_to_kwargs
async def move_entries(dataset_id, tenant_id):
    try:
        kb = _get_authorized_kb(dataset_id, tenant_id)
        payload = await get_request_json()
        ids = payload.get("ids")
        destination_id = payload.get("destination_id")
        if not isinstance(ids, list) or not destination_id:
            raise ValueError("ids must be a list and destination_id is required.")
        return get_result(data=KnowledgeFileService.move_entries(kb, kb.tenant_id, ids, destination_id))
    except Exception as exc:
        return _error_response(exc)


@manager.route("/datasets/<dataset_id>/entries/delete-preview", methods=["POST"])  # noqa: F821
@login_required
@add_tenant_id_to_kwargs
async def preview_delete_entries(dataset_id, tenant_id):
    try:
        kb = _get_authorized_kb(dataset_id, tenant_id)
        payload = await get_request_json()
        ids = payload.get("ids")
        if not isinstance(ids, list):
            raise ValueError("ids must be a list.")
        count = KnowledgeFileService.count_descendant_documents(kb, kb.tenant_id, ids)
        return get_result(data={"document_count": count})
    except Exception as exc:
        return _error_response(exc)


@manager.route("/datasets/<dataset_id>/entries", methods=["DELETE"])  # noqa: F821
@login_required
@add_tenant_id_to_kwargs
async def delete_entries(dataset_id, tenant_id):
    try:
        kb = _get_authorized_kb(dataset_id, tenant_id)
        payload = await get_request_json()
        ids = payload.get("ids")
        if not isinstance(ids, list):
            raise ValueError("ids must be a list.")
        result = KnowledgeFileService.delete_entries(kb, kb.tenant_id, ids)
        if result["failed"]:
            return get_json_result(
                code=RetCode.SERVER_ERROR,
                message="Some entries could not be deleted.",
                data=result,
            )
        return get_result(data=result)
    except Exception as exc:
        return _error_response(exc)
