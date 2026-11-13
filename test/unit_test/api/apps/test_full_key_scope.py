"""Full-key dataset boundaries with real Quart/Peewee and isolated external services."""

import ast
import importlib.util
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from functools import wraps
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Annotated, Literal

import peewee
import pydantic
import pytest
from playhouse.migrate import SqliteMigrator, migrate
from quart import Quart, current_app, g, jsonify, request
from quart_auth import Unauthorized
from werkzeug.exceptions import Forbidden

from api.db import APIKeyType
from common.constants import RetCode

ROOT = Path(__file__).resolve().parents[4]


def _definitions(path, names, namespace, *, undecorated=False):
    tree = ast.parse((ROOT / path).read_text())
    nodes = []
    for node in tree.body:
        name = getattr(node, "name", None)
        if isinstance(node, ast.Assign):
            name = getattr(node.targets[0], "id", None)
        if name in names:
            if undecorated and isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                node.decorator_list = []
            nodes.append(node)
    assert len(nodes) == len(names), (path, names)
    future = ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0)
    exec(compile(ast.fix_missing_locations(ast.Module(body=[future, *nodes], type_ignores=[])), str(path), "exec"), namespace)  # noqa: S102 - trusted definitions, isolated dependencies


@pytest.fixture
def environment(tmp_path, monkeypatch):
    database = peewee.SqliteDatabase(tmp_path / "full-key.db")
    db_instance = database

    class Base(peewee.Model):
        def to_dict(self):
            return self.__data__.copy()

        @classmethod
        def query(cls, **kwargs):
            return list(cls.select().filter(**kwargs))

        class Meta:
            database = db_instance

    ns = {**vars(peewee), "DataBaseModel": Base, "LongTextField": peewee.TextField, "json_dumps": json.dumps, "json_loads": json.loads, "timezone": timezone}
    _definitions("api/db/db_models.py", {"JSONField", "UTCDateTimeField", "APIToken", "ScopedAPIToken"}, ns)
    full, retrieval = ns["APIToken"], ns["ScopedAPIToken"]
    database.create_tables([full, retrieval])
    models = ModuleType("api.db.db_models")
    models.APIToken, models.ScopedAPIToken = full, retrieval
    monkeypatch.setitem(sys.modules, models.__name__, models)
    redis = ModuleType("rag.utils.redis_conn")
    redis.REDIS_CONN = SimpleNamespace()
    monkeypatch.setitem(sys.modules, redis.__name__, redis)
    apps = ModuleType("api.apps")
    apps.__path__ = [str(ROOT / "api/apps")]
    monkeypatch.setitem(sys.modules, apps.__name__, apps)
    spec = importlib.util.spec_from_file_location("api.apps.api_key_auth", ROOT / "api/apps/api_key_auth.py")
    auth = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, auth)
    spec.loader.exec_module(auth)
    app = Quart(__name__)
    state = SimpleNamespace(context=None, calls=[])

    def load_user(_auth_types):
        g.user = SimpleNamespace(id="owner")
        g.api_key_context = state.context
        return g.user

    guard_ns = {
        "wraps": wraps,
        "os": os,
        "time": time,
        "logging": logging,
        "g": g,
        "request": request,
        "current_app": current_app,
        "_load_user": load_user,
        "_normalize_auth_types": lambda value: {"API"},
        "AUTH_BETA": "BETA",
        "APIKeyType": APIKeyType,
        "RetCode": RetCode,
        "KNOWLEDGE_RETRIEVE_SCOPE": auth.KNOWLEDGE_RETRIEVE_SCOPE,
        "get_json_result": lambda **kwargs: jsonify(kwargs),
        "QuartAuthUnauthorized": Unauthorized,
    }
    _definitions("api/apps/__init__.py", {"login_required"}, guard_ns)

    @app.errorhandler(Exception)
    async def http_error(error):
        if hasattr(error, "code"):
            return jsonify(code=error.code, message=str(error)), error.code
        raise error

    async def resource(**kwargs):
        state.calls.append((request.method, request.path))
        if request.method == "POST" and request.path.endswith("/documents"):
            files = await request.files
            if "file" in files:
                state.upload = files["file"].read()
        return jsonify(code=0)

    routes = [
        ("/api/v1/datasets", ["GET", "POST", "DELETE"]),
        ("/api/v1/datasets/<dataset_id>/documents", ["GET", "POST", "DELETE"]),
        ("/api/v1/datasets/<dataset_id>/documents/<document_id>/qa", ["POST"]),
        ("/api/v1/datasets/<dataset_id>/documents/<document_id>/chunks/<chunk_id>", ["PATCH"]),
        ("/api/v1/datasets/<dataset_id>/documents/<document_id>/chunks", ["PATCH", "DELETE"]),
        ("/api/v1/datasets/<dataset_id>/chunks", ["POST", "DELETE"]),
        ("/api/v1/datasets/<dataset_id>/documents/parse", ["POST"]),
        ("/api/v1/datasets/<dataset_id>/documents/batch-update-status", ["POST"]),
        ("/api/v1/datasets/<dataset_id>/graph", ["GET"]),
        ("/api/v1/datasets/search", ["POST"]),
        ("/api/v1/datasets/tags/aggregation", ["GET"]),
        ("/api/v1/datasets/metadata/flattened", ["GET"]),
        ("/api/v1/retrieval", ["POST"]),
        ("/api/v1/documents/<document_id>", ["GET"]),
        ("/api/v1/system/tokens", ["GET", "POST", "PATCH"]),
        ("/v1/kb/list", ["POST"]),
        ("/api/v1/files/link-to-datasets", ["POST"]),
    ]
    for index, (rule, methods) in enumerate(routes):
        app.add_url_rule(rule, f"resource{index}", guard_ns["login_required"](resource), methods=methods)
    full.create(tenant_id="owner", token="scoped", allowed_dataset_ids=["allowed"])
    state.context = auth.resolve_api_key("scoped")
    yield SimpleNamespace(db=database, models=ns, full=full, auth=auth, app=app, client=app.test_client(), state=state)
    database.close()


