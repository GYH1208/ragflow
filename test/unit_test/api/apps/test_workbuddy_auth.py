from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
from quart import Quart, g
from werkzeug.datastructures import Headers
from api.auth.workbuddy.types import WorkBuddyAccessContext, WorkBuddyAuthenticationError
from test.unit_test.api.db.services.test_center_dataset_service import add_kb, setup_center
from test.unit_test.api.db.services.test_workbuddy_employee_service import employee_db as employee_db, profile as profile  # noqa: PLC0414


@pytest.fixture
def guarded_app(monkeypatch):
    import api.apps as auth

    app = Quart("employee-auth")
    app.secret_key = "test"
    calls = []

    async def authenticate(token):
        calls.append(token)
        if token != "valid":
            raise WorkBuddyAuthenticationError("invalid")
        return SimpleNamespace(id="employee", account_source="workbuddy"), WorkBuddyAccessContext("employee", "123", "2", frozenset({"kb-a"}))

    monkeypatch.setattr(auth, "authenticate_workbuddy", authenticate, raising=False)

    @app.route("/read")
    @auth.login_required(auth_types=(*auth.DEFAULT_AUTH_TYPES, "WORKBUDDY"), api_scope="knowledge:retrieve")
    async def read():
        from api.apps.api_key_auth import allowed_dataset_ids

        return {"actor": auth.current_user.id, "datasets": sorted(allowed_dataset_ids())}

    @app.route("/write", methods=["POST"])
    @auth.login_required
    async def write():
        return {"unsafe": True}

    @app.route("/bad-scope")
    @auth.login_required(auth_types=("WORKBUDDY",))
    async def bad_scope():
        return {"unsafe": True}

    return app, calls


async def test_employee_requires_explicit_route_and_scope(guarded_app):
    app, calls = guarded_app
    client = app.test_client()
    headers = {"x-oneid-access-token": "valid"}
    res = await client.get("/read", headers=headers)
    assert res.status_code == 200 and (await res.get_json()) == {"actor": "employee", "datasets": ["kb-a"]}
    for method, path in [("post", "/write"), ("get", "/bad-scope")]:
        res = await getattr(client, method)(path, headers=headers)
        assert res.status_code in (401, 403)
    assert calls == ["valid"]


@pytest.mark.parametrize(
    "headers",
    [
        [("x-oneid-access-token", "invalid"), ("Authorization", "Bearer valid-old-key")],
        [("x-oneid-access-token", "valid"), ("x-oneid-access-token", "valid")],
        [("x-oneid-access-token", "valid,valid")],
        [("x-oneid-access-token", "")],
    ],
)
async def test_invalid_or_duplicate_oneid_header_never_falls_back(guarded_app, headers):
    app, calls = guarded_app

    @app.before_request
    async def old_user():
        g.user = SimpleNamespace(id="old-admin")
        g.auth_type = "JWT"

    res = await app.test_client().get("/read", headers=Headers(headers))
    assert res.status_code in (401, 403)


async def test_foreign_enterprise_never_provisions(monkeypatch):
    from api.apps import workbuddy_auth as auth
    from api.auth.workbuddy.types import WorkBuddyAuthenticationError

    app = Quart("foreign-employee")
    verifier = SimpleNamespace(verify_employee=AsyncMock(side_effect=WorkBuddyAuthenticationError("Wrong enterprise")))
    app.extensions["workbuddy_oneid"] = verifier
    forbidden = AsyncMock()
    monkeypatch.setattr(auth, "_sync_context", forbidden)
    async with app.app_context():
        with pytest.raises(WorkBuddyAuthenticationError):
            await auth.authenticate_workbuddy("foreign-ticket")
    forbidden.assert_not_called()


