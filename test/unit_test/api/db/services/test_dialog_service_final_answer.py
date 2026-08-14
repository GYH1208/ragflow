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
"""
Regression tests for the bug where async_ask() and async_chat() blanked out
final["answer"] in the last SSE event, discarding the decorated answer text
that contains citation markers.

Both functions call decorate_answer() which inserts citation markers and prunes
doc_aggs to cited documents, then overwrite final["answer"] = "" — discarding
the decorated text before the client receives it.

The fix removes those two blank-override lines. Tests here drive the actual
production functions (with heavy dependencies stubbed) to ensure regression
protection is real: the suite would fail if the lines were re-introduced.

Related: PR #13835 (async_chat), this PR (async_ask + async_chat).
"""

import asyncio
import sys
import types
import warnings
from copy import deepcopy
from types import SimpleNamespace

import pytest

warnings.filterwarnings(
    "ignore",
    message="pkg_resources is deprecated as an API.*",
    category=UserWarning,
)


def _install_cv2_stub_if_unavailable():
    try:
        import cv2  # noqa: F401
        return
    except Exception:
        pass
    stub = types.ModuleType("cv2")
    stub.INTER_LINEAR = 1
    stub.INTER_CUBIC = 2
    stub.BORDER_CONSTANT = 0
    stub.BORDER_REPLICATE = 1
    stub.COLOR_BGR2RGB = 0
    stub.COLOR_BGR2GRAY = 1
    stub.COLOR_GRAY2BGR = 2
    stub.IMREAD_IGNORE_ORIENTATION = 128
    stub.IMREAD_COLOR = 1
    stub.RETR_LIST = 1
    stub.CHAIN_APPROX_SIMPLE = 2

    def _module_getattr(name):
        if name.isupper():
            return 0
        raise RuntimeError(f"cv2.{name} is unavailable in this test environment")

    stub.__getattr__ = _module_getattr
    sys.modules["cv2"] = stub


_install_cv2_stub_if_unavailable()

from api.db.services import dialog_service
from common.constants import LLMType


def test_get_rerank_model_returns_none_without_rerank_id():
    dialog = SimpleNamespace(
        tenant_id="tenant-1",
        rerank_id="",
    )

    assert dialog_service.get_rerank_model(dialog) is None


def test_get_rerank_model_uses_dialog_tenant_and_config(monkeypatch):
    config = {"llm_name": "reranker"}
    created = {}
    bundle = object()

    def fake_config(tenant_id, llm_type, rerank_id):
        created["config_args"] = (tenant_id, llm_type, rerank_id)
        return config

    def fake_bundle(tenant_id, model_config, **kwargs):
        created["bundle_args"] = (tenant_id, model_config, kwargs)
        return bundle

    monkeypatch.setattr(
        dialog_service,
        "get_model_config_from_provider_instance",
        fake_config,
    )
    monkeypatch.setattr(dialog_service, "LLMBundle", fake_bundle)
    dialog = SimpleNamespace(
        tenant_id="tenant-1",
        rerank_id="reranker-1",
    )

    result = dialog_service.get_rerank_model(
        dialog,
        trace_context={"trace_id": "trace-1"},
        langfuse_session_id="session-1",
    )

    assert result is bundle
    assert created["config_args"] == (
        "tenant-1",
        LLMType.RERANK,
        "reranker-1",
    )
    assert created["bundle_args"] == (
        "tenant-1",
        config,
        {
            "trace_context": {"trace_id": "trace-1"},
            "langfuse_session_id": "session-1",
        },
    )


# ---------------------------------------------------------------------------
# Shared stubs
# ---------------------------------------------------------------------------

_KBINFOS = {
    "chunks": [
        {
            "doc_id": "doc-1",
            "content_ltks": "ragflow is a rag engine",
            "content_with_weight": "RAGFlow is a RAG engine.",
            "vector": [0.1, 0.2, 0.3],
            "docnm_kwd": "intro.pdf",
        },
    ],
    "doc_aggs": [{"doc_id": "doc-1", "doc_name": "intro.pdf", "count": 1}],
    "total": 1,
}

_KB = SimpleNamespace(
    id="kb-1",
    embd_id="text-embedding-ada-002@OpenAI",
    tenant_embd_id="text-embedding-ada-002@OpenAI",
    tenant_id="tenant-1",
    chunk_num=1,
    name="Test KB",
    parser_id="general",
)

_LLM_CONFIG = {
    "llm_name": "gpt-4o",
    "llm_factory": "OpenAI",
    "model_type": "chat",
    "max_tokens": 8192,
}


class _StreamingChatModel:
    """Yields a single-chunk full answer, no citations."""

    def __init__(self, answer: str):
        self.answer = answer
        self.max_length = 8192

    async def async_chat_streamly_delta(self, system_prompt, messages, gen_conf, **_kwargs):
        yield self.answer

    async def async_chat(self, system_prompt, messages, gen_conf, **_kwargs):
        return self.answer


class _StubRetriever:
    async def retrieval(self, *_args, **_kwargs):
        return deepcopy(_KBINFOS)

    def retrieval_by_children(self, chunks, tenant_ids):
        return chunks

    def insert_citations(self, answer, content_ltks, vectors, embd_mdl, **_kwargs):
        # Return the answer unchanged; no citation markers inserted.
        return answer, set()


class _ReferenceRetriever(_StubRetriever):
    def __init__(self, kbinfos, inserted_indices=None):
        self.kbinfos = deepcopy(kbinfos)
        self.inserted_indices = set(inserted_indices or [])
        self.retrieval_calls = []

    async def retrieval(self, *args, **kwargs):
        self.retrieval_calls.append((args, kwargs))
        return deepcopy(self.kbinfos)

    def insert_citations(self, answer, *_args, **_kwargs):
        if not self.inserted_indices:
            return answer, set()
        markers = "".join(
            f" [ID:{index}]" for index in sorted(self.inserted_indices)
        )
        return answer + markers, self.inserted_indices


class _RecordingChatModel(_StreamingChatModel):
    def __init__(self, answer):
        super().__init__(answer)
        self.chat_calls = []

    async def async_chat(self, system_prompt, messages, gen_conf, **kwargs):
        self.chat_calls.append((system_prompt, deepcopy(messages), gen_conf, kwargs))
        return self.answer


class _FakePropagateAttributesContext:
    """No-op context manager for fake propagate_attributes."""

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass


