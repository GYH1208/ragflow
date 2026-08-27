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
from contextlib import nullcontext
from types import SimpleNamespace

import pytest

from api.apps.services import dataset_api_service
from api.apps.services.dataset_api_service import create_dataset, get_dataset, list_datasets, update_dataset
from api.db import TenantPermission
from api.db.services.connector_service import Connector2KbService
from api.db.services.document_service import DocumentService
from api.db.services.knowledgebase_service import KnowledgebaseService
from api.db.services.team_service import TeamAuthorizationService, TeamMemberService, TeamService
from api.db.services.user_service import TenantService, UserService
from common.constants import StatusEnum


def _dataset(
    dataset_id="kb-1",
    owner_id="owner-1",
    *,
    permission=TenantPermission.ME.value,
    team_id=None,
):
    dataset = SimpleNamespace(
        id=dataset_id,
        tenant_id=owner_id,
        created_by=owner_id,
        name=dataset_id,
        embd_id="embedding-1",
        parser_config={},
        parser_id="naive",
        pipeline_id=None,
        pagerank=0,
        permission=permission,
        team_id=team_id,
        status=StatusEnum.VALID.value,
    )
    dataset.to_dict = lambda: {
        "id": dataset.id,
        "tenant_id": dataset.tenant_id,
        "created_by": dataset.created_by,
        "name": dataset.name,
        "embd_id": dataset.embd_id,
        "permission": dataset.permission,
        "team_id": dataset.team_id,
        "status": dataset.status,
    }
    return dataset


def _stub_team_queries(monkeypatch, names=None):
    names = names or {}
    calls = []

    def query_teams(**kwargs):
        calls.append(list(kwargs.get("id", [])))
        return [
            SimpleNamespace(id=team_id, tenant_id="owner-1", name=name)
            for team_id, name in names.items()
            if team_id in kwargs.get("id", [])
        ]

    monkeypatch.setattr(TeamService, "query", query_teams)
    return calls


def _stub_create(monkeypatch, *, admin, owned_team_ids=(), team_names=None):
    saved = {}

    def create_with_name(*, name, tenant_id, parser_id=None, **kwargs):
        return True, {
            "id": "kb-created",
            "name": name,
            "tenant_id": tenant_id,
            "created_by": tenant_id,
            "embd_id": "embedding-1",
            "permission": kwargs.get("permission", TenantPermission.ME.value),
            "team_id": kwargs.get("team_id"),
            "status": StatusEnum.VALID.value,
        }

    monkeypatch.setattr(UserService, "is_admin", lambda _user_id: admin)
    monkeypatch.setattr(
        TeamService,
        "get_owned_team",
        lambda team_id, owner_id: SimpleNamespace(id=team_id, tenant_id=owner_id) if team_id in owned_team_ids else None,
    )
    monkeypatch.setattr(
        TeamService,
        "get_owned_team_for_update",
        lambda team_id, owner_id: SimpleNamespace(id=team_id, tenant_id=owner_id) if team_id in owned_team_ids else None,
        raising=False,
    )
    monkeypatch.setattr(dataset_api_service, "DB", SimpleNamespace(atomic=nullcontext), raising=False)
    monkeypatch.setattr(KnowledgebaseService, "create_with_name", create_with_name)
    monkeypatch.setattr(KnowledgebaseService, "save", lambda **payload: saved.update(payload) or True)
    monkeypatch.setattr(
        KnowledgebaseService,
        "save_in_transaction",
        lambda **payload: saved.update(payload) or True,
        raising=False,
    )
    monkeypatch.setattr(
        KnowledgebaseService,
        "get_by_id",
        lambda _dataset_id: (True, _dataset("kb-created", saved.get("tenant_id", "owner-1"), permission=saved.get("permission", "me"), team_id=saved.get("team_id"))),
    )
    monkeypatch.setattr(TenantService, "get_by_id", lambda _tenant_id: (True, SimpleNamespace(embd_id="embedding-1")))
    monkeypatch.setattr(
        "api.apps.services.dataset_api_service.verify_embedding_availability",
        lambda _embedding_id, _tenant_id: (True, None),
    )
    team_calls = _stub_team_queries(monkeypatch, team_names)
    return saved, team_calls


