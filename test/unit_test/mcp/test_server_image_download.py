"""Exercise image file delivery through real MCP and HTTP request handling."""

import asyncio
import base64
import hashlib
import json
import logging
from datetime import UTC, datetime
from types import SimpleNamespace
from urllib.parse import urlsplit

import httpx
import pytest

from test.unit_test.mcp.test_server_chunk_images import _image
from test.unit_test.mcp.test_server_streamable_http import _client_for, _load_mcp_server


@pytest.fixture
def server(monkeypatch):
    monkeypatch.delenv("RAGFLOW_MCP_PUBLIC_URL", raising=False)
    module = _load_mcp_server()
    module.MODE = module.LaunchMode.HOST
    module.AUTH_MODE = "workbuddy"
    module.TRANSPORT_SSE_ENABLED = False
    module.STREAMABLE_HTTP_ALLOWED_HOSTS = ["mcp.test"]
    return module


def connect(server, monkeypatch, *, body=None, mime="image/png", status=200):
    data = _image() if body is None else body
    requests = []

    def backend(request):
        requests.append(request)
        assert request.url.path == "/api/v1/datasets/kb-a/documents/doc-a/chunks/chunk-a/image"
        if request.headers.get("x-oneid-access-token") == "denied-user":
            return httpx.Response(403, json={"code": 403, "message": "Access denied"})
        return httpx.Response(status, content=data, headers={"Content-Type": mime})

    async def get_client(self):
        if self._async_client is None:
            self._async_client = httpx.AsyncClient(transport=httpx.MockTransport(backend))
        return self._async_client

    monkeypatch.setattr(server.RAGFlowConnector, "_get_client", get_client)
    return data, requests


async def call(client, actor="employee-a", auth_mode="workbuddy"):
    headers = {"accept": "application/json, text/event-stream"}
    headers.update({"x-oneid-access-token": actor} if auth_mode == "workbuddy" else {"authorization": f"Bearer {actor}"})
    response = await client.post(
        "/mcp",
        headers=headers,
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "get_chunk_image", "arguments": {"dataset_id": "kb-a", "document_id": "doc-a", "chunk_id": "chunk-a"}},
        },
    )
    assert response.status_code == 200
    return response.json()["result"]


def metadata(result):
    assert not result.get("isError"), result
    return json.loads(result["content"][0]["text"])


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "format,mime,extension", [("PNG", "image/png", "png"), ("JPEG", "image/jpeg", "jpg"), ("GIF", "image/gif", "gif"), ("WEBP", "image/webp", "webp"), ("BMP", "image/bmp", "bmp")]
)
async def test_image_download_preserves_original_bytes_and_native_content(server, monkeypatch, format, mime, extension):
    data, requests = connect(server, monkeypatch, body=_image(format), mime=mime)
    async with _client_for(server) as client:
        result = await call(client)
        info = metadata(result)
        assert info["dataset_id"] == "kb-a" and info["document_id"] == "doc-a" and info["chunk_id"] == "chunk-a"
        assert info["filename"] == f"doc-a_chunk-a.{extension}"
        assert info["mime_type"] == mime
        assert info["size_bytes"] == len(data)
        assert info["sha256"] == hashlib.sha256(data).hexdigest()
        assert 890 < (datetime.fromisoformat(info["expires_at"]) - datetime.now(UTC)).total_seconds() <= 900
        assert "download_url" in info["delivery_instructions"] and "present_files" in info["delivery_instructions"]
        assert info["download_url"].startswith("http://mcp.test/mcp/image-download/")
        assert "employee-a" not in json.dumps(info)
        assert [c["type"] for c in result["content"]] == ["text", "image"]
        assert base64.b64decode(result["content"][1]["data"]) == data
        download = await client.get(info["download_url"])
        assert download.status_code == 200 and download.content == data
        assert download.headers["content-type"] == mime
        assert info["filename"] in download.headers["content-disposition"]
        assert "attachment" in download.headers["content-disposition"]
        assert download.headers["cache-control"] == "private, no-store"
        assert download.headers["x-content-type-options"] == "nosniff"
        assert (await client.head(info["download_url"])).content == b""
        assert (await client.get("/mcp/image-download/unknown")).status_code == 404
        assert (await client.post("/mcp/image-download/unknown")).status_code == 401
        assert len(requests) == 1
        assert requests[0].headers["x-oneid-access-token"] == "employee-a"


@pytest.mark.asyncio
async def test_image_download_requires_fresh_authorization_and_expires(server, monkeypatch):
    connect(server, monkeypatch)
    async with _client_for(server) as client:
        info = metadata(await call(client))
        denied = await call(client, actor="denied-user")
        assert denied["isError"] and "download_url" not in json.dumps(denied)
        assert len(server.IMAGE_DOWNLOADS.images) == 1
        # Like catalog exports, issued capabilities remain usable until expiry.
        assert (await client.get(info["download_url"])).status_code == 200
        token = info["download_url"].rsplit("/", 1)[1]
        server.IMAGE_DOWNLOADS.images[token]["expires_at"] = 0
        assert (await client.get(info["download_url"])).status_code == 404
        assert not server.IMAGE_DOWNLOADS.images
        second = metadata(await call(client))
        assert second["download_url"] != info["download_url"]
        store = server.IMAGE_DOWNLOADS
    assert not store.images
    async with _client_for(server) as client:
        assert (await client.get(second["download_url"])).status_code == 404


