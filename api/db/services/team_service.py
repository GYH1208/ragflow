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
from api.db import TeamMemberState, TenantPermission
from api.db.db_models import DB, Knowledgebase, Team, TeamMember
from api.db.services.common_service import CommonService
from api.db.services.user_service import UserService
from common.constants import StatusEnum


class TeamService(CommonService):
    model = Team

    @staticmethod
    def normalize_name(name: str) -> str:
        return name.strip()

    @classmethod
    @DB.connection_context()
    def get_owned_team(cls, team_id: str, admin_id: str) -> Team | None:
        return (
            cls.model.select()
            .where(
                cls.model.id == team_id,
                cls.model.tenant_id == admin_id,
                cls.model.status == StatusEnum.VALID.value,
            )
            .first()
        )

    @classmethod
    @DB.connection_context()
    def list_owned(cls, admin_id: str) -> list[dict]:
        return list(
            cls.model.select()
            .where(
                cls.model.tenant_id == admin_id,
                cls.model.status == StatusEnum.VALID.value,
            )
            .order_by(cls.model.create_time.desc())
            .dicts()
        )

    @classmethod
    @DB.connection_context()
    def deactivate(cls, team_id: str) -> int:
        return (
            cls.model.update({"status": StatusEnum.INVALID.value})
            .where(
                cls.model.id == team_id,
                cls.model.status == StatusEnum.VALID.value,
            )
            .execute()
        )


class TeamMemberService(CommonService):
    model = TeamMember

    @classmethod
    @DB.connection_context()
    def active_team_ids(cls, user_id: str) -> list[str]:
        teams = (
            cls.model.select(cls.model.team_id)
            .join(Team, on=(cls.model.team_id == Team.id))
            .where(
                cls.model.user_id == user_id,
                cls.model.state == TeamMemberState.ACTIVE.value,
                cls.model.status == StatusEnum.VALID.value,
                Team.status == StatusEnum.VALID.value,
            )
            .distinct()
        )
        return [team["team_id"] for team in teams.dicts()]

    @classmethod
    @DB.connection_context()
    def visible_owner_ids(cls, user_id: str) -> list[str]:
        owners = (
            cls.model.select(Team.tenant_id)
            .join(Team, on=(cls.model.team_id == Team.id))
            .where(
                cls.model.user_id == user_id,
                cls.model.state == TeamMemberState.ACTIVE.value,
                cls.model.status == StatusEnum.VALID.value,
                Team.status == StatusEnum.VALID.value,
            )
            .distinct()
            .order_by(Team.tenant_id)
        )
        return [owner["tenant_id"] for owner in owners.dicts()]

    @classmethod
    @DB.connection_context()
    def deactivate_by_team(cls, team_id: str) -> int:
        return (
            cls.model.update({"status": StatusEnum.INVALID.value})
            .where(
                cls.model.team_id == team_id,
                cls.model.status == StatusEnum.VALID.value,
            )
            .execute()
        )


class TeamAuthorizationService:
    @classmethod
    def can_manage_team(cls, user_id: str, team_id: str) -> bool:
        return UserService.is_admin(user_id) and TeamService.get_owned_team(team_id, user_id) is not None

    @classmethod
    def can_access_kb(cls, user_id: str, kb: Knowledgebase) -> bool:
        if kb.status != StatusEnum.VALID.value:
            return False
        if kb.tenant_id == user_id:
            return True
        if kb.permission != TenantPermission.TEAM.value or not kb.team_id:
            return False
        return kb.team_id in set(TeamMemberService.active_team_ids(user_id))

    @classmethod
    def validate_assignment(
        cls,
        user_id: str,
        kb: Knowledgebase,
        permission: str,
        team_id: str | None,
    ) -> tuple[bool, str | None]:
        if not UserService.is_admin(user_id):
            return False, "System administrator permission is required."
        if kb.tenant_id != user_id:
            return False, "No authorization to assign this dataset."
        if permission == TenantPermission.ME.value:
            if team_id is not None:
                return False, "team_id must be empty when permission is me."
            return True, None
        if permission == TenantPermission.TEAM.value:
            if not team_id:
                return False, "team_id is required when permission is team."
            if TeamService.get_owned_team(team_id, kb.tenant_id) is None:
                return False, "The team and dataset must have the same owner."
            return True, None
        return False, "Unsupported permission."
