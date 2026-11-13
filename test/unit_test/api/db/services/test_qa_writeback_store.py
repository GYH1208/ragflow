"""Exercise the real ledger SQL against an isolated SQLite database."""

import ast
import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import peewee
import pytest

ROOT = Path(__file__).resolve().parents[5]


@pytest.fixture
def setup(tmp_path, monkeypatch):
    app_spec = importlib.util.spec_from_file_location("api.apps.services.qa_writeback_service", ROOT / "api/apps/services/qa_writeback_service.py")
    app = importlib.util.module_from_spec(app_spec)
    monkeypatch.setitem(sys.modules, app_spec.name, app)
    app_spec.loader.exec_module(app)
    test_db = peewee.SqliteDatabase(tmp_path / "ledger.db")

    class Base(peewee.Model):
        class Meta:
            database = test_db

    class JSONField(peewee.TextField):
        def db_value(self, value):
            return json.dumps(value) if value is not None else None

        def python_value(self, value):
            return json.loads(value) if value is not None else None

    namespace = {**vars(peewee), "DataBaseModel": Base, "JSONField": JSONField}
    tree = ast.parse((ROOT / "api/db/db_models.py").read_text())
    model = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "QAWriteback")
    exec(compile(ast.Module(body=[model], type_ignores=[]), "api/db/db_models.py", "exec"), namespace)  # noqa: S102 - trusted model definition, isolated DB
    ledger = namespace["QAWriteback"]

    class Knowledgebase(Base):
        id = peewee.CharField(primary_key=True)
        tenant_id = peewee.CharField(default="tenant")
        chunk_num = peewee.IntegerField(default=0)
        token_num = peewee.IntegerField(default=0)
        status = peewee.CharField(default="1")

    class Document(Base):
        id = peewee.CharField(primary_key=True)
        kb_id = peewee.CharField()
        chunk_num = peewee.IntegerField(default=0)
        token_num = peewee.IntegerField(default=0)
        status = peewee.CharField(default="1")
        run = peewee.CharField(default="0")
        parser_id = peewee.CharField(default="qa")

    models = ModuleType("api.db.db_models")
    models.DB, models.QAWriteback, models.Document, models.Knowledgebase = test_db, ledger, Document, Knowledgebase
    monkeypatch.setitem(sys.modules, "api.db.db_models", models)
    store_spec = importlib.util.spec_from_file_location("qa_writeback_store_under_test", ROOT / "api/db/services/qa_writeback_service.py")
    store_module = importlib.util.module_from_spec(store_spec)
    store_spec.loader.exec_module(store_module)
    test_db.create_tables([ledger, Knowledgebase, Document])
    Knowledgebase.create(id="kb")
    Document.create(id="doc", kb_id="kb")
    identity = {"id": "a" * 64, "tenant_id": "tenant", "dataset_id": "kb", "document_id": "doc", "source_system": "oa", "external_id": "CaseSensitive-ID"}
    yield store_module.QAWritebackStore(), ledger, Document, Knowledgebase, identity, app, store_module
    test_db.close()


def test_claim_conflict_and_concurrent_processing(setup):
    store, ledger, _, _, identity, app, _ = setup
    row, owner = store.claim(identity, "fingerprint", "chunk")
    assert owner and not row["completed"]
    with pytest.raises(app.QAWritebackError) as exc:
        store.claim(identity, "fingerprint", "chunk")
    assert exc.value.error_code == "REQUEST_PROCESSING"
    with pytest.raises(app.QAWritebackError) as exc:
        store.claim(identity, "different", "chunk")
    assert exc.value.error_code == "IDEMPOTENCY_CONFLICT"
    assert ledger.select().count() == 1


def test_expired_claim_is_recovered_and_old_owner_cannot_write(setup):
    store, ledger, _, _, identity, app, _ = setup
    _, owner = store.claim(identity, "fingerprint", "chunk")
    ledger.update(lease_until=0).execute()
    _, next_owner = store.claim(identity, "fingerprint", "chunk")
    assert next_owner != owner
    with pytest.raises(app.QAWritebackError):
        store.prepare(identity["id"], owner, {"token_num": 7})
    store.release(identity["id"], owner)
    assert ledger.get_by_id(identity["id"]).lease_owner == next_owner


def test_prepared_result_survives_release_and_reclaim(setup):
    store, _, _, _, identity, _, _ = setup
    _, owner = store.claim(identity, "fingerprint", "chunk")
    prepared = {"chunk": {"important_kwd": ["word"], "q_2_vec": [0.1, 0.2]}, "keyword_status": "generated", "token_num": 7}
    store.prepare(identity["id"], owner, prepared)
    store.release(identity["id"], owner)
    row, _ = store.claim(identity, "fingerprint", "chunk")
    assert row["prepared"] == prepared