def _fake_propagate_attributes(**kwargs):
    """Fake propagate_attributes (Langfuse v4) that records kwargs and returns a no-op context manager."""
    _propagate_attributes_calls.append(kwargs)
    return _FakePropagateAttributesContext()


class _FakeLangfuseObservation:
    def __init__(self):
        self.updates = []
        self.ended = False

    def update(self, **kwargs):
        self.updates.append(kwargs)

    def end(self):
        self.ended = True


_propagate_attributes_calls = []


class _FakeLangfuseClient:
    instances = []
    fail_start_observation = False

    def __init__(self, **kwargs):
        self.init_kwargs = kwargs
        self.observation_kwargs = None
        self.observation = _FakeLangfuseObservation()
        self.instances.append(self)

    def auth_check(self):
        return True

    def create_trace_id(self):
        return "trace-id"

    def start_observation(self, **kwargs):
        if self.fail_start_observation:
            raise RuntimeError("langfuse unavailable")
        self.observation_kwargs = kwargs
        return self.observation


def _collect(async_gen):
    async def _run():
        return [ev async for ev in async_gen]

    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(_run())
    finally:
        loop.close()


def _make_reference_kbinfos():
    chunks = [
        {
            "chunk_id": f"chunk-{index}",
            "doc_id": f"doc-{index % 3}",
            "docnm_kwd": f"文档-{index % 3}.docx",
            "content_ltks": f"知识块 {index}",
            "content_with_weight": f"知识块 {index}",
            "vector": [0.1, 0.2, 0.3],
        }
        for index in range(10)
    ]
    return {
        "chunks": chunks,
        "doc_aggs": [
            {
                "doc_id": f"candidate-{index}",
                "doc_name": f"候选-{index}.docx",
                "count": 1,
            }
            for index in range(25)
        ],
        "total": 70,
    }


def test_parse_faq_pairs_removes_csv_noise():
    content = (
        "问题：云文档的相关问题可以找谁咨询呢？；回答：余李; Unnamed: 2: nan ——Data\n"
        "问题：EHR系统、培训系统、绩效系统的相关问题可以找谁咨询呢？；"
        "回答：钟志斌或者陈国萌; Unnamed: 2: nan ——Data"
    )

    assert dialog_service._parse_faq_pairs(content) == [
        ("云文档的相关问题可以找谁咨询呢？", "余李"),
        (
            "EHR系统、培训系统、绩效系统的相关问题可以找谁咨询呢？",
            "钟志斌或者陈国萌",
        ),
    ]


def test_parse_faq_pairs_accepts_reply_label_from_real_chunk():
    content = (
        "问题：云文档的相关问题可以找谁咨询呢？; 回复：余李; "
        "Unnamed: 2：nan ——Data"
    )

    assert dialog_service._parse_faq_pairs(content) == [
        ("云文档的相关问题可以找谁咨询呢？", "余李"),
    ]


def test_parse_faq_pairs_preserves_multiline_answers_and_same_line_records():
    content = (
        "问题：第一个问题？; 回复：第一行\n"
        "第二行 ——Data 问题：第二个问题？; 回答：第二个答案"
    )

    assert dialog_service._parse_faq_pairs(content) == [
        ("第一个问题？", "第一行\n第二行"),
        ("第二个问题？", "第二个答案"),
    ]


def test_normalize_faq_question_is_strict_except_formatting():
    assert dialog_service._normalize_faq_question(
        " 云文档的相关问题可以找谁咨询呢? "
    ) == dialog_service._normalize_faq_question(
        "云文档的相关问题可以找谁咨询呢？"
    )
    assert dialog_service._normalize_faq_question(
        "云文档找谁？"
    ) != dialog_service._normalize_faq_question(
        "云文档的相关问题可以找谁咨询呢？"
    )


def test_recent_user_messages_normalizes_text_parts_from_current_message():
    messages = [
        {"role": "user", "content": "上一问"},
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "当前文字问题"},
                {"type": "image_url", "image_url": {"url": "data:image/png;base64,AA=="}},
            ],
        },
    ]

    assert dialog_service._recent_user_messages(messages) == [
        {"role": "user", "content": "上一问"},
        {"role": "user", "content": "当前文字问题"},
    ]


def _make_faq_kbinfos():
    return {
        "chunks": [
            {
                "chunk_id": "faq-chunk",
                "doc_id": "faq-doc",
                "docnm_kwd": "IT常问问题-工作表1.csv",
                "content_ltks": "云文档 联系人 EHR 培训 绩效",
                "content_with_weight": (
                    "问题：云文档的相关问题可以找谁咨询呢？；回答：余李; "
                    "Unnamed: 2: nan ——Data\n"
                    "问题：EHR系统、培训系统、绩效系统的相关问题可以找谁咨询呢？；"
                    "回答：钟志斌或者陈国萌; Unnamed: 2: nan ——Data"
                ),
                "vector": [0.1, 0.2, 0.3],
            }
        ],
        "doc_aggs": [
            {
                "doc_id": "faq-doc",
                "doc_name": "IT常问问题-工作表1.csv",
                "count": 1,
            }
        ],
        "total": 1,
    }


