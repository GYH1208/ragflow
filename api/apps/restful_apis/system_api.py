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

import json
import logging
import secrets
from datetime import datetime, timedelta, timezone
from timeit import default_timer as timer

from quart import jsonify, request

from api.apps import login_required, current_user
from api.db import APIKeyType, UserTenantRole
from api.utils.api_utils import build_error_result, generate_confirmation_token, get_data_error_result, get_json_result
from api.utils.health_utils import run_health_checks, get_oceanbase_status
from api.utils.validation_utils import CreateAPIKeyReq, DeleteAPIKeyReq, UpdateAPIKeyReq, validate_and_parse_json_request
from common.versions import get_ragflow_version
from common.time_utils import current_timestamp, datetime_format
from api.db.db_models import APIToken, ScopedAPIToken
from api.db.services.api_service import APITokenService, ScopedAPITokenService
from api.db.services.knowledgebase_service import KnowledgebaseService
from api.db.services.user_service import UserTenantService
from common.constants import RetCode, StatusEnum
from common.log_utils import get_log_levels, set_log_level
from common import settings
from rag.utils.redis_conn import REDIS_CONN


def _management_error(code, message):
    return build_error_result(code=code, message=message)


def _management_failure():
    logging.error("API key management request failed")
    return _management_error(RetCode.SERVER_ERROR, "API key management request failed.")


def _managed_tenant_id():
    memberships = UserTenantService.query(user_id=current_user.id, status=StatusEnum.VALID.value)
    is_superuser = bool(getattr(current_user, "is_superuser", False))
    allowed_roles = {UserTenantRole.OWNER.value, UserTenantRole.ADMIN.value}
    eligible = [
        membership
        for membership in memberships
        if is_superuser or str(getattr(membership, "role", "")) in allowed_roles
    ]
    if not eligible:
        return None

    role_priority = {
        UserTenantRole.OWNER.value: 0,
        UserTenantRole.ADMIN.value: 1,
    }
    eligible.sort(
        key=lambda membership: (
            role_priority.get(str(getattr(membership, "role", "")), 2),
            str(membership.tenant_id),
        )
    )
    return eligible[0].tenant_id


def _record_data(record):
    if isinstance(record, dict):
        return record.copy()
    return record.to_dict().copy()


def _serialize_api_key(record, key_type):
    data = _record_data(record)
    result = {
        "token": data["token"],
        "name": data.get("name", "旧版 API Key"),
        "key_type": key_type.value,
        "legacy": bool(data.get("is_legacy", False)) if key_type is APIKeyType.FULL_ACCESS else False,
        "allowed_dataset_ids": list(data.get("allowed_dataset_ids") or []) if key_type is APIKeyType.RETRIEVAL else [],
        "expires_at": data.get("expires_at") if key_type is APIKeyType.RETRIEVAL else None,
        "enabled": bool(data.get("enabled", True)),
        "total_calls": data.get("total_calls", 0),
        "retrieval_calls": data.get("retrieval_calls", 0),
        "last_used_at": data.get("last_used_at"),
        "last_result": data.get("last_result"),
        "create_time": data.get("create_time"),
        "create_date": data.get("create_date"),
        "update_time": data.get("update_time"),
        "update_date": data.get("update_date"),
    }
    if key_type is APIKeyType.FULL_ACCESS:
        result["beta"] = data.get("beta")
    return result


def _datasets_belong_to_tenant(dataset_ids, tenant_id):
    return all(
        KnowledgebaseService.query(id=dataset_id, tenant_id=tenant_id, status=StatusEnum.VALID.value)
        for dataset_id in dataset_ids
    )


def _token_exists(token):
    return bool(APITokenService.query(token=token) or ScopedAPITokenService.query(token=token))


def _generate_unique_token(key_type):
    while True:
        token = generate_confirmation_token() if key_type is APIKeyType.FULL_ACCESS else f"ragflow-rk-{secrets.token_urlsafe(32)}"
        if not _token_exists(token):
            return token


def _expiry_from_days(expires_in_days, now):
    return None if expires_in_days is None else now + timedelta(days=expires_in_days)

@manager.route("/system/ping", methods=["GET"])  # noqa: F821
async def ping():
    return "pong", 200

@manager.route("/system/version", methods=["GET"])  # noqa: F821
def version():
    """
    Get the current version of the application.
    ---
    tags:
      - System
    security:
      - ApiKeyAuth: []
    responses:
      200:
        description: Version retrieved successfully.
        schema:
          type: object
          properties:
            version:
              type: string
              description: Version number.
    """
    return get_json_result(data=get_ragflow_version())


