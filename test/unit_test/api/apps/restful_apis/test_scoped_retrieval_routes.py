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
from __future__ import annotations

from collections.abc import Collection
from importlib import import_module
from types import SimpleNamespace

import pytest
from peewee import SqliteDatabase
from quart import g
from werkzeug.exceptions import ServiceUnavailable, TooManyRequests

from api.apps.api_key_auth import APIKeyContext
from api.apps.restful_apis import chunk_api, dataset_api, document_api
from api.apps.services import dataset_api_service
from api.db import APIKeyType, TenantPermission
from api.db.db_models import Knowledgebase, Team
from api.db.services.knowledgebase_service import KnowledgebaseService
from common.constants import RetCode, StatusEnum

DATASET_A = "00000000000010008000000000000001"
DATASET_B = "00000000000010008000000000000002"
DATASET_C = "00000000000010008000000000000003"
DOCUMENT_A = "00000000000010008000000000000011"


@pytest.fixture()
def dataset_database():
    database = SqliteDatabase(":memory:")
    models = [Knowledgebase, Team]
    with database.bind_ctx(models), database.connection_context():
        database.create_tables(models)
        yield


def _persist_dataset(dataset_id: str, *, owner_id: str = "tenant-1", name: str | None = None):
    return Knowledgebase.create(
        id=dataset_id,
        tenant_id=owner_id,
        name=name or dataset_id,
        embd_id="embedding-1",
        created_by=owner_id,
        permission=TenantPermission.ME.value,
        status=StatusEnum.VALID.value,
    )


def _get_list(
    *,
    page: int = 1,
    page_size: int = 30,
    dataset_id: str | None = None,
    name: str | None = None,
    allowed_dataset_ids: Collection[str] | None = None,
):
    return KnowledgebaseService.get_list(
        [],
        "tenant-1",
        page,
        page_size,
        "name",
        False,
        dataset_id,
        name,
        "",
        allowed_dataset_ids=allowed_dataset_ids,
    )


def test_get_list_filters_allowlist_before_count_and_pagination(dataset_database):
    """Moving the allowlist after paginate would leak totals and drop authorized page rows."""
    for index in range(1, 7):
        _persist_dataset(f"kb-{index}")

    datasets, total = _get_list(
        page=2,
        page_size=2,
        allowed_dataset_ids=["kb-2", "kb-4", "kb-6"],
    )

    assert [dataset["id"] for dataset in datasets] == ["kb-6"]
    assert total == 3


def test_get_list_empty_allowlist_returns_no_rows_or_total(dataset_database):
    """Treating an empty allowlist like None would expose every visible dataset."""
    _persist_dataset("kb-1")
    _persist_dataset("kb-2")

    datasets, total = _get_list(allowed_dataset_ids=[])

    assert datasets == []
    assert total == 0


def test_get_list_allowlist_combines_with_visibility_id_name_and_duplicates(dataset_database):
    """Replacing existing predicates or counting duplicate IDs would broaden discovery."""
    _persist_dataset("kb-1", name="Alpha")
    _persist_dataset("kb-2", name="Beta")
    _persist_dataset("kb-other-owner", owner_id="tenant-2", name="Other")
    allowlist = ["kb-1", "kb-1", "kb-2", "kb-other-owner"]

    all_allowed, all_total = _get_list(allowed_dataset_ids=allowlist)
    by_id, id_total = _get_list(dataset_id="kb-1", allowed_dataset_ids=allowlist)
    by_name, name_total = _get_list(name="Beta", allowed_dataset_ids=allowlist)
    unauthorized_name, unauthorized_total = _get_list(name="Other", allowed_dataset_ids=allowlist)
    legacy_rows, legacy_total = _get_list(allowed_dataset_ids=None)

    assert [dataset["id"] for dataset in all_allowed] == ["kb-1", "kb-2"]
    assert all_total == 2
    assert [dataset["id"] for dataset in by_id] == ["kb-1"]
    assert id_total == 1
    assert [dataset["id"] for dataset in by_name] == ["kb-2"]
    assert name_total == 1
    assert unauthorized_name == []
    assert unauthorized_total == 0
    assert [dataset["id"] for dataset in legacy_rows] == ["kb-1", "kb-2"]
    assert legacy_total == 2