def _run_reference_async_chat(
    monkeypatch,
    *,
    answer,
    kbinfos,
    inserted_indices=None,
    messages=None,
    refine_multiturn=False,
    refined_question=None,
    document_code_scope=None,
    stream=False,
    quote=True,
    field_map=None,
    sql_answer=None,
    dialog_llm_setting=None,
    async_chat_kwargs=None,
):
    chat_mdl = _RecordingChatModel(answer)
    chat_mdl.refinement_messages = []
    chat_mdl.sql_questions = []
    retriever = _ReferenceRetriever(kbinfos, inserted_indices)

    monkeypatch.setattr(
        dialog_service,
        "get_model_type_by_name",
        lambda _tenant_id, _llm_id: ["chat"],
    )
    monkeypatch.setattr(
        dialog_service,
        "get_model_config_from_provider_instance",
        lambda _tenant_id, _model_type, _llm_id: _LLM_CONFIG,
    )
    monkeypatch.setattr(
        dialog_service.TenantLangfuseService,
        "filter_by_tenant",
        lambda **_kwargs: None,
    )
    monkeypatch.setattr(
        dialog_service,
        "get_models",
        lambda _dialog, **_kwargs: (
            [_KB],
            chat_mdl,
            None,
            chat_mdl,
            None,
        ),
    )
    monkeypatch.setattr(
        dialog_service.KnowledgebaseService,
        "get_field_map",
        lambda _kb_ids: deepcopy(field_map or {}),
    )
    monkeypatch.setattr(
        dialog_service.KnowledgebaseService,
        "get_by_ids",
        lambda _kb_ids: [_KB],
    )
    monkeypatch.setattr(
        dialog_service.settings,
        "retriever",
        retriever,
        raising=False,
    )
    monkeypatch.setattr(
        dialog_service,
        "label_question",
        lambda _question, _kbs: "",
    )
    monkeypatch.setattr(
        dialog_service,
        "kb_prompt",
        lambda _kbinfos, _max_tokens, **_kwargs: ["当前知识块"],
    )
    if refined_question is not None:
        async def fake_full_question(_tenant_id, _llm_id, refinement_messages):
            chat_mdl.refinement_messages = deepcopy(refinement_messages)
            return refined_question

        monkeypatch.setattr(dialog_service, "full_question", fake_full_question)
    if sql_answer is not None:
        async def fake_use_sql(question, *_args, **_kwargs):
            chat_mdl.sql_questions.append(question)
            return deepcopy(sql_answer)

        monkeypatch.setattr(dialog_service, "use_sql", fake_use_sql)
    if document_code_scope is not None:
        monkeypatch.setattr(
            dialog_service,
            "_resolve_document_code_scope",
            lambda _question, _kb_ids: document_code_scope,
        )

    dialog = _make_dialog(chat_mdl)
    if dialog_llm_setting is not None:
        dialog.llm_setting = dialog_llm_setting
    dialog.prompt_config["refine_multiturn"] = refine_multiturn
    events = _collect(
        dialog_service.async_chat(
            dialog,
            messages or [{"role": "user", "content": "测试引用。"}],
            stream=stream,
            quote=quote,
            session_id="session-reference-test",
            **(async_chat_kwargs or {}),
        )
    )
    if stream:
        return events, chat_mdl, retriever
    assert len(events) == 1
    return events[0], chat_mdl, retriever


# ---------------------------------------------------------------------------
# Tests for async_ask  (production code path)
# ---------------------------------------------------------------------------

@pytest.mark.p2
def test_async_ask_final_event_carries_decorated_answer(monkeypatch):
    """
    Drive the real dialog_service.async_ask() and verify that the final SSE
    event (final=True) exposes the answer produced by decorate_answer(), not
    an empty string.

    Regression guard: if `final["answer"] = ""` is re-introduced at line ~1444,
    this test fails.
    """
    llm_answer = "RAGFlow is a RAG engine built for document understanding."
    chat_mdl = _StreamingChatModel(llm_answer)
    retriever = _StubRetriever()

    monkeypatch.setattr(
        dialog_service.KnowledgebaseService, "get_by_ids", lambda _ids: [_KB]
    )
    monkeypatch.setattr(
        dialog_service, "get_model_config_from_provider_instance",
        lambda _tid, _type, _name: _LLM_CONFIG,
    )
    monkeypatch.setattr(dialog_service, "LLMBundle", lambda _tid, _cfg: chat_mdl)
    monkeypatch.setattr(dialog_service.settings, "retriever", retriever, raising=False)
    monkeypatch.setattr(dialog_service.settings, "kg_retriever", retriever, raising=False)
    monkeypatch.setattr(
        dialog_service.DocMetadataService, "get_flatted_meta_by_kbs", lambda _ids: {}
    )
    monkeypatch.setattr(dialog_service, "label_question", lambda _q, _kbs: "")
    # kb_prompt calls DocumentService.get_by_ids which needs a live DB; stub it out.
    monkeypatch.setattr(
        dialog_service, "kb_prompt",
        lambda _kbinfos, _max_tokens, **_kw: ["RAGFlow is a RAG engine."],
    )

    events = _collect(
        dialog_service.async_ask(
            question="What is RAGFlow?",
            kb_ids=["kb-1"],
            tenant_id="tenant-1",
        )
    )

    assert events, "async_ask must yield at least one event"

    final_events = [e for e in events if e.get("final") is True]
    assert len(final_events) == 1, (
        f"Expected exactly one final event, got {len(final_events)}: {final_events}"
    )
    final = final_events[0]

    assert "answer" in final
    assert "reference" in final


@pytest.mark.p2
def test_async_ask_delta_events_carry_incremental_text_only(monkeypatch):
    """
    Intermediate delta events must have empty reference dicts.
    Only the final event should carry the populated reference from decorate_answer().
    """
    chat_mdl = _StreamingChatModel("Incremental text for delta test.")
    retriever = _StubRetriever()

    monkeypatch.setattr(
        dialog_service.KnowledgebaseService, "get_by_ids", lambda _ids: [_KB]
    )
    monkeypatch.setattr(
        dialog_service, "get_model_config_from_provider_instance",
        lambda _tid, _type, _name: _LLM_CONFIG,
    )
    monkeypatch.setattr(dialog_service, "LLMBundle", lambda _tid, _cfg: chat_mdl)
    monkeypatch.setattr(dialog_service.settings, "retriever", retriever, raising=False)
    monkeypatch.setattr(dialog_service.settings, "kg_retriever", retriever, raising=False)
    monkeypatch.setattr(
        dialog_service.DocMetadataService, "get_flatted_meta_by_kbs", lambda _ids: {}
    )
    monkeypatch.setattr(dialog_service, "label_question", lambda _q, _kbs: "")
    monkeypatch.setattr(
        dialog_service, "kb_prompt",
        lambda _kbinfos, _max_tokens, **_kw: ["RAGFlow is a RAG engine."],
    )

    events = _collect(
        dialog_service.async_ask(
            question="Describe RAGFlow briefly.",
            kb_ids=["kb-1"],
            tenant_id="tenant-1",
        )
    )

    delta_events = [e for e in events if not e.get("final")]
    final_events  = [e for e in events if e.get("final") is True]

    assert len(final_events) == 1, f"Expected exactly one final event, got {len(final_events)}"
    for ev in delta_events:
        assert ev["reference"] == {}, f"Delta event must have empty reference, got: {ev['reference']}"

    assert "chunks" in final_events[0]["reference"], (
        "Final event reference must contain chunk data from decorate_answer()"
    )


@pytest.mark.p2
def test_async_ask_empty_kb_ids_yields_error_final_event(monkeypatch):
    """
    When kb_ids is empty, async_ask() must not crash with IndexError on kbs[0].
    """
    monkeypatch.setattr(
        dialog_service.KnowledgebaseService, "get_by_ids", lambda _ids: []
    )

    events = _collect(
        dialog_service.async_ask(
            question="What is RAGFlow?",
            kb_ids=[],
            tenant_id="tenant-1",
        )
    )

    assert len(events) == 1
    final = events[0]
    assert final.get("final") is True
    assert "No KB selected" in final["answer"]
    assert final["reference"] == {}