@manager.route("/system/status", methods=["GET"])  # noqa: F821
@login_required
def status():
    """
    Get the system status.
    ---
    tags:
      - System
    security:
      - ApiKeyAuth: []
    responses:
      200:
        description: System is operational.
        schema:
          type: object
          properties:
            es:
              type: object
              description: Elasticsearch status.
            storage:
              type: object
              description: Storage status.
            database:
              type: object
              description: Database status.
      503:
        description: Service unavailable.
        schema:
          type: object
          properties:
            error:
              type: string
              description: Error message.
    """
    res = {}
    st = timer()
    try:
        res["doc_engine"] = settings.docStoreConn.health()
        res["doc_engine"]["elapsed"] = "{:.1f}".format((timer() - st) * 1000.0)
    except Exception as e:
        res["doc_engine"] = {
            "type": "unknown",
            "status": "red",
            "elapsed": "{:.1f}".format((timer() - st) * 1000.0),
            "error": str(e),
        }

    st = timer()
    try:
        settings.STORAGE_IMPL.health()
        res["storage"] = {
            "storage": settings.STORAGE_IMPL_TYPE.lower(),
            "status": "green",
            "elapsed": "{:.1f}".format((timer() - st) * 1000.0),
        }
    except Exception as e:
        res["storage"] = {
            "storage": settings.STORAGE_IMPL_TYPE.lower(),
            "status": "red",
            "elapsed": "{:.1f}".format((timer() - st) * 1000.0),
            "error": str(e),
        }

    st = timer()
    try:
        KnowledgebaseService.get_by_id("x")
        res["database"] = {
            "database": settings.DATABASE_TYPE.lower(),
            "status": "green",
            "elapsed": "{:.1f}".format((timer() - st) * 1000.0),
        }
    except Exception as e:
        res["database"] = {
            "database": settings.DATABASE_TYPE.lower(),
            "status": "red",
            "elapsed": "{:.1f}".format((timer() - st) * 1000.0),
            "error": str(e),
        }

    st = timer()
    try:
        if not REDIS_CONN.health():
            raise Exception("Lost connection!")
        res["redis"] = {
            "status": "green",
            "elapsed": "{:.1f}".format((timer() - st) * 1000.0),
        }
    except Exception as e:
        res["redis"] = {
            "status": "red",
            "elapsed": "{:.1f}".format((timer() - st) * 1000.0),
            "error": str(e),
        }

    task_executor_heartbeats = {}
    try:
        task_executors = REDIS_CONN.smembers("TASKEXE")
        now = datetime.now().timestamp()
        for task_executor_id in task_executors:
            heartbeats = REDIS_CONN.zrangebyscore(task_executor_id, now - 60 * 30, now)
            heartbeats = [json.loads(heartbeat) for heartbeat in heartbeats]
            task_executor_heartbeats[task_executor_id] = heartbeats
    except Exception:
        logging.exception("get task executor heartbeats failed!")
    res["task_executor_heartbeats"] = task_executor_heartbeats

    return get_json_result(data=res)


@manager.route("/system/oceanbase/status", methods=["GET"])  # noqa: F821
@login_required
def oceanbase_status():
    """
    Get OceanBase health status and performance metrics.
    ---
    tags:
      - System
    security:
      - ApiKeyAuth: []
    responses:
      200:
        description: OceanBase status retrieved successfully.
        schema:
          type: object
          properties:
            status:
              type: string
              description: Status (alive/timeout).
            message:
              type: object
              description: Detailed status information including health and performance metrics.
    """
    try:
        status_info = get_oceanbase_status()
        return get_json_result(data=status_info)
    except Exception as e:
        return get_json_result(
            data={
                "status": "error",
                "message": f"Failed to get OceanBase status: {str(e)}"
            },
            code=500
        )


@manager.route("/system/config", methods=["GET"])  # noqa: F821
def get_config():
    """
    Get system configuration.
    ---
    tags:
        - System
    responses:
        200:
            description: Return system configuration
            schema:
                type: object
                properties:
                    registerEnable:
                        type: integer 0 means disabled, 1 means enabled
                        description: Whether user registration is enabled
    """
    return get_json_result(data={
        "registerEnabled": settings.REGISTER_ENABLED,
        "disablePasswordLogin": settings.DISABLE_PASSWORD_LOGIN,
    })

@manager.route("/system/healthz", methods=["GET"])  # noqa: F821
def healthz():
    result, all_ok = run_health_checks()
    return jsonify(result), (200 if all_ok else 500)