def test_list_datasets_passes_allowlist_to_real_pre_paginated_query(dataset_database, monkeypatch):
    """Dropping the route-service allowlist argument would expose unauthorized list rows."""
    _persist_dataset("kb-1")
    _persist_dataset("kb-2")
    monkeypatch.setattr(dataset_api_service.TeamMemberService, "active_team_ids", lambda _tenant_id: [])
    monkeypatch.setattr(dataset_api_service.UserService, "get_by_ids", lambda _tenant_ids: [])

    success, result = dataset_api_service.list_datasets(
        "tenant-1",
        {"page": 1, "page_size": 30, "orderby": "name", "desc": False},
        allowed_dataset_ids=["kb-2"],
    )

    assert success is True
    assert [dataset["id"] for dataset in result["data"]] == ["kb-2"]
    assert result["total"] == 1


@pytest.mark.parametrize(
    ("filter_name", "existing_value", "missing_value"),
    [("id", "kb-hidden", "kb-missing"), ("name", "Hidden", "Missing")],
)
def test_scoped_list_filter_cannot_distinguish_unallowlisted_existing_from_missing_dataset(
    dataset_database,
    monkeypatch,
    filter_name,
    existing_value,
    missing_value,
):
    """Tenant-wide ID/name preflights would expose whether an unallowlisted filter exists."""
    _persist_dataset("kb-allowed", name="Allowed")
    _persist_dataset("kb-hidden", name="Hidden")
    monkeypatch.setattr(dataset_api_service.TeamMemberService, "active_team_ids", lambda _tenant_id: [])
    monkeypatch.setattr(dataset_api_service.UserService, "get_by_ids", lambda _tenant_ids: [])
    base_args = {"page": 1, "page_size": 30, "orderby": "name", "desc": False}

    existing_success, existing_result = dataset_api_service.list_datasets(
        "tenant-1",
        {**base_args, filter_name: existing_value},
        allowed_dataset_ids=["kb-allowed"],
    )
    missing_success, missing_result = dataset_api_service.list_datasets(
        "tenant-1",
        {**base_args, filter_name: missing_value},
        allowed_dataset_ids=["kb-allowed"],
    )

    assert (existing_success, existing_result) == (True, {"data": [], "total": 0})
    assert (missing_success, missing_result) == (True, {"data": [], "total": 0})


@pytest.mark.parametrize(("filter_name", "filter_value"), [("id", "kb-allowed"), ("name", "Allowed")])
def test_scoped_list_allowed_id_and_name_filters_still_return_authorized_dataset(
    dataset_database,
    monkeypatch,
    filter_name,
    filter_value,
):
    """Removing scoped preflights must not suppress an allowlisted ID or name match."""
    _persist_dataset("kb-allowed", name="Allowed")
    _persist_dataset("kb-hidden", name="Hidden")
    monkeypatch.setattr(dataset_api_service.TeamMemberService, "active_team_ids", lambda _tenant_id: [])
    monkeypatch.setattr(dataset_api_service.UserService, "get_by_ids", lambda _tenant_ids: [])

    success, result = dataset_api_service.list_datasets(
        "tenant-1",
        {"page": 1, "page_size": 1, "orderby": "name", "desc": False, filter_name: filter_value},
        allowed_dataset_ids=["kb-allowed"],
    )

    assert success is True
    assert [dataset["id"] for dataset in result["data"]] == ["kb-allowed"]
    assert result["total"] == 1


@pytest.mark.parametrize(("filter_name", "filter_value"), [("id", "kb-missing"), ("name", "Missing")])
def test_unscoped_list_missing_id_and_name_keep_legacy_data_error(
    dataset_database,
    monkeypatch,
    filter_name,
    filter_value,
):
    """Removing preflights for every caller would change the legacy JWT/full-access contract."""
    _persist_dataset("kb-allowed", name="Allowed")
    monkeypatch.setattr(dataset_api_service.TeamMemberService, "active_team_ids", lambda _tenant_id: [])
    monkeypatch.setattr(dataset_api_service.UserService, "get_by_ids", lambda _tenant_ids: [])

    success, result = dataset_api_service.list_datasets(
        "tenant-1",
        {"page": 1, "page_size": 30, "orderby": "name", "desc": False, filter_name: filter_value},
        allowed_dataset_ids=None,
    )

    assert success is False
    assert result == f"User 'tenant-1' lacks permission for dataset '{filter_value}'"


