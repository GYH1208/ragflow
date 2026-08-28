from contextlib import nullcontext
from types import SimpleNamespace

import pytest
from peewee import SqliteDatabase

from api.db import TenantPermission
from api.db.db_models import Knowledgebase, KnowledgebaseCategory, Team
from api.db.services.knowledgebase_category_service import KnowledgebaseCategoryService
from api.db.services.team_service import TeamMemberService
from common.constants import StatusEnum


@pytest.fixture()
def category_database():
    database = SqliteDatabase(":memory:")
    models = [Knowledgebase, KnowledgebaseCategory, Team]
    with database.bind_ctx(models), database.connection_context():
        database.create_tables(models)
        yield


def _category(category_id: str, owner_id: str):
    return KnowledgebaseCategory.create(
        id=category_id,
        tenant_id=owner_id,
        name=category_id,
        created_by=owner_id,
        status=StatusEnum.VALID.value,
    )


def _kb(kb_id: str, owner_id: str, *, category_id: str, permission: str, team_id: str | None):
    return Knowledgebase.create(
        id=kb_id,
        tenant_id=owner_id,
        name=kb_id,
        embd_id="embedding-1",
        created_by=owner_id,
        category_id=category_id,
        permission=permission,
        team_id=team_id,
        status=StatusEnum.VALID.value,
    )


def _team(team_id: str, owner_id: str):
    return Team.create(
        id=team_id,
        tenant_id=owner_id,
        name=team_id,
        created_by=owner_id,
        status=StatusEnum.VALID.value,
    )


def test_visible_tenant_ids_include_owner_and_active_team_owners_only(monkeypatch):
    monkeypatch.setattr(TeamMemberService, "visible_owner_ids", lambda _user_id: ["owner-a", "owner-b", "owner-a"])
    monkeypatch.setattr(
        "api.db.services.user_service.TenantService.get_joined_tenants_by_user_id",
        lambda _user_id: [{"tenant_id": "legacy-owner"}],
    )

    assert KnowledgebaseCategoryService.visible_tenant_ids("me") == ["me", "owner-a", "owner-b"]


def test_resolve_owner_ids_never_expands_visible_scope(monkeypatch):
    monkeypatch.setattr(
        KnowledgebaseCategoryService,
        "visible_tenant_ids",
        classmethod(lambda cls, _user_id: ["me", "team-a"]),
    )

    assert KnowledgebaseCategoryService.resolve_owner_ids("me", ["team-a", "hidden"]) == ["team-a"]
    assert KnowledgebaseCategoryService.resolve_owner_ids("me", []) == ["me", "team-a"]


def test_category_counts_use_active_team_ids_instead_of_legacy_tenants(category_database, monkeypatch):
    _team("team-active", "owner-1")
    _team("team-other", "owner-1")
    _team("team-hidden", "owner-2")
    _team("team-cross-owner", "owner-2")
    _category("cat-mine", "member-1")
    _category("cat-owner", "owner-1")
    _category("cat-hidden", "owner-2")
    _kb(
        "kb-mine",
        "member-1",
        category_id="cat-mine",
        permission=TenantPermission.ME.value,
        team_id=None,
    )
    _kb(
        "kb-active",
        "owner-1",
        category_id="cat-owner",
        permission=TenantPermission.TEAM.value,
        team_id="team-active",
    )
    _kb(
        "kb-owner-private",
        "owner-1",
        category_id="cat-owner",
        permission=TenantPermission.ME.value,
        team_id=None,
    )
    _kb(
        "kb-other-team",
        "owner-1",
        category_id="cat-owner",
        permission=TenantPermission.TEAM.value,
        team_id="team-other",
    )
    _kb(
        "kb-legacy-without-team",
        "owner-1",
        category_id="cat-owner",
        permission=TenantPermission.TEAM.value,
        team_id=None,
    )
    _kb(
        "kb-hidden-owner",
        "owner-2",
        category_id="cat-hidden",
        permission=TenantPermission.TEAM.value,
        team_id="team-hidden",
    )
    _kb(
        "kb-cross-owner",
        "owner-1",
        category_id="cat-owner",
        permission=TenantPermission.TEAM.value,
        team_id="team-cross-owner",
    )
    monkeypatch.setattr(TeamMemberService, "visible_owner_ids", lambda _user_id: ["owner-1"])
    monkeypatch.setattr(
        TeamMemberService,
        "active_team_ids",
        lambda _user_id: ["team-active", "team-cross-owner"],
    )
    monkeypatch.setattr(
        "api.db.services.user_service.TenantService.get_joined_tenants_by_user_id",
        lambda _user_id: [{"tenant_id": "owner-1"}, {"tenant_id": "owner-2"}],
    )

    result = KnowledgebaseCategoryService.list_with_counts("member-1")

    assert {category["id"]: category["count"] for category in result["categories"]} == {
        "cat-mine": 1,
        "cat-owner": 1,
    }
    assert result["total_count"] == 2
    assert result["uncategorized_count"] == 0


def test_delete_and_unassign_updates_before_delete(monkeypatch):
    calls = []
    category = SimpleNamespace(id="cat-1", tenant_id="team-a")
    monkeypatch.setattr(
        "api.db.services.knowledgebase_category_service.DB.atomic",
        lambda: nullcontext(),
    )
    monkeypatch.setattr(
        KnowledgebaseCategoryService,
        "get_or_none",
        classmethod(lambda cls, **_kwargs: category),
    )
    monkeypatch.setattr(
        "api.db.services.knowledgebase_category_service.KnowledgebaseService.filter_update",
        lambda _filters, data: calls.append(("unassign", data)) or 2,
    )
    monkeypatch.setattr(
        KnowledgebaseCategoryService,
        "delete_by_id",
        classmethod(lambda cls, category_id: calls.append(("delete", category_id)) or 1),
    )

    assert KnowledgebaseCategoryService.delete_and_unassign("cat-1", "team-a") == 2
    assert calls == [("unassign", {"category_id": None}), ("delete", "cat-1")]


def test_delete_and_unassign_rejects_other_tenant(monkeypatch):
    category = SimpleNamespace(id="cat-1", tenant_id="team-a")
    monkeypatch.setattr(
        KnowledgebaseCategoryService,
        "get_or_none",
        classmethod(lambda cls, **_kwargs: category),
    )

    assert KnowledgebaseCategoryService.delete_and_unassign("cat-1", "team-b") is None