@manager.route("/system/tokens", methods=["GET"])  # noqa: F821
@login_required
def token_list():
    """
    List all API tokens for the current user.
    ---
    tags:
      - API Tokens
    security:
      - ApiKeyAuth: []
    responses:
      200:
        description: List of API tokens.
        schema:
          type: object
          properties:
            tokens:
              type: array
              items:
                type: object
                properties:
                  token:
                    type: string
                    description: The API token.
                  name:
                    type: string
                    description: Name of the token.
                  create_time:
                    type: string
                    description: Token creation time.
    """
    tenant_id = _managed_tenant_id()
    if tenant_id is None:
        return _management_error(RetCode.FORBIDDEN, "API key management requires an owner or administrator.")

    try:
        rows = []
        for record in APITokenService.query(tenant_id=tenant_id):
            data = _record_data(record)
            if not data.get("beta"):
                beta = generate_confirmation_token().replace("ragflow-", "")[:32]
                APITokenService.filter_update(
                    [APIToken.tenant_id == tenant_id, APIToken.token == data["token"]],
                    {"beta": beta, "update_time": current_timestamp(), "update_date": datetime_format(datetime.now())},
                )
                data["beta"] = beta
            rows.append(_serialize_api_key(data, APIKeyType.FULL_ACCESS))
        rows.extend(
            _serialize_api_key(record, APIKeyType.RETRIEVAL)
            for record in ScopedAPITokenService.query(tenant_id=tenant_id)
        )
        rows.sort(key=lambda row: (row.get("create_time") or 0, row["token"]), reverse=True)
        return get_json_result(data=rows)
    except Exception:
        return _management_failure()


@manager.route("/system/tokens", methods=["POST"])  # noqa: F821
@login_required
async def new_token():
    """
    Generate a new API token.
    ---
    tags:
      - API Tokens
    security:
      - ApiKeyAuth: []
    requestBody:
      required: true
      content:
        application/json:
          schema:
            type: object
            required:
              - name
              - key_type
            properties:
              name:
                type: string
                maxLength: 64
              key_type:
                type: string
                enum: [full_access, retrieval]
              allowed_dataset_ids:
                type: array
                items:
                  type: string
              expires_in_days:
                type: integer
                nullable: true
                enum: [30, 90, 180, 365]
    responses:
      200:
        description: Token generated successfully.
        schema:
          type: object
          properties:
            token:
              type: string
              description: The generated API token.
    """
    tenant_id = _managed_tenant_id()
    if tenant_id is None:
        return _management_error(RetCode.FORBIDDEN, "API key management requires an owner or administrator.")

    req, err = await validate_and_parse_json_request(request, CreateAPIKeyReq, redact_validation_inputs=True)
    if err is not None:
        return _management_error(RetCode.BAD_REQUEST, err)

    try:
        key_type = APIKeyType(req["key_type"])
        if key_type is APIKeyType.RETRIEVAL and not _datasets_belong_to_tenant(req["allowed_dataset_ids"], tenant_id):
            return _management_error(RetCode.BAD_REQUEST, "Every selected dataset must be valid and belong to the managed tenant.")

        now = datetime.now(timezone.utc)
        obj = {
            "tenant_id": tenant_id,
            "token": _generate_unique_token(key_type),
            "name": req["name"],
            "enabled": True,
            "total_calls": 0,
            "retrieval_calls": 0,
            "last_used_at": None,
            "last_result": None,
            "create_time": current_timestamp(),
            "create_date": datetime_format(datetime.now()),
            "update_time": None,
            "update_date": None,
        }
        if key_type is APIKeyType.FULL_ACCESS:
            obj.update(
                beta=generate_confirmation_token().replace("ragflow-", "")[:32],
                is_legacy=False,
            )
            saved = APITokenService.save(**obj)
        else:
            obj.update(
                allowed_dataset_ids=req["allowed_dataset_ids"],
                expires_at=_expiry_from_days(req["expires_in_days"], now),
            )
            saved = ScopedAPITokenService.save(**obj)

        if not saved:
            return _management_failure()
        return get_json_result(data=_serialize_api_key(obj, key_type))
    except Exception:
        return _management_failure()


