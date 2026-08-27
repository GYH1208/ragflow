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
import asyncio

import pytest
from peewee import SqliteDatabase

from api.apps.services import team_api_service
from api.apps.services.dataset_api_service import update_dataset
from api.db import TeamMemberState, TenantPermission
from api.db.db_models import Connector2Kb, Document, Knowledgebase, Team, TeamMember, User
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

    def create_private_dataset(self, dataset_id, *, owner):
        dataset = Knowledgebase.create(
            id=dataset_id,
            tenant_id=owner,
            name=dataset_id,
            embd_id="embedding-1",
            created_by=owner,
            permission=TenantPermission.ME.value,
            team_id=None,
            doc_num=1,
            status=StatusEnum.VALID.value,
        )
        Document.create(
            id=f"doc-{dataset_id}",
            kb_id=dataset_id,
            parser_id="naive",
            type="txt",
            created_by=owner,
            name=f"{dataset_id}.txt",
            location=f"/{dataset_id}.txt",
            size=12,
            suffix="txt",
            status=StatusEnum.VALID.value,
        )
        return dataset

    def assign_dataset(self, dataset_id, team_id, *, owner):
        return asyncio.run(
            update_dataset(
                owner,
                dataset_id,
                {"permission": TenantPermission.TEAM.value, "team_id": team_id},
            )
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
        return Document.select().where(Document.kb_id == dataset_id).count()

    def documents(self, dataset_id):
        return list(Document.select().where(Document.kb_id == dataset_id).order_by(Document.id))

    def index_owner(self, dataset_id):
        return self.dataset(dataset_id).tenant_id


@pytest.fixture()
def multi_team_harness(monkeypatch):
    database = SqliteDatabase(":memory:")
    models = [User, Team, TeamMember, Knowledgebase, Document, Connector2Kb]
    with database.bind_ctx(models), database.connection_context():
        database.create_tables(models)
        monkeypatch.setattr(team_api_service, "DB", database)
        for user_id, admin in (("admin-1", True), ("admin-2", True), ("alice", False), ("bob", False)):
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
    multi_team_harness.create_team("team-other-owner", "外部团队", owner="admin-2")
    multi_team_harness.add_active_member("team-hr", "alice")
    multi_team_harness.add_active_member("team-sales", "bob")
    multi_team_harness.create_private_dataset("kb-hr", owner="admin-1")
    multi_team_harness.create_private_dataset("kb-sales", owner="admin-1")

    assert multi_team_harness.dataset("kb-hr").permission == TenantPermission.ME.value
    assert multi_team_harness.assign_dataset("kb-hr", "team-other-owner", owner="admin-1") == (
        False,
        "The team and dataset must have the same owner.",
    )

    hr_ok, hr_assignment = multi_team_harness.assign_dataset("kb-hr", "team-hr", owner="admin-1")
    sales_ok, sales_assignment = multi_team_harness.assign_dataset("kb-sales", "team-sales", owner="admin-1")

    assert hr_ok is True
    assert hr_assignment["permission"] == TenantPermission.TEAM.value
    assert hr_assignment["team_id"] == "team-hr"
    assert sales_ok is True
    assert sales_assignment["team_id"] == "team-sales"

    assert multi_team_harness.visible_dataset_ids("alice") == {"kb-hr"}
    assert multi_team_harness.visible_dataset_ids("bob") == {"kb-sales"}
    assert multi_team_harness.visible_dataset_ids("admin-1") == {"kb-hr", "kb-sales"}

    reassign_ok, reassignment = multi_team_harness.assign_dataset("kb-hr", "team-sales", owner="admin-1")
    assert reassign_ok is True
    assert reassignment["team_id"] == "team-sales"
    assert multi_team_harness.visible_dataset_ids("alice") == set()
    assert multi_team_harness.visible_dataset_ids("bob") == {"kb-hr", "kb-sales"}

    restore_ok, restored = multi_team_harness.assign_dataset("kb-hr", "team-hr", owner="admin-1")
    assert restore_ok is True
    assert restored["team_id"] == "team-hr"

    original_document_count = multi_team_harness.document_count("kb-hr")
    original_documents = [document.to_dict() for document in multi_team_harness.documents("kb-hr")]
    original_declared_document_count = multi_team_harness.dataset("kb-hr").doc_num
    original_index_owner = multi_team_harness.index_owner("kb-hr")
    multi_team_harness.delete_team("team-hr", owner="admin-1")

    dataset = multi_team_harness.dataset("kb-hr")
    assert dataset.permission == "me"
    assert dataset.team_id is None
    assert multi_team_harness.document_count("kb-hr") == original_document_count == 1
    assert dataset.doc_num == original_declared_document_count == 1
    assert [document.to_dict() for document in multi_team_harness.documents("kb-hr")] == original_documents
    assert original_documents[0]["kb_id"] == "kb-hr"
    assert original_documents[0]["created_by"] == "admin-1"
    assert multi_team_harness.index_owner("kb-hr") == original_index_owner == "admin-1"


def test_delete_team_rolls_back_dataset_unassignment_after_late_failure(multi_team_harness, monkeypatch):
    multi_team_harness.create_team("team-hr", "HR 团队", owner="admin-1")
    multi_team_harness.add_active_member("team-hr", "alice")
    multi_team_harness.create_private_dataset("kb-hr", owner="admin-1")
    ok, _ = multi_team_harness.assign_dataset("kb-hr", "team-hr", owner="admin-1")
    assert ok is True

    monkeypatch.setattr(Team, "update", lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("late failure")))

    with pytest.raises(RuntimeError, match="late failure"):
        team_api_service.delete_team("admin-1", "team-hr")

    dataset = multi_team_harness.dataset("kb-hr")
    assert dataset.permission == TenantPermission.TEAM.value
    assert dataset.team_id == "team-hr"
    assert dataset.doc_num == 1
    assert Team.get_by_id("team-hr").status == StatusEnum.VALID.value
    assert TeamMember.get_by_id("team-hr-alice").status == StatusEnum.VALID.value
    documents = multi_team_harness.documents("kb-hr")
    assert [(document.id, document.kb_id, document.created_by) for document in documents] == [
        ("doc-kb-hr", "kb-hr", "admin-1")
    ]
