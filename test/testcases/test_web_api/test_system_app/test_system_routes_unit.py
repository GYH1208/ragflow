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

import asyncio
import importlib.util
import inspect
import sys
from datetime import datetime, timezone
from enum import StrEnum
from functools import wraps
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest
from pydantic import ValidationError

pytestmark = pytest.mark.p2


class _DummyManager:
    def __init__(self):
        self.routes = []

    def route(self, rule, **kwargs):
        self.routes.append((rule, tuple(kwargs.get("methods", ()))))

        def decorator(func):
            return func

        return decorator


class _ExprField:
    def __init__(self, name):
        self.name = name

    def __eq__(self, other):
        return (self.name, other)


class _DummyAPITokenModel:
    tenant_id = _ExprField("tenant_id")
    token = _ExprField("token")


class _DummyScopedAPITokenModel:
    tenant_id = _ExprField("tenant_id")
    token = _ExprField("token")


class _APIKeyType(StrEnum):
    FULL_ACCESS = "full_access"
    RETRIEVAL = "retrieval"


class _UserTenantRole(StrEnum):
    OWNER = "owner"
    ADMIN = "admin"
    NORMAL = "normal"


class _DummyResponse(dict):
    def __init__(self, *, code=0, message="success", data=None, status_code=200):
        super().__init__(code=code, message=message, data=data)
        self.status_code = status_code


class _DummyRequest:
    mimetype = "application/json"
    content_type = "application/json"

    def __init__(self, payload=None):
        self.payload = payload

    async def get_json(self):
        return self.payload


class _Record(SimpleNamespace):
    def to_dict(self):
        return vars(self).copy()


def _matches(record, filters):
    return all(getattr(record, name) == value for name, value in filters)


def _service(store):
    def query(**kwargs):
        return [record for record in store if all(getattr(record, key, None) == value for key, value in kwargs.items())]

    def save(**kwargs):
        store.append(_Record(**kwargs))
        return True

    def filter_update(filters, update_data):
        updated = 0
        for record in store:
            if _matches(record, filters):
                vars(record).update(update_data)
                updated += 1
        return updated

    def filter_delete(filters):
        matches = [record for record in store if _matches(record, filters)]
        for record in matches:
            store.remove(record)
        return len(matches)

    return SimpleNamespace(query=query, save=save, filter_update=filter_update, filter_delete=filter_delete)


@pytest.fixture(scope="session")
def auth():
    return "unit-auth"


@pytest.fixture(scope="session", autouse=True)
def set_tenant_info():
    return None