def test_full_key_scope_is_persisted_and_resolved(environment):
    env = environment
    assert "allowed_dataset_ids" in env.full._meta.fields
    assert env.full.get_by_id(("owner", "scoped")).allowed_dataset_ids == ["allowed"]
    assert env.state.context.allowed_dataset_ids == frozenset({"allowed"})


def test_existing_full_key_default_remains_unrestricted(environment):
    environment.full.create(tenant_id="owner", token="old")
    assert environment.auth.resolve_api_key("old").allowed_dataset_ids is None


@pytest.mark.parametrize("scope", [None, "allowed", {"allowed": True}, ["allowed", 3], [""]])
def test_malformed_full_scope_rejects_authentication(environment, monkeypatch, scope):
    record = SimpleNamespace(tenant_id="owner", token="malformed", enabled=True, allowed_dataset_ids=scope)
    monkeypatch.setattr(environment.full, "query", lambda **kwargs: [record])
    assert environment.auth.resolve_api_key("malformed") is None


async def test_scope_helper_limits_full_keys(environment):
    async with environment.app.test_request_context("/"):
        g.api_key_context = environment.state.context
        assert environment.auth.allowed_dataset_ids() == frozenset({"allowed"})
        with pytest.raises(Forbidden, match="requested dataset"):
            environment.auth.require_dataset_access(["other"])


@pytest.mark.parametrize("method,suffix", [("POST", "/documents"), ("POST", "/documents/doc/qa"), ("DELETE", "/documents"), ("PATCH", "/documents/doc/chunks/chunk"), ("GET", "/graph")])
async def test_dataset_paths_reject_outside_scope_before_handler(environment, method, suffix):
    env = environment
    denied = await env.client.open("/api/v1/datasets/other" + suffix, method=method, json={})
    assert denied.status_code == 403 and not env.state.calls
    allowed = await env.client.open("/api/v1/datasets/allowed" + suffix, method=method, json={})
    assert allowed.status_code == 200 and len(env.state.calls) == 1


