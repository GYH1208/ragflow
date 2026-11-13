"""Storage regression tests for scoped dataset deletion and parent expansion."""

import ast
import logging
import sys
from collections import defaultdict
from pathlib import Path
from types import ModuleType, SimpleNamespace

import peewee
import pytest

ROOT = Path(__file__).resolve().parents[4]


def load_definition(path, name, namespace, *, parent=None):
    tree = ast.parse((ROOT / path).read_text())
    nodes = tree.body if parent is None else next(node for node in tree.body if getattr(node, "name", None) == parent).body
    node = next(node for node in nodes if getattr(node, "name", None) == name)
    node.decorator_list = []
    exec(compile(ast.fix_missing_locations(ast.Module(body=[node], type_ignores=[])), path, "exec"), namespace)  # noqa: S102
    return namespace[name]


async def test_deleting_dataset_preserves_other_same_named_folders(monkeypatch):
    database = peewee.SqliteDatabase(":memory:")

    class File(peewee.Model):
        id = peewee.TextField(primary_key=True)
        tenant_id = peewee.TextField()
        name = peewee.TextField()
        type = peewee.TextField(default="folder")
        source_type = peewee.TextField(default="knowledgebase")

        class Meta:
            database = None

    File._meta.set_database(database)
    database.create_tables([File])
    File.insert_many(
        [
            {"id": "selected-root", "tenant_id": "owner", "name": "Reports"},
            {"id": "excluded-root", "tenant_id": "owner", "name": "Reports"},
            {"id": "other-tenant-root", "tenant_id": "other-owner", "name": "Reports"},
        ]
    ).execute()
    kb = SimpleNamespace(id="allowed", tenant_id="owner", name="Reports")
    search = ModuleType("rag.nlp.search")
    search.index_name = lambda tenant_id: tenant_id
    nlp = ModuleType("rag.nlp")
    nlp.search = search
    monkeypatch.setitem(sys.modules, "rag.nlp", nlp)
    monkeypatch.setitem(sys.modules, "rag.nlp.search", search)
    ns = {
        "logging": logging,
        "File": File,
        "FileSource": SimpleNamespace(KNOWLEDGEBASE="knowledgebase"),
        "KnowledgebaseService": SimpleNamespace(get_or_none=lambda **kwargs: kb, delete_by_id=lambda _: True),
        "DocumentService": SimpleNamespace(query=lambda **kwargs: []),
        "FileService": SimpleNamespace(
            filter_delete=lambda conditions: File.delete().where(*conditions).execute(),
            get_or_create_kb_root=lambda kb_id, tenant_id: {"id": "selected-root"},
        ),
        "settings": SimpleNamespace(docStoreConn=SimpleNamespace(delete_idx=lambda *args: None)),
    }
    delete = load_definition("api/apps/services/dataset_api_service.py", "delete_datasets", ns)
    try:
        assert await delete("owner", ids=["allowed"]) == (True, {"success_count": 1})
        assert {row.id for row in File.select()} == {"excluded-root", "other-tenant-root"}
    finally:
        database.close()


@pytest.mark.parametrize("parent_dataset,parent_doc", [("excluded", "doc"), (["allowed", "excluded"], "doc"), ("allowed", "other-doc"), (None, "doc")])
def test_parent_expansion_rejects_mismatched_storage_binding(parent_dataset, parent_doc):
    child = {"chunk_id": "child", "mom_id": "parent", "kb_id": "allowed", "doc_id": "doc", "content_ltks": "safe", "content_with_weight": "safe", "similarity": 0.8}
    parent = {"kb_id": parent_dataset, "doc_id": parent_doc, "content_with_weight": "excluded content", "docnm_kwd": "excluded name"}
    ns = {"defaultdict": defaultdict, "logging": logging, "index_name": lambda value: value, "np": SimpleNamespace(mean=lambda values: sum(values) / len(values))}
    expand = load_definition("rag/nlp/search.py", "retrieval_by_children", ns, parent="Dealer")
    dealer = SimpleNamespace(dataStore=SimpleNamespace(get=lambda *args: parent))
    assert expand(dealer, [child.copy()], ["owner"]) == [child]


def test_parent_expansion_preserves_valid_same_document_parent():
    child = {"chunk_id": "child", "mom_id": "parent", "kb_id": "allowed", "doc_id": "doc", "content_ltks": "safe", "similarity": 0.8}
    parent = {"kb_id": ["allowed"], "doc_id": "doc", "content_with_weight": "complete answer"}
    ns = {"defaultdict": defaultdict, "logging": logging, "index_name": lambda value: value, "np": SimpleNamespace(mean=lambda values: sum(values) / len(values))}
    expand = load_definition("rag/nlp/search.py", "retrieval_by_children", ns, parent="Dealer")
    dealer = SimpleNamespace(dataStore=SimpleNamespace(get=lambda *args: parent))
    result = expand(dealer, [child], ["owner"])
    assert len(result) == 1 and result[0]["chunk_id"] == "parent"
    assert result[0]["content_with_weight"] == "complete answer"
    other_child = {**child, "chunk_id": "another-child", "doc_id": "other-doc"}
    assert expand(dealer, [child.copy(), other_child.copy()], ["owner"]) == [child, other_child]