def _stub_update(monkeypatch, dataset, *, admin, owned_team_ids=(), team_names=None):
    updates = []

    def update_by_id(_dataset_id, payload):
        updates.append(dict(payload))
        for key, value in payload.items():
            setattr(dataset, key, value)
        return 1

    monkeypatch.setattr(UserService, "is_admin", lambda _user_id: admin)
    monkeypatch.setattr(
        TeamService,
        "get_owned_team",
        lambda team_id, owner_id: SimpleNamespace(id=team_id, tenant_id=owner_id) if team_id in owned_team_ids else None,
    )
    monkeypatch.setattr(
        TeamService,
        "get_owned_team_for_update",
        lambda team_id, owner_id: SimpleNamespace(id=team_id, tenant_id=owner_id) if team_id in owned_team_ids else None,
        raising=False,
    )
    monkeypatch.setattr(dataset_api_service, "DB", SimpleNamespace(atomic=nullcontext), raising=False)
    monkeypatch.setattr(KnowledgebaseService, "get_or_none", lambda **kwargs: dataset if kwargs.get("tenant_id") == dataset.tenant_id else None)
    monkeypatch.setattr(KnowledgebaseService, "update_by_id", update_by_id)
    monkeypatch.setattr(KnowledgebaseService, "update_by_id_in_transaction", update_by_id)
    monkeypatch.setattr(
        KnowledgebaseService,
        "get_owned_for_update",
        lambda dataset_id, owner_id: dataset if (dataset_id, owner_id) == (dataset.id, dataset.tenant_id) else None,
        raising=False,
    )
    monkeypatch.setattr(KnowledgebaseService, "get_by_id", lambda _dataset_id: (True, dataset))
    monkeypatch.setattr(Connector2KbService, "link_connectors", lambda *_args: [])
    team_calls = _stub_team_queries(monkeypatch, team_names)
    return updates, team_calls


async def test_ordinary_user_can_create_private_dataset_without_assignment_governance(monkeypatch):
    saved, _ = _stub_create(monkeypatch, admin=False)

    ok, result = await create_dataset("owner-1", {"name": "Private", "ext": {}})

    assert ok is True
    assert saved["permission"] == TenantPermission.ME.value
    assert saved["team_id"] is None
    assert result["team_id"] is None
    assert result["team_name"] is None


async def test_admin_can_create_dataset_for_owned_team(monkeypatch):
    saved, team_calls = _stub_create(
        monkeypatch,
        admin=True,
        owned_team_ids={"team-1"},
        team_names={"team-1": "Finance"},
    )

    ok, result = await create_dataset(
        "owner-1",
        {"name": "Shared", "permission": TenantPermission.TEAM.value, "team_id": "team-1", "ext": {}},
    )

    assert ok is True
    assert saved["permission"] == TenantPermission.TEAM.value
    assert saved["team_id"] == "team-1"
    assert result["team_id"] == "team-1"
    assert result["team_name"] == "Finance"
    assert team_calls == [["team-1"]]


async def test_create_revalidates_team_inside_the_persistence_transaction(monkeypatch):
    saved, _ = _stub_create(monkeypatch, admin=True, owned_team_ids={"team-1"})
    monkeypatch.setattr(TeamService, "get_owned_team_for_update", lambda *_args: None, raising=False)

    result = await create_dataset(
        "owner-1",
        {"name": "Shared", "permission": TenantPermission.TEAM.value, "team_id": "team-1", "ext": {}},
    )

    assert result == (False, "The team and dataset must have the same owner.")
    assert saved == {}


async def test_ordinary_user_cannot_create_team_dataset(monkeypatch):
    saved, _ = _stub_create(monkeypatch, admin=False, owned_team_ids={"team-1"})

    ok, result = await create_dataset(
        "owner-1",
        {"name": "Shared", "permission": TenantPermission.TEAM.value, "team_id": "team-1", "ext": {}},
    )

    assert ok is False
    assert result == "System administrator permission is required."
    assert saved == {}


async def test_create_rejects_inconsistent_permission_team_tuple(monkeypatch):
    saved, _ = _stub_create(monkeypatch, admin=True, owned_team_ids={"team-1"})

    private_ok, private_error = await create_dataset(
        "owner-1",
        {"name": "Private", "permission": TenantPermission.ME.value, "team_id": "team-1", "ext": {}},
    )
    team_ok, team_error = await create_dataset(
        "owner-1",
        {"name": "Shared", "permission": TenantPermission.TEAM.value, "team_id": None, "ext": {}},
    )

    assert (private_ok, private_error) == (False, "team_id must be empty when permission is me.")
    assert (team_ok, team_error) == (False, "team_id is required when permission is team.")
    assert saved == {}


async def test_create_never_accepts_another_owner_from_ext(monkeypatch):
    saved, _ = _stub_create(monkeypatch, admin=True)

    try:
        result = await create_dataset(
            "owner-1",
            {"name": "Private", "ext": {"tenant_id": "owner-2"}},
        )
    except TypeError as error:
        pytest.fail(f"dataset owner override must be rejected before persistence: {error}")

    assert result == (False, "Dataset owner cannot be changed.")
    assert saved == {}