@manager.route("/system/tokens", methods=["PATCH"])  # noqa: F821
@login_required
async def update_token():
    """
    Update an API token.
    ---
    tags:
      - API Tokens
    security:
      - ApiKeyAuth: []
    requestBody:
      required: true
      content:
        application/json:
          schema:
            type: object
            required:
              - token
            properties:
              token:
                type: string
              name:
                type: string
                maxLength: 64
              allowed_dataset_ids:
                type: array
                items:
                  type: string
              expires_in_days:
                type: integer
                nullable: true
                enum: [30, 90, 180, 365]
              enabled:
                type: boolean
    responses:
      200:
        description: Token updated successfully.
    """
    tenant_id = _managed_tenant_id()
    if tenant_id is None:
        return _management_error(RetCode.FORBIDDEN, "API key management requires an owner or administrator.")

    req, err = await validate_and_parse_json_request(
        request,
        UpdateAPIKeyReq,
        exclude_unset=True,
        redact_validation_inputs=True,
    )
    if err is not None:
        return _management_error(RetCode.BAD_REQUEST, err)

    try:
        token = req["token"]
        scoped_records = ScopedAPITokenService.query(tenant_id=tenant_id, token=token)
        full_records = APITokenService.query(tenant_id=tenant_id, token=token)
        if scoped_records:
            key_type = APIKeyType.RETRIEVAL
            record = scoped_records[0]
            service = ScopedAPITokenService
            model = ScopedAPIToken
        elif full_records:
            key_type = APIKeyType.FULL_ACCESS
            record = full_records[0]
            service = APITokenService
            model = APIToken
        else:
            return _management_error(RetCode.NOT_FOUND, "API key not found.")

        if key_type is APIKeyType.FULL_ACCESS and ({"allowed_dataset_ids", "expires_in_days"} & req.keys()):
            return _management_error(RetCode.BAD_REQUEST, "Full-access API keys cannot define dataset scope or expiry.")
        if "allowed_dataset_ids" in req and not _datasets_belong_to_tenant(req["allowed_dataset_ids"], tenant_id):
            return _management_error(RetCode.BAD_REQUEST, "Every selected dataset must be valid and belong to the managed tenant.")

        update_data = {key: req[key] for key in ("name", "allowed_dataset_ids", "enabled") if key in req}
        if "expires_in_days" in req:
            update_data["expires_at"] = _expiry_from_days(req["expires_in_days"], datetime.now(timezone.utc))
        update_data.update(update_time=current_timestamp(), update_date=datetime_format(datetime.now()))
        service.filter_update([model.tenant_id == tenant_id, model.token == token], update_data)

        merged = _record_data(record)
        merged.update(update_data)
        return get_json_result(data=_serialize_api_key(merged, key_type))
    except Exception:
        return _management_failure()


@manager.route("/system/tokens", methods=["DELETE"])  # noqa: F821
@login_required
async def rm():
    """
    Remove an API token.
    ---
    tags:
      - API Tokens
    security:
      - ApiKeyAuth: []
    requestBody:
      required: true
      content:
        application/json:
          schema:
            type: object
            required:
              - token
            properties:
              token:
                type: string
                description: The API token to remove.
    responses:
      200:
        description: Token removed successfully.
        schema:
          type: object
          properties:
            success:
              type: boolean
              description: Deletion status.
    """
    tenant_id = _managed_tenant_id()
    if tenant_id is None:
        return _management_error(RetCode.FORBIDDEN, "API key management requires an owner or administrator.")

    req, err = await validate_and_parse_json_request(request, DeleteAPIKeyReq, redact_validation_inputs=True)
    if err is not None:
        return _management_error(RetCode.BAD_REQUEST, err)

    try:
        token = req["token"]
        APITokenService.filter_delete([APIToken.tenant_id == tenant_id, APIToken.token == token])
        ScopedAPITokenService.filter_delete([ScopedAPIToken.tenant_id == tenant_id, ScopedAPIToken.token == token])
        return get_json_result(data=True)
    except Exception:
        return _management_failure()


@manager.route("/system/config/log", methods=["GET"])  # noqa: F821
@login_required
async def get_logger_levels():
    """
    Get current log levels for all packages.
    ---
    tags:
        - System
    responses:
        200:
            description: Return current log levels
    """
    return get_json_result(data=get_log_levels())


@manager.route("/system/config/log", methods=["PUT"])  # noqa: F821
@login_required
async def set_logger_level():
    """
    Set log level for a package.
    ---
    tags:
        - System
    parameters:
        - in: body
          name: body
          required: true
          schema:
            type: object
            properties:
                pkg_name:
                    type: string
                    description: Package name (e.g., "rag.utils.es_conn")
                level:
                    type: string
                    description: Log level (DEBUG, INFO, WARNING, ERROR)
    responses:
        200:
            description: Log level updated successfully
    """
    from quart import request
    data = await request.get_json()
    if not data or "pkg_name" not in data or "level" not in data:
        return get_data_error_result(message="pkg_name and level are required")
    pkg_name = data["pkg_name"]
    level = data["level"]
    success = set_log_level(pkg_name, level)
    if success:
        return get_json_result(data={"pkg_name": pkg_name, "level": level})
    else:
        return get_data_error_result(message=f"Invalid log level: {level}")
