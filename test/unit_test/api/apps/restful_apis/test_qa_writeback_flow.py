"""HTTP -> application service -> real SQLite ledger; model/index boundaries are isolated."""

import asyncio
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

from test.unit_test.api.apps.restful_apis import test_qa_writeback_http as http_tests

ROOT = Path(__file__).resolve().parents[5]
HEADERS, PAYLOAD, URL = http_tests.HEADERS, http_tests.PAYLOAD, http_tests.URL
setup = http_tests.setup


def _load_test_module(name, relative_path):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def flow(setup, tmp_path, monkeypatch):
    client, state, _, _ = setup
    sql_tests = _load_test_module("qa_flow_sql_fixture", "test/unit_test/api/db/services/test_qa_writeback_store.py")
    sql_fixture = sql_tests.setup.__wrapped__(tmp_path, monkeypatch)
    store, ledger, document, kb_model, _, app_service, _ = next(sql_fixture)
    try:
        runtime_tests = _load_test_module("qa_flow_runtime_fixture", "test/unit_test/api/apps/services/test_qa_writeback_service.py")
        runtime = runtime_tests.Runtime()
        service = app_service.QAWritebackService(store=store, runtime=runtime)
        state.kb.tenant_id = "tenant"
        route_globals = client.app.view_functions["qa.create_qa"].__wrapped__.__globals__
        monkeypatch.setitem(route_globals, "QAWritebackService", lambda: service)
        monkeypatch.setitem(route_globals, "QAWritebackError", app_service.QAWritebackError)
        monkeypatch.setitem(route_globals, "normalize_request", app_service.normalize_request)
        yield SimpleNamespace(client=client, runtime=runtime, ledger=ledger, document=document, kb=kb_model)
    finally:
        # Resume the fixture so its post-yield database cleanup runs normally.
        next(sql_fixture, None)


async def _post(flow, payload=None, headers=None):
    response = await flow.client.post(URL, headers=HEADERS if headers is None else headers, json=PAYLOAD if payload is None else payload)
    return response.status_code, await response.get_json()


async def test_http_write_and_duplicate_commit_one_chunk(flow):
    status, first = await _post(flow)
    retry_status, duplicate = await _post(flow)
    assert status == retry_status == 200
    assert first["code"] == duplicate["code"] == 0
    assert first["data"]["created"] is True and duplicate["data"]["created"] is False
    chunk_id = first["data"]["chunk_id"]
    assert chunk_id == duplicate["data"]["chunk_id"]
    assert flow.runtime.chunks[chunk_id]["content_with_weight"] == "问题：问题\t回答：第一行\n第二行"
    assert flow.runtime.chunks[chunk_id]["q_2_vec"] == [0.1, 0.2]
    assert flow.runtime.embedded_question == PAYLOAD["question"]
    assert flow.runtime.extract_calls == flow.runtime.embed_calls == flow.runtime.insert_calls == 1
    assert flow.document.get_by_id("doc").chunk_num == flow.kb.get_by_id("kb").chunk_num == 1
    assert flow.document.get_by_id("doc").token_num == flow.kb.get_by_id("kb").token_num == 7
    assert flow.ledger.select().count() == 1 and flow.ledger.get().completed


@pytest.mark.parametrize("manual", [True, False])
async def test_http_keyword_precedence_and_extraction_failure(flow, manual):
    flow.runtime.keyword_output = RuntimeError("simulated chat failure")
    payload = {**PAYLOAD, "important_keywords": ["电源", " 电源 "]} if manual else PAYLOAD
    status, result = await _post(flow, payload)
    assert status == 200 and result["code"] == 0
    assert result["data"]["keyword_status"] == ("provided" if manual else "failed")
    assert result["data"]["important_keywords"] == (["电源"] if manual else [])
    assert flow.runtime.extract_calls == (0 if manual else 1)
    assert flow.document.get_by_id("doc").chunk_num == 1