def _api_key_context(
    key_type: APIKeyType = APIKeyType.RETRIEVAL,
    allowed_dataset_ids: Collection[str] | None = (DATASET_A,),
) -> APIKeyContext:
    return APIKeyContext(
        key_type=key_type,
        tenant_id="tenant-1",
        token="ragflow-rk-route-test" if key_type is APIKeyType.RETRIEVAL else "ragflow-full-route-test",
        record=SimpleNamespace(id="key-1"),
        allowed_dataset_ids=None if allowed_dataset_ids is None else frozenset(allowed_dataset_ids),
    )


@pytest.fixture()
def api_client(monkeypatch):
    apps_module = import_module("api.apps")
    state = {"context": _api_key_context(), "usage": []}
    user = SimpleNamespace(id="tenant-1", access_token="unit-access-token", is_active=True)

    def load_user(_auth_types=None):
        g.user = user
        g.api_key_context = state["context"]
        g.auth_type = apps_module.AUTH_API if state["context"] is not None else apps_module.AUTH_JWT
        return user

    monkeypatch.setattr(apps_module, "_load_user", load_user)
    monkeypatch.setattr(
        apps_module.APIKeyUsageService,
        "record",
        lambda **kwargs: state["usage"].append(kwargs),
    )
    return apps_module.app.test_client(), state


def _kb(dataset_id: str, *, embedding: str = "embedding-1", status: str = StatusEnum.VALID.value):
    return SimpleNamespace(
        id=dataset_id,
        tenant_id="tenant-1",
        embd_id=embedding,
        status=status,
    )


def _configure_successful_retrieval(monkeypatch, events: list[str], *, accessible_ids=None):
    accessible = set([DATASET_A, DATASET_B] if accessible_ids is None else accessible_ids)
    monkeypatch.setattr(
        chunk_api.KnowledgebaseService,
        "accessible",
        lambda kb_id, user_id: kb_id in accessible and user_id == "tenant-1",
    )
    monkeypatch.setattr(
        chunk_api.KnowledgebaseService,
        "get_by_ids",
        lambda dataset_ids: [_kb(dataset_id) for dataset_id in dataset_ids if dataset_id in accessible],
    )
    monkeypatch.setattr(
        chunk_api.KnowledgebaseService,
        "get_by_id",
        lambda dataset_id: (dataset_id in accessible, _kb(dataset_id) if dataset_id in accessible else None),
    )
    monkeypatch.setattr(chunk_api, "split_model_name", lambda model_name: (model_name, ""))
    monkeypatch.setattr(
        chunk_api,
        "get_model_config_from_provider_instance",
        lambda *_args, **_kwargs: {"model": "embedding-1"},
    )
    monkeypatch.setattr(chunk_api, "LLMBundle", lambda *_args, **_kwargs: SimpleNamespace())
    monkeypatch.setattr(chunk_api, "label_question", lambda *_args, **_kwargs: {})

    async def retrieve(_question, _model, _tenant_ids, dataset_ids, *_args, **_kwargs):
        events.append("retrieval:" + ",".join(dataset_ids))
        return {"total": 0, "chunks": [], "doc_aggs": []}

    monkeypatch.setattr(
        chunk_api.settings,
        "retriever",
        SimpleNamespace(
            retrieval=retrieve,
            retrieval_by_children=lambda chunks, _tenant_ids: chunks,
        ),
    )
    monkeypatch.setattr(
        chunk_api,
        "consume_retrieval_quota",
        lambda _context: events.append("quota"),
        raising=False,
    )
    monkeypatch.setattr(
        chunk_api,
        "mark_retrieval_started",
        lambda: events.append("mark"),
        raising=False,
    )