async def test_multipart_upload_scope_is_checked_without_consuming_body(environment):
    from io import BytesIO

    from werkzeug.datastructures import FileStorage

    file = FileStorage(stream=BytesIO(b"test"), filename="test.txt", content_type="text/plain")
    response = await environment.client.post("/api/v1/datasets/other/documents", files={"file": file})
    assert response.status_code == 403 and not environment.state.calls
    file = FileStorage(stream=BytesIO(b"test"), filename="test.txt", content_type="text/plain")
    response = await environment.client.post("/api/v1/datasets/allowed/documents", files={"file": file})
    assert response.status_code == 200 and environment.state.upload == b"test"


@pytest.mark.parametrize("path", ["/api/v1/datasets", "/api/v1/datasets/search"])
async def test_mixed_bulk_dataset_ids_are_denied_before_any_side_effect(environment, path):
    method = "DELETE" if path.endswith("/datasets") else "POST"
    field = "ids" if method == "DELETE" else "dataset_ids"
    response = await environment.client.open(path, method=method, json={field: ["allowed", "other"]})
    assert response.status_code == 403 and not environment.state.calls


async def test_bulk_dataset_deletion_requires_explicit_ids(environment):
    response = await environment.client.delete("/api/v1/datasets", json={})
    assert response.status_code in (400, 403) and not environment.state.calls


@pytest.mark.parametrize("path", ["/api/v1/datasets/tags/aggregation", "/api/v1/datasets/metadata/flattened"])
async def test_aggregations_reject_any_outside_dataset(environment, path):
    response = await environment.client.get(path + "?dataset_ids=allowed,other")
    assert response.status_code == 403 and not environment.state.calls


@pytest.mark.parametrize(
    "method,path",
    [
        ("POST", "/api/v1/datasets"),
        ("POST", "/api/v1/system/tokens"),
        ("PATCH", "/api/v1/system/tokens"),
        ("GET", "/api/v1/system/tokens"),
        ("POST", "/api/v1/files/link-to-datasets"),
        ("POST", "/v1/kb/list"),
    ],
)
async def test_global_and_management_routes_cannot_bypass_scope(environment, method, path):
    response = await environment.client.open(path, method=method, json={"allowed_dataset_ids": []})
    assert response.status_code == 403 and not environment.state.calls


async def test_unrestricted_full_key_keeps_management_access(environment):
    environment.full.create(tenant_id="owner", token="unrestricted")
    environment.state.context = environment.auth.resolve_api_key("unrestricted")
    response = await environment.client.get("/api/v1/system/tokens")
    assert response.status_code == 200


@pytest.mark.parametrize("dataset_id,status", [("allowed", 200), ("other", 403)])
async def test_document_only_alias_resolves_scope_before_reading(environment, monkeypatch, dataset_id, status):
    module = ModuleType("api.db.services.document_service")
    module.DocumentService = SimpleNamespace(get_by_id=lambda doc_id: (True, SimpleNamespace(kb_id=dataset_id)))
    monkeypatch.setitem(sys.modules, module.__name__, module)
    response = await environment.client.get("/api/v1/documents/doc")
    assert response.status_code == status
    assert len(environment.state.calls) == (1 if status == 200 else 0)


async def test_selected_full_key_does_not_accept_its_beta_alias(environment):
    env = environment
    env.full.update(beta="beta-alias").execute()
    ns = {
        "request": request,
        "g": g,
        "logging": logging,
        "APIToken": env.full,
        "_normalize_auth_types": set,
        "AUTH_API": "API",
        "AUTH_JWT": "JWT",
        "AUTH_BETA": "BETA",
        "UserService": SimpleNamespace(query=lambda **kwargs: [SimpleNamespace(id="owner")]),
    }
    _definitions("api/apps/__init__.py", {"_load_user"}, ns)
    async with env.app.test_request_context("/", headers={"Authorization": "beta-alias"}):
        assert ns["_load_user"](["BETA"]) is None


@pytest.mark.parametrize("suffix,field", [("/chunks", "document_ids"), ("/documents/parse", "document_ids"), ("/documents/batch-update-status", "doc_ids")])
async def test_mixed_document_batches_are_denied_before_partial_writes(environment, monkeypatch, suffix, field):
    module = ModuleType("api.db.services.document_service")
    module.DocumentService = SimpleNamespace(get_by_ids=lambda ids: [SimpleNamespace(id="doc", kb_id="allowed"), SimpleNamespace(id="outside", kb_id="other")])
    monkeypatch.setitem(sys.modules, module.__name__, module)
    response = await environment.client.post("/api/v1/datasets/allowed" + suffix, json={field: ["doc", "outside"]})
    assert response.status_code == 403 and not environment.state.calls