def _load_system_module(
    monkeypatch,
    *,
    payload=None,
    current_user=None,
    memberships=None,
    datasets=None,
    api_key_type=None,
    full_tokens=None,
    scoped_tokens=None,
):
    # Load the real request validators before installing the lightweight route dependencies.
    validation_utils_mod = importlib.import_module("api.utils.validation_utils")
    repo_root = Path(__file__).resolve().parents[4]
    current_user = current_user or SimpleNamespace(id="user-1", is_superuser=False)
    memberships = memberships if memberships is not None else [SimpleNamespace(role="owner", tenant_id="tenant-1", status="1")]
    datasets = datasets or {}
    full_tokens = full_tokens if full_tokens is not None else []
    scoped_tokens = scoped_tokens if scoped_tokens is not None else []
    dummy_request = _DummyRequest(payload)

    api_pkg = ModuleType("api")
    api_pkg.__path__ = [str(repo_root / "api")]
    monkeypatch.setitem(sys.modules, "api", api_pkg)

    apps_mod = ModuleType("api.apps")
    apps_mod.__path__ = [str(repo_root / "api" / "apps")]
    apps_mod.current_user = current_user

    def login_required(func=None, *, api_scope=None, **_kwargs):
        def decorator(handler):
            if inspect.iscoroutinefunction(handler):
                @wraps(handler)
                async def async_wrapper(*args, **kwargs):
                    if api_key_type == _APIKeyType.RETRIEVAL and api_scope is None:
                        return _DummyResponse(code=403, message="API key is not allowed to access this route.", status_code=403)
                    return await handler(*args, **kwargs)

                return async_wrapper

            @wraps(handler)
            def sync_wrapper(*args, **kwargs):
                if api_key_type == _APIKeyType.RETRIEVAL and api_scope is None:
                    return _DummyResponse(code=403, message="API key is not allowed to access this route.", status_code=403)
                return handler(*args, **kwargs)

            return sync_wrapper

        return decorator if func is None else decorator(func)

    apps_mod.login_required = login_required
    monkeypatch.setitem(sys.modules, "api.apps", apps_mod)

    api_db_mod = ModuleType("api.db")
    api_db_mod.APIKeyType = _APIKeyType
    api_db_mod.UserTenantRole = _UserTenantRole
    monkeypatch.setitem(sys.modules, "api.db", api_db_mod)

    common_pkg = ModuleType("common")
    common_pkg.__path__ = [str(repo_root / "common")]
    monkeypatch.setitem(sys.modules, "common", common_pkg)

    constants_mod = ModuleType("common.constants")
    constants_mod.RetCode = SimpleNamespace(
        SUCCESS=0, DATA_ERROR=102, BAD_REQUEST=400, FORBIDDEN=403, NOT_FOUND=404, SERVER_ERROR=500
    )
    constants_mod.StatusEnum = SimpleNamespace(VALID=SimpleNamespace(value="1"))
    monkeypatch.setitem(sys.modules, "common.constants", constants_mod)

    settings_mod = ModuleType("common.settings")
    settings_mod.docStoreConn = SimpleNamespace(health=lambda: {"type": "doc", "status": "green"})
    settings_mod.STORAGE_IMPL = SimpleNamespace(health=lambda: True)
    settings_mod.STORAGE_IMPL_TYPE = "MINIO"
    settings_mod.DATABASE_TYPE = "MYSQL"
    settings_mod.REGISTER_ENABLED = True
    settings_mod.DISABLE_PASSWORD_LOGIN = False
    common_pkg.settings = settings_mod
    monkeypatch.setitem(sys.modules, "common.settings", settings_mod)

    versions_mod = ModuleType("common.versions")
    versions_mod.get_ragflow_version = lambda: "0.0.0-unit"
    monkeypatch.setitem(sys.modules, "common.versions", versions_mod)

    time_utils_mod = ModuleType("common.time_utils")
    time_utils_mod.current_timestamp = lambda: 111
    time_utils_mod.datetime_format = lambda _dt: "2026-01-01 00:00:00"
    monkeypatch.setitem(sys.modules, "common.time_utils", time_utils_mod)

    api_utils_mod = ModuleType("api.utils.api_utils")
    api_utils_mod.get_json_result = lambda data=None, message="success", code=0: _DummyResponse(code=code, message=message, data=data)
    api_utils_mod.get_data_error_result = lambda message="", code=102, data=None: _DummyResponse(code=code, message=message, data=data)
    api_utils_mod.build_error_result = lambda code=403, message="success": _DummyResponse(
        code=code, message=message, status_code=code
    )
    api_utils_mod.server_error_response = lambda exc: _DummyResponse(code=100, message=repr(exc), data=None)
    api_utils_mod.generate_confirmation_token = lambda: "ragflow-abcdefghijklmnopqrstuvwxyz0123456789"
    monkeypatch.setitem(sys.modules, "api.utils.api_utils", api_utils_mod)
    monkeypatch.setitem(sys.modules, "api.utils.validation_utils", validation_utils_mod)

    api_service_mod = ModuleType("api.db.services.api_service")
    api_service_mod.APITokenService = _service(full_tokens)
    api_service_mod.ScopedAPITokenService = _service(scoped_tokens)
    monkeypatch.setitem(sys.modules, "api.db.services.api_service", api_service_mod)

    kb_service_mod = ModuleType("api.db.services.knowledgebase_service")
    kb_service_mod.KnowledgebaseService = SimpleNamespace(
        get_by_id=lambda _kb_id: True,
        query=lambda **kwargs: [
            dataset
            for dataset in datasets.values()
            if all(getattr(dataset, key, None) == value for key, value in kwargs.items())
        ],
    )
    monkeypatch.setitem(sys.modules, "api.db.services.knowledgebase_service", kb_service_mod)

    user_service_mod = ModuleType("api.db.services.user_service")
    user_service_mod.UserTenantService = SimpleNamespace(
        query=lambda **kwargs: [
            member
            for member in memberships
            if kwargs.get("status") is None or getattr(member, "status", "1") == kwargs["status"]
        ]
    )
    monkeypatch.setitem(sys.modules, "api.db.services.user_service", user_service_mod)

    db_models_mod = ModuleType("api.db.db_models")
    db_models_mod.APIToken = _DummyAPITokenModel
    db_models_mod.ScopedAPIToken = _DummyScopedAPITokenModel
    monkeypatch.setitem(sys.modules, "api.db.db_models", db_models_mod)

    rag_pkg = ModuleType("rag")
    rag_pkg.__path__ = []
    monkeypatch.setitem(sys.modules, "rag", rag_pkg)

    rag_utils_pkg = ModuleType("rag.utils")
    rag_utils_pkg.__path__ = []
    monkeypatch.setitem(sys.modules, "rag.utils", rag_utils_pkg)

    redis_mod = ModuleType("rag.utils.redis_conn")
    redis_mod.REDIS_CONN = SimpleNamespace(
        health=lambda: True,
        smembers=lambda *_args, **_kwargs: set(),
        zrangebyscore=lambda *_args, **_kwargs: [],
    )
    monkeypatch.setitem(sys.modules, "rag.utils.redis_conn", redis_mod)

    health_utils_mod = ModuleType("api.utils.health_utils")
    health_utils_mod.run_health_checks = lambda: ({"status": "ok"}, True)
    health_utils_mod.get_oceanbase_status = lambda: {"status": "alive"}
    monkeypatch.setitem(sys.modules, "api.utils.health_utils", health_utils_mod)

    quart_mod = ModuleType("quart")
    quart_mod.jsonify = lambda value: value
    quart_mod.request = dummy_request
    monkeypatch.setitem(sys.modules, "quart", quart_mod)

    module_path = repo_root / "api" / "apps" / "restful_apis" / "system_api.py"
    spec = importlib.util.spec_from_file_location("test_system_routes_unit_module", module_path)
    module = importlib.util.module_from_spec(spec)
    module.manager = _DummyManager()
    monkeypatch.setitem(sys.modules, "test_system_routes_unit_module", module)
    spec.loader.exec_module(module)
    module._test_request = dummy_request
    module._test_full_tokens = full_tokens
    module._test_scoped_tokens = scoped_tokens
    return module


