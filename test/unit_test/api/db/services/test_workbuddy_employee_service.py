from dataclasses import replace
import pytest
from peewee import SqliteDatabase
from api.db import db_models as m
from api.auth.workbuddy.types import DepartmentNode, DepartmentPath, VerifiedEmployee

MODELS = [m.User, m.WorkBuddyEmployeeIdentity, m.EnterpriseDepartment, m.CenterDatasetGrant, m.Tenant, m.UserTenant, m.Team, m.TeamMember, m.File, m.Knowledgebase]


@pytest.fixture
def employee_db():
    db = SqliteDatabase(":memory:")
    with db.bind_ctx(MODELS), db.connection_context():
        db.create_tables(MODELS)
        yield db


@pytest.fixture
def profile():
    path = DepartmentPath("3", True, True, (DepartmentNode("1", "公司", None), DepartmentNode("2", "中心", "1"), DepartmentNode("3", "项目组", "2")))
    return VerifiedEmployee("123", "employee", "issuer", "app", "worker", "员工", "Enabled", (path,))


def test_jit_creates_mcp_only_account(employee_db, profile):
    from api.db.services.workbuddy_employee_service import WorkBuddyEmployeeService as S

    user = S.sync_employee(profile)
    assert user.account_source == "workbuddy"
    assert user.password is None and user.access_token is None
    assert not user.is_superuser
    assert user.email == f"wb-{user.id}@workbuddy.invalid"
    assert all(model.select().count() == 0 for model in [m.Tenant, m.UserTenant, m.TeamMember, m.File])
    assert m.CenterDatasetGrant.select().count() == 0
    assert S.sync_employee(profile).id == user.id


def test_existing_local_user_is_not_merged(employee_db, profile):
    from api.db.services.workbuddy_employee_service import WorkBuddyEmployeeService as S

    m.User.create(id="local", nickname=profile.name, email="worker@example.com")
    assert S.sync_employee(profile).id != "local"
    assert m.User.select().count() == 2


def test_profile_sync_does_not_reactivate_disabled_user(employee_db, profile):
    from api.db.services.workbuddy_employee_service import WorkBuddyEmployeeService as S

    user = S.sync_employee(profile)
    S.set_enabled(user.id, False, "admin")
    S.sync_employee(replace(profile, name="新名称"))
    assert m.User.get_by_id(user.id).is_active == "0"
    assert m.User.get_by_id(user.id).nickname == "新名称"
    assert m.WorkBuddyEmployeeIdentity.get().enabled_updated_by == "admin"


def test_concurrent_first_login_reuses_one_user(employee_db, profile, monkeypatch):
    from api.db.services.workbuddy_employee_service import WorkBuddyEmployeeService as S

    # Insert winner in the outer transaction; losing savepoint must not leave a User.
    winner = S.sync_employee(profile)
    lookup = m.WorkBuddyEmployeeIdentity.get_or_none
    attempts = iter([True, False])

    def stale_lookup(*args, **kwargs):
        return None if next(attempts, False) else lookup(*args, **kwargs)

    monkeypatch.setattr(m.WorkBuddyEmployeeIdentity, "get_or_none", stale_lookup)
    assert S.sync_employee(profile).id == winner.id
    assert all(S.sync_employee(profile).id == winner.id for _ in range(4))
    assert m.User.select().count() == m.WorkBuddyEmployeeIdentity.select().count() == 1


def test_incomplete_paths_are_pending_and_local_accounts_cannot_be_toggled(employee_db, profile):
    from api.db.services.workbuddy_employee_service import WorkBuddyEmployeeService as S

    p = replace(profile, paths=(replace(profile.paths[0], complete=False),))
    S.sync_employee(p)
    assert m.WorkBuddyEmployeeIdentity.get().sync_status == "pending"
    m.User.create(id="local", nickname="Local", email="local@example.com")
    with pytest.raises(ValueError):
        S.set_enabled("local", False, "admin")
