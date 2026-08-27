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
from peewee import IntegrityError

from api.db import TeamMemberState, TenantPermission
from api.db.db_models import DB, Knowledgebase, Team, TeamMember, User
from api.db.services.knowledgebase_service import KnowledgebaseService
from api.db.services.team_service import TeamAuthorizationService, TeamMemberService, TeamService
from api.db.services.user_service import UserService
from common.constants import StatusEnum
from common.misc_utils import get_uuid

NO_AUTHORIZATION = "No authorization."


def _team_name_exists(admin_id: str, name: str, *, exclude_id: str | None = None) -> bool:
    teams = TeamService.query(tenant_id=admin_id, name=name)
    return any(team.id != exclude_id for team in teams)


def _team_counts(team_id: str) -> tuple[int, int]:
    member_count = (
        TeamMember.select()
        .where(
            TeamMember.team_id == team_id,
            TeamMember.state == TeamMemberState.ACTIVE.value,
            TeamMember.status == StatusEnum.VALID.value,
        )
        .count()
    )
    dataset_count = (
        Knowledgebase.select()
        .where(
            Knowledgebase.team_id == team_id,
            Knowledgebase.status == StatusEnum.VALID.value,
        )
        .count()
    )
    return member_count, dataset_count


def _serialize_team(team: Team, membership_state: str) -> dict:
    member_count, dataset_count = _team_counts(team.id)
    return {
        **team.to_dict(),
        "membership_state": membership_state,
        "member_count": member_count,
        "dataset_count": dataset_count,
        "can_manage": membership_state == "owner",
    }


def create_team(admin_id: str, name: str):
    if not UserService.is_admin(admin_id):
        return False, NO_AUTHORIZATION
    name = TeamService.normalize_name(name)
    if _team_name_exists(admin_id, name):
        return False, "Team name already exists."
    team_id = get_uuid()
    try:
        TeamService.insert(
            id=team_id,
            tenant_id=admin_id,
            name=name,
            created_by=admin_id,
            status=StatusEnum.VALID.value,
        )
    except IntegrityError:
        return False, "Team name already exists."
    team = TeamService.get_owned_team(team_id, admin_id)
    return True, team.to_dict()


def rename_team(admin_id: str, team_id: str, name: str):
    if not TeamAuthorizationService.can_manage_team(admin_id, team_id):
        return False, NO_AUTHORIZATION
    name = TeamService.normalize_name(name)
    if _team_name_exists(admin_id, name, exclude_id=team_id):
        return False, "Team name already exists."
    try:
        TeamService.update_by_id(team_id, {"name": name})
    except IntegrityError:
        return False, "Team name already exists."
    team = TeamService.get_owned_team(team_id, admin_id)
    return True, team.to_dict()


def delete_team(admin_id: str, team_id: str):
    if not TeamAuthorizationService.can_manage_team(admin_id, team_id):
        return False, NO_AUTHORIZATION
    with DB.atomic():
        unassigned_count = KnowledgebaseService.filter_update(
            [Knowledgebase.team_id == team_id, Knowledgebase.tenant_id == admin_id],
            {"permission": TenantPermission.ME.value, "team_id": None},
        )
        TeamMemberService.deactivate_by_team(team_id)
        TeamService.deactivate(team_id)
    return True, {"unassigned_dataset_count": unassigned_count}


def list_teams(user_id: str):
    visible: dict[str, tuple[Team, str]] = {}
    for team in Team.select().where(
        Team.tenant_id == user_id,
        Team.status == StatusEnum.VALID.value,
    ):
        visible[team.id] = (team, "owner")

    memberships = (
        TeamMember.select(TeamMember, Team)
        .join(Team, on=(TeamMember.team_id == Team.id))
        .where(
            TeamMember.user_id == user_id,
            TeamMember.status == StatusEnum.VALID.value,
            Team.status == StatusEnum.VALID.value,
            TeamMember.state.in_([TeamMemberState.ACTIVE.value, TeamMemberState.INVITED.value]),
        )
    )
    for membership in memberships:
        if membership.team_id not in visible:
            visible[membership.team_id] = (membership.team, membership.state)

    teams = [_serialize_team(team, state) for team, state in visible.values()]
    teams.sort(key=lambda item: ((item.get("create_time") or 0), item["id"]), reverse=True)
    return True, teams