@pytest.mark.asyncio
async def test_scoped_metadata_routes_filter_resources_report_totals_and_never_consume_quota(
    api_client,
    monkeypatch,
):
    """Missing route scopes or allowlist plumbing would deny or disclose metadata rows."""
    client, state = api_client
    state["context"] = _api_key_context(allowed_dataset_ids=[DATASET_A])
    quota_calls = []
    monkeypatch.setattr(
        import_module("api.apps.api_key_auth").REDIS_CONN,
        "lua_token_bucket",
        lambda **kwargs: quota_calls.append(kwargs) or [1, 59],
    )

    def list_datasets(_tenant_id, _args, allowed_dataset_ids=None):
        rows = [{"id": dataset_id} for dataset_id in sorted(allowed_dataset_ids or [])]
        return True, {"data": rows, "total": len(rows)}

    monkeypatch.setattr(dataset_api.dataset_api_service, "list_datasets", list_datasets)
    monkeypatch.setattr(
        dataset_api.dataset_api_service,
        "get_dataset",
        lambda dataset_id, _tenant_id: (True, {"id": dataset_id}),
    )
    monkeypatch.setattr(
        dataset_api.dataset_api_service.KnowledgebaseService,
        "get_by_id",
        lambda dataset_id: (True, _kb(dataset_id)),
    )
    monkeypatch.setattr(document_api.KnowledgebaseService, "get_by_id", lambda dataset_id: (True, _kb(dataset_id)))
    monkeypatch.setattr(document_api.KnowledgebaseService, "accessible", lambda **_kwargs: True)
    monkeypatch.setattr(
        document_api,
        "_get_docs_with_request",
        lambda _request, _dataset_id: (RetCode.SUCCESS, "", [], 7),
    )

    list_response = await client.get("/api/v1/datasets")
    detail_response = await client.get(f"/api/v1/datasets/{DATASET_A}")
    docs_response = await client.get(f"/api/v1/datasets/{DATASET_A}/documents")

    assert list_response.status_code == 200
    assert (await list_response.get_json())["data"] == [{"id": DATASET_A}]
    assert (await list_response.get_json())["total_datasets"] == 1
    assert detail_response.status_code == 200
    assert (await detail_response.get_json())["data"]["id"] == DATASET_A
    assert docs_response.status_code == 200
    assert (await docs_response.get_json())["data"] == {"docs": [], "total": 7}
    assert quota_calls == []
    assert len(state["usage"]) == 3
    assert all(event["retrieval_started"] is False for event in state["usage"])


@pytest.mark.asyncio
@pytest.mark.parametrize("path", [f"/api/v1/datasets/{DATASET_C}", f"/api/v1/datasets/{DATASET_C}/documents"])
async def test_scoped_resource_routes_reject_out_of_allowlist_before_resource_work(api_client, monkeypatch, path):
    """Checking tenant access before key scope would allow probing an unauthorized dataset."""
    client, state = api_client
    state["context"] = _api_key_context(allowed_dataset_ids=[DATASET_A])
    monkeypatch.setattr(
        dataset_api.dataset_api_service,
        "get_dataset",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("detail work must not start")),
    )
    monkeypatch.setattr(
        document_api.KnowledgebaseService,
        "accessible",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("document work must not start")),
    )

    response = await client.get(path)

    assert response.status_code == RetCode.FORBIDDEN
    assert (await response.get_json())["code"] == RetCode.FORBIDDEN


@pytest.mark.asyncio
@pytest.mark.parametrize("path", [f"/api/v1/datasets/{DATASET_A}", f"/api/v1/datasets/{DATASET_A}/documents"])
async def test_scoped_resource_routes_return_404_for_allowlisted_deleted_dataset(api_client, monkeypatch, path):
    """Treating an allowlisted deleted ID as a permission error hides the resource lifecycle contract."""
    client, state = api_client
    state["context"] = _api_key_context(allowed_dataset_ids=[DATASET_A])
    monkeypatch.setattr(
        dataset_api.dataset_api_service.KnowledgebaseService,
        "get_by_id",
        lambda _dataset_id: (False, None),
    )
    monkeypatch.setattr(document_api.KnowledgebaseService, "get_by_id", lambda _dataset_id: (False, None))

    response = await client.get(path)

    assert response.status_code == RetCode.NOT_FOUND
    assert (await response.get_json())["code"] == RetCode.NOT_FOUND


