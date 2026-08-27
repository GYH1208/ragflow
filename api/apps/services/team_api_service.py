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
from api.db.services.team_service import TeamAuthorizationService, TeamMemberService, TeamService, select_for_update
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


def _serialize_team(team: Team, membership_state: str, *, can_manage: bool) -> dict:
    member_count, dataset_count = _team_counts(team.id)
    return {
        **team.to_dict(),
        "membership_state": membership_state,
        "member_count": member_count,
        "dataset_count": dataset_count,
        "can_manage": can_manage,
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
        team = select_for_update(
            Team.select().where(
                Team.id == team_id,
                Team.tenant_id == admin_id,
                Team.status == StatusEnum.VALID.value,
            )
        ).get_or_none()
        if team is None:
            return False, NO_AUTHORIZATION

        datasets = list(
            select_for_update(
                Knowledgebase.select().where(
                    Knowledgebase.team_id == team_id,
                    Knowledgebase.tenant_id == admin_id,
                )
            ).order_by(Knowledgebase.id)
        )
        memberships = list(
            select_for_update(
                TeamMember.select().where(
                    TeamMember.team_id == team_id,
                    TeamMember.status == StatusEnum.VALID.value,
                )
            ).order_by(TeamMember.id)
        )

        unassigned_count = (
            Knowledgebase.update({"permission": TenantPermission.ME.value, "team_id": None})
            .where(
                Knowledgebase.team_id == team_id,
                Knowledgebase.tenant_id == admin_id,
            )
            .execute()
        )
        if unassigned_count != len(datasets):
            raise RuntimeError("Dataset assignments changed concurrently.")

        deactivated_member_count = (
            TeamMember.update({"status": StatusEnum.INVALID.value})
            .where(
                TeamMember.team_id == team_id,
                TeamMember.status == StatusEnum.VALID.value,
            )
            .execute()
        )
        if deactivated_member_count != len(memberships):
            raise RuntimeError("Team memberships changed concurrently.")

        deactivated_team_count = (
            Team.update({"status": StatusEnum.INVALID.value})
            .where(
                Team.id == team_id,
                Team.tenant_id == admin_id,
                Team.status == StatusEnum.VALID.value,
            )
            .execute()
        )
        if deactivated_team_count != 1:
            raise RuntimeError("Team changed concurrently.")
    return True, {"unassigned_dataset_count": unassigned_count}


def list_teams(user_id: str):
    visible: dict[str, tuple[Team, str]] = {}
    is_admin = UserService.is_admin(user_id)
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

    teams = [_serialize_team(team, state, can_manage=is_admin and state == "owner") for team, state in visible.values()]
    teams.sort(key=lambda item: ((item.get("create_time") or 0), item["id"]), reverse=True)
    return True, teams


def get_team(user_id: str, team_id: str):
    team = TeamService.get_or_none(id=team_id, status=StatusEnum.VALID.value)
    if team is None:
        return False, NO_AUTHORIZATION
    can_manage = TeamAuthorizationService.can_manage_team(user_id, team_id)
    if team.tenant_id == user_id:
        return True, _serialize_team(team, "owner", can_manage=can_manage)
    membership = TeamMemberService.get_or_none(
        team_id=team_id,
        user_id=user_id,
        status=StatusEnum.VALID.value,
    )
    if membership is None or membership.state not in {TeamMemberState.ACTIVE.value, TeamMemberState.INVITED.value}:
        return False, NO_AUTHORIZATION
    return True, _serialize_team(team, membership.state, can_manage=False)


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

    with DB.atomic():
        team = select_for_update(
            Team.select().where(
                Team.id == team_id,
                Team.tenant_id == admin_id,
                Team.status == StatusEnum.VALID.value,
            )
        ).get_or_none()
        if team is None:
            return False, NO_AUTHORIZATION

        relationship = select_for_update(
            TeamMember.select().where(
                TeamMember.team_id == team_id,
                TeamMember.user_id == user.id,
            )
        ).get_or_none()
        duplicate_error = _duplicate_membership_error(relationship)
        if duplicate_error is not None:
            return False, duplicate_error

        if relationship is not None:
            restored = (
                TeamMember.update(
                    {
                        "state": TeamMemberState.INVITED.value,
                        "invited_by": admin_id,
                        "status": StatusEnum.VALID.value,
                    }
                )
                .where(
                    TeamMember.id == relationship.id,
                    TeamMember.status == StatusEnum.INVALID.value,
                )
                .execute()
            )
            if restored == 0:
                winner = TeamMember.get_or_none(
                    TeamMember.team_id == team_id,
                    TeamMember.user_id == user.id,
                )
                return False, _duplicate_membership_error(winner) or "Team invitation changed concurrently."
        else:
            try:
                with DB.atomic():
                    inserted = TeamMember(
                        id=get_uuid(),
                        team_id=team_id,
                        user_id=user.id,
                        state=TeamMemberState.INVITED.value,
                        invited_by=admin_id,
                        status=StatusEnum.VALID.value,
                    ).save(force_insert=True)
                    if inserted != 1:
                        raise RuntimeError("Team invitation changed concurrently.")
            except IntegrityError:
                winner = TeamMember.get_or_none(
                    TeamMember.team_id == team_id,
                    TeamMember.user_id == user.id,
                )
                return False, _duplicate_membership_error(winner) or "Team invitation changed concurrently."
    return True, {
        "id": user.id,
        "email": user.email,
        "nickname": user.nickname,
        "avatar": user.avatar,
        "state": TeamMemberState.INVITED.value,
        "_team_name": team.name,
    }


def _duplicate_membership_error(relationship: TeamMember | None) -> str | None:
    if relationship is None or relationship.status != StatusEnum.VALID.value:
        return None
    if relationship.state == TeamMemberState.ACTIVE.value:
        return "User is already in the team."
    if relationship.state == TeamMemberState.INVITED.value:
        return "User has already been invited."
    return "User already has a team relationship."


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
        return False, NO_AUTHORIZATION
    values = {"state": TeamMemberState.ACTIVE.value} if action == "accept" else {"status": StatusEnum.INVALID.value}
    TeamMemberService.update_by_id(relationship.id, values)
    return True, True