async def test_ordinary_owner_can_update_without_changing_private_tuple(monkeypatch):
    dataset = _dataset()
    updates, _ = _stub_update(monkeypatch, dataset, admin=False)

    ok, result = await update_dataset(
        "owner-1",
        "kb-1",
        {"description": "updated", "permission": TenantPermission.ME.value},
    )

    assert ok is True
    assert updates == [{"description": "updated", "permission": TenantPermission.ME.value}]
    assert result["team_id"] is None
    assert result["team_name"] is None


async def test_ordinary_owner_cannot_change_team_assignment(monkeypatch):
    dataset = _dataset(permission=TenantPermission.TEAM.value, team_id="team-old")
    updates, _ = _stub_update(monkeypatch, dataset, admin=False, owned_team_ids={"team-new"})

    ok, result = await update_dataset("owner-1", "kb-1", {"team_id": "team-new"})

    assert ok is False
    assert result == "System administrator permission is required."
    assert updates == []


async def test_update_merges_partial_assignment_with_current_tuple(monkeypatch):
    permission_dataset = _dataset()
    permission_updates, _ = _stub_update(monkeypatch, permission_dataset, admin=True, owned_team_ids={"team-1"})

    permission_ok, permission_error = await update_dataset(
        "owner-1",
        "kb-1",
        {"permission": TenantPermission.TEAM.value},
    )

    team_dataset = _dataset()
    team_updates, _ = _stub_update(monkeypatch, team_dataset, admin=True, owned_team_ids={"team-1"})
    team_ok, team_error = await update_dataset("owner-1", "kb-1", {"team_id": "team-1"})

    assert (permission_ok, permission_error) == (False, "team_id is required when permission is team.")
    assert (team_ok, team_error) == (False, "team_id must be empty when permission is me.")
    assert permission_updates == []
    assert team_updates == []


async def test_switching_to_private_explicitly_clears_team_id(monkeypatch):
    dataset = _dataset(permission=TenantPermission.TEAM.value, team_id="team-old")
    updates, _ = _stub_update(monkeypatch, dataset, admin=True, owned_team_ids={"team-old"})

    ok, result = await update_dataset("owner-1", "kb-1", {"permission": TenantPermission.ME.value})

    assert ok is True
    assert updates == [{"permission": TenantPermission.ME.value, "team_id": None}]
    assert result["permission"] == TenantPermission.ME.value
    assert result["team_id"] is None


async def test_update_rejects_another_owners_team(monkeypatch):
    dataset = _dataset(permission=TenantPermission.TEAM.value, team_id="team-old")
    updates, _ = _stub_update(monkeypatch, dataset, admin=True, owned_team_ids={"team-old"})

    ok, result = await update_dataset("owner-1", "kb-1", {"team_id": "team-other-owner"})

    assert ok is False
    assert result == "The team and dataset must have the same owner."
    assert updates == []


async def test_update_revalidates_team_and_dataset_inside_the_mutation_transaction(monkeypatch):
    dataset = _dataset(permission=TenantPermission.TEAM.value, team_id="team-old")
    updates, _ = _stub_update(monkeypatch, dataset, admin=True, owned_team_ids={"team-old", "team-new"})
    monkeypatch.setattr(TeamService, "get_owned_team_for_update", lambda team_id, _owner_id: None if team_id == "team-new" else SimpleNamespace(id=team_id), raising=False)

    result = await update_dataset("owner-1", "kb-1", {"team_id": "team-new"})

    assert result == (False, "The team and dataset must have the same owner.")
    assert updates == []


async def test_admin_cannot_update_another_owners_dataset(monkeypatch):
    dataset = _dataset(owner_id="owner-2", permission=TenantPermission.TEAM.value, team_id="team-2")
    updates, _ = _stub_update(monkeypatch, dataset, admin=True, owned_team_ids={"team-2"})

    ok, result = await update_dataset("admin-1", "kb-1", {"team_id": "team-2"})

    assert ok is False
    assert result == "User 'admin-1' lacks permission for dataset 'kb-1'"
    assert updates == []


async def test_update_never_changes_dataset_owner_from_ext(monkeypatch):
    dataset = _dataset(owner_id="owner-1")
    updates, _ = _stub_update(monkeypatch, dataset, admin=True)

    ok, result = await update_dataset(
        "owner-1",
        "kb-1",
        {"ext": {"tenant_id": "owner-2"}},
    )

    assert ok is False
    assert result == "Dataset owner cannot be changed."
    assert dataset.tenant_id == "owner-1"
    assert updates == []


async def test_team_member_cannot_update_dataset_core_configuration(monkeypatch):
    dataset = _dataset(owner_id="owner-1", permission=TenantPermission.TEAM.value, team_id="team-1")
    updates, _ = _stub_update(monkeypatch, dataset, admin=False)

    ok, result = await update_dataset("member-2", "kb-1", {"parser_id": "table"})

    assert ok is False
    assert result == "User 'member-2' lacks permission for dataset 'kb-1'"
    assert updates == []