def _call(handler, *args):
    result = handler(*args)
    return asyncio.run(result) if inspect.isawaitable(result) else result


@pytest.mark.p2
def test_status_branch_matrix_unit(monkeypatch):
    module = _load_system_module(monkeypatch)

    monkeypatch.setattr(module.settings, "docStoreConn", SimpleNamespace(health=lambda: {"type": "es", "status": "green"}))
    monkeypatch.setattr(module.settings, "STORAGE_IMPL", SimpleNamespace(health=lambda: True))
    monkeypatch.setattr(module.KnowledgebaseService, "get_by_id", lambda _kb_id: True)
    monkeypatch.setattr(module.REDIS_CONN, "health", lambda: True)
    monkeypatch.setattr(module.REDIS_CONN, "smembers", lambda _key: {"executor-1"})
    monkeypatch.setattr(module.REDIS_CONN, "zrangebyscore", lambda *_args, **_kwargs: ['{"beat": 1}'])

    res = _call(module.status)
    assert res["code"] == 0
    assert res["data"]["doc_engine"]["status"] == "green"
    assert res["data"]["storage"]["status"] == "green"
    assert res["data"]["database"]["status"] == "green"
    assert res["data"]["redis"]["status"] == "green"
    assert res["data"]["task_executor_heartbeats"]["executor-1"][0]["beat"] == 1

    monkeypatch.setattr(
        module.settings,
        "docStoreConn",
        SimpleNamespace(health=lambda: (_ for _ in ()).throw(RuntimeError("doc down"))),
    )
    monkeypatch.setattr(
        module.settings,
        "STORAGE_IMPL",
        SimpleNamespace(health=lambda: (_ for _ in ()).throw(RuntimeError("storage down"))),
    )
    monkeypatch.setattr(
        module.KnowledgebaseService,
        "get_by_id",
        lambda _kb_id: (_ for _ in ()).throw(RuntimeError("db down")),
    )
    monkeypatch.setattr(module.REDIS_CONN, "health", lambda: False)
    monkeypatch.setattr(module.REDIS_CONN, "smembers", lambda _key: (_ for _ in ()).throw(RuntimeError("hb down")))

    res = _call(module.status)
    assert res["code"] == 0
    assert res["data"]["doc_engine"]["status"] == "red"
    assert "doc down" in res["data"]["doc_engine"]["error"]
    assert res["data"]["storage"]["status"] == "red"
    assert "storage down" in res["data"]["storage"]["error"]
    assert res["data"]["database"]["status"] == "red"
    assert "db down" in res["data"]["database"]["error"]
    assert res["data"]["redis"]["status"] == "red"
    assert "Lost connection!" in res["data"]["redis"]["error"]
    assert res["data"]["task_executor_heartbeats"] == {}


