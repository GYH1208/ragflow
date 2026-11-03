import importlib.util
import json
from pathlib import Path

import pytest


@pytest.fixture
def service(monkeypatch, tmp_path):
    path = Path(__file__).resolve().parents[5] / "api/db/services/mcp_analytics_service.py"
    spec = importlib.util.spec_from_file_location("mcp_analytics_unit", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    counters = tmp_path / "counts.json"
    monkeypatch.setenv("RAGFLOW_MCP_ANALYTICS_COUNTS_FILE", str(counters))
    return module.MCPAnalyticsService, counters


def test_aggregates_real_counters_and_preserves_unknown_tools(service):
    reader, path = service
    path.write_text(
        json.dumps(
            {
                "ragflow_retrieval": 73,
                "search_documents": 79,
                "get_document_chunks": 69,
                "get_chunk_image": 2,
                "list_knowledge_bases": 1,
                "list_documents": 5,
                "export_document_catalog": 2,
                "future_tool": 3,
            }
        )
    )
    original = path.read_bytes()
    result = reader.dashboard()
    assert result["available"] is True
    assert result["total_count"] == 234
    assert result["categories"] == {"retrieval": 152, "content": 69, "other": 13}
    assert result["tools"][0] == {"name": "search_documents", "count": 79}
    assert {item["name"]: item["count"] for item in result["tools"]}["future_tool"] == 3
    assert result["updated_at"].endswith("+00:00")
    assert path.read_bytes() == original


def test_reads_latest_snapshot_without_caching(service):
    reader, path = service
    path.write_text('{"search_documents": 1}')
    assert reader.dashboard()["total_count"] == 1
    path.write_text('{"search_documents": 2}')
    assert reader.dashboard()["total_count"] == 2


def test_zero_is_available_and_known_tools_are_included(service):
    reader, path = service
    path.write_text("{}")
    result = reader.dashboard()
    assert result["available"] is True
    assert result["total_count"] == 0
    assert len(result["tools"]) == 7
    assert all(item["count"] == 0 for item in result["tools"])


@pytest.mark.parametrize("content", [None, "bad json", "[]", "null", '{"a": -1}', '{"a": true}', '{"a": 1.5}', '{"a": "4"}'])
def test_unavailable_counters_never_appear_as_zero(service, content):
    reader, path = service
    if content is not None:
        path.write_text(content)
    result = reader.dashboard()
    assert result == {"available": False}
    assert not path.exists() if content is None else path.read_text() == content