async def test_team_switch_changes_member_access_and_returns_new_team_name(monkeypatch):
    dataset = _dataset(permission=TenantPermission.TEAM.value, team_id="team-old")
    updates, _ = _stub_update(
        monkeypatch,
        dataset,
        admin=True,
        owned_team_ids={"team-old", "team-new"},
        team_names={"team-new": "Research"},
    )

    ok, result = await update_dataset("owner-1", "kb-1", {"team_id": "team-new"})
    monkeypatch.setattr(
        TeamMemberService,
        "active_team_ids",
        lambda user_id: ["team-old"] if user_id == "old-member" else ["team-new"],
    )

    assert ok is True
    assert updates == [{"team_id": "team-new"}]
    assert result["team_name"] == "Research"
    assert TeamAuthorizationService.can_access_kb("old-member", dataset) is False
    assert TeamAuthorizationService.can_access_kb("new-member", dataset) is True


def test_list_datasets_uses_active_team_ids_and_batches_team_names(monkeypatch):
    captured = {}
    team_calls = _stub_team_queries(monkeypatch, {"team-1": "Finance"})
    monkeypatch.setattr(TeamMemberService, "active_team_ids", lambda _user_id: ["team-1"])
    monkeypatch.setattr(TenantService, "get_joined_tenants_by_user_id", lambda _user_id: [{"tenant_id": "legacy-owner"}])
    monkeypatch.setattr(UserService, "get_by_ids", lambda _ids: [])

    def get_list(*args, **kwargs):
        captured["team_ids"] = args[0]
        return [
            {"id": "kb-team", "tenant_id": "owner-1", "team_id": "team-1", "permission": "team"},
            {"id": "kb-private", "tenant_id": "member-1", "team_id": None, "permission": "me"},
        ], 2

    monkeypatch.setattr(KnowledgebaseService, "get_list", get_list)

    ok, result = list_datasets("member-1", {"ext": {}})

    assert ok is True
    assert captured["team_ids"] == ["team-1"]
    assert [item["team_name"] for item in result["data"]] == ["Finance", None]
    assert team_calls == [["team-1"]]


def test_team_name_attachment_requires_matching_dataset_and_team_owner(monkeypatch):
    monkeypatch.setattr(
        TeamService,
        "query",
        lambda **_kwargs: [SimpleNamespace(id="team-shared", tenant_id="owner-2", name="Owner 2 team")],
    )
    datasets = [
        {"id": "kb-corrupt", "tenant_id": "owner-1", "team_id": "team-shared", "permission": "team"},
        {"id": "kb-valid", "tenant_id": "owner-2", "team_id": "team-shared", "permission": "team"},
    ]

    result = dataset_api_service._attach_team_names(datasets)

    assert [dataset["team_name"] for dataset in result] == [None, "Owner 2 team"]


def test_list_owner_filter_does_not_replace_active_team_visibility(monkeypatch):
    captured = {}
    _stub_team_queries(monkeypatch)
    monkeypatch.setattr(TeamMemberService, "active_team_ids", lambda _user_id: ["team-active"])
    monkeypatch.setattr(TenantService, "get_joined_tenants_by_user_id", lambda _user_id: [{"tenant_id": "legacy-owner"}])
    monkeypatch.setattr(UserService, "get_by_ids", lambda _ids: [])

    def get_list(*args, **kwargs):
        captured["team_ids"] = args[0]
        captured["owner_ids"] = kwargs.get("owner_ids")
        return [], 0

    monkeypatch.setattr(KnowledgebaseService, "get_list", get_list)

    ok, _ = list_datasets("member-1", {"ext": {"owner_ids": ["owner-1"]}})

    assert ok is True
    assert captured == {"team_ids": ["team-active"], "owner_ids": ["owner-1"]}


def test_dataset_detail_returns_team_fields(monkeypatch):
    dataset = _dataset(permission=TenantPermission.TEAM.value, team_id="team-1")
    team_calls = _stub_team_queries(monkeypatch, {"team-1": "Finance"})
    monkeypatch.setattr(KnowledgebaseService, "accessible", lambda *_args: True)
    monkeypatch.setattr(KnowledgebaseService, "get_by_id", lambda _dataset_id: (True, dataset))
    monkeypatch.setattr(DocumentService, "get_total_size_by_kb_id", lambda _dataset_id: 0)
    monkeypatch.setattr(Connector2KbService, "list_connectors", lambda _dataset_id: [])

    ok, result = get_dataset("kb-1", "member-1")

    assert ok is True
    assert result["team_id"] == "team-1"
    assert result["team_name"] == "Finance"
    assert team_calls == [["team-1"]]
