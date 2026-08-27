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
from types import SimpleNamespace

import pytest
from peewee import SqliteDatabase

from api.db import TeamMemberState, TenantPermission
from api.db.db_models import Team, TeamMember
from api.db.services.team_service import TeamAuthorizationService, TeamMemberService, TeamService
from common.constants import StatusEnum


@pytest.fixture
def team_database():
    database = SqliteDatabase(":memory:")
    models = [Team, TeamMember]
    with database.bind_ctx(models), database.connection_context():
        database.create_tables(models)
        yield


def create_team(team_id, owner_id, *, name=None, status=StatusEnum.VALID.value):
    return Team.create(
        id=team_id,
        tenant_id=owner_id,
        name=name or team_id,
        created_by=owner_id,
        status=status,
    )


def create_member(member_id, team_id, user_id, *, state=TeamMemberState.ACTIVE.value, status=StatusEnum.VALID.value):
    return TeamMember.create(
        id=member_id,
        team_id=team_id,
        user_id=user_id,
        state=state,
        invited_by="owner-1",
        status=status,
    )


def test_team_service_normalizes_and_returns_only_valid_owned_teams(team_database):
    create_team("team-owned", "owner-1", name="  Finance  ")
    create_team("team-invalid", "owner-1", status=StatusEnum.INVALID.value)
    create_team("team-other", "owner-2")

    assert TeamService.normalize_name("  Finance  ") == "Finance"
    assert TeamService.get_owned_team("team-owned", "owner-1").id == "team-owned"
    assert TeamService.get_owned_team("team-invalid", "owner-1") is None
    assert TeamService.get_owned_team("team-other", "owner-1") is None
    assert [team["id"] for team in TeamService.list_owned("owner-1")] == ["team-owned"]


def test_active_team_ids_and_visible_owners_exclude_invited_invalid_members_and_invalid_teams(team_database):
    create_team("team-active-1", "owner-1")
    create_team("team-active-2", "owner-1")
    create_team("team-invalid", "owner-2", status=StatusEnum.INVALID.value)
    create_team("team-invited", "owner-3")
    create_team("team-invalid-member", "owner-4")
    create_member("member-active-1", "team-active-1", "member-1")
    create_member("member-active-2", "team-active-2", "member-1")
    create_member("member-invalid-team", "team-invalid", "member-1")
    create_member("member-invited", "team-invited", "member-1", state=TeamMemberState.INVITED.value)
    create_member("member-invalid", "team-invalid-member", "member-1", status=StatusEnum.INVALID.value)

    assert set(TeamMemberService.active_team_ids("member-1")) == {"team-active-1", "team-active-2"}
    assert TeamMemberService.visible_owner_ids("member-1") == ["owner-1"]


def test_only_an_admin_who_owns_the_team_can_manage_it(team_database, monkeypatch):
    create_team("team-owned", "admin-1")
    create_team("team-other", "admin-2")
    monkeypatch.setattr(
        "api.db.services.team_service.UserService.is_admin",
        lambda user_id: user_id in {"admin-1", "admin-2"},
    )

    assert TeamAuthorizationService.can_manage_team("admin-1", "team-owned") is True
    assert TeamAuthorizationService.can_manage_team("admin-1", "team-other") is False
    assert TeamAuthorizationService.can_manage_team("member-1", "team-owned") is False


@pytest.mark.parametrize(
    ("permission", "team_id", "active_team_ids", "expected"),
    [
        ("me", None, ["team-1"], False),
        ("team", "team-1", [], False),
        ("team", "team-1", ["team-1"], True),
    ],
)
def test_member_kb_access(permission, team_id, active_team_ids, expected, monkeypatch):
    kb = SimpleNamespace(
        tenant_id="owner-1",
        permission=permission,
        team_id=team_id,
        status=StatusEnum.VALID.value,
    )
    monkeypatch.setattr(TeamMemberService, "active_team_ids", lambda _user_id: active_team_ids)

    assert TeamAuthorizationService.can_access_kb("member-1", kb) is expected


def test_owner_always_accesses_own_valid_kb():
    kb = SimpleNamespace(
        tenant_id="owner-1",
        permission=TenantPermission.ME.value,
        team_id=None,
        status=StatusEnum.VALID.value,
    )

    assert TeamAuthorizationService.can_access_kb("owner-1", kb) is True


def test_invalid_kb_is_inaccessible_even_to_its_owner():
    kb = SimpleNamespace(
        tenant_id="owner-1",
        permission=TenantPermission.ME.value,
        team_id=None,
        status=StatusEnum.INVALID.value,
    )

    assert TeamAuthorizationService.can_access_kb("owner-1", kb) is False


def test_validate_assignment_requires_a_system_administrator(monkeypatch):
    kb = SimpleNamespace(tenant_id="owner-1")
    monkeypatch.setattr("api.db.services.team_service.UserService.is_admin", lambda _user_id: False)

    assert TeamAuthorizationService.validate_assignment("owner-1", kb, "me", None) == (
        False,
        "System administrator permission is required.",
    )


def test_validate_assignment_rejects_another_owners_dataset(monkeypatch):
    kb = SimpleNamespace(tenant_id="owner-2")
    monkeypatch.setattr("api.db.services.team_service.UserService.is_admin", lambda _user_id: True)

    assert TeamAuthorizationService.validate_assignment("admin-1", kb, "me", None) == (
        False,
        "No authorization to assign this dataset.",
    )


def test_validate_assignment_rejects_a_team_id_for_private_permission(monkeypatch):
    kb = SimpleNamespace(tenant_id="owner-1")
    monkeypatch.setattr("api.db.services.team_service.UserService.is_admin", lambda _user_id: True)

    assert TeamAuthorizationService.validate_assignment("owner-1", kb, "me", "team-1") == (
        False,
        "team_id must be empty when permission is me.",
    )


def test_validate_assignment_requires_team_for_team_permission(monkeypatch):
    kb = SimpleNamespace(tenant_id="owner-1")
    monkeypatch.setattr("api.db.services.team_service.UserService.is_admin", lambda _user_id: True)

    assert TeamAuthorizationService.validate_assignment("owner-1", kb, "team", None) == (
        False,
        "team_id is required when permission is team.",
    )


def test_validate_assignment_requires_team_and_dataset_to_share_owner(team_database, monkeypatch):
    create_team("team-other", "owner-2")
    kb = SimpleNamespace(tenant_id="owner-1")
    monkeypatch.setattr("api.db.services.team_service.UserService.is_admin", lambda _user_id: True)

    assert TeamAuthorizationService.validate_assignment("owner-1", kb, "team", "team-other") == (
        False,
        "The team and dataset must have the same owner.",
    )


def test_validate_assignment_accepts_owned_team_for_an_administrator(team_database, monkeypatch):
    create_team("team-owned", "owner-1")
    kb = SimpleNamespace(tenant_id="owner-1")
    monkeypatch.setattr("api.db.services.team_service.UserService.is_admin", lambda _user_id: True)

    assert TeamAuthorizationService.validate_assignment("owner-1", kb, "team", "team-owned") == (True, None)
