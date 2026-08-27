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
"""Idempotently assign legacy shared knowledge bases to a default team."""

from peewee import IntegrityError

from api.db import TeamMemberState, TenantPermission, UserTenantRole
from api.db.db_models import DB, Knowledgebase, Team, TeamMember, Tenant, UserTenant
from common.constants import StatusEnum
from common.misc_utils import get_uuid

DEFAULT_TEAM_NAME = "默认团队"


def _get_or_create_default_team(tenant_id: str, created_by: str) -> tuple[Team, bool]:
    team = Team.get_or_none(tenant_id=tenant_id, name=DEFAULT_TEAM_NAME)
    if team:
        _activate_team_if_needed(team)
        return team, False

    try:
        with DB.atomic():
            Team.insert(
                id=get_uuid(),
                tenant_id=tenant_id,
                name=DEFAULT_TEAM_NAME,
                created_by=created_by,
            ).execute()
        return Team.get(tenant_id=tenant_id, name=DEFAULT_TEAM_NAME), True
    except IntegrityError:
        # Another migrator may have inserted the unique default team first.
        team = Team.get(tenant_id=tenant_id, name=DEFAULT_TEAM_NAME)
        _activate_team_if_needed(team)
        return team, False


def _activate_team_if_needed(team: Team) -> None:
    if team.status == StatusEnum.VALID.value:
        return
    Team.update(status=StatusEnum.VALID.value).where(Team.id == team.id).execute()
    team.status = StatusEnum.VALID.value


def _default_team_creator(tenant_id: str) -> str:
    owner = UserTenant.get_or_none(
        tenant_id=tenant_id,
        role=UserTenantRole.OWNER.value,
        status=StatusEnum.VALID.value,
    )
    if owner:
        return owner.user_id

    member = (
        UserTenant.select(UserTenant.user_id)
        .where(
            UserTenant.tenant_id == tenant_id,
            UserTenant.status == StatusEnum.VALID.value,
            UserTenant.role.in_([UserTenantRole.NORMAL.value, UserTenantRole.INVITE.value]),
        )
        .first()
    )
    if member:
        return member.user_id

    knowledgebase = (
        Knowledgebase.select(Knowledgebase.created_by)
        .where(
            Knowledgebase.tenant_id == tenant_id,
            Knowledgebase.status == StatusEnum.VALID.value,
            Knowledgebase.permission == TenantPermission.TEAM.value,
            Knowledgebase.team_id.is_null(True),
        )
        .first()
    )
    return knowledgebase.created_by if knowledgebase else tenant_id


def _tenant_ids_to_backfill() -> set[str]:
    member_tenant_ids = set()
    for user_tenant in (
        UserTenant.select()
        .join(Tenant, on=(UserTenant.tenant_id == Tenant.id))
        .where(
            Tenant.status == StatusEnum.VALID.value,
            UserTenant.status == StatusEnum.VALID.value,
            UserTenant.role.in_([UserTenantRole.NORMAL.value, UserTenantRole.INVITE.value]),
        )
    ):
        team = Team.get_or_none(tenant_id=user_tenant.tenant_id, name=DEFAULT_TEAM_NAME)
        if not team or not TeamMember.get_or_none(team_id=team.id, user_id=user_tenant.user_id):
            member_tenant_ids.add(user_tenant.tenant_id)
    dataset_tenant_ids = {
        knowledgebase.tenant_id
        for knowledgebase in Knowledgebase.select(Knowledgebase.tenant_id)
        .join(Tenant, on=(Knowledgebase.tenant_id == Tenant.id))
        .where(
            Tenant.status == StatusEnum.VALID.value,
            Knowledgebase.status == StatusEnum.VALID.value,
            Knowledgebase.permission == TenantPermission.TEAM.value,
            Knowledgebase.team_id.is_null(True),
        )
    }
    return member_tenant_ids | dataset_tenant_ids


def backfill_default_teams() -> dict[str, int]:
    """Create one default team per eligible tenant and assign legacy shared KBs.

    The migration intentionally leaves legacy ``UserTenant`` records unchanged.
    Existing (including inactive) team memberships are also left intact so later
    invitation flows can reactivate them explicitly.
    """
    counts = {"teams_created": 0, "members_created": 0, "datasets_assigned": 0}

    with DB.atomic():
        for tenant_id in _tenant_ids_to_backfill():
            team, created = _get_or_create_default_team(tenant_id, _default_team_creator(tenant_id))
            counts["teams_created"] += int(created)

            legacy_members = UserTenant.select().where(
                UserTenant.tenant_id == tenant_id,
                UserTenant.status == StatusEnum.VALID.value,
                UserTenant.role.in_([UserTenantRole.NORMAL.value, UserTenantRole.INVITE.value]),
            )
            for legacy_member in legacy_members:
                if TeamMember.get_or_none(team_id=team.id, user_id=legacy_member.user_id):
                    continue
                state = TeamMemberState.ACTIVE.value if legacy_member.role == UserTenantRole.NORMAL.value else TeamMemberState.INVITED.value
                try:
                    with DB.atomic():
                        TeamMember.insert(
                            id=get_uuid(),
                            team_id=team.id,
                            user_id=legacy_member.user_id,
                            state=state,
                            invited_by=legacy_member.invited_by,
                        ).execute()
                    counts["members_created"] += 1
                except IntegrityError:
                    # A concurrent migration inserted this unique member first.
                    TeamMember.get(team_id=team.id, user_id=legacy_member.user_id)

            counts["datasets_assigned"] += (
                Knowledgebase.update(team_id=team.id)
                .where(
                    Knowledgebase.tenant_id == tenant_id,
                    Knowledgebase.status == StatusEnum.VALID.value,
                    Knowledgebase.permission == TenantPermission.TEAM.value,
                    Knowledgebase.team_id.is_null(True),
                )
                .execute()
            )

    return counts
