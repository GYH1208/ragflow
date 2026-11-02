"""WorkBuddy catalog start/poll/download and complete workbook contracts."""

import asyncio
import hashlib
import io
import json
import logging

import httpx
import pytest
from openpyxl import load_workbook

from test.unit_test.mcp.test_server_streamable_http import _client_for, _initialize_request, _load_mcp_server


@pytest.fixture
def server():
    module = _load_mcp_server()
    module.MODE = module.LaunchMode.HOST
    module.AUTH_MODE = "workbuddy"
    module.TRANSPORT_SSE_ENABLED = False
    module.STREAMABLE_HTTP_ALLOWED_HOSTS = ["mcp.test"]
    return module


def headers(actor="employee-a"):
    return {"accept": "application/json, text/event-stream", "x-oneid-access-token": actor}


async def call(client, arguments, actor="employee-a"):
    response = await client.post("/mcp", headers=headers(actor), json={"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "export_document_catalog", "arguments": arguments}})
    assert response.status_code == 200
    result = response.json()["result"]
    if result.get("isError"):
        return result
    return json.loads(result["content"][0]["text"])


class Backend:
    def __init__(self, size=205):
        self.size = size
        self.pages = []
        self.revoked = False
        self.changed = False
        self.duplicate = False
        self.requests = []

    def __call__(self, request):
        self.requests.append(request)
        if self.revoked:
            return httpx.Response(403, json={"code": 403, "message": "Access denied"})
        if request.url.path.endswith("/datasets"):
            return httpx.Response(200, json={"code": 0, "total": 1, "data": [{"id": "kb-a", "name": "声学文献库", "description": "", "document_count": self.size}]})
        assert request.url.path == "/api/v1/datasets/kb-a/documents"
        assert request.url.params["orderby"] == "id"
        page, size = int(request.url.params["page"]), int(request.url.params["page_size"])
        self.pages.append(page)
        docs = [
            {
                "id": f"doc-{i:06}",
                "dataset_id": "kb-a",
                "name": f"声学论文{i}.pdf",
                "type": "pdf",
                "run": "DONE",
                "chunk_count": 2,
                "update_date": "2026-11-02",
                "location": "storage/object",
                "path": "声学/论文.pdf" if i == 0 else "",
            }
            for i in range((page - 1) * size, min(page * size, self.size))
        ]
        if page == 1 and docs:
            docs[0]["name"] = '=HYPERLINK("https://untrusted.invalid","点击")'
        if self.duplicate and page == 2 and docs:
            docs[0]["id"] = "doc-000000"
        return httpx.Response(200, json={"code": 0, "data": {"total": self.size + int(self.changed and page > 1), "docs": docs}})


def connect_backend(monkeypatch, server, backend):
    async def get_client(self):
        if self._async_client is None:
            self._async_client = httpx.AsyncClient(transport=httpx.MockTransport(backend))
        return self._async_client

    monkeypatch.setattr(server.RAGFlowConnector, "_get_client", get_client)


async def finish_job(server, export_id):
    await server.CATALOG_EXPORTS.jobs[export_id]["task"]


@pytest.mark.asyncio
@pytest.mark.parametrize("size", [0, 205, 5000])
async def test_mcp_export_downloads_complete_workbook_without_full_catalog_in_context(server, monkeypatch, size):
    backend = Backend(size)
    connect_backend(monkeypatch, server, backend)
    async with _client_for(server) as client:
        await client.post("/mcp", headers=headers(), json=_initialize_request())
        started = await call(client, {"dataset_id": "kb-a"})
        assert started["status"] == "processing"
        assert "download_url" not in started
        await finish_job(server, started["export_id"])
        result = await call(client, {"dataset_id": "kb-a", "export_id": started["export_id"]})
        assert result["status"] == "ready", result
        assert result["document_count"] == size
        assert len(result["preview"]) <= 5
        assert len(json.dumps(result, ensure_ascii=False)) < 5000
        assert "employee-a" not in json.dumps(result)
        download = await client.get(result["download_url"])
        assert download.status_code == 200
        assert download.headers["content-type"].startswith("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        assert "attachment" in download.headers["content-disposition"]
        assert download.headers["cache-control"] == "private, no-store"
        assert hashlib.sha256(download.content).hexdigest() == result["sha256"]
        workbook = load_workbook(io.BytesIO(download.content))
        sheet = workbook["文献目录"]
        assert sheet.max_row == size + 1
        assert sheet.freeze_panes == "A2"
        assert sheet.auto_filter.ref == f"A1:H{max(1, size + 1)}"
        assert workbook["导出说明"]["B2"].value == "声学文献库"
        assert workbook["导出说明"]["B4"].value == size
        if size:
            assert sheet["B2"].value.startswith("=HYPERLINK")
            assert sheet["B2"].data_type == "s"
            assert sheet["F2"].value == "声学/论文.pdf"
            assert sheet["F3"].value is None
            assert sheet.cell(size + 1, 7).value == f"doc-{size - 1:06}"
        assert backend.pages == list(range(1, max(1, (size + 99) // 100) + 1))
        assert all(r.headers["x-oneid-access-token"] == "employee-a" for r in backend.requests)
        # File capabilities must not be enumerable or readable after expiry.
        assert (await client.get("/mcp/catalog-download/not-a-valid-token")).status_code == 404
        server.CATALOG_EXPORTS.jobs[started["export_id"]]["expires_at"] = 0
        assert (await client.get(result["download_url"])).status_code == 404


@pytest.mark.asyncio
async def test_export_poll_is_bound_to_caller_and_rechecks_permissions(server, monkeypatch):
    backend = Backend(2)
    connect_backend(monkeypatch, server, backend)
    async with _client_for(server) as client:
        first = await call(client, {"dataset_id": "kb-a"})
        await finish_job(server, first["export_id"])
        denied = await call(client, {"dataset_id": "kb-a", "export_id": first["export_id"]}, actor="employee-b")
        assert denied["isError"] is True
        assert "download_url" not in str(denied)
        backend.revoked = True
        denied = await call(client, {"dataset_id": "kb-a", "export_id": first["export_id"]})
        assert denied["isError"] is True
        denied = await call(client, {"dataset_id": "kb-a"})
        assert denied["isError"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize("problem", ["changed", "duplicate", "too_many"])
async def test_export_never_publishes_partial_or_unbounded_catalog(server, monkeypatch, problem):
    backend = Backend(205 if problem != "too_many" else 50001)
    setattr(backend, problem, True)
    connect_backend(monkeypatch, server, backend)
    async with _client_for(server) as client:
        first = await call(client, {"dataset_id": "kb-a"})
        await finish_job(server, first["export_id"])
        result = await call(client, {"dataset_id": "kb-a", "export_id": first["export_id"]})
        assert result["status"] == "failed"
        assert "download_url" not in result
        assert server.CATALOG_EXPORTS.jobs[first["export_id"]].get("data") is None
        if problem == "too_many":
            assert backend.pages == [1]


@pytest.mark.asyncio
async def test_unknown_dataset_and_invalid_arguments_do_not_export(server, monkeypatch):
    backend = Backend()
    connect_backend(monkeypatch, server, backend)
    async with _client_for(server) as client:
        for arguments in [{}, {"dataset_id": "../secret"}, {"dataset_id": "kb-b"}, {"dataset_id": "kb-a", "export_id": "unknown"}]:
            result = await call(client, arguments)
            assert result["isError"] is True
        assert not backend.pages


@pytest.mark.asyncio
async def test_exports_stop_at_shutdown_and_bound_active_jobs(server, monkeypatch):
    gate = asyncio.Event()
    backend = Backend()

    async def slow_backend(request):
        if request.url.path.endswith("/documents"):
            await gate.wait()
        return backend(request)

    connect_backend(monkeypatch, server, slow_backend)
    async with _client_for(server) as client:
        one = await call(client, {"dataset_id": "kb-a"})
        two = await call(client, {"dataset_id": "kb-a"})
        three = await call(client, {"dataset_id": "kb-a"})
        assert one["status"] == two["status"] == "processing"
        assert three["isError"] is True
        tasks = [job["task"] for job in server.CATALOG_EXPORTS.jobs.values()]
    assert all(task.done() for task in tasks)


def test_workbook_strings_are_literal_and_control_characters_are_safe(server):
    docs = [{"document_id": "doc-a", "name": "=SUM(1,2)\x00", "type": "pdf", "run": "DONE", "update_date": "2026-11-02", "path": "+folder/file"}]
    data = server._build_catalog_workbook("kb-a", "声学\x01", docs, "2026-11-02T08:00:00+00:00")
    workbook = load_workbook(io.BytesIO(data))
    assert workbook["文献目录"]["B2"].value == "=SUM(1,2)"
    assert workbook["文献目录"]["B2"].data_type == "s"
    assert workbook["文献目录"]["F2"].value == "+folder/file"


def test_catalog_download_tokens_are_redacted_from_access_logs(server):
    record = logging.LogRecord("uvicorn.access", logging.INFO, "", 0, '%s - "%s %s HTTP/%s" %d', ("127.0.0.1", "GET", "/mcp/catalog-download/secret-token", "1.1", 200), None)
    assert server.CatalogDownloadLogFilter().filter(record)
    assert "secret-token" not in record.getMessage()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["timeout", "file_size", "total_storage", "empty_page", "revoked_before_publish"])
async def test_export_failure_paths_never_issue_a_download(server, monkeypatch, failure):
    backend = Backend(2)
    dataset_reads = 0

    async def failing_backend(request):
        nonlocal dataset_reads
        if request.url.path.endswith("/datasets"):
            dataset_reads += 1
            if failure == "revoked_before_publish" and dataset_reads == 2:
                return httpx.Response(403, json={"code": 403, "message": "Access denied"})
        if request.url.path.endswith("/documents"):
            if failure == "timeout":
                await asyncio.sleep(1)
            if failure == "empty_page":
                return httpx.Response(200, json={"code": 0, "data": {"total": 2, "docs": []}})
        return backend(request)

    connect_backend(monkeypatch, server, failing_backend)
    if failure == "timeout":
        monkeypatch.setattr(server.CatalogExports, "TIMEOUT", 0.01)
    elif failure == "file_size":
        monkeypatch.setattr(server.CatalogExports, "MAX_FILE_BYTES", 1)
    elif failure == "total_storage":
        monkeypatch.setattr(server.CatalogExports, "MAX_BYTES", 1)
    async with _client_for(server) as client:
        first = await call(client, {"dataset_id": "kb-a"})
        await finish_job(server, first["export_id"])
        result = await call(client, {"dataset_id": "kb-a", "export_id": first["export_id"]})
        assert result["status"] == "failed"
        assert "download_url" not in result
        assert "data" not in server.CATALOG_EXPORTS.jobs[first["export_id"]]


@pytest.mark.asyncio
async def test_export_uses_public_endpoint_and_preserves_counts(server, monkeypatch, tmp_path):
    backend = Backend(2)
    connect_backend(monkeypatch, server, backend)
    monkeypatch.setenv("RAGFLOW_MCP_PUBLIC_URL", "https://catalog.example/prefix/mcp")
    server.TOOL_COUNTS_FILE = tmp_path / "counts.json"
    server.TOOL_COUNTS_FILE.write_text(json.dumps({"ragflow_retrieval": 69}))
    async with _client_for(server) as client:
        first = await call(client, {"dataset_id": "kb-a"})
        await finish_job(server, first["export_id"])
        result = await call(client, {"dataset_id": "kb-a", "export_id": first["export_id"]})
        assert result["download_url"].startswith("https://catalog.example/prefix/mcp/catalog-download/")
        assert server.TOOL_CALL_COUNTS.counts["export_document_catalog"] == 2
    saved = json.loads(server.TOOL_COUNTS_FILE.read_text())
    assert saved["ragflow_retrieval"] == 69
    assert saved["export_document_catalog"] == 2


@pytest.mark.asyncio
async def test_retained_jobs_have_a_capacity_limit_and_expire(server, monkeypatch):
    connect_backend(monkeypatch, server, Backend(0))
    monkeypatch.setattr(server.CatalogExports, "MAX_JOBS", 1)
    async with _client_for(server) as client:
        first = await call(client, {"dataset_id": "kb-a"})
        await finish_job(server, first["export_id"])
        assert (await call(client, {"dataset_id": "kb-a"}))["isError"] is True
        server.CATALOG_EXPORTS.jobs[first["export_id"]]["expires_at"] = 0
        second = await call(client, {"dataset_id": "kb-a"})
        assert second["status"] == "processing"
        await finish_job(server, second["export_id"])
        assert first["export_id"] not in server.CATALOG_EXPORTS.jobs
        # Completed records contain a digest, never the employee's actual ticket.
        assert "employee-a" not in repr(server.CATALOG_EXPORTS.jobs[second["export_id"]])


@pytest.mark.asyncio
async def test_download_rejects_non_ascii_tokens_without_server_error(server):
    async with _client_for(server) as client:
        # Include a ready entry so the comparison is exercised.
        done = asyncio.create_task(asyncio.sleep(0))
        await done
        server.CATALOG_EXPORTS.jobs["job"] = {"status": "ready", "download_token": "secret", "expires_at": float("inf"), "task": done}
        response = await client.get("/mcp/catalog-download/非法")
        assert response.status_code == 404
        del server.CATALOG_EXPORTS.jobs["job"]


@pytest.mark.asyncio
async def test_workbook_timeout_does_not_release_capacity_until_builder_exits(server, monkeypatch):
    import threading

    release = threading.Event()
    entered = threading.Event()
    exited = threading.Event()
    original = server._build_catalog_workbook

    def blocking_builder(*args):
        entered.set()
        try:
            assert release.wait(3)
            return original(*args)
        finally:
            exited.set()

    monkeypatch.setattr(server, "_build_catalog_workbook", blocking_builder)
    monkeypatch.setattr(server.CatalogExports, "TIMEOUT", 0.02)
    monkeypatch.setattr(server.CatalogExports, "MAX_ACTIVE", 1)
    connect_backend(monkeypatch, server, Backend(1))
    async with _client_for(server) as client:
        first = await call(client, {"dataset_id": "kb-a"})
        try:
            assert await asyncio.to_thread(entered.wait, 1)
            await asyncio.sleep(0.05)
            assert not exited.is_set()
            assert server.CATALOG_EXPORTS.jobs[first["export_id"]]["status"] == "processing"
            assert (await call(client, {"dataset_id": "kb-a"}))["isError"] is True
        finally:
            release.set()
            await finish_job(server, first["export_id"])
        assert exited.is_set()
        assert server.CATALOG_EXPORTS.jobs[first["export_id"]]["status"] == "failed"


@pytest.mark.asyncio
async def test_shutdown_waits_for_workbook_builder_cleanup(server, monkeypatch):
    import threading

    release, entered, exited = threading.Event(), threading.Event(), threading.Event()
    original = server._build_catalog_workbook

    def blocking_builder(*args):
        entered.set()
        try:
            assert release.wait(3)
            return original(*args)
        finally:
            exited.set()

    monkeypatch.setattr(server, "_build_catalog_workbook", blocking_builder)
    connect_backend(monkeypatch, server, Backend(1))
    manager = await server.CatalogExports().__aenter__()
    connector = server.RAGFlowConnector("http://backend.test", auth_mode="workbuddy")
    closing = None
    try:
        await manager.request(connector, api_key="employee-a", dataset_id="kb-a", export_id=None, base_url="http://mcp.test/mcp")
        assert await asyncio.to_thread(entered.wait, 1)
        closing = asyncio.create_task(manager.__aexit__(None, None, None))
        await asyncio.sleep(0.05)
        assert not closing.done()
    finally:
        release.set()
        if closing is not None:
            await closing
        else:
            await manager.__aexit__(None, None, None)
        await connector.close()
    assert exited.is_set()