@pytest.mark.asyncio
@pytest.mark.parametrize("path", [f"/api/v1/datasets/{DATASET_A}", f"/api/v1/datasets/{DATASET_A}/documents"])
async def test_scoped_resource_routes_return_404_for_allowlisted_soft_deleted_dataset(api_client, monkeypatch, path):
    """Checking only row existence would expose a soft-deleted dataset as a permission failure."""
    client, state = api_client
    state["context"] = _api_key_context(allowed_dataset_ids=[DATASET_A])
    deleted = _kb(DATASET_A, status=StatusEnum.INVALID.value)
    monkeypatch.setattr(
        dataset_api.dataset_api_service.KnowledgebaseService,
        "get_by_id",
        lambda _dataset_id: (True, deleted),
    )
    monkeypatch.setattr(document_api.KnowledgebaseService, "get_by_id", lambda _dataset_id: (True, deleted))
    monkeypatch.setattr(document_api.KnowledgebaseService, "accessible", lambda *_args, **_kwargs: False)

    response = await client.get(path)

    assert response.status_code == RetCode.NOT_FOUND
    assert (await response.get_json())["code"] == RetCode.NOT_FOUND


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("post", "/api/v1/datasets"),
        ("put", f"/api/v1/datasets/{DATASET_A}"),
        ("delete", "/api/v1/datasets"),
        ("post", f"/api/v1/datasets/{DATASET_A}/documents"),
        ("patch", f"/api/v1/datasets/{DATASET_A}/documents/{DOCUMENT_A}"),
        ("delete", f"/api/v1/datasets/{DATASET_A}/documents"),
        ("post", f"/api/v1/datasets/{DATASET_A}/chunks"),
        ("delete", f"/api/v1/datasets/{DATASET_A}/chunks"),
    ],
)
async def test_retrieval_key_cannot_reach_non_whitelisted_dataset_document_or_chunk_routes(
    api_client,
    method,
    path,
):
    """Adding the retrieval scope to any write or extra read route would expand key authority."""
    client, _state = api_client

    response = await getattr(client, method)(path, json={})

    assert response.status_code == RetCode.FORBIDDEN
    assert (await response.get_json())["code"] == RetCode.FORBIDDEN


@pytest.mark.asyncio
async def test_retrieval_key_can_list_chunks_for_allowlisted_dataset(api_client, monkeypatch):
    """The MCP document reader must work with a scoped retrieval key inside its allowlist."""
    client, state = api_client
    state["context"] = _api_key_context(allowed_dataset_ids=[DATASET_A])

    class _Document:
        def to_dict(self):
            return {"id": DOCUMENT_A, "name": "handbook.pdf", "run": "3", "chunk_count": 0}

    monkeypatch.setattr(chunk_api, "_get_authorized_kb", lambda dataset_id, user_id: _kb(dataset_id))
    monkeypatch.setattr(chunk_api.DocumentService, "query", lambda **_kwargs: [_Document()])
    monkeypatch.setattr(chunk_api.settings.docStoreConn, "index_exist", lambda *_args: False)

    response = await client.get(f"/api/v1/datasets/{DATASET_A}/documents/{DOCUMENT_A}/chunks")

    assert response.status_code == 200
    payload = await response.get_json()
    assert payload["code"] == RetCode.SUCCESS
    assert payload["data"]["total"] == 0
    assert payload["data"]["doc"]["id"] == DOCUMENT_A


@pytest.mark.asyncio
async def test_retrieval_key_cannot_list_chunks_outside_dataset_allowlist(api_client, monkeypatch):
    """Allowing the route scope without the dataset allowlist check would broaden key authority."""
    client, state = api_client
    state["context"] = _api_key_context(allowed_dataset_ids=[DATASET_A])
    monkeypatch.setattr(
        chunk_api,
        "_get_authorized_kb",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("resource work must not start")),
    )

    response = await client.get(f"/api/v1/datasets/{DATASET_C}/documents/{DOCUMENT_A}/chunks")

    assert response.status_code == RetCode.FORBIDDEN
    assert (await response.get_json())["code"] == RetCode.FORBIDDEN