@pytest.mark.parametrize("binding", [{"kb_id": "other", "doc_id": "outside-doc"}, {"kb_id": "allowed", "doc_id": "outside-doc"}])
async def test_chunk_state_changes_verify_actual_binding_before_index_updates(environment, monkeypatch, binding):
    settings = ModuleType("common.settings")
    settings.docStoreConn = SimpleNamespace(get=lambda chunk_id, *args: {"kb_id": "allowed", "doc_id": "doc"} if chunk_id == "inside" else binding)
    kb_module = ModuleType("api.db.services.knowledgebase_service")
    kb_module.KnowledgebaseService = SimpleNamespace(get_by_id=lambda id: (True, SimpleNamespace(id=id, tenant_id="owner")))
    search = ModuleType("rag.nlp.search")
    search.index_name = lambda tenant: "ragflow_" + tenant
    for module in (settings, kb_module, search):
        monkeypatch.setitem(sys.modules, module.__name__, module)
    import common

    monkeypatch.setattr(common, "settings", settings, raising=False)
    response = await environment.client.patch("/api/v1/datasets/allowed/documents/doc/chunks", json={"chunk_ids": ["inside", "outside"], "available": False})
    assert response.status_code == 403 and not environment.state.calls


@pytest.fixture
def validation():
    ns = {name: getattr(pydantic, name) for name in ("BaseModel", "ConfigDict", "Field", "StringConstraints", "field_validator", "model_validator")}
    ns.update(Annotated=Annotated, Literal=Literal)
    _definitions("api/utils/validation_utils.py", {"Base", "APIKeyName", "APIKeyDatasetID", "APIKeyToken", "APIKeyExpiryDays", "CreateAPIKeyReq", "UpdateAPIKeyReq"}, ns)
    ns["CreateAPIKeyReq"].model_rebuild(_types_namespace=ns)
    ns["UpdateAPIKeyReq"].model_rebuild(_types_namespace=ns)
    return ns


def test_full_key_creation_accepts_a_dataset_allowlist(validation):
    parsed = validation["CreateAPIKeyReq"](name="FDE", key_type="full_access", allowed_dataset_ids=["allowed", "allowed"])
    assert parsed.allowed_dataset_ids == ["allowed"]


def test_scope_reset_is_valid_for_full_key_management(validation):
    assert validation["UpdateAPIKeyReq"](token="full", allowed_dataset_ids=[]).allowed_dataset_ids == []


def test_full_key_expiry_is_still_rejected(validation):
    with pytest.raises(pydantic.ValidationError):
        validation["CreateAPIKeyReq"](name="FDE", key_type="full_access", expires_in_days=30)


def test_retrieval_creation_still_requires_nonempty_scope(validation):
    with pytest.raises(pydantic.ValidationError):
        validation["CreateAPIKeyReq"](name="Search", key_type="retrieval", allowed_dataset_ids=[])


def test_scope_migration_is_idempotent_and_keeps_legacy_keys_unrestricted(environment):
    database = peewee.SqliteDatabase(":memory:")
    ns = {
        **environment.models,
        "alter_db_add_column": lambda migrator, table, name, field: migrate(migrator.add_column(table, name, field)) if name not in {c.name for c in database.get_columns(table)} else None,
    }
    _definitions("api/db/db_models.py", {"migrate_api_token_metadata"}, ns)
    with database.connection_context():
        database.execute_sql("CREATE TABLE api_token (tenant_id TEXT, token TEXT)")
        database.execute_sql("INSERT INTO api_token VALUES ('owner', 'legacy')")
        ns["migrate_api_token_metadata"](SqliteMigrator(database))
        ns["migrate_api_token_metadata"](SqliteMigrator(database))
        row = database.execute_sql("SELECT allowed_dataset_ids FROM api_token").fetchone()
        assert json.loads(row[0]) == []