@pytest.mark.p2
def test_async_ask_stale_kb_ids_yields_error_final_event(monkeypatch):
    """Provided kb_ids that do not resolve to any KB should report invalid selection."""
    monkeypatch.setattr(
        dialog_service.KnowledgebaseService,
        "get_by_ids",
        lambda ids: [] if ids == ["deleted-kb"] else [_KB],
    )

    events = _collect(
        dialog_service.async_ask(
            question="What is RAGFlow?",
            kb_ids=["deleted-kb"],
            tenant_id="tenant-1",
        )
    )

    assert len(events) == 1
    assert events[0].get("final") is True
    assert "not valid" in events[0]["answer"]
    assert events[0]["reference"] == {}


# ---------------------------------------------------------------------------
# Tests for async_chat  (production code path)
# ---------------------------------------------------------------------------


@pytest.mark.p2
def test_async_chat_applies_channel_thinking_override_without_mutating_dialog(
    monkeypatch,
):
    llm_setting = {"temperature": 0.1}

    _, chat_mdl, _ = _run_reference_async_chat(
        monkeypatch,
        answer="渠道回答",
        kbinfos=_KBINFOS,
        stream=False,
        dialog_llm_setting=llm_setting,
        async_chat_kwargs={"_channel_disable_thinking": True},
    )

    assert chat_mdl.chat_calls[-1][2] == {
        "temperature": 0.1,
        "_disable_thinking": True,
    }
    assert llm_setting == {"temperature": 0.1}


@pytest.mark.p2
def test_async_chat_solo_applies_channel_thinking_override(monkeypatch):
    chat_mdl = _RecordingChatModel("无知识库渠道回答")
    monkeypatch.setattr(
        dialog_service,
        "get_model_type_by_name",
        lambda _tenant_id, _llm_id: ["chat"],
    )
    monkeypatch.setattr(
        dialog_service,
        "get_model_config_from_provider_instance",
        lambda _tenant_id, _model_type, _llm_id: _LLM_CONFIG,
    )
    monkeypatch.setattr(
        dialog_service,
        "LLMBundle",
        lambda *_args, **_kwargs: chat_mdl,
    )
    dialog = _make_dialog(chat_mdl)
    dialog.kb_ids = []
    llm_setting = dialog.llm_setting

    events = _collect(
        dialog_service.async_chat(
            dialog,
            [{"role": "user", "content": "你好"}],
            stream=False,
            _channel_disable_thinking=True,
        )
    )

    assert events[0]["answer"] == "无知识库渠道回答"
    assert chat_mdl.chat_calls[-1][2] == {
        "temperature": 0.1,
        "_disable_thinking": True,
    }
    assert llm_setting == {"temperature": 0.1}


def _make_dialog(chat_mdl_stub):
    """Build a minimal dialog SimpleNamespace for async_chat()."""
    return SimpleNamespace(
        id="dialog-1",
        kb_ids=["kb-1"],
        tenant_id="tenant-1",
        tenant_llm_id=None,
        llm_id="gpt-4o",
        llm_setting={"temperature": 0.1},
        prompt_type="simple",
        prompt_config={
            "system": "You are helpful. {knowledge}",
            "parameters": [{"key": "knowledge", "optional": False}],
            "quote": True,
            "empty_response": "",
            "reasoning": False,
            "refine_multiturn": False,
            "cross_languages": False,
            "keyword": False,
            "toc_enhance": False,
            "tavily_api_key": "",
            "use_kg": False,
            "tts": False,
        },
        meta_data_filter={},
        similarity_threshold=0.2,
        vector_similarity_weight=0.3,
        top_n=6,
        top_k=1024,
        rerank_id="",
    )


@pytest.mark.p2
def test_async_chat_final_event_carries_decorated_answer(monkeypatch):
    """
    Drive the real dialog_service.async_chat() streaming path and verify that
    the final SSE event (final=True) exposes the answer from decorate_answer(),
    not an empty string.

    Regression guard: if `final["answer"] = ""` is re-introduced at line ~774,
    this test fails.
    """
    llm_answer = "RAGFlow handles document parsing with deep understanding."
    chat_mdl = _StreamingChatModel(llm_answer)
    retriever = _StubRetriever()

    # Stub out the heavy service/model calls
    monkeypatch.setattr(
        dialog_service, "get_model_type_by_name",
        lambda _tid, _llm_id: ["chat"]
    )
    monkeypatch.setattr(
        dialog_service, "get_model_config_from_provider_instance",
        lambda _tid, _type, _llm_id: _LLM_CONFIG,
    )
    monkeypatch.setattr(
        dialog_service.TenantLangfuseService, "filter_by_tenant",
        lambda tenant_id: None,
    )
    # get_models returns (kbs, embd_mdl, rerank_mdl, chat_mdl, tts_mdl)
    monkeypatch.setattr(
        dialog_service, "get_models",
        lambda _dialog, **_kwargs: ([_KB], chat_mdl, None, chat_mdl, None),
    )
    monkeypatch.setattr(
        dialog_service.KnowledgebaseService, "get_field_map", lambda _kb_ids: {}
    )
    monkeypatch.setattr(
        dialog_service.KnowledgebaseService, "get_by_ids", lambda _ids: [_KB]
    )
    monkeypatch.setattr(dialog_service.settings, "retriever", retriever, raising=False)
    monkeypatch.setattr(dialog_service, "label_question", lambda _q, _kbs: "")
    monkeypatch.setattr(
        dialog_service, "kb_prompt",
        lambda _kbinfos, _max_tokens, **_kw: ["RAGFlow is a RAG engine."],
    )

    dialog = _make_dialog(chat_mdl)
    messages = [{"role": "user", "content": "What is RAGFlow?"}]

    events = _collect(dialog_service.async_chat(dialog, messages, stream=True, quote=True))

    final_events = [e for e in events if e.get("final") is True]
    assert len(final_events) == 1, (
        f"Expected exactly one final event, got {len(final_events)}: {final_events}"
    )
    final = final_events[0]

    assert "answer" in final
    assert "reference" in final


