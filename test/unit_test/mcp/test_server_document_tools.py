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
import importlib.util
import json
from pathlib import Path

import pytest


def _load_mcp_server():
    server_path = Path(__file__).resolve().parents[3] / "mcp" / "server" / "server.py"
    spec = importlib.util.spec_from_file_location("ragflow_mcp_server_document_tools_unit", server_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _FakeResponse:
    status_code = 200

    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


@pytest.fixture()
def mcp_server():
    return _load_mcp_server()


@pytest.mark.asyncio
async def test_list_tools_exposes_document_discovery_and_ordered_chunk_reading(monkeypatch, mcp_server):
    connector = mcp_server.RAGFlowConnector(base_url=mcp_server.BASE_URL)

    async def _list_datasets(**_kwargs):
        return '{"description":"Policies","id":"dataset-1"}'

    monkeypatch.setattr(connector, "list_datasets", _list_datasets)

    tools = await mcp_server.list_tools.__wrapped__(connector=connector, api_key="unit-key")

    assert [tool.name for tool in tools] == ["ragflow_retrieval", "search_documents", "get_document_chunks"]
    search_schema = tools[1].inputSchema
    assert search_schema["required"] == ["query"]
    assert search_schema["properties"]["query"]["minLength"] == 1
    assert search_schema["properties"]["dataset_ids"]["items"]["type"] == "string"
    assert search_schema["properties"]["metadata"]["type"] == "object"
    chunks_schema = tools[2].inputSchema
    assert chunks_schema["required"] == ["dataset_id", "document_id"]
    assert chunks_schema["properties"]["page_size"]["default"] == 10
    assert chunks_schema["properties"]["page_size"]["maximum"] == 50


@pytest.mark.asyncio
async def test_search_documents_resolves_datasets_and_returns_compact_metadata(monkeypatch, mcp_server):
    connector = mcp_server.RAGFlowConnector(base_url=mcp_server.BASE_URL)
    requests = []

    async def _resolve_dataset_ids(*, api_key):
        assert api_key == "unit-key"
        return ["dataset-1", "dataset-2"]

    async def _get(path, params=None, api_key=""):
        requests.append((path, params, api_key))
        dataset_id = path.split("/")[2]
        return _FakeResponse(
            {
                "code": 0,
                "data": {
                    "total": 1,
                    "docs": [
                        {
                            "id": f"document-{dataset_id[-1]}",
                            "name": "物料报废申请表.xlsx",
                            "location": f"制度/{dataset_id}/物料报废申请表.xlsx",
                            "type": "xlsx",
                            "chunk_count": 8,
                            "update_date": "2026-09-29 07:00:00",
                            "dataset_id": dataset_id,
                            "meta_fields": {"department": "仓储"},
                            "parser_config": {"large": "field that must not leak"},
                        }
                    ],
                },
            }
        )

    monkeypatch.setattr(connector, "resolve_dataset_ids", _resolve_dataset_ids)
    monkeypatch.setattr(connector, "_get", _get)

    content = await mcp_server.call_tool.__wrapped__(
        "search_documents",
        {"query": "物料报废", "limit": 10},
        connector=connector,
        api_key="unit-key",
    )

    result = json.loads(content[0].text)
    assert result["total_matches"] == 2
    assert result["returned"] == 2
    assert result["documents"] == [
        {
            "dataset_id": "dataset-1",
            "document_id": "document-1",
            "name": "物料报废申请表.xlsx",
            "location": "制度/dataset-1/物料报废申请表.xlsx",
            "type": "xlsx",
            "chunk_count": 8,
            "update_date": "2026-09-29 07:00:00",
            "meta_fields": {"department": "仓储"},
        },
        {
            "dataset_id": "dataset-2",
            "document_id": "document-2",
            "name": "物料报废申请表.xlsx",
            "location": "制度/dataset-2/物料报废申请表.xlsx",
            "type": "xlsx",
            "chunk_count": 8,
            "update_date": "2026-09-29 07:00:00",
            "meta_fields": {"department": "仓储"},
        },
    ]
    assert requests == [
        ("/datasets/dataset-1/documents", {"page": 1, "page_size": 100, "keywords": "物料报废", "orderby": "update_time", "desc": True}, "unit-key"),
        ("/datasets/dataset-2/documents", {"page": 1, "page_size": 100, "keywords": "物料报废", "orderby": "update_time", "desc": True}, "unit-key"),
    ]


@pytest.mark.asyncio
async def test_search_documents_ranks_exact_filename_across_datasets_before_truncating(monkeypatch, mcp_server):
    connector = mcp_server.RAGFlowConnector(base_url=mcp_server.BASE_URL)

    async def _get(path, params=None, api_key=""):
        dataset_id = path.split("/")[2]
        name = "物料报废流程.xlsx" if dataset_id == "dataset-1" else "物料报废.xlsx"
        return _FakeResponse(
            {
                "code": 0,
                "data": {
                    "total": 1,
                    "docs": [{"id": f"document-{dataset_id[-1]}", "name": name, "dataset_id": dataset_id, "update_date": "2026-09-29 07:00:00"}],
                },
            }
        )

    monkeypatch.setattr(connector, "_get", _get)

    content = await connector.search_documents(
        api_key="unit-key",
        query="物料报废",
        dataset_ids=["dataset-1", "dataset-2"],
        limit=1,
    )

    result = json.loads(content[0].text)
    assert result["documents"][0]["document_id"] == "document-2"


@pytest.mark.asyncio
async def test_search_documents_finds_older_exact_match_after_first_backend_page(monkeypatch, mcp_server):
    connector = mcp_server.RAGFlowConnector(base_url=mcp_server.BASE_URL)
    requested_pages = []

    async def _get(_path, params=None, api_key=""):
        requested_pages.append(params["page"])
        if params["page"] == 1:
            docs = [{"id": f"loose-{index}", "name": f"report-{index}.txt", "update_date": "2026-09-29"} for index in range(100)]
        else:
            docs = [{"id": "exact", "name": "report.txt", "update_date": "2020-01-01"}]
        return _FakeResponse({"code": 0, "data": {"total": 101, "docs": docs}})

    monkeypatch.setattr(connector, "_get", _get)

    content = await connector.search_documents(api_key="unit-key", query="report.txt", dataset_ids=["dataset-1"], limit=1)

    result = json.loads(content[0].text)
    assert requested_pages == [1, 2]
    assert result["documents"][0]["document_id"] == "exact"


@pytest.mark.asyncio
async def test_search_documents_does_not_rank_different_extension_as_exact(monkeypatch, mcp_server):
    connector = mcp_server.RAGFlowConnector(base_url=mcp_server.BASE_URL)

    async def _get(_path, params=None, api_key=""):
        return _FakeResponse(
            {
                "code": 0,
                "data": {
                    "total": 2,
                    "docs": [
                        {"id": "newer-pdf", "name": "report.pdf", "update_date": "2026-09-29"},
                        {"id": "older-txt", "name": "report.txt", "update_date": "2020-01-01"},
                    ],
                },
            }
        )

    monkeypatch.setattr(connector, "_get", _get)

    content = await connector.search_documents(api_key="unit-key", query="report.txt", dataset_ids=["dataset-1"], limit=1)

    result = json.loads(content[0].text)
    assert result["documents"][0]["document_id"] == "older-txt"


@pytest.mark.asyncio
async def test_search_documents_bounds_parallel_dataset_requests_and_forwards_metadata(monkeypatch, mcp_server):
    connector = mcp_server.RAGFlowConnector(base_url=mcp_server.BASE_URL)
    active_requests = 0
    peak_requests = 0
    seen_metadata = []

    async def _get(_path, params=None, api_key=""):
        nonlocal active_requests, peak_requests
        active_requests += 1
        peak_requests = max(peak_requests, active_requests)
        seen_metadata.append(params["metadata"])
        await asyncio.sleep(0.001)
        active_requests -= 1
        return _FakeResponse({"code": 0, "data": {"total": 0, "docs": []}})

    monkeypatch.setattr(connector, "_get", _get)

    await connector.search_documents(
        api_key="unit-key",
        query="制度",
        dataset_ids=[f"dataset-{index}" for index in range(20)],
        metadata={"department": "仓储"},
    )

    assert 1 < peak_requests <= 8
    assert set(seen_metadata) == {'{"department": "仓储"}'}


@pytest.mark.asyncio
async def test_get_document_chunks_returns_backend_order_and_pagination(monkeypatch, mcp_server):
    connector = mcp_server.RAGFlowConnector(base_url=mcp_server.BASE_URL)
    requests = []

    async def _get(path, params=None, api_key=""):
        requests.append((path, params, api_key))
        return _FakeResponse(
            {
                "code": 0,
                "data": {
                    "total": 3,
                    "doc": {
                        "id": "document-1",
                        "name": "制度.docx",
                        "location": "制度/制度.docx",
                        "type": "docx",
                        "chunk_count": 3,
                        "parser_config": {"large": "field that must not leak"},
                    },
                    "chunks": [
                        {
                            "id": "chunk-2",
                            "content": "第二段",
                            "document_id": "document-1",
                            "dataset_id": "dataset-1",
                            "positions": [[2, 0, 1, 1, 1]],
                            "runtime_field": "must not leak",
                        },
                        {
                            "id": "chunk-3",
                            "content": "第三段",
                            "document_id": "document-1",
                            "dataset_id": "dataset-1",
                            "positions": [[3, 0, 1, 1, 1]],
                        },
                    ],
                },
            }
        )

    monkeypatch.setattr(connector, "_get", _get)

    content = await mcp_server.call_tool.__wrapped__(
        "get_document_chunks",
        {"dataset_id": "dataset-1", "document_id": "document-1", "page": 2, "page_size": 2},
        connector=connector,
        api_key="unit-key",
    )

    result = json.loads(content[0].text)
    assert result["document"] == {
        "document_id": "document-1",
        "name": "制度.docx",
        "location": "制度/制度.docx",
        "type": "docx",
        "chunk_count": 3,
    }
    assert [chunk["content"] for chunk in result["chunks"]] == ["第二段", "第三段"]
    assert "runtime_field" not in result["chunks"][0]
    assert result["pagination"] == {"page": 2, "page_size": 2, "total_chunks": 3, "total_pages": 2}
    assert requests == [
        (
            "/datasets/dataset-1/documents/document-1/chunks",
            {"page": 2, "page_size": 2},
            "unit-key",
        )
    ]


@pytest.mark.asyncio
async def test_document_tools_reject_malformed_success_payloads(monkeypatch, mcp_server):
    connector = mcp_server.RAGFlowConnector(base_url=mcp_server.BASE_URL)

    async def _get(_path, params=None, api_key=""):
        return _FakeResponse({"code": 0, "data": []})

    monkeypatch.setattr(connector, "_get", _get)

    with pytest.raises(Exception) as search_error:
        await connector.search_documents(api_key="unit-key", query="制度", dataset_ids=["dataset-1"])
    with pytest.raises(Exception) as chunks_error:
        await connector.get_document_chunks(api_key="unit-key", dataset_id="dataset-1", document_id="document-1")

    assert search_error.value.args[0][0].text == "Cannot process this operation."
    assert chunks_error.value.args[0][0].text == "Cannot process this operation."


@pytest.mark.asyncio
async def test_search_documents_rejects_empty_query_without_listing_every_document(mcp_server):
    connector = mcp_server.RAGFlowConnector(base_url=mcp_server.BASE_URL)

    with pytest.raises(Exception) as exc_info:
        await connector.search_documents(api_key="unit-key", query="  ", dataset_ids=["dataset-1"])

    assert exc_info.value.args[0][0].text == "A non-empty filename query is required."


@pytest.mark.asyncio
async def test_search_documents_requires_narrowing_when_dataset_count_exceeds_safe_limit(mcp_server):
    connector = mcp_server.RAGFlowConnector(base_url=mcp_server.BASE_URL)

    with pytest.raises(Exception) as exc_info:
        await connector.search_documents(
            api_key="unit-key",
            query="制度",
            dataset_ids=[f"dataset-{index}" for index in range(33)],
        )

    assert exc_info.value.args[0][0].text == "Too many datasets to search safely. Provide at most 32 dataset_ids."


@pytest.mark.asyncio
async def test_search_documents_caps_pages_and_reports_truncated_scan(monkeypatch, mcp_server):
    connector = mcp_server.RAGFlowConnector(base_url=mcp_server.BASE_URL)
    requested_pages = []

    async def _get(_path, params=None, api_key=""):
        requested_pages.append(params["page"])
        offset = (params["page"] - 1) * 100
        docs = [
            {"id": f"document-{offset + index}", "name": f"制度-{offset + index}.pdf", "update_date": "2026-09-29"}
            for index in range(100)
        ]
        return _FakeResponse({"code": 0, "data": {"total": 1000, "docs": docs}})

    monkeypatch.setattr(connector, "_get", _get)

    content = await connector.search_documents(api_key="unit-key", query="制度", dataset_ids=["dataset-1"], limit=10)

    result = json.loads(content[0].text)
    assert requested_pages == [1, 2, 3, 4, 5]
    assert result["returned"] == 10
    assert result["query_info"]["scan_truncated"] is True


@pytest.mark.asyncio
async def test_document_tools_clamp_page_sizes_to_rest_limit(monkeypatch, mcp_server):
    connector = mcp_server.RAGFlowConnector(base_url=mcp_server.BASE_URL)
    requests = []

    async def _get(path, params=None, api_key=""):
        requests.append((path, params))
        if path.endswith("/chunks"):
            return _FakeResponse({"code": 0, "data": {"total": 0, "doc": {"id": "document-1"}, "chunks": []}})
        return _FakeResponse({"code": 0, "data": {"total": 0, "docs": []}})

    monkeypatch.setattr(connector, "_get", _get)

    await connector.search_documents(api_key="unit-key", query="制度", dataset_ids=["dataset-1"], limit=1000)
    await connector.get_document_chunks(api_key="unit-key", dataset_id="dataset-1", document_id="document-1", page_size=1000)

    assert requests[0][1]["page_size"] == 100
    assert requests[1][1]["page_size"] == 50
