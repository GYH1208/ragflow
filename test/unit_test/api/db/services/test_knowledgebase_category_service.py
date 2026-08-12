from contextlib import nullcontext
from types import SimpleNamespace

from api.db.services.knowledgebase_category_service import KnowledgebaseCategoryService


def test_visible_tenant_ids_include_owner_and_joined_tenants(monkeypatch):
    monkeypatch.setattr(
        "api.db.services.knowledgebase_category_service.TenantService.get_joined_tenants_by_user_id",
        lambda _user_id: [{"tenant_id": "team-a"}, {"tenant_id": "team-b"}, {"tenant_id": "team-a"}],
    )

    assert KnowledgebaseCategoryService.visible_tenant_ids("me") == ["me", "team-a", "team-b"]


def test_resolve_owner_ids_never_expands_visible_scope(monkeypatch):
    monkeypatch.setattr(
        KnowledgebaseCategoryService,
        "visible_tenant_ids",
        classmethod(lambda cls, _user_id: ["me", "team-a"]),
    )

    assert KnowledgebaseCategoryService.resolve_owner_ids("me", ["team-a", "hidden"]) == ["team-a"]
    assert KnowledgebaseCategoryService.resolve_owner_ids("me", []) == ["me", "team-a"]


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