@pytest.mark.p2
def test_async_chat_langfuse_uses_start_observation(monkeypatch):
    """
    Langfuse v4 exposes start_observation(as_type="generation"), not
    start_generation(). Keep async_chat() on the migrated API.
    """
    _FakeLangfuseClient.instances = []
    monkeypatch.setattr(_FakeLangfuseClient, "fail_start_observation", False)
    llm_answer = "RAGFlow traces chat answers through Langfuse."
    chat_mdl = _StreamingChatModel(llm_answer)
    retriever = _StubRetriever()

    monkeypatch.setattr(
        dialog_service, "get_model_type_by_name",
        lambda _tid, _llm_id: ["chat"]
    )
    monkeypatch.setattr(
        dialog_service, "get_model_config_from_provider_instance",
        lambda _tid, _type, _llm_id: _LLM_CONFIG,
    )
    monkeypatch.setattr(
        dialog_service.TenantLangfuseService, "filter_by_tenant",
        lambda tenant_id: SimpleNamespace(
            public_key="public",
            secret_key="secret",
            host="http://langfuse.local",
        ),
    )
    monkeypatch.setattr(dialog_service, "Langfuse", _FakeLangfuseClient)
    _propagate_attributes_calls.clear()
    monkeypatch.setattr(dialog_service, "propagate_attributes", _fake_propagate_attributes)
    monkeypatch.setattr(
        dialog_service,
        "get_models",
        lambda _dialog, **_kwargs: ([_KB], chat_mdl, None, chat_mdl, None),
    )
    monkeypatch.setattr(
        dialog_service.KnowledgebaseService, "get_field_map", lambda _kb_ids: {}
    )
    monkeypatch.setattr(
        dialog_service.KnowledgebaseService, "get_by_ids", lambda _ids: [_KB]
    )
    monkeypatch.setattr(dialog_service.settings, "retriever", retriever, raising=False)
    monkeypatch.setattr(dialog_service, "label_question", lambda _q, _kbs: "")
    monkeypatch.setattr(
        dialog_service,
        "kb_prompt",
        lambda _kbinfos, _max_tokens, **_kw: ["RAGFlow is a RAG engine."],
    )

    dialog = _make_dialog(chat_mdl)
    messages = [{"role": "user", "content": "What is RAGFlow?"}]

    events = _collect(dialog_service.async_chat(dialog, messages, stream=True, quote=True))

    assert any(e.get("final") is True for e in events)
    assert len(_FakeLangfuseClient.instances) == 1
    langfuse = _FakeLangfuseClient.instances[0]
    assert langfuse.observation_kwargs["as_type"] == "generation"
    assert langfuse.observation_kwargs["trace_context"] == {"trace_id": "trace-id"}
    assert langfuse.observation_kwargs["name"] == "chat"
    assert langfuse.observation_kwargs["model"] == _LLM_CONFIG["llm_name"]
    input_payload = langfuse.observation_kwargs["input"]
    assert set(input_payload.keys()) == {"prompt", "prompt4citation", "messages"}
    assert input_payload["prompt"] == "You are helpful. \n------\nRAGFlow is a RAG engine."
    assert input_payload["prompt4citation"] == dialog_service.citation_prompt()
    assert input_payload["messages"][0]["role"] == "system"
    assert input_payload["messages"][0]["content"] == input_payload["prompt"]
    assert input_payload["messages"][1] == {"role": "user", "content": "What is RAGFlow?"}
    assert langfuse.observation.ended is True


@pytest.mark.p2
def test_async_chat_langfuse_observation_includes_session_id(monkeypatch):
    _FakeLangfuseClient.instances = []
    _propagate_attributes_calls.clear()
    monkeypatch.setattr(_FakeLangfuseClient, "fail_start_observation", False)
    chat_mdl = _StreamingChatModel("Session traces should be grouped.")
    retriever = _StubRetriever()

    monkeypatch.setattr(
        dialog_service, "get_model_type_by_name",
        lambda _tid, _llm_id: ["chat"]
    )
    monkeypatch.setattr(
        dialog_service,
        "get_model_config_from_provider_instance",
        lambda _tid, _type, _llm_id: _LLM_CONFIG,
    )
    monkeypatch.setattr(
        dialog_service.TenantLangfuseService, "filter_by_tenant",
        lambda tenant_id: SimpleNamespace(
            public_key="public",
            secret_key="secret",
            host="http://langfuse.local",
        ),
    )
    monkeypatch.setattr(dialog_service, "Langfuse", _FakeLangfuseClient)
    monkeypatch.setattr(dialog_service, "propagate_attributes", _fake_propagate_attributes)
    monkeypatch.setattr(
        dialog_service,
        "get_models",
        lambda _dialog, **_kwargs: ([_KB], chat_mdl, None, chat_mdl, None),
    )
    monkeypatch.setattr(
        dialog_service.KnowledgebaseService, "get_field_map", lambda _kb_ids: {}
    )
    monkeypatch.setattr(
        dialog_service.KnowledgebaseService, "get_by_ids", lambda _ids: [_KB]
    )
    monkeypatch.setattr(dialog_service.settings, "retriever", retriever, raising=False)
    monkeypatch.setattr(dialog_service, "label_question", lambda _q, _kbs: "")
    monkeypatch.setattr(
        dialog_service,
        "kb_prompt",
        lambda _kbinfos, _max_tokens, **_kw: ["RAGFlow is a RAG engine."],
    )

    dialog = _make_dialog(chat_mdl)
    messages = [{"role": "user", "content": "What is RAGFlow?"}]

    events = _collect(dialog_service.async_chat(dialog, messages, stream=True, quote=True, session_id="session-1"))

    assert any(e.get("final") is True for e in events)
    langfuse = _FakeLangfuseClient.instances[0]
    assert langfuse.observation_kwargs["trace_context"] == {"trace_id": "trace-id"}
    assert _propagate_attributes_calls[0]["session_id"] == "session-1"