@pytest.mark.p2
def test_get_config_returns_register_enabled_unit(monkeypatch):
    module = _load_system_module(monkeypatch)
    monkeypatch.setattr(module.settings, "REGISTER_ENABLED", False)
    res = module.get_config()
    assert res["code"] == 0
    assert res["data"]["registerEnabled"] is False


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"name": "   ", "key_type": "full_access"},
        {"name": "x" * 65, "key_type": "full_access"},
        {"name": "key", "key_type": "unknown"},
        {"name": "key", "key_type": "retrieval", "allowed_dataset_ids": []},
        {"name": "key", "key_type": "full_access", "allowed_dataset_ids": ["dataset-1"]},
        {"name": "key", "key_type": "full_access", "expires_in_days": None},
        {"name": "key", "key_type": "retrieval", "allowed_dataset_ids": ["dataset-1"], "expires_in_days": 31},
    ],
)
def test_create_api_key_request_rejects_invalid_contract(payload):
    from api.utils.validation_utils import CreateAPIKeyReq

    with pytest.raises(ValidationError):
        CreateAPIKeyReq(**payload)


def test_create_api_key_request_deduplicates_retrieval_datasets_in_caller_order():
    from api.utils.validation_utils import CreateAPIKeyReq

    req = CreateAPIKeyReq(
        name="  WorkBuddy  ",
        key_type="retrieval",
        allowed_dataset_ids=["dataset-2", "dataset-1", "dataset-2"],
        expires_in_days=90,
    )
    assert req.name == "WorkBuddy"
    assert req.allowed_dataset_ids == ["dataset-2", "dataset-1"]


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"name": "Renamed"},
        {"token": "   ", "name": "Renamed"},
        {"token": "key"},
        {"token": "key", "key_type": "full_access"},
        {"token": "key", "name": " "},
        {"token": "key", "allowed_dataset_ids": []},
        {"token": "key", "expires_in_days": 12},
        {"token": "key", "enabled": "yes"},
    ],
)
def test_update_api_key_request_rejects_empty_immutable_or_invalid_edits(payload):
    from api.utils.validation_utils import UpdateAPIKeyReq

    with pytest.raises(ValidationError):
        UpdateAPIKeyReq(**payload)


def test_management_body_models_require_nonblank_token_and_keep_it_out_of_mutable_fields():
    from api.utils import validation_utils

    assert hasattr(validation_utils, "DeleteAPIKeyReq")
    DeleteAPIKeyReq = validation_utils.DeleteAPIKeyReq
    UpdateAPIKeyReq = validation_utils.UpdateAPIKeyReq

    update = UpdateAPIKeyReq(token="  ragflow-secret-value  ", name="Renamed")
    deletion = DeleteAPIKeyReq(token="  ragflow-secret-value  ")

    assert update.token == "ragflow-secret-value"
    assert update.name == "Renamed"
    assert deletion.token == "ragflow-secret-value"
    for payload in (
        {"name": "Renamed"},
        {"token": "   ", "name": "Renamed"},
        {"token": "ragflow-secret-value"},
        {"token": "ragflow-secret-value", "key_type": "retrieval"},
    ):
        with pytest.raises(ValidationError):
            UpdateAPIKeyReq(**payload)
    for payload in ({}, {"token": "   "}, {"token": "ragflow-secret-value", "name": "extra"}):
        with pytest.raises(ValidationError):
            DeleteAPIKeyReq(**payload)


def test_update_and_delete_register_only_fixed_management_paths(monkeypatch):
    module = _load_system_module(monkeypatch)

    registered = set(module.manager.routes)
    assert ("/system/tokens", ("PATCH",)) in registered
    assert ("/system/tokens", ("DELETE",)) in registered
    assert not any("<token>" in rule for rule, _methods in registered)


@pytest.mark.parametrize("handler_name,args", [("token_list", ()), ("new_token", ()), ("update_token", ()), ("rm", ())])
def test_ordinary_member_cannot_manage_any_api_key_route(monkeypatch, handler_name, args):
    module = _load_system_module(
        monkeypatch,
        payload={"name": "member key", "key_type": "full_access"},
        memberships=[SimpleNamespace(role="normal", tenant_id="tenant-1", status="1")],
    )
    response = _call(getattr(module, handler_name), *args)
    assert response.status_code == 403
    assert response["code"] == 403


