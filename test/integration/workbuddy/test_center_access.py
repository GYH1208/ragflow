"""Opt-in real database tests. Never fall back to the configured production DB."""

import asyncio
import importlib.util
import os
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from urllib.parse import unquote, urlparse
import httpx
import pytest
from peewee import MySQLDatabase
from playhouse.migrate import MySQLMigrator, migrate


@pytest.fixture
def database():
    if os.getenv("RAGFLOW_WORKBUDDY_RUN_INTEGRATION") != "1":
        pytest.skip("WorkBuddy database integration is opt in")
    url = urlparse(os.environ["RAGFLOW_WORKBUDDY_TEST_DATABASE_URL"])
    name = url.path.lstrip("/")
    if url.scheme != "mysql" or not re.fullmatch(r"ragflow_wb_test_[a-f0-9]{16}", name):
        pytest.fail("Refusing database outside the dedicated WorkBuddy test namespace")
    from api.db import db_models as m

    models = [m.User, m.WorkBuddyEmployeeIdentity, m.EnterpriseDepartment, m.CenterDatasetGrant, m.Knowledgebase, m.Document]
    db = MySQLDatabase(name, user=unquote(url.username or ""), password=unquote(url.password or ""), host=url.hostname, port=url.port or 3306, charset="utf8mb4")
    with db.bind_ctx(models), db.connection_context():
        db.create_tables(models)
        try:
            yield db, m
        finally:
            db.drop_tables(models, safe=True)
    db.close()


def profile():
    from api.auth.workbuddy.types import VerifiedEmployee

    return VerifiedEmployee("123", "employee", "issuer", "app", "worker", "Employee", "Enabled", ())


def test_migration_on_legacy_schema_twice(database, monkeypatch):
    from api.db.workbuddy_migration import migrate_workbuddy_schema

    db, m = database
    m.User.create(id="old", nickname="Existing", email="existing@example.invalid")
    m.Knowledgebase.create(id="kb", tenant_id="original-owner", name="existing", embd_id="model", created_by="owner", permission="me")
    before = m.Knowledgebase.get_by_id("kb").to_dict().copy()
    migrate(MySQLMigrator(db).drop_column("user", "account_source"))
    monkeypatch.setenv("RAGFLOW_WORKBUDDY_TENANT_ID", "1494560257098951656")
    migrate_workbuddy_schema(MySQLMigrator(db))
    migrate_workbuddy_schema(MySQLMigrator(db))
    assert m.User.get_by_id("old").account_source == "local"
    assert m.Knowledgebase.get_by_id("kb").to_dict() == before
    assert m.CenterDatasetGrant.select().count() == 0
    assert m.EnterpriseDepartment.select().count() == 1


def test_concurrent_jit_has_single_mapping_and_no_orphan_user(database, monkeypatch):
    from api.apps.workbuddy_auth import _sync_context

    db, m = database
    from threading import Barrier, local
    from api.auth.workbuddy.types import DepartmentNode, DepartmentPath

    barrier = Barrier(4)
    seen = local()
    original = m.EnterpriseDepartment.get

    def aligned_lookup(*args, **kwargs):
        try:
            return original(*args, **kwargs)
        except m.EnterpriseDepartment.DoesNotExist:
            if kwargs.get("department_id") == "99901" and not getattr(seen, "aligned", False):
                seen.aligned = True
                barrier.wait(timeout=10)
            raise

    monkeypatch.setattr(m.EnterpriseDepartment, "get", aligned_lookup)
    shared = replace(profile(), paths=(DepartmentPath("99902", True, True, (DepartmentNode("99901", "Root", None), DepartmentNode("99902", "Leaf", "99901"))),))
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: _sync_context(shared)[0].id, range(8)))
    assert len(set(results)) == 1
    assert m.User.select().count() == m.WorkBuddyEmployeeIdentity.select().count() == 1
    assert m.User.get().password is None


async def test_live_request_chain_cannot_bypass_center_grants(database, monkeypatch):
    from quart import Quart
    import api.apps as auth
    from api.apps.restful_apis import dataset_api, document_api, chunk_api
    from api.db.services.center_dataset_service import CenterDatasetService as C
    from api.db.services.workbuddy_employee_service import WorkBuddyEmployeeService as E
    from test.unit_test.api.auth.workbuddy.test_oneid_client import make_client

    db, m = database
    application = Quart("real-workbuddy-integration")
    application.secret_key = "test"
    for mod in (dataset_api, document_api, chunk_api):
        application.register_blueprint(mod.manager, url_prefix="/api/v1")
    verifier, _ = make_client()
    application.extensions["workbuddy_oneid"] = verifier
    target = application.test_client()

    async def backend(req):
        res = await target.open(req.url.raw_path.decode(), method=req.method, headers=dict(req.headers), data=req.content or None)
        return httpx.Response(res.status_code, headers=dict(res.headers), content=await res.get_data())

    path = Path(__file__).resolve().parents[3] / "mcp/server/server.py"
    spec = importlib.util.spec_from_file_location("workbuddy_mysql_mcp", path)
    server = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(server)
    server.AUTH_MODE = "workbuddy"
    server.MODE = server.LaunchMode.HOST
    server.TRANSPORT_SSE_ENABLED = False
    server.TRANSPORT_STREAMABLE_HTTP_ENABLED = True
    server.JSON_RESPONSE = True
    server.STREAMABLE_HTTP_ALLOWED_HOSTS = ["mcp.test"]
    server.STREAMABLE_HTTP_ALLOWED_ORIGINS = ["http://mcp.test"]

    async def make_http(conn):
        if conn._async_client is None:
            conn._async_client = httpx.AsyncClient(transport=httpx.MockTransport(backend))
        return conn._async_client

    monkeypatch.setattr(server.RAGFlowConnector, "_get_client", make_http)
    app = server.create_starlette_app()
    headers = {"accept": "application/json, text/event-stream", "content-type": "application/json", "x-oneid-access-token": "employee-ticket"}
    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "ragflow_retrieval", "arguments": {"question": ""}}}
    kb = "a" * 12 + "1aaa8" + "a" * 15
    async with verifier, app.router.lifespan_context(app), httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://mcp.test") as client:
        first = await client.post("/mcp", headers=headers, json=body)
        assert first.status_code == 200
        assert "No accessible datasets" in first.text or "暂未配置" in first.text
        user = m.User.get()
        assert user.account_source == "workbuddy"
        C.set_center("123", "2", True, "admin")
        m.Knowledgebase.create(id=kb, tenant_id="original-owner", name="Approved", description="", embd_id="model", created_by="owner")
        C.replace_grants("123", "2", [kb], "admin")
        allowed = await client.post("/mcp", headers=headers, json=body)
        assert allowed.status_code == 200
        assert allowed.json()["result"].get("isError") is not True, allowed.text
        C.replace_grants("123", "2", [], "admin")
        denied = await client.post("/mcp", headers=headers, json=body)
        assert "No accessible datasets" in denied.text or "暂未配置" in denied.text
        E.set_enabled(user.id, False, "admin")
        disabled = await client.post("/mcp", headers=headers, json=body)
        assert disabled.json()["result"].get("isError") is True
        assert m.User.select().count() == 1