def get_team(user_id: str, team_id: str):
    team = TeamService.get_or_none(id=team_id, status=StatusEnum.VALID.value)
    if team is None:
        return False, NO_AUTHORIZATION
    if TeamAuthorizationService.can_manage_team(user_id, team_id):
        return True, _serialize_team(team, "owner")
    membership = TeamMemberService.get_or_none(
        team_id=team_id,
        user_id=user_id,
        status=StatusEnum.VALID.value,
    )
    if membership is None or membership.state not in {TeamMemberState.ACTIVE.value, TeamMemberState.INVITED.value}:
        return False, NO_AUTHORIZATION
    return True, _serialize_team(team, membership.state)


def list_members(admin_id: str, team_id: str):
    if not TeamAuthorizationService.can_manage_team(admin_id, team_id):
        return False, NO_AUTHORIZATION
    relationships = (
        TeamMember.select(TeamMember, User)
        .join(User, on=(TeamMember.user_id == User.id))
        .where(
            TeamMember.team_id == team_id,
            TeamMember.status == StatusEnum.VALID.value,
            User.status == StatusEnum.VALID.value,
        )
        .order_by(User.email)
    )
    return True, [
        {
            "id": relationship.user.id,
            "email": relationship.user.email,
            "nickname": relationship.user.nickname,
            "avatar": relationship.user.avatar,
            "state": relationship.state,
        }
        for relationship in relationships
    ]


def invite_member(admin_id: str, team_id: str, email: str):
    if not TeamAuthorizationService.can_manage_team(admin_id, team_id):
        return False, NO_AUTHORIZATION
    users = list(UserService.query(email=email, status=StatusEnum.VALID.value))
    if not users:
        return False, "User not found."
    user = users[0]
    relationships = list(TeamMemberService.query(team_id=team_id, user_id=user.id))
    relationship = relationships[0] if relationships else None
    if relationship is not None and relationship.status == StatusEnum.VALID.value:
        if relationship.state == TeamMemberState.ACTIVE.value:
            return False, "User is already in the team."
        return False, "User has already been invited."

    with DB.atomic():
        if relationship is None:
            TeamMemberService.insert(
                team_id=team_id,
                user_id=user.id,
                state=TeamMemberState.INVITED.value,
                invited_by=admin_id,
                status=StatusEnum.VALID.value,
            )
        else:
            TeamMemberService.update_by_id(
                relationship.id,
                {
                    "state": TeamMemberState.INVITED.value,
                    "invited_by": admin_id,
                    "status": StatusEnum.VALID.value,
                },
            )
    return True, {
        "id": user.id,
        "email": user.email,
        "nickname": user.nickname,
        "avatar": user.avatar,
        "state": TeamMemberState.INVITED.value,
    }


def remove_or_leave_member(actor_id: str, team_id: str, user_id: str):
    if actor_id == user_id:
        relationship = TeamMemberService.get_or_none(
            team_id=team_id,
            user_id=user_id,
            state=TeamMemberState.ACTIVE.value,
            status=StatusEnum.VALID.value,
        )
        if relationship is None:
            return False, NO_AUTHORIZATION
    else:
        if not TeamAuthorizationService.can_manage_team(actor_id, team_id):
            return False, NO_AUTHORIZATION
        relationship = TeamMemberService.get_or_none(
            team_id=team_id,
            user_id=user_id,
            status=StatusEnum.VALID.value,
        )
        if relationship is None:
            return False, "Team member not found."
    TeamMemberService.update_by_id(relationship.id, {"status": StatusEnum.INVALID.value})
    return True, True


def update_invitation(user_id: str, team_id: str, action: str):
    relationship = TeamMemberService.get_or_none(
        team_id=team_id,
        user_id=user_id,
        state=TeamMemberState.INVITED.value,
        status=StatusEnum.VALID.value,
    )
    if relationship is None:
        return False, "Invitation not found."
    values = {"state": TeamMemberState.ACTIVE.value} if action == "accept" else {"status": StatusEnum.INVALID.value}
    TeamMemberService.update_by_id(relationship.id, values)
    return True, True