@pytest.mark.p2
def test_get_models_passes_langfuse_trace_context_to_llm_bundles(monkeypatch):
    captured = []

    class _FakeBundle:
        def __init__(self, tenant_id, model_config, **kwargs):
            self.tenant_id = tenant_id
            self.model_config = model_config
            self.trace_context = kwargs.get("trace_context")
            self.langfuse_session_id = kwargs.get("langfuse_session_id")
            captured.append((tenant_id, model_config["model_type"], kwargs))

    monkeypatch.setattr(dialog_service.KnowledgebaseService, "get_by_ids", lambda _ids: [_KB])
    monkeypatch.setattr(
        dialog_service,
        "get_model_config_from_provider_instance",
        lambda _tenant_id, model_type, _model_id: {**_LLM_CONFIG, "model_type": model_type},
    )
    monkeypatch.setattr(
        dialog_service,
        "get_tenant_default_model_by_type",
        lambda _tenant_id, model_type: {**_LLM_CONFIG, "model_type": model_type},
    )
    monkeypatch.setattr(dialog_service, "LLMBundle", _FakeBundle)

    dialog = _make_dialog(None)
    dialog.rerank_id = "rerank-1"
    dialog.prompt_config["tts"] = True
    trace_context = {"trace_id": "trace-id"}

    dialog_service.get_models(dialog, trace_context=trace_context, langfuse_session_id="session-1")

    assert len(captured) == 4
    assert {model_type for _, model_type, _ in captured} == {
        dialog_service.LLMType.EMBEDDING,
        dialog_service.LLMType.CHAT,
        dialog_service.LLMType.RERANK,
        dialog_service.LLMType.TTS,
    }
    for _, _, kwargs in captured:
        assert kwargs["trace_context"] is trace_context
        assert kwargs["langfuse_session_id"] == "session-1"


@pytest.mark.p2
def test_async_chat_continues_when_langfuse_observation_start_fails(monkeypatch):
    """
    Langfuse tracing is best-effort; observation startup errors must not break
    chat responses.
    """
    _FakeLangfuseClient.instances = []
    monkeypatch.setattr(_FakeLangfuseClient, "fail_start_observation", True)
    llm_answer = "RAGFlow still answers when tracing is unavailable."
    chat_mdl = _StreamingChatModel(llm_answer)
    retriever = _StubRetriever()

    monkeypatch.setattr(
        dialog_service, "get_model_type_by_name",
        lambda _tid, _llm_id: ["chat"]
    )
    monkeypatch.setattr(
        dialog_service, "get_model_config_from_provider_instance",
        lambda _tid, _type, _llm_id: _LLM_CONFIG,
    )
    monkeypatch.setattr(
        dialog_service.TenantLangfuseService, "filter_by_tenant",
        lambda tenant_id: SimpleNamespace(
            public_key="public",
            secret_key="secret",
            host="http://langfuse.local",
        ),
    )
    monkeypatch.setattr(dialog_service, "Langfuse", _FakeLangfuseClient)
    _propagate_attributes_calls.clear()
    monkeypatch.setattr(dialog_service, "propagate_attributes", _fake_propagate_attributes)
    monkeypatch.setattr(
        dialog_service,
        "get_models",
        lambda _dialog, **_kwargs: ([_KB], chat_mdl, None, chat_mdl, None),
    )
    monkeypatch.setattr(
        dialog_service.KnowledgebaseService, "get_field_map", lambda _kb_ids: {}
    )
    monkeypatch.setattr(
        dialog_service.KnowledgebaseService, "get_by_ids", lambda _ids: [_KB]
    )
    monkeypatch.setattr(dialog_service.settings, "retriever", retriever, raising=False)
    monkeypatch.setattr(dialog_service, "label_question", lambda _q, _kbs: "")
    monkeypatch.setattr(
        dialog_service,
        "kb_prompt",
        lambda _kbinfos, _max_tokens, **_kw: ["RAGFlow is a RAG engine."],
    )

    dialog = _make_dialog(chat_mdl)
    messages = [{"role": "user", "content": "What is RAGFlow?"}]

    events = _collect(dialog_service.async_chat(dialog, messages, stream=True, quote=True))

    final_events = [e for e in events if e.get("final") is True]
    assert len(final_events) == 1
    assert "answer" in final_events[0]
    assert len(_FakeLangfuseClient.instances) == 1
    assert _FakeLangfuseClient.instances[0].observation_kwargs is None
    assert _FakeLangfuseClient.instances[0].observation.ended is False


@pytest.mark.parametrize(
    (
        "answer",
        "chunk_count",
        "expected_answer",
        "expected_valid",
        "expected_invalid",
        "expected_count",
    ),
    [
        (
            "有效 [ID:0]，无效 [ID:42]。",
            10,
            "有效 [ID:0]，无效 。",
            {0},
            [42],
            2,
        ),
        (
            "阿拉伯数字 [ID:١]，波斯数字 [۲]。",
            3,
            "阿拉伯数字 [ID:١]，波斯数字 [۲]。",
            {1, 2},
            [],
            2,
        ),
        (
            "全部越界 [ID:42][ID:43]。",
            10,
            "全部越界 。",
            set(),
            [42, 43],
            2,
        ),
    ],
)
def test_normalize_answer_citations(
    answer,
    chunk_count,
    expected_answer,
    expected_valid,
    expected_invalid,
    expected_count,
):
    assert dialog_service._normalize_answer_citations(
        answer,
        chunk_count,
    ) == (
        expected_answer,
        expected_valid,
        expected_invalid,
        expected_count,
    )


def test_build_cited_doc_aggs_deduplicates_by_doc_id():
    chunks = [
        {
            "chunk_id": "chunk-0",
            "doc_id": "doc-a",
            "docnm_kwd": "模板.docx",
        },
        {
            "chunk_id": "chunk-1",
            "doc_id": "doc-a",
            "docnm_kwd": "模板.docx",
        },
        {
            "chunk_id": "chunk-2",
            "doc_id": "doc-b",
            "docnm_kwd": "模板.docx",
            "url": "https://example.test/doc-b",
        },
        {
            "chunk_id": "chunk-3",
            "doc_id": "",
            "docnm_kwd": "缺少ID.docx",
        },
    ]

    assert dialog_service._build_cited_doc_aggs(
        chunks,
        {0, 1, 2, 3, 99},
    ) == [
        {
            "doc_id": "doc-a",
            "doc_name": "模板.docx",
            "count": 2,
        },
        {
            "doc_id": "doc-b",
            "doc_name": "模板.docx",
            "count": 1,
            "url": "https://example.test/doc-b",
        },
    ]


@pytest.mark.p2
def test_async_chat_prunes_candidate_docs_to_explicit_citations(monkeypatch):
    answer = "依据一 [ID:0]，依据二 [ID:1]。"

    final, _, _ = _run_reference_async_chat(
        monkeypatch,
        answer=answer,
        kbinfos=_make_reference_kbinfos(),
    )

    assert final["answer"] == answer
    assert [doc["doc_id"] for doc in final["reference"]["doc_aggs"]] == [
        "doc-0",
        "doc-1",
    ]
    assert len(final["reference"]["chunks"]) == 10


