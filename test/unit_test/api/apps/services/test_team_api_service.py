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
import pytest
from peewee import SqliteDatabase

from api.apps.services import team_api_service
from api.apps.services.team_api_service import (
    create_team,
    delete_team,
    invite_member,
    list_members,
    list_teams,
    remove_or_leave_member,
    rename_team,
    update_invitation,
)
from api.db import TeamMemberState, TenantPermission
from api.db.db_models import Knowledgebase, Team, TeamMember, User
from api.db.services.knowledgebase_service import KnowledgebaseService
from api.db.services.team_service import TeamAuthorizationService, TeamMemberService, TeamService
from common.constants import StatusEnum


@pytest.fixture()
def team_database(monkeypatch):
    database = SqliteDatabase(":memory:")
    models = [User, Team, TeamMember, Knowledgebase]
    with database.bind_ctx(models), database.connection_context():
        database.create_tables(models)
        monkeypatch.setattr(team_api_service, "DB", database)
        yield database


def _user(user_id, email=None, *, admin=False, status=StatusEnum.VALID.value):
    return User.create(
        id=user_id,
        email=email or f"{user_id}@example.com",
        nickname=user_id,
        is_superuser=admin,
        status=status,
    )


def _team(team_id, owner_id, name=None, *, status=StatusEnum.VALID.value):
    return Team.create(
        id=team_id,
        tenant_id=owner_id,
        name=name or team_id,
        created_by=owner_id,
        status=status,
    )


def _member(member_id, team_id, user_id, *, state=TeamMemberState.ACTIVE.value, status=StatusEnum.VALID.value):
    return TeamMember.create(
        id=member_id,
        team_id=team_id,
        user_id=user_id,
        state=state,
        invited_by="admin-1",
        status=status,
    )


def _kb(kb_id, owner_id, *, team_id=None, status=StatusEnum.VALID.value):
    return Knowledgebase.create(
        id=kb_id,
        tenant_id=owner_id,
        name=kb_id,
        embd_id="embedding-1",
        created_by=owner_id,
        permission=TenantPermission.TEAM.value if team_id else TenantPermission.ME.value,
        team_id=team_id,
        status=status,
    )


def test_create_team_requires_an_admin_and_rejects_duplicate_names(team_database):
    _user("member-1")
    _user("admin-1", admin=True)
    _team("existing", "admin-1", "Finance")

    assert create_team("member-1", "Engineering") == (False, "No authorization.")
    assert create_team("admin-1", " Finance ") == (False, "Team name already exists.")

    ok, created = create_team("admin-1", " Engineering ")
    assert ok is True
    assert created["name"] == "Engineering"
    assert created["tenant_id"] == "admin-1"


def test_rename_team_hides_cross_owner_teams_and_rejects_duplicate_names(team_database):
    _user("admin-1", admin=True)
    _user("admin-2", admin=True)
    _team("team-1", "admin-1", "Finance")
    _team("team-2", "admin-1", "Engineering")
    _team("team-other", "admin-2", "Research")

    assert rename_team("admin-1", "team-other", "Hidden") == (False, "No authorization.")
    assert rename_team("admin-1", "team-1", " Engineering ") == (False, "Team name already exists.")

    ok, renamed = rename_team("admin-1", "team-1", " Accounting ")
    assert ok is True
    assert renamed["name"] == "Accounting"


def test_delete_team_unassigns_datasets_atomically(monkeypatch):
    updates = []
    deletions = []
    monkeypatch.setattr(TeamAuthorizationService, "can_manage_team", lambda *_args: True)
    monkeypatch.setattr(
        KnowledgebaseService,
        "filter_update",
        lambda conditions, values: updates.append(values) or 2,
    )
    monkeypatch.setattr(TeamMemberService, "deactivate_by_team", lambda team_id: deletions.append(("members", team_id)))
    monkeypatch.setattr(TeamService, "deactivate", lambda team_id: deletions.append(("team", team_id)))

    ok, result = delete_team("admin-1", "team-1")

    assert ok is True
    assert result == {"unassigned_dataset_count": 2}
    assert updates == [{"permission": "me", "team_id": None}]
    assert deletions == [("members", "team-1"), ("team", "team-1")]


def test_deactivate_helpers_only_invalidate_current_valid_rows(team_database):
    _team("team-1", "admin-1")
    _team("team-2", "admin-1")
    _member("member-1", "team-1", "user-1")
    _member("member-2", "team-1", "user-2", status=StatusEnum.INVALID.value)
    _member("member-3", "team-2", "user-3")

    assert TeamMemberService.deactivate_by_team("team-1") == 1
    assert TeamMemberService.deactivate_by_team("team-1") == 0
    assert TeamService.deactivate("team-1") == 1
    assert TeamService.deactivate("team-1") == 0
    assert TeamMember.get_by_id("member-3").status == StatusEnum.VALID.value
    assert Team.get_by_id("team-2").status == StatusEnum.VALID.value


def test_delete_team_rolls_back_every_step_on_failure(team_database, monkeypatch):
    _user("admin-1", admin=True)
    _team("team-1", "admin-1")
    _member("member-1", "team-1", "member-1")
    _kb("kb-1", "admin-1", team_id="team-1")
    monkeypatch.setattr(TeamService, "deactivate", lambda _team_id: (_ for _ in ()).throw(RuntimeError("boom")))

    with pytest.raises(RuntimeError, match="boom"):
        delete_team("admin-1", "team-1")

    assert Knowledgebase.get_by_id("kb-1").permission == TenantPermission.TEAM.value
    assert Knowledgebase.get_by_id("kb-1").team_id == "team-1"
    assert TeamMember.get_by_id("member-1").status == StatusEnum.VALID.value
    assert Team.get_by_id("team-1").status == StatusEnum.VALID.value