@pytest.mark.parametrize("handler_name,args", [("token_list", ()), ("new_token", ()), ("update_token", ()), ("rm", ())])
def test_retrieval_key_is_denied_by_default_on_every_management_route(monkeypatch, handler_name, args):
    module = _load_system_module(
        monkeypatch,
        payload={"name": "retrieval key", "key_type": "retrieval", "allowed_dataset_ids": ["dataset-1"]},
        api_key_type=_APIKeyType.RETRIEVAL,
    )
    response = _call(getattr(module, handler_name), *args)
    assert response.status_code == 403
    assert response["code"] == 403


def test_full_access_key_mapped_to_owner_can_create_management_key(monkeypatch):
    module = _load_system_module(
        monkeypatch,
        payload={"name": "admin integration", "key_type": "full_access"},
        api_key_type=_APIKeyType.FULL_ACCESS,
    )
    response = _call(module.new_token)
    assert response.status_code == 200
    assert response["code"] == 0
    assert response["data"]["key_type"] == "full_access"
    assert response["data"]["legacy"] is False


@pytest.mark.parametrize(
    "dataset",
    [
        SimpleNamespace(id="dataset-other", tenant_id="tenant-2", status="1"),
        SimpleNamespace(id="dataset-other", tenant_id="tenant-1", status="0"),
    ],
)
def test_retrieval_creation_rejects_cross_tenant_or_invalid_dataset(monkeypatch, dataset):
    module = _load_system_module(
        monkeypatch,
        payload={"name": "cross tenant", "key_type": "retrieval", "allowed_dataset_ids": ["dataset-other"]},
        datasets={"dataset-other": dataset},
    )
    response = _call(module.new_token)
    assert response.status_code == 400
    assert response["code"] == 400
    assert module._test_scoped_tokens == []


def test_managed_tenant_prefers_owner_then_admin_deterministically(monkeypatch):
    module = _load_system_module(
        monkeypatch,
        memberships=[
            SimpleNamespace(role="admin", tenant_id="tenant-b", status="1"),
            SimpleNamespace(role="owner", tenant_id="tenant-z", status="1"),
            SimpleNamespace(role="owner", tenant_id="tenant-a", status="1"),
        ],
    )
    assert module._managed_tenant_id() == "tenant-a"


def test_admin_membership_can_manage_api_keys(monkeypatch):
    module = _load_system_module(
        monkeypatch,
        payload={"name": "admin key", "key_type": "full_access"},
        memberships=[SimpleNamespace(role="admin", tenant_id="tenant-1", status="1")],
    )
    response = _call(module.new_token)
    assert response.status_code == 200
    assert response["data"]["key_type"] == "full_access"


def test_unified_crud_preserves_omitted_fields_and_keeps_beta_full_access_only(monkeypatch):
    full_tokens = [
        _Record(
            tenant_id="tenant-1",
            token="ragflow-legacy",
            name="Legacy",
            is_legacy=True,
            beta="legacy-beta",
            enabled=True,
            total_calls=3,
            retrieval_calls=1,
            last_used_at=None,
            last_result="success",
            create_time=100,
            create_date="2025-01-01 00:00:00",
            update_time=None,
            update_date=None,
        )
    ]
    module = _load_system_module(
        monkeypatch,
        payload={
            "name": "WorkBuddy",
            "key_type": "retrieval",
            "allowed_dataset_ids": ["dataset-1", "dataset-1"],
            "expires_in_days": 90,
        },
        datasets={"dataset-1": SimpleNamespace(id="dataset-1", tenant_id="tenant-1", status="1")},
        full_tokens=full_tokens,
    )

    created = _call(module.new_token)
    scoped_token = created["data"]["token"]
    assert scoped_token.startswith("ragflow-rk-")
    assert "beta" not in created["data"]
    assert created["data"]["allowed_dataset_ids"] == ["dataset-1"]

    listed = _call(module.token_list)["data"]
    assert {item["key_type"] for item in listed} == {"full_access", "retrieval"}
    full_row = next(item for item in listed if item["key_type"] == "full_access")
    scoped_row = next(item for item in listed if item["key_type"] == "retrieval")
    assert full_row["allowed_dataset_ids"] == []
    assert full_row["expires_at"] is None
    assert full_row["legacy"] is True
    assert full_row["beta"] == "legacy-beta"
    assert "beta" not in scoped_row

    original_expiry = scoped_row["expires_at"]
    module._test_request.payload = {"token": scoped_token, "name": "Renamed"}
    renamed = _call(module.update_token)
    assert renamed["data"]["name"] == "Renamed"
    assert renamed["data"]["allowed_dataset_ids"] == ["dataset-1"]
    assert renamed["data"]["expires_at"] == original_expiry

    module._test_request.payload = {"token": scoped_token, "enabled": False, "expires_in_days": None}
    disabled = _call(module.update_token)
    assert disabled["data"]["enabled"] is False
    assert disabled["data"]["expires_at"] is None

    module._test_request.payload = {"token": scoped_token}
    deleted = _call(module.rm)
    assert deleted["data"] is True
    assert module._test_scoped_tokens == []


