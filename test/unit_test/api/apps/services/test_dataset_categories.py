from contextlib import nullcontext
from types import SimpleNamespace

from api.apps.services import dataset_api_service
from api.apps.services.dataset_api_service import (
    create_dataset,
    create_dataset_category,
    delete_dataset_category,
    list_datasets,
    update_dataset,
    update_dataset_category,
    validate_category_assignment,
)
from api.db.services.connector_service import Connector2KbService
from api.db.services.knowledgebase_category_service import KnowledgebaseCategoryService
from api.db.services.knowledgebase_service import KnowledgebaseService
from api.db.services.team_service import TeamMemberService
from api.db.services.user_service import TenantService, UserService


def test_create_category_rejects_case_insensitive_duplicate(monkeypatch):
    monkeypatch.setattr(
        KnowledgebaseCategoryService,
        "name_exists",
        classmethod(lambda cls, tenant_id, name, exclude_id=None: True),
    )

    ok, result = create_dataset_category("owner-1", {"name": "财务"})

    assert ok is False
    assert result == "Dataset category name already exists"


def test_member_cannot_rename_owner_category(monkeypatch):
    category = SimpleNamespace(id="cat-1", tenant_id="owner-1", name="财务")
    monkeypatch.setattr(
        KnowledgebaseCategoryService,
        "get_or_none",
        classmethod(lambda cls, **kwargs: category),
    )

    ok, result = update_dataset_category("member-2", "cat-1", {"name": "预算"})

    assert ok is False
    assert result == "No authorization to manage this dataset category"


def test_delete_category_returns_unassigned_count(monkeypatch):
    monkeypatch.setattr(
        KnowledgebaseCategoryService,
        "delete_and_unassign",
        classmethod(lambda cls, category_id, tenant_id: 3),
    )

    ok, result = delete_dataset_category("owner-1", "cat-1")

    assert ok is True
    assert result == {"unassigned_count": 3}


def test_validate_category_assignment_rejects_cross_tenant(monkeypatch):
    category = SimpleNamespace(id="cat-1", tenant_id="owner-1", status="1")
    monkeypatch.setattr(
        KnowledgebaseCategoryService,
        "get_or_none",
        classmethod(lambda cls, **kwargs: category),
    )

    ok, result = validate_category_assignment("owner-2", "owner-2", "cat-1")

    assert ok is False
    assert result == "No authorization to assign this dataset category"


def test_validate_category_assignment_accepts_none():
    assert validate_category_assignment("owner-1", "owner-1", None) == (True, None)


def test_list_datasets_forwards_real_category_filter(monkeypatch):
    captured = {}
    monkeypatch.setattr(TeamMemberService, "active_team_ids", lambda _user_id: [])
    monkeypatch.setattr(UserService, "get_by_ids", lambda _ids: [])

    def fake_get_list(*args, **_kwargs):
        captured["args"] = args
        return [], 0

    monkeypatch.setattr(KnowledgebaseService, "get_list", fake_get_list)

    ok, result = list_datasets(
        "owner-1",
        {"page": 2, "page_size": 10, "ext": {"category_id": "cat-1", "keywords": "财务"}},
    )

    assert ok is True
    assert result == {"data": [], "total": 0}
    assert captured["args"][-2:] == ("cat-1", False)


def test_list_datasets_maps_uncategorized_filter(monkeypatch):
    captured = {}
    monkeypatch.setattr(TeamMemberService, "active_team_ids", lambda _user_id: [])
    monkeypatch.setattr(UserService, "get_by_ids", lambda _ids: [])

    def fake_get_list(*args, **_kwargs):
        captured["args"] = args
        return [], 0

    monkeypatch.setattr(KnowledgebaseService, "get_list", fake_get_list)

    ok, _ = list_datasets("owner-1", {"ext": {"category_id": "uncategorized"}})

    assert ok is True
    assert captured["args"][-2:] == (None, True)


async def test_create_dataset_persists_category_id(monkeypatch):
    category = SimpleNamespace(id="cat-1", tenant_id="owner-1", status="1")
    captured = {}
    monkeypatch.setattr(
        KnowledgebaseCategoryService,
        "get_or_none",
        classmethod(lambda cls, **kwargs: category),
    )

    def fake_create_with_name(**kwargs):
        captured.update(kwargs)
        return True, {"id": "kb-1", "category_id": kwargs["category_id"], "embd_id": "embd-1"}

    monkeypatch.setattr(KnowledgebaseService, "create_with_name", fake_create_with_name)
    monkeypatch.setattr(dataset_api_service, "DB", SimpleNamespace(atomic=nullcontext))
    monkeypatch.setattr(KnowledgebaseService, "save_in_transaction", lambda **_kwargs: 1)
    monkeypatch.setattr(
        KnowledgebaseService,
        "get_by_id",
        lambda _id: (True, SimpleNamespace(to_dict=lambda: {"id": "kb-1", "category_id": "cat-1"})),
    )
    monkeypatch.setattr(TenantService, "get_by_id", lambda _id: (True, SimpleNamespace(embd_id="embd-1")))
    monkeypatch.setattr(
        "api.apps.services.dataset_api_service.verify_embedding_availability",
        lambda _embd_id, _tenant_id: (True, None),
    )

    ok, result = await create_dataset(
        "owner-1",
        {"name": "预算库", "parser_id": "naive", "category_id": "cat-1", "ext": {}},
    )

    assert ok is True
    assert captured["category_id"] == "cat-1"
    assert result["category_id"] == "cat-1"


async def test_update_dataset_can_move_to_uncategorized(monkeypatch):
    kb = SimpleNamespace(
        id="kb-1",
        tenant_id="owner-1",
        parser_config={},
        parser_id="naive",
        pipeline_id=None,
        name="预算库",
        embd_id="embd-1",
        pagerank=0,
        permission="me",
        team_id=None,
    )
    updates = []
    monkeypatch.setattr(KnowledgebaseService, "get_or_none", lambda **_kwargs: kb)
    monkeypatch.setattr(
        KnowledgebaseService,
        "update_by_id",
        lambda _id, payload: updates.append(dict(payload)) or 1,
    )
    monkeypatch.setattr(
        KnowledgebaseService,
        "get_by_id",
        lambda _id: (True, SimpleNamespace(to_dict=lambda: {"id": "kb-1", "category_id": None})),
    )
    monkeypatch.setattr(Connector2KbService, "link_connectors", lambda *_args: [])

    ok, result = await update_dataset("owner-1", "kb-1", {"category_id": None})

    assert ok is True
    assert updates == [{"category_id": None}]
    assert result["category_id"] is None
