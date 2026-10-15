import asyncio
import httpx
import pytest
from test.unit_test.mcp.test_server_streamable_http import _load_mcp_server, _client_for, _initialize_request


@pytest.fixture
def wb_mcp():
    module = _load_mcp_server()
    module.AUTH_MODE = "workbuddy"
    module.MODE = module.LaunchMode.HOST
    module.TRANSPORT_SSE_ENABLED = False
    module.TRANSPORT_STREAMABLE_HTTP_ENABLED = True
    module.JSON_RESPONSE = True
    module.STREAMABLE_HTTP_ALLOWED_HOSTS = ["mcp.test"]
    module.STREAMABLE_HTTP_ALLOWED_ORIGINS = ["http://mcp.test"]
    return module


def headers(ticket):
    return {"accept": "application/json, text/event-stream", "content-type": "application/json", "x-oneid-access-token": ticket}


async def test_workbuddy_streamable_http_forwards_ticket_without_api_key(wb_mcp):
    async with _client_for(wb_mcp) as client:
        res = await client.post("/mcp", headers=headers("personal-ticket"), json=_initialize_request())
        assert res.status_code == 200
        res = await client.post("/mcp", headers=headers("personal-ticket"), json={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
        assert res.status_code == 200
        tools = res.json()["result"]["tools"]
        assert len(tools) == 4
        assert all("api_key" not in t["inputSchema"]["properties"] for t in tools)
    seen = []

    async def backend(req):
        seen.append(req)
        return httpx.Response(200, json={"code": 0, "data": [], "total": 0})

    conn = wb_mcp.RAGFlowConnector("http://backend.test", auth_mode="workbuddy")
    conn._async_client = httpx.AsyncClient(transport=httpx.MockTransport(backend))
    try:
        await conn._get("/datasets", api_key="personal-ticket")
    finally:
        await conn.close()
    assert seen[0].headers["x-oneid-access-token"] == "personal-ticket"
    assert "authorization" not in seen[0].headers


@pytest.mark.parametrize(
    "bad_headers",
    [
        [("Authorization", "Bearer old-key")],
        [("x-oneid-access-token", "a"), ("x-oneid-access-token", "b")],
        [("x-oneid-access-token", "a,b")],
    ],
)
async def test_duplicate_ticket_and_legacy_only_request_are_rejected(wb_mcp, bad_headers):
    async with _client_for(wb_mcp) as client:
        res = await client.post("/mcp", headers=bad_headers, json=_initialize_request())
        assert res.status_code == 401


async def test_reused_ticket_does_not_return_cached_revoked_metadata(wb_mcp):
    count = 0

    async def backend(req):
        nonlocal count
        count += 1
        if count > 1:
            return httpx.Response(403, json={"code": 403, "message": "Access revoked"})
        return httpx.Response(200, json={"code": 0, "data": [{"id": "kb-a", "name": "Allowed", "description": ""}], "total": 1})

    conn = wb_mcp.RAGFlowConnector("http://backend.test", auth_mode="workbuddy")
    conn._async_client = httpx.AsyncClient(transport=httpx.MockTransport(backend))
    try:
        assert await conn.resolve_dataset_ids(api_key="ticket") == ["kb-a"]
        with pytest.raises(Exception):
            await conn.resolve_dataset_ids(api_key="ticket")
        assert not conn._dataset_id_cache
    finally:
        await conn.close()


async def test_two_concurrent_employees_never_share_credentials(wb_mcp):
    seen = []

    async def backend(req):
        await asyncio.sleep(0)
        seen.append(req.headers["x-oneid-access-token"])
        return httpx.Response(200, json={"code": 0, "data": []})

    conn = wb_mcp.RAGFlowConnector("http://backend.test", auth_mode="workbuddy")
    conn._async_client = httpx.AsyncClient(transport=httpx.MockTransport(backend))
    try:
        await asyncio.gather(conn._get("/datasets", api_key="employee-a"), conn._get("/datasets", api_key="employee-b"))
    finally:
        await conn.close()
    assert sorted(seen) == ["employee-a", "employee-b"]
