"""Verify production adapters without invoking configured providers or live indexes."""

import importlib.util
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest


@pytest.fixture
def setup(monkeypatch):
    root = Path(__file__).resolve().parents[5]
    spec = importlib.util.spec_from_file_location("qa_writeback_runtime_under_test", root / "api/apps/services/qa_writeback_service.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    state = SimpleNamespace(config_calls=[], default_calls=[], closed=0, encoded=[], insert_errors=[], inserted=[], index_exists=False, max_length=8192)

    def config(tenant, model_type, name):
        assert name, "An unset chat model must resolve through tenant defaults"
        state.config_calls.append((tenant, model_type, name))
        return {"llm_name": name}

    provider = ModuleType("api.db.joint_services.tenant_model_service")
    provider.get_model_config_from_provider_instance = config
    provider.get_tenant_default_model_by_type = lambda tenant, typ: state.default_calls.append((tenant, typ)) or {"llm_name": "tenant-default"}
    monkeypatch.setitem(sys.modules, provider.__name__, provider)

    class Model:
        def __init__(self, tenant, config, **kwargs):
            state.last_model = config["llm_name"]
            self.max_length = state.max_length

        def __enter__(self):
            return self

        def __exit__(self, *args):
            self.close()

        def close(self):
            state.closed += 1

        def encode(self, texts):
            state.encoded.append(texts)
            return [[0.1, 0.2]], 3

    bundle = ModuleType("api.db.services.llm_service")
    bundle.LLMBundle = Model
    monkeypatch.setitem(sys.modules, bundle.__name__, bundle)
    documents = ModuleType("api.db.services.document_service")
    documents.DocumentService = SimpleNamespace(get_embd_id=lambda doc: "dataset-embedding")
    monkeypatch.setitem(sys.modules, documents.__name__, documents)
    generator = ModuleType("rag.prompts.generator")

    async def extract(model, content, top_n):
        state.extraction = (content, top_n)
        return "电源,开机"

    generator.keyword_extraction = extract
    monkeypatch.setitem(sys.modules, generator.__name__, generator)
    token = ModuleType("common.token_utils")
    token.num_tokens_from_string = len
    monkeypatch.setitem(sys.modules, token.__name__, token)
    nlp = ModuleType("rag.nlp")
    nlp.rag_tokenizer = SimpleNamespace(tokenize=lambda text: "tokens:" + text, fine_grained_tokenize=lambda text: "fine:" + text)
    monkeypatch.setitem(sys.modules, nlp.__name__, nlp)
    search = ModuleType("rag.nlp.search")
    search.index_name = lambda tenant: "ragflow_" + tenant
    monkeypatch.setitem(sys.modules, search.__name__, search)
    settings = ModuleType("common.settings")
    settings.docStoreConn = SimpleNamespace(
        index_exist=lambda *args: state.index_exists,
        create_idx=lambda *args: setattr(state, "created_index", args),
        insert=lambda rows, *args: state.inserted.extend(rows) or state.insert_errors,
        get=lambda *args: {"id": args[0]},
    )
    monkeypatch.setitem(sys.modules, settings.__name__, settings)
    import common

    monkeypatch.setattr(common, "settings", settings, raising=False)
    kb = SimpleNamespace(id="kb", tenant_id="owner", language="Chinese", parser_config={})
    doc = SimpleNamespace(id="doc", name="QA", parser_id="qa", parser_config={})
    return mod, mod.QAWritebackRuntime(), state, kb, doc


async def test_unset_chat_model_uses_tenant_default(setup):
    _, runtime, state, kb, doc = setup
    assert await runtime.extract_keywords(kb, doc, "问答", 5) == "电源,开机"
    assert state.default_calls == [("owner", "chat")]
    assert state.last_model == "tenant-default" and state.closed == 1


async def test_document_chat_model_takes_precedence(setup):
    _, runtime, state, kb, doc = setup
    kb.parser_config = {"llm_id": "kb-chat"}
    doc.parser_config = {"llm_id": "doc-chat"}
    await runtime.extract_keywords(kb, doc, "完整问答", 3)
    assert state.config_calls == [("owner", "chat", "doc-chat")]
    assert state.extraction == ("完整问答", 3) and state.closed == 1


async def test_embedding_uses_question_and_dataset_model(setup):
    _, runtime, state, kb, doc = setup
    vector, tokens = await runtime.embed(kb, doc, "问题")
    assert vector == [0.1, 0.2] and tokens == 3
    assert state.encoded == [["问题"]] and state.config_calls == [("owner", "embedding", "dataset-embedding")]
    assert state.closed == 1


async def test_model_length_limit_rejects_instead_of_truncating(setup):
    mod, runtime, state, kb, doc = setup
    state.max_length = 4
    with pytest.raises(mod.QAWritebackError) as exc:
        await runtime.embed(kb, doc, "question")
    assert exc.value.status == 400 and not state.encoded and state.closed == 1


def test_chunk_has_question_and_keyword_fields_and_full_answer(setup):
    _, runtime, _, kb, doc = setup
    chunk = runtime.build_chunk(kb, doc, {"question": "问题", "answer": "第一行\n第二行"}, "chunk", ["电源"], [0.1, 0.2])
    assert chunk["content_with_weight"] == "问题：问题\t回答：第一行\n第二行"
    assert chunk["question_kwd"] == ["问题"] and chunk["question_tks"] == "tokens:问题"
    assert chunk["important_kwd"] == ["电源"] and chunk["important_tks"] == "tokens:电源"
    assert chunk["q_2_vec"] == [0.1, 0.2] and chunk["available_int"] == 1


def test_missing_index_is_initialized_before_insert(setup):
    _, runtime, state, kb, doc = setup
    chunk = {"id": "chunk", "q_2_vec": [0.1, 0.2]}
    runtime.insert_chunk(kb, doc, chunk)
    assert state.created_index == ("ragflow_owner", "kb", 2, "qa")
    assert state.inserted == [chunk]


def test_nonempty_storage_error_list_is_failure(setup):
    _, runtime, state, kb, doc = setup
    state.index_exists = True
    state.insert_errors = ["bulk item rejected"]
    with pytest.raises(RuntimeError):
        runtime.insert_chunk(kb, doc, {"id": "chunk", "q_2_vec": [0.1, 0.2]})


def test_get_missing_index_returns_no_chunk(setup):
    _, runtime, _, kb, _ = setup
    assert runtime.get_chunk(kb, "chunk") is None
