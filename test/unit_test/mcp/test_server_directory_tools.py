"""Directory browsing contracts: bounded pages, caller isolation, and honest metadata."""

import asyncio
import json

import httpx
import pytest

from test.unit_test.mcp.test_server_streamable_http import _client_for, _initialize_request, _load_mcp_server


@pytest.fixture
def server():
    return _load_mcp_server()


@pytest.fixture
def connector(server):
    return server.RAGFlowConnector("http://backend.test", auth_mode="workbuddy")


@pytest.mark.asyncio
async def test_knowledge_base_page_preserves_total_and_returns_compact_metadata(server, connector):
    def backend(request):
        assert request.url.path == "/api/v1/datasets"
        assert dict(request.url.params) == {"page": "2", "page_size": "1", "orderby": "create_time", "desc": "false", "name": "声学文献库"}
        assert request.headers["x-oneid-access-token"] == "employee-a"
        return httpx.Response(200, json={"code": 0, "total": 3, "data": [{"id": "kb-2", "name": "声学文献库", "description": "声学资料", "document_count": 42, "parser_config": {"private": True}}]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(backend)) as client:
        connector._async_client = client
        content = await server.call_tool.__wrapped__("list_knowledge_bases", {"page": 2, "page_size": 1, "name": "声学文献库"}, connector=connector, api_key="employee-a")
    result = json.loads(content[0].text)
    assert result == {
        "knowledge_bases": [{"dataset_id": "kb-2", "name": "声学文献库", "description": "声学资料", "document_count": 42}],
        "pagination": {"page": 2, "page_size": 1, "total": 3, "total_pages": 3, "has_more": True},
    }


@pytest.mark.asyncio
async def test_document_page_preserves_status_and_only_explicit_source_paths(server, connector):
    def backend(request):
        assert request.url.path == "/api/v1/datasets/kb-a/documents"
        assert dict(request.url.params) == {"page": "1", "page_size": "20", "orderby": "id", "desc": "false"}
        assert request.headers["x-oneid-access-token"] == "employee-a"
        return httpx.Response(
            200,
            json={
                "code": 0,
                "data": {
                    "total": 2,
                    "docs": [
                        {
                            "id": "doc-a",
                            "dataset_id": "kb-a",
                            "name": "水声.pdf",
                            "location": "storage/key.pdf",
                            "path": "声学/水声.pdf",
                            "type": "pdf",
                            "run": "DONE",
                            "chunk_count": 12,
                            "update_date": "2026-11-02",
                            "thumbnail": "large",
                            "parser_config": {"private": True},
                        },
                        {"id": "doc-b", "name": "未解析.pdf", "location": "storage/another.pdf", "type": "pdf", "run": "UNSTART", "chunk_count": 0},
                    ],
                },
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(backend)) as client:
        connector._async_client = client
        content = await server.call_tool.__wrapped__("list_documents", {"dataset_id": "kb-a"}, connector=connector, api_key="employee-a")
    result = json.loads(content[0].text)
    assert result["documents"] == [
        {
            "dataset_id": "kb-a",
            "document_id": "doc-a",
            "name": "水声.pdf",
            "location": "storage/key.pdf",
            "path": "声学/水声.pdf",
            "type": "pdf",
            "run": "DONE",
            "chunk_count": 12,
            "update_date": "2026-11-02",
        },
        {"dataset_id": "kb-a", "document_id": "doc-b", "name": "未解析.pdf", "location": "storage/another.pdf", "path": "", "type": "pdf", "run": "UNSTART", "chunk_count": 0, "update_date": ""},
    ]
    assert result["pagination"] == {"page": 1, "page_size": 20, "total": 2, "total_pages": 1, "has_more": False}


@pytest.mark.asyncio
@pytest.mark.parametrize("tool", ["list_knowledge_bases", "list_documents"])
@pytest.mark.parametrize(("page", "total"), [(1, 0), (4, 3)])
async def test_empty_and_past_end_pages_are_not_errors(connector, tool, page, total):
    def backend(request):
        assert request.url.params["page"] == str(page)
        assert request.url.params["page_size"] == "2"
        return httpx.Response(200, json={"code": 0, "total": total, "data": []} if tool == "list_knowledge_bases" else {"code": 0, "data": {"total": total, "docs": []}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(backend)) as client:
        connector._async_client = client
        args = {} if tool == "list_knowledge_bases" else {"dataset_id": "kb-a"}
        result = json.loads((await getattr(connector, tool)(api_key="employee-a", page=page, page_size=2, **args))[0].text)
    assert result["pagination"] == {"page": page, "page_size": 2, "total": total, "total_pages": (total + 1) // 2, "has_more": False}


@pytest.mark.asyncio
@pytest.mark.parametrize("tool", ["list_knowledge_bases", "list_documents"])
async def test_lists_use_each_callers_credentials_and_do_not_cache_revoked_access(connector, tool):
    revoked = False

    async def backend(request):
        await asyncio.sleep(0)
        actor = request.headers["x-oneid-access-token"]
        assert "authorization" not in request.headers
        if revoked:
            return httpx.Response(403, json={"code": 403, "message": "Access revoked"})
        item = {"id": actor, "name": actor, "description": ""}
        payload = {"code": 0, "total": 1, "data": [item]} if tool == "list_knowledge_bases" else {"code": 0, "data": {"total": 1, "docs": [item]}}
        return httpx.Response(200, json=payload)

    async with httpx.AsyncClient(transport=httpx.MockTransport(backend)) as client:
        connector._async_client = client
        args = {} if tool == "list_knowledge_bases" else {"dataset_id": "kb-a"}
        results = await asyncio.gather(*(getattr(connector, tool)(api_key=actor, **args) for actor in ["employee-a", "employee-b"]))
        field = "knowledge_bases" if tool == "list_knowledge_bases" else "documents"
        assert [json.loads(r[0].text)[field][0]["name"] for r in results] == ["employee-a", "employee-b"]
        revoked = True
        with pytest.raises(Exception, match="Access revoked"):
            await getattr(connector, tool)(api_key="employee-a", **args)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("tool", "payload"),
    [
        ("list_knowledge_bases", {"code": 0, "data": []}),
        ("list_knowledge_bases", {"code": 0, "data": [], "total": -1}),
        ("list_knowledge_bases", {"code": 0, "data": [], "total": True}),
        ("list_knowledge_bases", {"code": 0, "data": [{"id": "", "description": ""}], "total": 1}),
        ("list_documents", {"code": 0, "data": {"docs": []}}),
        ("list_documents", {"code": 0, "data": {"docs": [], "total": -1}}),
        ("list_documents", {"code": 0, "data": {"docs": [], "total": True}}),
        ("list_documents", {"code": 0, "data": {"docs": [None], "total": 1}}),
        ("list_documents", {"code": 0, "data": {"docs": [{"id": "doc", "dataset_id": "other-kb"}], "total": 1}}),
        ("list_documents", {"code": 403, "message": "Access denied"}),
    ],
)
async def test_invalid_backend_response_is_not_reported_as_empty_success(connector, tool, payload):
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, json=payload))) as client:
        connector._async_client = client
        args = {} if tool == "list_knowledge_bases" else {"dataset_id": "kb-a"}
        method = getattr(connector, tool)
        with pytest.raises(Exception, match="Invalid|Access denied|Cannot process"):
            await method(api_key="employee-a", **args)