def test_invite_requires_valid_registered_user_and_rejects_live_relationships(team_database):
    _user("admin-1", admin=True)
    _user("inactive", "inactive@example.com", status=StatusEnum.INVALID.value)
    _user("active", "active@example.com")
    _user("invited", "invited@example.com")
    _team("team-1", "admin-1", "Finance")
    _member("active-rel", "team-1", "active")
    _member("invited-rel", "team-1", "invited", state=TeamMemberState.INVITED.value)

    assert invite_member("admin-1", "team-1", "missing@example.com") == (False, "User not found.")
    assert invite_member("admin-1", "team-1", "inactive@example.com") == (False, "User not found.")
    assert invite_member("admin-1", "team-1", "active@example.com") == (False, "User is already in the team.")
    assert invite_member("admin-1", "team-1", "invited@example.com") == (False, "User has already been invited.")


def test_invite_reuses_an_invalid_unique_relationship(team_database):
    _user("admin-1", admin=True)
    _user("member-1", "member@example.com")
    _team("team-1", "admin-1")
    _member("old-rel", "team-1", "member-1", status=StatusEnum.INVALID.value)

    ok, invited = invite_member("admin-1", "team-1", "member@example.com")

    assert ok is True
    assert invited["id"] == "member-1"
    assert invited["state"] == TeamMemberState.INVITED.value
    assert TeamMember.select().count() == 1
    relation = TeamMember.get_by_id("old-rel")
    assert relation.status == StatusEnum.VALID.value
    assert relation.state == TeamMemberState.INVITED.value
    assert relation.invited_by == "admin-1"


def test_invitation_accept_and_reject_only_change_the_named_current_user(team_database):
    _team("team-1", "admin-1")
    _member("invite-1", "team-1", "member-1", state=TeamMemberState.INVITED.value)
    _member("invite-2", "team-1", "member-2", state=TeamMemberState.INVITED.value)

    assert update_invitation("member-1", "team-1", "accept") == (True, True)
    assert TeamMember.get_by_id("invite-1").state == TeamMemberState.ACTIVE.value
    assert TeamMember.get_by_id("invite-2").state == TeamMemberState.INVITED.value
    assert update_invitation("member-2", "team-1", "reject") == (True, True)
    assert TeamMember.get_by_id("invite-2").status == StatusEnum.INVALID.value
    assert update_invitation("member-3", "team-1", "accept") == (False, "Invitation not found.")


def test_member_can_leave_self_and_only_owner_admin_can_remove_others(team_database):
    _user("admin-1", admin=True)
    _user("admin-2", admin=True)
    _team("team-1", "admin-1")
    _member("member-1", "team-1", "user-1")
    _member("member-2", "team-1", "user-2")

    assert remove_or_leave_member("user-1", "team-1", "user-1") == (True, True)
    assert TeamMember.get_by_id("member-1").status == StatusEnum.INVALID.value
    assert remove_or_leave_member("admin-2", "team-1", "user-2") == (False, "No authorization.")
    assert remove_or_leave_member("admin-1", "team-1", "user-2") == (True, True)
    assert TeamMember.get_by_id("member-2").status == StatusEnum.INVALID.value


def test_self_leave_requires_active_membership(team_database):
    _team("team-1", "admin-1")
    _member("invite-1", "team-1", "user-1", state=TeamMemberState.INVITED.value)

    assert remove_or_leave_member("user-1", "team-1", "user-1") == (False, "No authorization.")
    assert TeamMember.get_by_id("invite-1").status == StatusEnum.VALID.value


def test_list_teams_returns_owned_active_and_invited_entries_with_counts(team_database):
    _user("admin-1", admin=True)
    _team("owned", "admin-1")
    _team("joined", "admin-2")
    _team("invited", "admin-3")
    _team("hidden", "admin-4")
    _member("joined-rel", "joined", "admin-1")
    _member("invite-rel", "invited", "admin-1", state=TeamMemberState.INVITED.value)
    _member("other-member", "owned", "user-2")
    _kb("kb-1", "admin-1", team_id="owned")
    _kb("kb-2", "admin-1", team_id="owned")

    ok, teams = list_teams("admin-1")

    assert ok is True
    by_id = {team["id"]: team for team in teams}
    assert set(by_id) == {"owned", "joined", "invited"}
    assert by_id["owned"]["membership_state"] == "owner"
    assert by_id["owned"]["member_count"] == 1
    assert by_id["owned"]["dataset_count"] == 2
    assert by_id["joined"]["membership_state"] == TeamMemberState.ACTIVE.value
    assert by_id["invited"]["membership_state"] == TeamMemberState.INVITED.value


def test_members_are_visible_only_to_the_owner_admin(team_database):
    _user("admin-1", admin=True)
    _user("admin-2", admin=True)
    _user("member-1", "member@example.com")
    _team("team-1", "admin-1")
    _member("relation-1", "team-1", "member-1")

    assert list_members("admin-2", "team-1") == (False, "No authorization.")
    ok, members = list_members("admin-1", "team-1")
    assert ok is True
    assert members == [
        {
            "id": "member-1",
            "email": "member@example.com",
            "nickname": "member-1",
            "avatar": None,
            "state": TeamMemberState.ACTIVE.value,
        }
    ]