async def test_http_conflicting_content_does_not_overwrite(flow):
    await _post(flow)
    status, conflict = await _post(flow, {**PAYLOAD, "answer": "修改后的答案"})
    assert status == 409 and conflict["data"]["error_code"] == "IDEMPOTENCY_CONFLICT"
    assert conflict["data"]["retryable"] is False
    assert flow.runtime.embed_calls == flow.runtime.insert_calls == 1
    assert flow.document.get_by_id("doc").chunk_num == 1


async def test_http_embedding_failure_can_retry_same_request(flow):
    flow.runtime.embed_error = True
    status, failed = await _post(flow)
    assert status == 503 and failed["data"]["error_code"] == "EMBEDDING_UNAVAILABLE"
    assert failed["data"]["retryable"] is True
    assert not flow.runtime.chunks and not flow.ledger.get().completed
    assert flow.document.get_by_id("doc").chunk_num == flow.kb.get_by_id("kb").chunk_num == 0
    flow.runtime.embed_error = False
    retry_status, result = await _post(flow)
    assert retry_status == 200 and result["data"]["created"] is True
    assert flow.ledger.get().completed and flow.document.get_by_id("doc").chunk_num == 1


@pytest.mark.parametrize("failure", ["lost_index_response", "sql_failure"])
async def test_http_partial_write_retry_preserves_prepared_data_and_counts(flow, failure):
    database = flow.ledger._meta.database
    if failure == "lost_index_response":
        flow.runtime.ambiguous_insert = True
    else:
        database.execute_sql("CREATE TRIGGER reject_kb_update BEFORE UPDATE ON knowledgebase BEGIN SELECT RAISE(ABORT, 'simulated failure'); END")
    status, failed = await _post(flow)
    assert status == 503 and failed["data"]["retryable"] is True
    assert len(flow.runtime.chunks) == 1 and not flow.ledger.get().completed
    assert flow.document.get_by_id("doc").chunk_num == flow.kb.get_by_id("kb").chunk_num == 0
    if failure == "lost_index_response":
        flow.runtime.ambiguous_insert = False
    else:
        database.execute_sql("DROP TRIGGER reject_kb_update")
    retry_status, resumed = await _post(flow)
    assert retry_status == 200 and resumed["data"]["created"] is False
    assert flow.runtime.extract_calls == flow.runtime.embed_calls == flow.runtime.insert_calls == 1
    assert flow.document.get_by_id("doc").chunk_num == flow.kb.get_by_id("kb").chunk_num == 1
    assert flow.ledger.get().completed


async def test_http_concurrent_duplicate_is_processing(flow):
    flow.runtime.hold = asyncio.Event()
    first = asyncio.create_task(_post(flow))
    try:
        await asyncio.wait_for(flow.runtime.started.wait(), timeout=5)
        status, second = await _post(flow)
        assert status == 409 and second["data"]["error_code"] == "REQUEST_PROCESSING"
        assert second["data"]["retryable"] is True
    finally:
        flow.runtime.hold.set()
        first_status, _ = await asyncio.wait_for(first, timeout=5)
    assert first_status == 200 and flow.document.get_by_id("doc").chunk_num == 1
    assert flow.runtime.embed_calls == flow.runtime.insert_calls == 1


async def test_http_removed_chunk_is_not_recreated(flow):
    _, result = await _post(flow)
    del flow.runtime.chunks[result["data"]["chunk_id"]]
    status, missing = await _post(flow)
    assert status == 409 and missing["data"]["error_code"] == "CHUNK_REMOVED"
    assert not flow.runtime.chunks and flow.runtime.insert_calls == 1


@pytest.mark.parametrize("headers,status", [({}, 401), ({"Authorization": "Bearer readonly-key"}, 403)])
async def test_http_denied_request_never_reaches_ledger(flow, headers, status):
    actual_status, _ = await _post(flow, headers=headers)
    assert actual_status == status
    assert flow.ledger.select().count() == 0 and not flow.runtime.chunks
