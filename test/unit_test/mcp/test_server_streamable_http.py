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

import importlib.util
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import pytest


def _load_mcp_server():
    server_path = Path(__file__).resolve().parents[3] / "mcp" / "server" / "server.py"
    spec = importlib.util.spec_from_file_location("ragflow_mcp_server_streamable_http_unit", server_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def mcp_server():
    module = _load_mcp_server()
    module.MODE = module.LaunchMode.HOST
    module.TRANSPORT_SSE_ENABLED = False
    module.TRANSPORT_STREAMABLE_HTTP_ENABLED = True
    module.JSON_RESPONSE = True
    module.STREAMABLE_HTTP_ALLOWED_HOSTS = ["mcp.test"]
    module.STREAMABLE_HTTP_ALLOWED_ORIGINS = ["http://mcp.test"]
    return module


def _headers(token: str | None = None):
    headers = {
        "accept": "application/json, text/event-stream",
        "content-type": "application/json",
    }
    if token is not None:
        headers["authorization"] = f"Bearer {token}"
    return headers


def _initialize_request(request_id=1):
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "ragflow-test", "version": "1.0"},
        },
    }


@asynccontextmanager
async def _client_for(mcp_server):
    starlette_app = mcp_server.create_starlette_app()
    transport = httpx.ASGITransport(app=starlette_app)
    async with (
        starlette_app.router.lifespan_context(starlette_app),
        httpx.AsyncClient(transport=transport, base_url="http://mcp.test") as client,
    ):
        yield client


@pytest.mark.asyncio
async def test_host_streamable_http_rejects_initialize_without_api_key(mcp_server):
    async with _client_for(mcp_server) as client:
        response = await client.post("/mcp", headers=_headers(), json=_initialize_request())

    assert response.status_code == 401
    assert response.json() == {"error": "Missing or invalid authorization header"}


@pytest.mark.asyncio
async def test_host_streamable_http_rejects_untrusted_host(mcp_server):
    headers = _headers("ragflow-rk-test")
    headers["host"] = "untrusted.test"

    async with _client_for(mcp_server) as client:
        response = await client.post("/mcp", headers=headers, json=_initialize_request())

    assert response.status_code == 421
    assert response.text == "Invalid Host header"


@pytest.mark.asyncio
async def test_host_streamable_http_rejects_untrusted_origin(mcp_server):
    headers = _headers("ragflow-rk-test")
    headers["origin"] = "http://untrusted.test"

    async with _client_for(mcp_server) as client:
        response = await client.post("/mcp", headers=headers, json=_initialize_request())

    assert response.status_code == 403
    assert response.text == "Invalid Origin header"


@pytest.mark.asyncio
async def test_host_streamable_http_uses_api_key_from_each_tools_request(monkeypatch, mcp_server):
    received_api_keys = []

    async def list_datasets(_connector, *, api_key, **_kwargs):
        received_api_keys.append(api_key)
        return '{"description":"Human resources","id":"dataset-1"}'

    monkeypatch.setattr(mcp_server.RAGFlowConnector, "list_datasets", list_datasets)

    async with _client_for(mcp_server) as client:
        initialize_response = await client.post(
            "/mcp",
            headers=_headers("ragflow-rk-initialize-only"),
            json=_initialize_request(),
        )
        tools_response = await client.post(
            "/mcp",
            headers=_headers("ragflow-rk-tools-request"),
            json={"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        )

    assert initialize_response.status_code == 200
    assert initialize_response.json()["result"]["protocolVersion"] == "2025-06-18"
    assert tools_response.status_code == 200
    assert [tool["name"] for tool in tools_response.json()["result"]["tools"]] == ["ragflow_retrieval"]
    assert received_api_keys == ["ragflow-rk-tools-request"]


@pytest.mark.asyncio
async def test_host_streamable_http_does_not_reuse_initialize_api_key(monkeypatch, mcp_server):
    async def list_datasets(_connector, *, api_key, **_kwargs):
        raise AssertionError(f"unexpected backend call with {api_key=}")

    monkeypatch.setattr(mcp_server.RAGFlowConnector, "list_datasets", list_datasets)

    async with _client_for(mcp_server) as client:
        initialize_response = await client.post(
            "/mcp",
            headers=_headers("ragflow-rk-initialize-only"),
            json=_initialize_request(),
        )
        tools_response = await client.post(
            "/mcp",
            headers=_headers(),
            json={"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        )

    assert initialize_response.status_code == 200
    assert tools_response.status_code == 401
    assert tools_response.json() == {"error": "Missing or invalid authorization header"}
