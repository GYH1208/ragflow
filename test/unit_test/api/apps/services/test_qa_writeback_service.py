"""Writeback behavior with provider/storage boundaries isolated from live services."""

import asyncio
import copy
import importlib.util
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.fixture
def module():
    path = Path(__file__).resolve().parents[5] / "api/apps/services/qa_writeback_service.py"
    spec = importlib.util.spec_from_file_location("qa_writeback_under_test", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


class Store:
    def __init__(self, mod):
        self.mod = mod
        self.records = {}
        self.count = 0
        self.fail_finish = False

    def claim(self, identity, fingerprint, chunk_id):
        key = identity["id"]
        row = self.records.setdefault(key, {**identity, "request_hash": fingerprint, "chunk_id": chunk_id, "prepared": None, "completed": False, "lease_owner": ""})
        if row["request_hash"] != fingerprint:
            raise self.mod.QAWritebackError(409, 409, "IDEMPOTENCY_CONFLICT", "Different content for this external ID.")
        if row["completed"]:
            return copy.deepcopy(row), ""
        if row["lease_owner"]:
            raise self.mod.QAWritebackError(409, 106, "REQUEST_PROCESSING", "Processing.", True)
        row["lease_owner"] = "owner"
        return copy.deepcopy(row), "owner"

    def prepare(self, key, owner, prepared):
        row = self.records[key]
        assert row["lease_owner"] == owner
        row["prepared"] = copy.deepcopy(prepared)

    def finish(self, key, owner, publish):
        created = publish()
        if self.fail_finish:
            raise OSError("database unavailable")
        row = self.records[key]
        assert row["lease_owner"] == owner
        if not row["completed"]:
            self.count += 1
            row["completed"] = True
        return created

    def release(self, key, owner):
        if self.records[key]["lease_owner"] == owner:
            self.records[key]["lease_owner"] = ""


class Runtime:
    def __init__(self):
        self.chunks = {}
        self.extract_calls = 0
        self.embed_calls = 0
        self.insert_calls = 0
        self.keyword_output = "无法开机，电源；电源\n指示灯"
        self.embed_error = False
        self.ambiguous_insert = False
        self.hold = None
        self.started = asyncio.Event()

    async def extract_keywords(self, kb, doc, content, top_n):
        self.extract_calls += 1
        self.started.set()
        self.keyword_content = content
        self.top_n = top_n
        if self.hold:
            await self.hold.wait()
        if isinstance(self.keyword_output, Exception):
            raise self.keyword_output
        return self.keyword_output

    async def embed(self, kb, doc, question):
        self.embed_calls += 1
        self.embedded_question = question
        if self.embed_error:
            raise OSError("provider failure")
        return [0.1, 0.2], 7

    def build_chunk(self, kb, doc, payload, chunk_id, keywords, vector):
        return {"id": chunk_id, "doc_id": doc.id, "kb_id": kb.id, "content_with_weight": f"问题：{payload['question']}\t回答：{payload['answer']}", "important_kwd": keywords, "q_2_vec": vector}

    def get_chunk(self, kb, chunk_id):
        return copy.deepcopy(self.chunks.get(chunk_id))

    def insert_chunk(self, kb, doc, chunk):
        self.insert_calls += 1
        self.chunks[chunk["id"]] = copy.deepcopy(chunk)
        if self.ambiguous_insert:
            raise OSError("response lost after index accepted the chunk")


@pytest.fixture
def setup(module):
    store = Store(module)
    runtime = Runtime()
    service = module.QAWritebackService(store=store, runtime=runtime)
    kb = SimpleNamespace(id="kb", tenant_id="tenant", language="Chinese")
    doc = SimpleNamespace(id="doc", kb_id="kb", name="External QA", parser_id="qa")
    payload = {"question": "电脑无法开机怎么办？", "answer": "第一行\n第二行", "source_system": "it_service", "external_id": "IT-0001"}
    return service, store, runtime, kb, doc, payload


@pytest.mark.parametrize(
    "change",
    [
        {"question": " "},
        {"answer": ""},
        {"question": 123},
        {"answer": None},
        {"source_system": "bad/source"},
        {"external_id": ""},
        {"external_id": "x" * 129},
        {"question": "x" * 2001},
        {"answer": "x" * 20001},
        {"keyword_top_n": 0},
        {"keyword_top_n": 11},
        {"keyword_top_n": True},
        {"keyword_top_n": "5"},
        {"important_keywords": "word"},
        {"important_keywords": [3]},
        {"important_keywords": ["x" * 101]},
        {"important_keywords": ["x"] * 21},
    ],
)
async def test_invalid_requests_do_not_call_dependencies(module, setup, change):
    service, store, runtime, kb, doc, payload = setup
    with pytest.raises(module.QAWritebackError) as exc:
        await service.create(kb, doc, {**payload, **change})
    assert exc.value.error_code == "INVALID_ARGUMENT"
    assert not store.records and runtime.extract_calls == runtime.embed_calls == 0


async def test_generated_keywords_question_embedding_and_content_preservation(setup):
    service, store, runtime, kb, doc, payload = setup
    result = await service.create(kb, doc, {**payload, "answer": " 第一行\r\n第二行 "})
    assert result["created"] is True
    assert result["important_keywords"] == ["无法开机", "电源", "指示灯"]
    assert result["keyword_status"] == "generated"
    assert runtime.embedded_question == payload["question"]
    assert "第一行\n第二行" in runtime.keyword_content
    assert "第一行\n第二行" in runtime.chunks[result["chunk_id"]]["content_with_weight"]
    assert runtime.top_n == 5 and store.count == 1


async def test_explicit_keywords_skip_model_and_ignore_order_on_retries(setup):
    service, store, runtime, kb, doc, payload = setup
    result = await service.create(kb, doc, {**payload, "important_keywords": [" 电源 ", "开机", "电源"]})
    retry = await service.create(kb, doc, {**payload, "important_keywords": ["开机", "电源"]})
    assert result["keyword_status"] == "provided" and runtime.extract_calls == 0
    assert retry["chunk_id"] == result["chunk_id"] and retry["created"] is False
    assert runtime.embed_calls == 1 and store.count == 1


@pytest.mark.parametrize("output", [RuntimeError("chat unavailable"), "", " \n", "**ERROR** unavailable"])
async def test_keyword_failure_does_not_block_writeback(setup, output):
    service, store, runtime, kb, doc, payload = setup
    runtime.keyword_output = output
    result = await service.create(kb, doc, payload)
    assert result["keyword_status"] == "failed" and result["important_keywords"] == []
    assert store.count == 1


async def test_keyword_timeout_degrades(module, setup, monkeypatch):
    service, store, runtime, kb, doc, payload = setup
    monkeypatch.setattr(module, "KEYWORD_TIMEOUT_SECONDS", 0.01)
    runtime.hold = asyncio.Event()
    result = await service.create(kb, doc, payload)
    assert result["keyword_status"] == "failed" and store.count == 1


async def test_empty_keywords_enable_auto_and_top_n_caps_generated_result(setup):
    service, _, runtime, kb, doc, payload = setup
    result = await service.create(kb, doc, {**payload, "important_keywords": [], "keyword_top_n": 2})
    assert runtime.top_n == 2 and result["important_keywords"] == ["无法开机", "电源"]


async def test_changed_content_conflicts_before_models(module, setup):
    service, _, runtime, kb, doc, payload = setup
    await service.create(kb, doc, payload)
    with pytest.raises(module.QAWritebackError) as exc:
        await service.create(kb, doc, {**payload, "answer": "different"})
    assert exc.value.error_code == "IDEMPOTENCY_CONFLICT"
    assert runtime.embed_calls == runtime.extract_calls == 1


async def test_identity_scopes_source_and_document(setup):
    service, store, _, kb, doc, payload = setup
    a = await service.create(kb, doc, payload)
    b = await service.create(kb, doc, {**payload, "source_system": "other"})
    other_doc = SimpleNamespace(id="other", kb_id=kb.id, name="Other QA")
    c = await service.create(kb, other_doc, payload)
    assert len({a["chunk_id"], b["chunk_id"], c["chunk_id"]}) == 3 and store.count == 3


async def test_embedding_failure_releases_claim_for_retry(module, setup):
    service, store, runtime, kb, doc, payload = setup
    runtime.embed_error = True
    with pytest.raises(module.QAWritebackError) as exc:
        await service.create(kb, doc, payload)
    assert exc.value.error_code == "EMBEDDING_UNAVAILABLE"
    assert store.count == 0 and not runtime.chunks
    runtime.embed_error = False
    assert (await service.create(kb, doc, payload))["created"] is True


@pytest.mark.parametrize("failure", ["ambiguous_insert", "fail_finish"])
async def test_partial_write_retry_recovers_without_repeating_models(module, setup, failure):
    service, store, runtime, kb, doc, payload = setup
    target = runtime if failure == "ambiguous_insert" else store
    setattr(target, failure, True)
    with pytest.raises(module.QAWritebackError) as exc:
        await service.create(kb, doc, payload)
    assert exc.value.retryable and store.count == 0 and len(runtime.chunks) == 1
    setattr(target, failure, False)
    result = await service.create(kb, doc, payload)
    assert result["created"] is False and store.count == 1
    assert runtime.embed_calls == runtime.extract_calls == runtime.insert_calls == 1


async def test_concurrent_duplicate_is_processing(module, setup):
    service, store, runtime, kb, doc, payload = setup
    runtime.hold = asyncio.Event()
    first = asyncio.create_task(service.create(kb, doc, payload))
    await runtime.started.wait()
    with pytest.raises(module.QAWritebackError) as exc:
        await service.create(kb, doc, payload)
    assert exc.value.error_code == "REQUEST_PROCESSING"
    runtime.hold.set()
    await first
    assert store.count == 1


async def test_cancellation_releases_claim(module, setup):
    service, store, runtime, kb, doc, payload = setup
    runtime.hold = asyncio.Event()
    task = asyncio.create_task(service.create(kb, doc, payload))
    await runtime.started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not next(iter(store.records.values()))["lease_owner"]
    assert runtime.embed_calls == 0


async def test_completed_but_removed_chunk_is_not_resurrected(module, setup):
    service, store, runtime, kb, doc, payload = setup
    await service.create(kb, doc, payload)
    runtime.chunks.clear()
    with pytest.raises(module.QAWritebackError) as exc:
        await service.create(kb, doc, payload)
    assert exc.value.error_code == "CHUNK_REMOVED"
    assert runtime.insert_calls == 1 and store.count == 1


async def test_empty_insert_error_list_without_actual_chunk_is_not_success(module, setup):
    service, store, runtime, kb, doc, payload = setup
    runtime.insert_chunk = lambda *args: None
    with pytest.raises(module.QAWritebackError) as exc:
        await service.create(kb, doc, payload)
    assert exc.value.error_code == "STORAGE_UNAVAILABLE"
    assert store.count == 0
    assert next(iter(store.records.values()))["prepared"] is not None


async def test_cancellation_during_index_write_keeps_lease_until_worker_settles(module, setup):
    service, store, runtime, kb, doc, payload = setup
    started = asyncio.Event()
    release = threading.Event()
    loop = asyncio.get_running_loop()
    original_insert = runtime.insert_chunk

    def blocked_insert(*args):
        loop.call_soon_threadsafe(started.set)
        assert release.wait(3)
        return original_insert(*args)

    runtime.insert_chunk = blocked_insert
    task = asyncio.create_task(service.create(kb, doc, payload))
    try:
        await asyncio.wait_for(started.wait(), 1)
        task.cancel()
        await asyncio.sleep(0.02)
        assert not task.done(), "Cancellation must not abandon a live index writer"
        with pytest.raises(module.QAWritebackError) as exc:
            await service.create(kb, doc, payload)
        assert exc.value.error_code == "REQUEST_PROCESSING"
    finally:
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert store.count == 1
    retry = await service.create(kb, doc, payload)
    assert retry["created"] is False and runtime.insert_calls == 1