@pytest.mark.asyncio
@pytest.mark.parametrize("requested_ids", [None, []])
async def test_retrieval_key_missing_or_empty_ids_uses_full_allowlist_in_deterministic_order(
    api_client,
    monkeypatch,
    requested_ids,
):
    """Keeping the JWT-required-ID branch for scoped keys would make their allowlist unusable."""
    client, state = api_client
    state["context"] = _api_key_context(allowed_dataset_ids=[DATASET_B, DATASET_A])
    events = []
    _configure_successful_retrieval(monkeypatch, events)
    payload = {"question": "where is it?"}
    if requested_ids is not None:
        payload["dataset_ids"] = requested_ids

    response = await client.post("/api/v1/retrieval", json=payload)

    assert response.status_code == 200
    assert (await response.get_json())["code"] == RetCode.SUCCESS
    assert events == ["quota", "mark", f"retrieval:{DATASET_A},{DATASET_B}"]


@pytest.mark.asyncio
async def test_retrieval_key_explicit_ids_deduplicate_in_caller_order(api_client, monkeypatch):
    """Set-based normalization would reorder caller-selected datasets before retrieval."""
    client, state = api_client
    state["context"] = _api_key_context(allowed_dataset_ids=[DATASET_A, DATASET_B])
    events = []
    _configure_successful_retrieval(monkeypatch, events)

    response = await client.post(
        "/api/v1/retrieval",
        json={"question": "where is it?", "dataset_ids": [DATASET_B, DATASET_A, DATASET_B]},
    )

    assert response.status_code == 200
    assert (await response.get_json())["code"] == RetCode.SUCCESS
    assert events == ["quota", "mark", f"retrieval:{DATASET_B},{DATASET_A}"]


@pytest.mark.asyncio
async def test_retrieval_key_mixed_allowed_and_disallowed_ids_rejects_whole_request(api_client, monkeypatch):
    """Filtering disallowed IDs instead of rejecting would permit partial retrieval."""
    client, state = api_client
    state["context"] = _api_key_context(allowed_dataset_ids=[DATASET_A])
    events = []
    _configure_successful_retrieval(monkeypatch, events)

    response = await client.post(
        "/api/v1/retrieval",
        json={"question": "where is it?", "dataset_ids": [DATASET_A, DATASET_C]},
    )

    assert response.status_code == RetCode.FORBIDDEN
    assert events == []


@pytest.mark.asyncio
async def test_retrieval_rejects_malformed_allowlisted_id_before_quota(api_client, monkeypatch):
    """Trusting key-management input would let malformed IDs reach database/retrieval work."""
    client, state = api_client
    state["context"] = _api_key_context(allowed_dataset_ids=["not-a-uuid"])
    events = []
    _configure_successful_retrieval(monkeypatch, events, accessible_ids=["not-a-uuid"])

    response = await client.post(
        "/api/v1/retrieval",
        json={"question": "where is it?", "dataset_ids": ["not-a-uuid"]},
    )

    assert response.status_code == 200
    assert (await response.get_json())["code"] == RetCode.ARGUMENT_ERROR
    assert events == []


@pytest.mark.asyncio
async def test_retrieval_rejects_cross_tenant_dataset_before_quota(api_client, monkeypatch):
    """Checking only the key allowlist would bypass tenant ownership checks."""
    client, state = api_client
    state["context"] = _api_key_context(allowed_dataset_ids=[DATASET_A])
    events = []
    _configure_successful_retrieval(monkeypatch, events, accessible_ids=[])
    monkeypatch.setattr(chunk_api.KnowledgebaseService, "get_by_ids", lambda _dataset_ids: [_kb(DATASET_A)])

    response = await client.post(
        "/api/v1/retrieval",
        json={"question": "where is it?", "dataset_ids": [DATASET_A]},
    )

    assert response.status_code == 200
    assert (await response.get_json())["code"] == RetCode.DATA_ERROR
    assert events == []