def test_patch_full_access_rejects_scope_and_key_type_changes(monkeypatch):
    full_tokens = [
        _Record(
            tenant_id="tenant-1",
            token="ragflow-full",
            name="Full",
            is_legacy=False,
            beta="beta",
            enabled=True,
            total_calls=0,
            retrieval_calls=0,
            last_used_at=None,
            last_result=None,
            create_time=100,
            create_date="2026-01-01 00:00:00",
            update_time=None,
            update_date=None,
        )
    ]
    module = _load_system_module(
        monkeypatch,
        payload={"token": "ragflow-full", "allowed_dataset_ids": ["dataset-1"]},
        full_tokens=full_tokens,
    )
    response = _call(module.update_token)
    assert response.status_code == 400

    module._test_request.payload = {"token": "ragflow-full", "key_type": "retrieval"}
    response = _call(module.update_token)
    assert response.status_code == 400
    assert full_tokens[0].name == "Full"


@pytest.mark.parametrize("collision_table", ["full", "scoped"])
def test_key_generation_retries_collision_found_in_either_table(monkeypatch, collision_table):
    collision = "ragflow-abcdefghijklmnopqrstuvwxyz0123456789"
    full_tokens = [_Record(tenant_id="other", token=collision)] if collision_table == "full" else []
    scoped_tokens = [_Record(tenant_id="other", token=collision)] if collision_table == "scoped" else []
    module = _load_system_module(
        monkeypatch,
        payload={"name": "collision safe", "key_type": "full_access"},
        full_tokens=full_tokens,
        scoped_tokens=scoped_tokens,
    )
    candidates = iter([collision, "ragflow-fresh", "ragflow-beta"])
    monkeypatch.setattr(module, "generate_confirmation_token", lambda: next(candidates))
    response = _call(module.new_token)
    assert response["data"]["token"] == "ragflow-fresh"


def test_management_failure_does_not_expose_raw_key_in_logs_or_response(monkeypatch, caplog):
    raw_key = "ragflow-secret-value"
    module = _load_system_module(monkeypatch, payload={"token": raw_key, "name": "Renamed"})
    monkeypatch.setattr(
        module.ScopedAPITokenService,
        "query",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError(raw_key)),
    )
    response = _call(module.update_token)
    assert response.status_code == 500
    assert raw_key not in response["message"]
    assert raw_key not in caplog.text


@pytest.mark.parametrize(
    "handler_name,args,payload",
    [
        (
            "new_token",
            (),
            {"name": "Round-tripped key", "key_type": "full_access", "token": "ragflow-plaintext-create-secret"},
        ),
        (
            "update_token",
            (),
            {
                "token": "ragflow-existing",
                "name": "Round-tripped key",
                "replacement_token": "ragflow-plaintext-patch-secret",
            },
        ),
        (
            "rm",
            (),
            {"token": "ragflow-existing", "replacement_token": "ragflow-plaintext-delete-secret"},
        ),
    ],
)
def test_management_validation_does_not_reflect_plaintext_token_extra(monkeypatch, caplog, handler_name, args, payload):
    raw_key = payload.get("replacement_token", payload["token"])
    module = _load_system_module(monkeypatch, payload=payload)
    response = _call(getattr(module, handler_name), *args)
    assert response.status_code == 400
    assert response["code"] == 400
    rejected_field = "replacement_token" if "replacement_token" in payload else "token"
    assert f"Field: <{rejected_field}>" in response["message"]
    assert "Value: <redacted>" in response["message"]
    assert raw_key not in response["message"]
    assert raw_key not in caplog.text


def test_superuser_with_valid_membership_can_manage_its_deterministic_tenant(monkeypatch):
    module = _load_system_module(
        monkeypatch,
        current_user=SimpleNamespace(id="root", is_superuser=True),
        memberships=[
            SimpleNamespace(role="normal", tenant_id="tenant-b", status="1"),
            SimpleNamespace(role="normal", tenant_id="tenant-a", status="1"),
        ],
    )
    assert module._managed_tenant_id() == "tenant-a"
