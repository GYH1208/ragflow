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
from api.db import TeamMemberState, TenantPermission
from api.db.db_models import Knowledgebase, Team, TeamMember, User
from api.db.services.knowledgebase_service import KnowledgebaseService
from api.db.services.team_service import TeamMemberService
from common.constants import StatusEnum


class MultiTeamHarness:
    def create_team(self, team_id, name, *, owner):
        return Team.create(
            id=team_id,
            tenant_id=owner,
            name=name,
            created_by=owner,
            status=StatusEnum.VALID.value,
        )

    def add_active_member(self, team_id, user_id):
        return TeamMember.create(
            id=f"{team_id}-{user_id}",
            team_id=team_id,
            user_id=user_id,
            state=TeamMemberState.ACTIVE.value,
            invited_by="admin-1",
            status=StatusEnum.VALID.value,
        )

    def assign_dataset(self, dataset_id, team_id, *, owner):
        return Knowledgebase.create(
            id=dataset_id,
            tenant_id=owner,
            name=dataset_id,
            embd_id="embedding-1",
            created_by=owner,
            permission=TenantPermission.TEAM.value,
            team_id=team_id,
            doc_num=2,
            status=StatusEnum.VALID.value,
        )

    def visible_dataset_ids(self, user_id):
        datasets, _ = KnowledgebaseService.get_list(
            TeamMemberService.active_team_ids(user_id),
            user_id,
            1,
            20,
            "create_time",
            False,
            None,
            None,
            None,
        )
        return {dataset["id"] for dataset in datasets}

    def delete_team(self, team_id, *, owner):
        assert team_api_service.delete_team(owner, team_id) == (True, {"unassigned_dataset_count": 1})

    def dataset(self, dataset_id):
        return Knowledgebase.get_by_id(dataset_id)

    def document_count(self, dataset_id):
        return self.dataset(dataset_id).doc_num

    def index_owner(self, dataset_id):
        return self.dataset(dataset_id).tenant_id


@pytest.fixture()
def multi_team_harness(monkeypatch):
    database = SqliteDatabase(":memory:")
    models = [User, Team, TeamMember, Knowledgebase]
    with database.bind_ctx(models), database.connection_context():
        database.create_tables(models)
        monkeypatch.setattr(team_api_service, "DB", database)
        for user_id, admin in (("admin-1", True), ("alice", False), ("bob", False)):
            User.create(
                id=user_id,
                email=f"{user_id}@example.com",
                nickname=user_id,
                is_superuser=admin,
                status=StatusEnum.VALID.value,
            )
        yield MultiTeamHarness()


def test_hr_and_sales_members_only_see_their_assigned_datasets(multi_team_harness):
    multi_team_harness.create_team("team-hr", "HR 团队", owner="admin-1")
    multi_team_harness.create_team("team-sales", "销售团队", owner="admin-1")
    multi_team_harness.add_active_member("team-hr", "alice")
    multi_team_harness.add_active_member("team-sales", "bob")
    multi_team_harness.assign_dataset("kb-hr", "team-hr", owner="admin-1")
    multi_team_harness.assign_dataset("kb-sales", "team-sales", owner="admin-1")

    assert multi_team_harness.visible_dataset_ids("alice") == {"kb-hr"}
    assert multi_team_harness.visible_dataset_ids("bob") == {"kb-sales"}
    assert multi_team_harness.visible_dataset_ids("admin-1") == {"kb-hr", "kb-sales"}

    original_document_count = multi_team_harness.document_count("kb-hr")
    original_index_owner = multi_team_harness.index_owner("kb-hr")
    multi_team_harness.delete_team("team-hr", owner="admin-1")

    dataset = multi_team_harness.dataset("kb-hr")
    assert dataset.permission == "me"
    assert dataset.team_id is None
    assert multi_team_harness.document_count("kb-hr") == original_document_count
    assert multi_team_harness.index_owner("kb-hr") == original_index_owner == "admin-1"