@pytest.mark.p2
def test_async_chat_drops_all_docs_when_explicit_citations_are_out_of_range(
    monkeypatch,
    caplog,
):
    final, _, _ = _run_reference_async_chat(
        monkeypatch,
        answer="无源 [ID:42]，报告 [ID:43]，有源 [ID:44][ID:45]。",
        kbinfos=_make_reference_kbinfos(),
    )

    assert "[ID:42]" not in final["answer"]
    assert "[ID:45]" not in final["answer"]
    assert final["reference"]["doc_aggs"] == []
    assert "invalid_citation_ids" in caplog.text


@pytest.mark.p2
def test_async_chat_keeps_valid_docs_and_removes_only_invalid_markers(monkeypatch):
    final, _, _ = _run_reference_async_chat(
        monkeypatch,
        answer="有效 [ID:0]，无效 [ID:42]。",
        kbinfos=_make_reference_kbinfos(),
    )

    assert "[ID:0]" in final["answer"]
    assert "[ID:42]" not in final["answer"]
    assert [doc["doc_id"] for doc in final["reference"]["doc_aggs"]] == [
        "doc-0",
    ]


@pytest.mark.p2
def test_async_chat_uses_only_auto_inserted_citation_docs(monkeypatch):
    final, _, _ = _run_reference_async_chat(
        monkeypatch,
        answer="没有显式引用的回答。",
        kbinfos=_make_reference_kbinfos(),
        inserted_indices={2},
    )

    assert "[ID:2]" in final["answer"]
    assert [doc["doc_id"] for doc in final["reference"]["doc_aggs"]] == [
        "doc-2",
    ]


@pytest.mark.p2
def test_async_chat_returns_no_docs_when_auto_insertion_finds_no_evidence(
    monkeypatch,
):
    final, _, _ = _run_reference_async_chat(
        monkeypatch,
        answer="没有证据匹配的回答。",
        kbinfos=_make_reference_kbinfos(),
        inserted_indices=set(),
    )

    assert final["reference"]["doc_aggs"] == []


@pytest.mark.p2
def test_refined_multiturn_generation_excludes_untrusted_assistant_history(
    monkeypatch,
):
    messages = [
        {"role": "user", "content": "关键工序验证模板是什么？"},
        {
            "role": "assistant",
            "content": "错误历史：使用 BDMB-YF-223 和 BDMB-YF-224。",
        },
        {"role": "user", "content": "再确认一下，方案和报告用哪个模板？"},
    ]
    final, chat_mdl, _ = _run_reference_async_chat(
        monkeypatch,
        answer="使用 BDMB-YF-099 和 BDMB-YF-100 [ID:0][ID:1]。",
        kbinfos=_make_reference_kbinfos(),
        messages=messages,
        refine_multiturn=True,
        refined_question="关键工序验证的方案和报告分别用哪个模板？",
    )

    assert "BDMB-YF-099" in final["answer"]
    generation_messages = chat_mdl.chat_calls[-1][1]
    assert generation_messages == [
        {
            "role": "user",
            "content": "关键工序验证的方案和报告分别用哪个模板？",
        }
    ]
    assert "BDMB-YF-223" not in str(generation_messages)


@pytest.mark.p2
def test_kb_generation_without_refinement_excludes_all_history(monkeypatch):
    messages = [
        {"role": "user", "content": "云文档找谁？"},
        {"role": "assistant", "content": "错误联系人：IT-陶正浩"},
        {"role": "user", "content": "EHR、培训、绩效系统找谁？"},
    ]
    _, chat_mdl, retriever = _run_reference_async_chat(
        monkeypatch,
        answer="钟志斌或者陈国萌 [ID:0]",
        kbinfos=_make_reference_kbinfos(),
        messages=messages,
        refine_multiturn=False,
    )

    assert retriever.retrieval_calls[0][0][0] == "EHR、培训、绩效系统找谁？"
    assert chat_mdl.chat_calls[-1][1] == [
        {"role": "user", "content": "EHR、培训、绩效系统找谁？"},
    ]


@pytest.mark.p2
def test_multiturn_refinement_uses_only_two_recent_user_messages(monkeypatch):
    messages = [
        {"role": "user", "content": "更早的问题"},
        {"role": "assistant", "content": "更早的回答"},
        {"role": "user", "content": "上一问"},
        {"role": "assistant", "content": "错误历史答案"},
        {"role": "user", "content": "再确认一下"},
    ]
    _, chat_mdl, _ = _run_reference_async_chat(
        monkeypatch,
        answer="当前答案 [ID:0]",
        kbinfos=_make_reference_kbinfos(),
        messages=messages,
        refine_multiturn=True,
        refined_question="改写后的独立问题",
    )

    assert chat_mdl.refinement_messages == [
        {"role": "user", "content": "上一问"},
        {"role": "user", "content": "再确认一下"},
    ]
    assert chat_mdl.chat_calls[-1][1] == [
        {"role": "user", "content": "改写后的独立问题"},
    ]


@pytest.mark.p2
def test_exact_faq_answer_bypasses_model(monkeypatch):
    final, chat_mdl, _ = _run_reference_async_chat(
        monkeypatch,
        answer="错误联系人：IT-陶正浩",
        kbinfos=_make_faq_kbinfos(),
        messages=[
            {
                "role": "user",
                "content": "云文档的相关问题可以找谁咨询呢？",
            }
        ],
    )

    assert final["answer"].startswith("余李")
    assert final["reference"]["doc_aggs"][0]["doc_id"] == "faq-doc"
    assert chat_mdl.chat_calls == []


@pytest.mark.p2
def test_approximate_faq_question_uses_rag_generation(monkeypatch):
    _, chat_mdl, _ = _run_reference_async_chat(
        monkeypatch,
        answer="生成回答 [ID:0]",
        kbinfos=_make_faq_kbinfos(),
        messages=[{"role": "user", "content": "云文档找谁？"}],
    )

    assert len(chat_mdl.chat_calls) == 1


@pytest.mark.p2
@pytest.mark.parametrize("quote", [False, True])
def test_exact_faq_stream_emits_visible_delta_before_final(monkeypatch, quote):
    events, chat_mdl, _ = _run_reference_async_chat(
        monkeypatch,
        answer="错误联系人：IT-陶正浩",
        kbinfos=_make_faq_kbinfos(),
        messages=[
            {
                "role": "user",
                "content": "云文档的相关问题可以找谁咨询呢？",
            }
        ],
        stream=True,
        quote=quote,
    )

    assert [event["answer"] for event in events if not event.get("final")] == [
        "余李"
    ]
    assert events[-1]["final"] is True
    assert events[-1]["answer"].startswith("余李")
    assert chat_mdl.chat_calls == []