@pytest.mark.asyncio
@pytest.mark.parametrize("remaining_rows", [[], [_kb(DATASET_A, status=StatusEnum.INVALID.value)]])
async def test_retrieval_key_returns_404_for_allowlisted_deleted_dataset(
    api_client,
    monkeypatch,
    remaining_rows,
):
    client, state = api_client
    state["context"] = _api_key_context(allowed_dataset_ids=[DATASET_A])
    events = []
    _configure_successful_retrieval(monkeypatch, events)
    monkeypatch.setattr(chunk_api.KnowledgebaseService, "get_by_ids", lambda _dataset_ids: remaining_rows)

    response = await client.post(
        "/api/v1/retrieval",
        json={"question": "where is it?", "dataset_ids": [DATASET_A]},
    )

    assert response.status_code == RetCode.NOT_FOUND
    assert (await response.get_json())["code"] == RetCode.NOT_FOUND
    assert events == []


@pytest.mark.asyncio
@pytest.mark.parametrize("key_type", [None, APIKeyType.FULL_ACCESS])
async def test_jwt_and_full_access_requests_keep_required_dataset_ids_contract(api_client, key_type):
    """Applying scoped-key defaults to JWT/full callers would silently change the public contract."""
    client, state = api_client
    state["context"] = None if key_type is None else _api_key_context(key_type, allowed_dataset_ids=None)

    response = await client.post("/api/v1/retrieval", json={"question": "where is it?"})

    assert response.status_code == 200
    payload = await response.get_json()
    assert payload["code"] == RetCode.DATA_ERROR
    assert payload["message"] == "`dataset_ids` is required."


@pytest.mark.asyncio
async def test_retrieval_validates_documents_and_embedding_compatibility_before_quota(api_client, monkeypatch):
    """Charging before document/model validation would consume quota for unusable requests."""
    client, state = api_client
    state["context"] = _api_key_context(allowed_dataset_ids=[DATASET_A, DATASET_B])
    events = []
    _configure_successful_retrieval(monkeypatch, events)
    monkeypatch.setattr(chunk_api.KnowledgebaseService, "list_documents_by_ids", lambda _dataset_ids: [])

    missing_doc = await client.post(
        "/api/v1/retrieval",
        json={"question": "where is it?", "dataset_ids": [DATASET_A], "document_ids": [DOCUMENT_A]},
    )
    assert (await missing_doc.get_json())["code"] == RetCode.DATA_ERROR
    assert events == []

    monkeypatch.setattr(
        chunk_api.KnowledgebaseService,
        "get_by_ids",
        lambda _dataset_ids: [_kb(DATASET_A, embedding="embedding-1"), _kb(DATASET_B, embedding="embedding-2")],
    )
    incompatible_models = await client.post(
        "/api/v1/retrieval",
        json={"question": "where is it?", "dataset_ids": [DATASET_A, DATASET_B]},
    )
    assert (await incompatible_models.get_json())["code"] == RetCode.DATA_ERROR
    assert events == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("quota_error", "expected_status"),
    [(TooManyRequests("limited"), 429), (ServiceUnavailable("unavailable"), 503)],
)
async def test_quota_failure_never_marks_or_starts_expensive_retrieval(
    api_client,
    monkeypatch,
    quota_error,
    expected_status,
):
    """Marking before quota success would record 429/503 requests as started retrievals."""
    client, state = api_client
    state["context"] = _api_key_context(allowed_dataset_ids=[DATASET_A])
    events = []
    _configure_successful_retrieval(monkeypatch, events)

    def reject_quota(_context):
        events.append("quota")
        raise quota_error

    monkeypatch.setattr(chunk_api, "consume_retrieval_quota", reject_quota, raising=False)

    response = await client.post(
        "/api/v1/retrieval",
        json={"question": "where is it?", "dataset_ids": [DATASET_A]},
    )

    assert response.status_code == expected_status
    assert events == ["quota"]
    assert state["usage"][-1]["retrieval_started"] is False