@pytest.mark.asyncio
@pytest.mark.parametrize("limit", ["MAX_IMAGES", "MAX_BYTES"])
async def test_full_image_cache_preserves_native_response_and_existing_links(server, monkeypatch, limit):
    data, _ = connect(server, monkeypatch)
    monkeypatch.setattr(server.ImageDownloads, limit, 1 if limit == "MAX_IMAGES" else len(data))
    async with _client_for(server) as client:
        first = metadata(await call(client))
        second_result = await call(client, actor="employee-b")
        second = metadata(second_result)
        assert "download_url" not in second and "capacity" in second["download_error"]
        assert base64.b64decode(second_result["content"][1]["data"]) == data
        assert len(server.IMAGE_DOWNLOADS.images) == 1
        assert (await client.get(first["download_url"])).content == data
        for entry in server.IMAGE_DOWNLOADS.images.values():
            entry["expires_at"] = 0
        assert "download_url" in metadata(await call(client))


@pytest.mark.asyncio
async def test_image_download_uses_configured_public_prefix_and_api_key(server, monkeypatch):
    _, requests = connect(server, monkeypatch)
    server.AUTH_MODE = "api-key"
    monkeypatch.setenv("RAGFLOW_MCP_PUBLIC_URL", "https://public.test/prefix/mcp/")
    async with _client_for(server) as client:
        info = metadata(await call(client, auth_mode="api-key"))
        assert info["download_url"].startswith("https://public.test/prefix/mcp/image-download/")
        assert requests[0].headers["authorization"] == "Bearer employee-a"
        assert (await client.get(urlsplit(info["download_url"]).path.removeprefix("/prefix"))).status_code == 200


@pytest.mark.asyncio
@pytest.mark.parametrize("public_url", ["ftp://private.test/mcp", "https://user:secret@private.test/mcp", "https://private.test/mcp?token=secret"])
async def test_invalid_public_url_preserves_native_image_without_exposing_configuration(server, monkeypatch, public_url):
    connect(server, monkeypatch)
    monkeypatch.setenv("RAGFLOW_MCP_PUBLIC_URL", public_url)
    async with _client_for(server) as client:
        result = await call(client)
        info = metadata(result)
        assert "download_error" in info and "download_url" not in info
        assert "secret" not in json.dumps(info) and "private.test" not in json.dumps(info)
        assert result["content"][1]["type"] == "image"
        assert not server.IMAGE_DOWNLOADS.images


@pytest.mark.asyncio
@pytest.mark.parametrize("problem", ["denied", "oversized", "format"])
async def test_invalid_backend_image_never_creates_download(server, monkeypatch, problem):
    connect(server, monkeypatch, body=b"not an image" if problem == "format" else None)
    if problem == "oversized":
        monkeypatch.setattr(server, "MCP_IMAGE_MAX_BYTES", 1)
    async with _client_for(server) as client:
        result = await call(client, actor="denied-user" if problem == "denied" else "employee-a")
        assert result["isError"] and "download_url" not in json.dumps(result)
        assert not server.IMAGE_DOWNLOADS.images


def test_image_download_tokens_redacted_in_access_logs(server):
    record = logging.LogRecord("uvicorn.access", logging.INFO, "", 0, '%s - "%s %s HTTP/%s" %d', ("127.0.0.1", "GET", "/prefix/mcp/image-download/secret-token?x=1", "1.1", 200), None)
    server.CatalogDownloadLogFilter().filter(record)
    assert "secret-token" not in record.getMessage()


@pytest.mark.asyncio
async def test_parallel_callers_have_distinct_links_and_request_credentials(server, monkeypatch):
    _, requests = connect(server, monkeypatch)
    async with _client_for(server) as client:
        results = await asyncio.gather(call(client, actor="employee-a"), call(client, actor="employee-b"))
        links = [metadata(result)["download_url"] for result in results]
        assert links[0] != links[1]
        assert {request.headers["x-oneid-access-token"] for request in requests} == {"employee-a", "employee-b"}
        for link in links:
            assert (await client.get(link)).status_code == 200


def test_sse_needs_explicit_public_endpoint(server, monkeypatch):
    monkeypatch.setattr(server, "app", SimpleNamespace(request_context=SimpleNamespace(request=SimpleNamespace(url="http://mcp.test/sse"))))
    with pytest.raises(server.ImageDownloadError, match="RAGFLOW_MCP_PUBLIC_URL"):
        server._image_download_base_url()
    monkeypatch.setenv("RAGFLOW_MCP_PUBLIC_URL", "https://public.test/prefix/mcp")
    assert server._image_download_base_url() == "https://public.test/prefix/mcp"


def test_maximum_length_ids_still_produce_portable_filenames(server):
    source = {"document_id": "d" * 128, "chunk_id": "c" * 128}
    info = server.ImageDownloads().publish(_image(), "image/png", source, "http://mcp.test/mcp")
    assert len(info["filename"].encode()) <= 255