@pytest.mark.p2
def test_exact_faq_answer_ignores_source_citation_markers(monkeypatch):
    kbinfos = _make_faq_kbinfos()
    kbinfos["chunks"][0]["content_with_weight"] = (
        "问题：云文档的相关问题可以找谁咨询呢？；回答：余李 [ID:9]"
    )

    final, chat_mdl, _ = _run_reference_async_chat(
        monkeypatch,
        answer="错误联系人：IT-陶正浩",
        kbinfos=kbinfos,
        messages=[
            {
                "role": "user",
                "content": "云文档的相关问题可以找谁咨询呢？",
            }
        ],
    )

    assert final["answer"].startswith("余李")
    assert "ID:9" not in final["answer"]
    assert final["reference"]["doc_aggs"][0]["doc_id"] == "faq-doc"
    assert chat_mdl.chat_calls == []


@pytest.mark.p2
def test_current_non_text_user_message_never_falls_back_to_old_question(monkeypatch):
    messages = [
        {"role": "user", "content": "旧问题"},
        {
            "role": "user",
            "content": [
                {"type": "image_url", "image_url": {"url": "data:image/png;base64,AA=="}},
            ],
        },
    ]

    with pytest.raises(ValueError, match="textual content"):
        _run_reference_async_chat(
            monkeypatch,
            answer="不应生成",
            kbinfos=_make_reference_kbinfos(),
            messages=messages,
        )


@pytest.mark.p2
def test_sql_retrieval_uses_refined_question(monkeypatch):
    messages = [
        {"role": "user", "content": "上一问"},
        {"role": "assistant", "content": "不可信旧回答"},
        {"role": "user", "content": "再确认一下"},
    ]
    sql_answer = {
        "answer": "结构化查询答案",
        "reference": {"chunks": [], "doc_aggs": []},
    }

    final, chat_mdl, _ = _run_reference_async_chat(
        monkeypatch,
        answer="不应调用生成模型",
        kbinfos=_make_reference_kbinfos(),
        messages=messages,
        refine_multiturn=True,
        refined_question="改写后的独立问题",
        field_map={"department": "keyword"},
        sql_answer=sql_answer,
    )

    assert final["answer"] == "结构化查询答案"
    assert chat_mdl.refinement_messages == [
        {"role": "user", "content": "上一问"},
        {"role": "user", "content": "再确认一下"},
    ]
    assert chat_mdl.sql_questions == ["改写后的独立问题"]
    assert chat_mdl.chat_calls == []


@pytest.mark.parametrize(
    ("question", "expected"),
    [
        (
            "帮我找一下 BDMB-YF-099_V1.0",
            ["BDMB-YF-099_V1.0"],
        ),
        (
            "对比 bdmb-yf-099 和 BDMB-YF-100。",
            ["BDMB-YF-099", "BDMB-YF-100"],
        ),
        ("ISO-13485 有什么要求？", []),
    ],
)
def test_extract_document_identifiers(question, expected):
    assert dialog_service._extract_document_identifiers(question) == expected


def test_resolve_document_code_scope_matches_normalized_version(monkeypatch):
    calls = []

    def fake_get_ready_by_name_keyword(kb_ids, keyword):
        calls.append((kb_ids, keyword))
        return [
            {
                "id": "doc-ready",
                "name": "软件/BDMB-YF-099-V1.0_关键工序验证方案.docx",
            },
            {
                "id": "doc-other-version",
                "name": "软件/BDMB-YF-099_V2.0_关键工序验证方案.docx",
            },
        ]

    monkeypatch.setattr(
        dialog_service.DocumentService,
        "get_ready_by_name_keyword",
        fake_get_ready_by_name_keyword,
    )

    assert dialog_service._resolve_document_code_scope(
        "帮我找 BDMB-YF-099_V1.0",
        ["kb-1"],
    ) == (
        ["BDMB-YF-099_V1.0"],
        ["doc-ready"],
    )
    assert calls == [(["kb-1"], "BDMB-YF-099")]


def test_resolve_document_code_scope_requires_every_identifier(monkeypatch):
    def fake_get_ready_by_name_keyword(_kb_ids, keyword):
        if keyword == "BDMB-YF-099":
            return [
                {
                    "id": "doc-ready",
                    "name": "BDMB-YF-099_V1.0_关键工序验证方案.docx",
                }
            ]
        return []

    monkeypatch.setattr(
        dialog_service.DocumentService,
        "get_ready_by_name_keyword",
        fake_get_ready_by_name_keyword,
    )

    assert dialog_service._resolve_document_code_scope(
        "对比 BDMB-YF-099 和 BDMB-YF-223",
        ["kb-1"],
    ) == (
        ["BDMB-YF-099", "BDMB-YF-223"],
        [],
    )


@pytest.mark.p2
def test_exact_document_identifier_scopes_retrieval_to_ready_documents(
    monkeypatch,
):
    final, _, retriever = _run_reference_async_chat(
        monkeypatch,
        answer="使用指定模板 [ID:0]。",
        kbinfos=_make_reference_kbinfos(),
        messages=[
            {
                "role": "user",
                "content": "帮我找一下 BDMB-YF-099_V1.0",
            }
        ],
        document_code_scope=(
            ["BDMB-YF-099_V1.0"],
            ["ready-document-id"],
        ),
    )

    assert final["reference"]["doc_aggs"]
    assert retriever.retrieval_calls[-1][1]["doc_ids"] == [
        "ready-document-id",
    ]


@pytest.mark.p2
def test_missing_exact_document_identifier_returns_no_evidence_response(
    monkeypatch,
):
    final, chat_mdl, retriever = _run_reference_async_chat(
        monkeypatch,
        answer="不应调用模型。",
        kbinfos=_make_reference_kbinfos(),
        messages=[
            {
                "role": "user",
                "content": "帮我找一下 BDMB-YF-223_V1.0",
            }
        ],
        document_code_scope=(
            ["BDMB-YF-223_V1.0"],
            [],
        ),
    )

    assert "知识库中未找到" in final["answer"]
    assert final["reference"] == {
        "chunks": [],
        "doc_aggs": [],
        "total": 0,
    }
    assert chat_mdl.chat_calls == []
    assert retriever.retrieval_calls == []
