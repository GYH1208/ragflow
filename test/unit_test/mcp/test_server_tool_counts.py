import asyncio
import importlib.util
import json
import threading
from pathlib import Path

import pytest


@pytest.fixture
def server():
    path = Path(__file__).resolve().parents[3] / "mcp/server/server.py"
    spec = importlib.util.spec_from_file_location("mcp_tool_counts_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.asyncio
async def test_counts_survive_restart_and_flush_on_shutdown(server, tmp_path):
    path = tmp_path / "counts.json"
    async with server.ToolCallCounts(path, interval=3600) as counts:
        counts.increment("ragflow_retrieval")
        counts.increment("ragflow_retrieval")
        counts.increment("search_documents")
    assert json.loads(path.read_text()) == {
        "ragflow_retrieval": 2,
        "search_documents": 1,
        "get_document_chunks": 0,
        "get_chunk_image": 0,
    }
    async with server.ToolCallCounts(path, interval=3600) as counts:
        counts.increment("ragflow_retrieval")
    assert json.loads(path.read_text())["ragflow_retrieval"] == 3


@pytest.mark.asyncio
async def test_periodic_flush_preserves_increments_during_write(server, tmp_path, monkeypatch):
    path = tmp_path / "counts.json"
    async with server.ToolCallCounts(path, interval=0.01) as counts:
        original = counts._write
        entered, release = threading.Event(), threading.Event()

        def slow_write(snapshot):
            entered.set()
            assert release.wait(3)
            original(snapshot)

        monkeypatch.setattr(counts, "_write", slow_write)
        counts.increment("ragflow_retrieval")
        try:
            assert await asyncio.to_thread(entered.wait, 3)
            counts.increment("ragflow_retrieval")
        finally:
            release.set()
    assert json.loads(path.read_text())["ragflow_retrieval"] == 2


@pytest.mark.asyncio
async def test_failed_write_keeps_previous_file_and_retries(server, tmp_path, monkeypatch):
    path = tmp_path / "counts.json"
    async with server.ToolCallCounts(path, interval=3600) as counts:
        counts.increment("search_documents")
        original = server.os.replace

        def fail(*args):
            raise OSError("disk unavailable")

        with monkeypatch.context() as patch:
            patch.setattr(server.os, "replace", fail)
            await counts.flush()
            assert json.loads(path.read_text())["search_documents"] == 0
        assert server.os.replace is original
        await counts.flush()
        assert json.loads(path.read_text())["search_documents"] == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("content", ["{", '{"ragflow_retrieval": -1}', '{"ragflow_retrieval": true}'])
async def test_invalid_file_is_not_silently_reset(server, tmp_path, content):
    path = tmp_path / "counts.json"
    path.write_text(content)
    with pytest.raises(ValueError):
        async with server.ToolCallCounts(path):
            pass
    assert path.read_text() == content


@pytest.mark.asyncio
async def test_second_writer_cannot_overwrite_counts(server, tmp_path):
    path = tmp_path / "counts.json"
    async with server.ToolCallCounts(path) as counts:
        counts.increment("get_chunk_image")
        with pytest.raises(BlockingIOError):
            async with server.ToolCallCounts(path):
                pass
    assert json.loads(path.read_text())["get_chunk_image"] == 1


@pytest.mark.asyncio
async def test_dispatch_counts_tools_including_errors_but_not_list_or_unknown(server, tmp_path):
    class Connector:
        async def retrieval(self, **kwargs):
            return []

        async def search_documents(self, **kwargs):
            raise RuntimeError("backend unavailable")

        async def get_document_chunks(self, **kwargs):
            return []

        async def get_chunk_image(self, **kwargs):
            return []

    path = tmp_path / "counts.json"
    async with server.ToolCallCounts(path) as counts:
        server.TOOL_CALL_COUNTS = counts
        connector = Connector()
        await server.list_tools.__wrapped__(connector=connector, api_key="test")
        for name in ("ragflow_retrieval", "get_document_chunks", "get_chunk_image"):
            await server.call_tool.__wrapped__(name, {}, connector=connector, api_key="test")
        with pytest.raises(RuntimeError):
            await server.call_tool.__wrapped__("search_documents", {}, connector=connector, api_key="test")
        with pytest.raises(ValueError):
            await server.call_tool.__wrapped__("unknown", {}, connector=connector, api_key="test")
    assert json.loads(path.read_text()) == {
        "ragflow_retrieval": 1,
        "search_documents": 1,
        "get_document_chunks": 1,
        "get_chunk_image": 1,
    }


@pytest.mark.asyncio
async def test_application_lifespan_owns_counter(server, tmp_path):
    path = tmp_path / "9396.json"
    server.TOOL_COUNTS_FILE = path
    server.TRANSPORT_SSE_ENABLED = False
    app = server.create_starlette_app()
    async with app.router.lifespan_context(app):
        server.TOOL_CALL_COUNTS.increment("get_document_chunks")
    assert json.loads(path.read_text())["get_document_chunks"] == 1
    assert server.TOOL_CALL_COUNTS is None