def test_local_disable_grant_revocation_and_move_apply_next_request(monkeypatch):
    from contextlib import nullcontext
    from dataclasses import replace
    from peewee import SqliteDatabase
    from api.db import db_models as m
    from api.auth.workbuddy.types import DepartmentPath, DepartmentNode, VerifiedEmployee
    from api.apps.workbuddy_auth import _sync_context
    from api.db.services.center_dataset_service import CenterDatasetService as C
    from api.db.services.workbuddy_employee_service import WorkBuddyEmployeeService as E
    from werkzeug.exceptions import Forbidden

    models = [m.User, m.WorkBuddyEmployeeIdentity, m.EnterpriseDepartment, m.CenterDatasetGrant, m.Knowledgebase]
    db = SqliteDatabase(":memory:")
    with db.bind_ctx(models), db.connection_context():
        db.create_tables(models)
        monkeypatch.setattr(db, "connection_context", lambda: nullcontext())
        path = DepartmentPath("3", True, True, (DepartmentNode("2", "中心", None), DepartmentNode("3", "项目组", "2")))
        profile = VerifiedEmployee("123", "employee", "issuer", "app", "alias", "员工", "Enabled", (path,))
        user, ctx = _sync_context(profile)
        assert ctx.allowed_dataset_ids == frozenset()
        C.set_center("123", "2", True, "admin")
        m.Knowledgebase.create(id="kb-a", tenant_id="owner", name="A", embd_id="model", created_by="owner")
        C.replace_grants("123", "2", ["kb-a"], "admin")
        assert _sync_context(profile)[1].allowed_dataset_ids == frozenset({"kb-a"})
        C.replace_grants("123", "2", [], "admin")
        assert _sync_context(profile)[1].allowed_dataset_ids == frozenset()
        C.replace_grants("123", "2", ["kb-a"], "admin")
        assert _sync_context(replace(profile, paths=()))[1].allowed_dataset_ids == frozenset()
        E.set_enabled(user.id, False, "admin")
        with pytest.raises(Forbidden):
            _sync_context(profile)
        assert m.User.get_by_id(user.id).is_active == "0"


def test_company_grants_apply_without_a_center_and_combine_with_center_grants(employee_db, profile, monkeypatch):
    from contextlib import nullcontext
    from dataclasses import replace

    from werkzeug.exceptions import Forbidden

    from api.apps.workbuddy_auth import _sync_context
    from api.db.services.workbuddy_employee_service import WorkBuddyEmployeeService as Employees

    monkeypatch.setattr(employee_db, "connection_context", nullcontext)
    centers = setup_center(profile)
    add_kb("kb-center")
    shared = add_kb("kb-shared")
    add_kb("kb-foreign")
    centers.replace_grants("123", "2", ["kb-center"], "admin")
    centers.replace_enterprise_grants("123", ["kb-shared"], "admin")
    centers.replace_enterprise_grants("456", ["kb-foreign"], "admin")

    _, with_center = _sync_context(profile)
    assert with_center.allowed_dataset_ids == frozenset({"kb-center", "kb-shared"})
    no_center_profile = replace(profile, paths=())
    user, no_center = _sync_context(no_center_profile)
    assert no_center.center_id is None
    assert no_center.allowed_dataset_ids == frozenset({"kb-shared"})
    assert centers.allowed_datasets("123", "2") == frozenset({"kb-center"})

    shared.status = "0"
    shared.save(only=[type(shared).status])
    assert _sync_context(no_center_profile)[1].allowed_dataset_ids == frozenset()
    shared.status = "1"
    shared.save(only=[type(shared).status])
    centers.replace_enterprise_grants("123", [], "admin")
    assert _sync_context(profile)[1].allowed_dataset_ids == frozenset({"kb-center"})
    assert _sync_context(no_center_profile)[1].allowed_dataset_ids == frozenset()

    centers.replace_enterprise_grants("123", ["kb-shared"], "admin")
    Employees.set_enabled(user.id, False, "admin")
    with pytest.raises(Forbidden):
        _sync_context(no_center_profile)


@pytest.mark.parametrize("ticket", ["valid", "invalid"])
async def test_employee_responses_are_never_cacheable(guarded_app, ticket):
    from api.apps.workbuddy_auth import install_workbuddy_lifecycle

    app, _ = guarded_app
    install_workbuddy_lifecycle(app)
    res = await app.test_client().get("/read", headers={"x-oneid-access-token": ticket})
    assert res.headers.get("Cache-Control") == "private, no-store"