def test_completion_and_both_counters_commit_once(setup):
    store, ledger, document, kb, identity, _, _ = setup
    _, owner = store.claim(identity, "fingerprint", "chunk")
    store.prepare(identity["id"], owner, {"token_num": 7})
    store.finish(identity["id"], owner, lambda: True)
    row, retry_owner = store.claim(identity, "fingerprint", "chunk")
    assert row["completed"] is True and retry_owner == ""
    assert ledger.get_by_id(identity["id"]).completed is True
    assert document.get_by_id("doc").chunk_num == kb.get_by_id("kb").chunk_num == 1
    assert document.get_by_id("doc").token_num == kb.get_by_id("kb").token_num == 7


def test_failure_updating_kb_rolls_back_document_counter_and_completion(setup):
    store, ledger, document, kb, identity, _, _ = setup
    _, owner = store.claim(identity, "fingerprint", "chunk")
    store.prepare(identity["id"], owner, {"token_num": 7})
    database = ledger._meta.database
    database.execute_sql("CREATE TRIGGER reject_kb_update BEFORE UPDATE ON knowledgebase BEGIN SELECT RAISE(ABORT, 'simulated write failure'); END")
    with pytest.raises(peewee.IntegrityError):
        store.finish(identity["id"], owner, lambda: True)
    assert not ledger.get_by_id(identity["id"]).completed
    assert document.get_by_id("doc").chunk_num == document.get_by_id("doc").token_num == 0
    database.execute_sql("DROP TRIGGER reject_kb_update")
    store.finish(identity["id"], owner, lambda: True)
    assert document.get_by_id("doc").chunk_num == kb.get_by_id("kb").chunk_num == 1


@pytest.mark.parametrize("change,error", [({"run": "1"}, "DOCUMENT_BUSY"), ({"parser_id": "naive"}, "INVALID_DOCUMENT_TYPE"), ({"status": "0"}, "TARGET_NOT_FOUND")])
def test_target_rechecked_before_counter_commit(setup, change, error):
    store, ledger, document, kb, identity, app, _ = setup
    _, owner = store.claim(identity, "fingerprint", "chunk")
    store.prepare(identity["id"], owner, {"token_num": 7})
    document.update(**change).execute()
    published = []
    with pytest.raises(app.QAWritebackError) as exc:
        store.finish(identity["id"], owner, lambda: published.append(True))
    assert exc.value.error_code == error
    assert not ledger.get_by_id(identity["id"]).completed
    assert kb.get_by_id("kb").chunk_num == 0
    assert not published, "Busy/deleted/non-QA targets must be rejected before index writes"


@pytest.mark.parametrize("change", ["deleted", "disabled", "owner_changed"])
def test_dataset_rechecked_before_index_publication(setup, change):
    store, ledger, document, kb, identity, app, _ = setup
    _, owner = store.claim(identity, "fingerprint", "chunk")
    store.prepare(identity["id"], owner, {"token_num": 7})
    if change == "deleted":
        kb.delete().execute()
    elif change == "disabled":
        kb.update(status="0").execute()
    else:
        kb.update(tenant_id="new-owner").execute()
    published = []
    with pytest.raises(app.QAWritebackError) as exc:
        store.finish(identity["id"], owner, lambda: published.append(True))
    assert exc.value.error_code == "TARGET_NOT_FOUND"
    assert not published
    assert not ledger.get_by_id(identity["id"]).completed
    assert document.get_by_id("doc").chunk_num == 0


async def test_application_service_with_real_sql_ledger_recovers_and_deduplicates(setup):
    store, ledger, document, kb_model, identity, app, _ = setup
    test_spec = importlib.util.spec_from_file_location("qa_runtime_fake", ROOT / "test/unit_test/api/apps/services/test_qa_writeback_service.py")
    test_module = importlib.util.module_from_spec(test_spec)
    test_spec.loader.exec_module(test_module)
    runtime = test_module.Runtime()
    service = app.QAWritebackService(store=store, runtime=runtime)
    kb = SimpleNamespace(id="kb", tenant_id="tenant")
    doc = SimpleNamespace(id="doc", name="External Q&A")
    request = {"question": "问题", "answer": "完整答案\n第二行", "source_system": identity["source_system"], "external_id": identity["external_id"]}
    runtime.ambiguous_insert = True
    with pytest.raises(app.QAWritebackError):
        await service.create(kb, doc, request)
    assert document.get_by_id("doc").chunk_num == kb_model.get_by_id("kb").chunk_num == 0
    runtime.ambiguous_insert = False
    resumed = await service.create(kb, doc, request)
    duplicate = await service.create(kb, doc, request)
    assert resumed["chunk_id"] == duplicate["chunk_id"]
    assert resumed["created"] is duplicate["created"] is False
    assert runtime.embed_calls == runtime.extract_calls == runtime.insert_calls == 1
    assert document.get_by_id("doc").chunk_num == kb_model.get_by_id("kb").chunk_num == 1
    assert ledger.select().count() == 1 and ledger.get().completed