@pytest.fixture
def management(environment, validation):
    env = environment

    def service(model):
        return SimpleNamespace(
            query=model.query,
            save=lambda **data: bool(model.create(**{key: value for key, value in data.items() if key in model._meta.fields})),
            filter_update=lambda conditions, data: model.update(**{key: value for key, value in data.items() if key in model._meta.fields}).where(*conditions).execute(),
        )

    async def validate(req, model, **kwargs):
        try:
            return model(**(await req.get_json())).model_dump(exclude_unset=kwargs.get("exclude_unset", False)), None
        except pydantic.ValidationError:
            return None, "Invalid key configuration"

    ns = {
        **validation,
        "APIKeyType": APIKeyType,
        "datetime": datetime,
        "timezone": timezone,
        "request": request,
        "RetCode": RetCode,
        "logging": logging,
        "StatusEnum": SimpleNamespace(VALID=SimpleNamespace(value="1")),
        "APIToken": env.full,
        "ScopedAPIToken": env.models["ScopedAPIToken"],
        "APITokenService": service(env.full),
        "ScopedAPITokenService": service(env.models["ScopedAPIToken"]),
        "_managed_tenant_id": lambda: "owner",
        "_generate_unique_token": lambda key_type: "created-key",
        "generate_confirmation_token": lambda: "unused-beta",
        "current_timestamp": lambda: 1,
        "datetime_format": str,
        "_expiry_from_days": lambda days, now: None,
        "get_json_result": lambda **kwargs: jsonify(code=0, **kwargs),
        "_management_error": lambda code, message: (jsonify(code=int(code), message=message), int(code)),
        "_management_failure": lambda: (jsonify(code=500), 500),
        "validate_and_parse_json_request": validate,
        "KnowledgebaseService": SimpleNamespace(query=lambda **kwargs: [True] if kwargs["id"] == "allowed" else []),
    }
    _definitions("api/apps/restful_apis/system_api.py", {"_record_data", "_serialize_api_key", "_datasets_belong_to_tenant", "new_token", "update_token"}, ns, undecorated=True)
    env.app.add_url_rule("/manage", "create_key", ns["new_token"], methods=["POST"])
    env.app.add_url_rule("/manage", "update_key", ns["update_token"], methods=["PATCH"])
    return env


async def test_full_key_creation_roundtrip_keeps_selected_scope(management):
    response = await management.client.post("/manage", json={"name": "FDE", "key_type": "full_access", "allowed_dataset_ids": ["allowed"]})
    assert response.status_code == 200
    result = await response.get_json()
    assert result["data"]["allowed_dataset_ids"] == ["allowed"]
    assert management.full.get_by_id(("owner", "created-key")).allowed_dataset_ids == ["allowed"]


async def test_full_key_scope_can_be_edited_and_reset_by_an_administrator(management):
    response = await management.client.patch("/manage", json={"token": "scoped", "allowed_dataset_ids": ["allowed"]})
    assert response.status_code == 200
    assert (await response.get_json())["data"]["allowed_dataset_ids"] == ["allowed"]
    response = await management.client.patch("/manage", json={"token": "scoped", "allowed_dataset_ids": []})
    assert response.status_code == 200
    assert management.auth.resolve_api_key("scoped").allowed_dataset_ids is None


@pytest.mark.parametrize("method", ["POST", "PATCH"])
async def test_management_rejects_selection_of_another_tenants_dataset(management, method):
    payload = {"allowed_dataset_ids": ["other"]}
    payload.update({"name": "FDE", "key_type": "full_access"} if method == "POST" else {"token": "scoped"})
    response = await management.client.open("/manage", method=method, json=payload)
    assert response.status_code == 400
    assert management.full.get_by_id(("owner", "scoped")).allowed_dataset_ids == ["allowed"]


async def test_retrieval_key_scope_cannot_be_reset_to_all_datasets(management):
    management.models["ScopedAPIToken"].create(tenant_id="owner", token="readonly", name="Read", allowed_dataset_ids=["allowed"])
    response = await management.client.patch("/manage", json={"token": "readonly", "allowed_dataset_ids": []})
    assert response.status_code == 400
    assert management.models["ScopedAPIToken"].get_by_id(("owner", "readonly")).allowed_dataset_ids == ["allowed"]