@pytest.mark.asyncio
async def test_directory_tools_through_mcp_validate_arguments_and_forward_identity(server, monkeypatch):
    server.MODE = server.LaunchMode.HOST
    server.AUTH_MODE = "workbuddy"
    server.TRANSPORT_SSE_ENABLED = False
    server.STREAMABLE_HTTP_ALLOWED_HOSTS = ["mcp.test"]
    seen = []

    def backend(request):
        seen.append(request)
        payload = {"code": 0, "data": [], "total": 0} if request.url.path.endswith("/datasets") else {"code": 0, "data": {"docs": [], "total": 0}}
        return httpx.Response(200, json=payload)

    async with httpx.AsyncClient(transport=httpx.MockTransport(backend)) as backend_client:

        async def get_client(self):
            return backend_client

        monkeypatch.setattr(server.RAGFlowConnector, "_get_client", get_client)
        async with _client_for(server) as client:
            headers = {"accept": "application/json, text/event-stream", "x-oneid-access-token": "employee-a"}
            await client.post("/mcp", headers=headers, json=_initialize_request())
            response = await client.post("/mcp", headers=headers, json={"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
            names = {t["name"] for t in response.json()["result"]["tools"]}
            assert {"list_knowledge_bases", "list_documents"} <= names
            assert not seen
            for tool, args in [("list_knowledge_bases", {}), ("list_documents", {"dataset_id": "kb-a"})]:
                response = await client.post("/mcp", headers=headers, json={"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": tool, "arguments": args}})
                assert response.json()["result"].get("isError", False) is False
            assert len(seen) == 2
            assert all(r.headers["x-oneid-access-token"] == "employee-a" for r in seen)
            for tool, args in [
                ("list_documents", {}),
                ("list_documents", {"dataset_id": "../kb"}),
                ("list_documents", {"dataset_id": "kb-a", "page_size": 101}),
                ("list_knowledge_bases", {"page": 0}),
                ("list_knowledge_bases", {"page_size": -1}),
            ]:
                response = await client.post("/mcp", headers=headers, json={"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": tool, "arguments": args}})
                assert response.json()["result"]["isError"] is True
            assert len(seen) == 2


@pytest.mark.asyncio
async def test_new_directory_counts_preserve_existing_totals(server, tmp_path):
    path = tmp_path / "counts.json"
    path.write_text(json.dumps({"ragflow_retrieval": 67, "search_documents": 67, "get_document_chunks": 53, "get_chunk_image": 2}))
    async with server.ToolCallCounts(path) as counts:
        counts.increment("list_knowledge_bases")
        counts.increment("list_documents")
    result = json.loads(path.read_text())
    assert result == {"ragflow_retrieval": 67, "search_documents": 67, "get_document_chunks": 53, "get_chunk_image": 2, "list_knowledge_bases": 1, "list_documents": 1, "export_document_catalog": 0}
