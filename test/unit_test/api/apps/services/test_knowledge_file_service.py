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
from types import SimpleNamespace
from contextlib import contextmanager, nullcontext

import pytest

from api.apps.services import knowledge_file_service as knowledge_file_service_module
from api.apps.services.knowledge_file_service import KnowledgeFileService
from api.db.services.document_service import DocumentService
from api.db.services.file2document_service import File2DocumentService
from api.db.services.file_service import FileService


def _entry(entry_id, parent_id, name, entry_type, create_time):
    return SimpleNamespace(
        id=entry_id,
        parent_id=parent_id,
        tenant_id="tenant-1",
        created_by="tenant-1",
        name=name,
        type=entry_type,
        source_type="knowledgebase",
        location="",
        size=0,
        create_time=create_time,
        update_time=create_time,
    )


@pytest.fixture()
def service_fixture(monkeypatch):
    kb = SimpleNamespace(id="kb-1", tenant_id="tenant-1", name="知识库")
    root = _entry("root", "kb-parent", "知识库", "folder", 1)
    top_folder = _entry("top", root.id, "2、二级文件", "folder", 4)
    nested_folder = _entry("nested", top_folder.id, "制度文件", "folder", 3)
    nested_file = _entry("nested-file", nested_folder.id, "A.docx", "doc", 2)
    root_file = _entry("root-file", root.id, "根目录.txt", "doc", 1)
    entries = {item.id: item for item in [root, top_folder, nested_folder, nested_file, root_file]}
    documents = {
        "doc-nested": {
            "id": "doc-nested",
            "kb_id": kb.id,
            "name": "A.docx",
            "type": "doc",
            "suffix": "docx",
            "status": "1",
            "run": "3",
            "chunk_num": 10,
            "token_num": 100,
            "create_time": 2,
            "update_time": 2,
        },
        "doc-root": {
            "id": "doc-root",
            "kb_id": kb.id,
            "name": "根目录.txt",
            "type": "doc",
            "suffix": "txt",
            "status": "1",
            "run": "0",
            "chunk_num": 0,
            "token_num": 0,
            "create_time": 1,
            "update_time": 1,
        },
    }
    links = [
        SimpleNamespace(file_id=nested_file.id, document_id="doc-nested"),
        SimpleNamespace(file_id=root_file.id, document_id="doc-root"),
    ]
    kb._entries = entries
    kb._documents = documents
    kb._links = links

    monkeypatch.setattr(FileService, "get_kb_folder", classmethod(lambda cls, _tenant_id: {"id": "kb-parent"}))
    monkeypatch.setattr(FileService, "new_a_file_from_kb", classmethod(lambda cls, *_args: {"id": root.id}))
    monkeypatch.setattr(FileService, "get_by_id", classmethod(lambda cls, entry_id: (entry_id in entries, entries.get(entry_id))))
    monkeypatch.setattr(FileService, "get_by_ids", classmethod(lambda cls, ids, cols=None: [entries[item_id] for item_id in ids if item_id in entries]))
    monkeypatch.setattr(
        FileService,
        "query",
        classmethod(
            lambda cls, **kwargs: [
                item
                for item in entries.values()
                if all(getattr(item, key) == value for key, value in kwargs.items())
            ]
        ),
    )
    monkeypatch.setattr(
        File2DocumentService,
        "get_by_file_ids",
        classmethod(lambda cls, file_ids: [link for link in links if link.file_id in file_ids]),
        raising=False,
    )
    monkeypatch.setattr(
        File2DocumentService,
        "get_by_document_ids",
        classmethod(lambda cls, document_ids: [vars(link) for link in links if link.document_id in document_ids]),
    )
    monkeypatch.setattr(
        DocumentService,
        "get_by_ids",
        classmethod(lambda cls, ids, cols=None: [SimpleNamespace(**documents[item_id]) for item_id in ids if item_id in documents]),
    )
    monkeypatch.setattr(
        FileService,
        "update_by_id",
        classmethod(lambda cls, entry_id, values: [setattr(entries[entry_id], key, value) for key, value in values.items()] or True),
    )
    monkeypatch.setattr(
        FileService,
        "insert",
        classmethod(lambda cls, data: entries.setdefault(data["id"], SimpleNamespace(**data))),
    )
    monkeypatch.setattr(FileService, "delete_by_id", classmethod(lambda cls, entry_id: entries.pop(entry_id, None) is not None))
    monkeypatch.setattr(
        FileService,
        "list_all_files_by_parent_id",
        classmethod(lambda cls, parent_id: [entry for entry in entries.values() if entry.parent_id == parent_id]),
    )
    monkeypatch.setattr(
        File2DocumentService,
        "get_by_file_id",
        classmethod(lambda cls, file_id: [link for link in links if link.file_id == file_id]),
    )
    monkeypatch.setattr(knowledge_file_service_module.DB, "atomic", lambda: nullcontext(), raising=False)

    def get_by_kb_id(_cls, _kb_id, page, page_size, _orderby, _desc, keywords, run_status, types, suffix, **_kwargs):
        matches = [doc for doc in documents.values() if keywords.lower() in doc["name"].lower()]
        if run_status:
            matches = [doc for doc in matches if doc["run"] in run_status]
        if types:
            matches = [doc for doc in matches if doc["type"] in types]
        if suffix:
            matches = [doc for doc in matches if doc["suffix"] in suffix]
        start = (page - 1) * page_size
        return [dict(doc) for doc in matches[start : start + page_size]], len(matches)

    monkeypatch.setattr(DocumentService, "get_by_kb_id", classmethod(get_by_kb_id))
    return kb, "tenant-1", root, top_folder, nested_folder, nested_file, root_file


def test_list_entries_returns_direct_folders_before_documents(service_fixture):
    kb, tenant_id, root, top_folder, _nested_folder, _nested_file, root_file = service_fixture

    result = KnowledgeFileService.list_entries(
        kb,
        tenant_id,
        parent_id=root.id,
        page=1,
        page_size=20,
        orderby="create_time",
        desc=True,
        keywords="",
        filters={},
    )

    assert [entry["entry_type"] for entry in result["entries"]] == ["folder", "document"]
    assert result["entries"][0]["id"] == top_folder.id
    assert result["entries"][1]["file_id"] == root_file.id
    assert result["total"] == 2


def test_global_search_includes_relative_path(service_fixture):
    kb, tenant_id, _root, _top_folder, nested_folder, _nested_file, _root_file = service_fixture

    result = KnowledgeFileService.list_entries(
        kb,
        tenant_id,
        parent_id=nested_folder.id,
        page=1,
        page_size=20,
        orderby="create_time",
        desc=True,
        keywords="A.docx",
        filters={},
    )

    assert [entry["entry_type"] for entry in result["entries"]] == ["document"]
    assert result["entries"][0]["relative_path"] == "2、二级文件/制度文件/A.docx"


def test_rejects_folder_from_another_knowledge_base(service_fixture, monkeypatch):
    kb, tenant_id, *_ = service_fixture
    other_folder = _entry("other", "other-root", "其他目录", "folder", 1)
    original_get_by_id = FileService.get_by_id
    monkeypatch.setattr(
        FileService,
        "get_by_id",
        classmethod(lambda cls, entry_id: (True, other_folder) if entry_id == other_folder.id else original_get_by_id(entry_id)),
    )

    with pytest.raises(PermissionError):
        KnowledgeFileService.list_entries(
            kb,
            tenant_id,
            parent_id=other_folder.id,
            page=1,
            page_size=20,
            orderby="create_time",
            desc=True,
            keywords="",
            filters={},
        )


def test_get_ancestors_returns_root_to_current_folder(service_fixture):
    kb, tenant_id, root, top_folder, nested_folder, *_ = service_fixture

    ancestors = KnowledgeFileService.get_ancestors(kb, tenant_id, nested_folder.id)

    assert [item["id"] for item in ancestors] == [root.id, top_folder.id, nested_folder.id]


def test_create_folder_adds_knowledge_base_folder(service_fixture, monkeypatch):
    kb, tenant_id, root, *_ = service_fixture
    monkeypatch.setattr(knowledge_file_service_module, "get_uuid", lambda: "new-folder")

    result = KnowledgeFileService.create_folder(kb, tenant_id, root.id, "新建目录")

    assert result["id"] == "new-folder"
    assert result["parent_id"] == root.id
    assert result["name"] == "新建目录"
    assert kb._entries["new-folder"].source_type == "knowledgebase"


def test_create_folder_does_not_close_connection_inside_transaction(service_fixture, monkeypatch):
    kb, tenant_id, root, *_ = service_fixture
    transaction_state = {"open": False}
    original_query = FileService.query
    original_insert = FileService.insert

    @contextmanager
    def guarded_atomic():
        transaction_state["open"] = True
        try:
            yield
        finally:
            transaction_state["open"] = False

    def guarded_query(cls, **kwargs):
        if transaction_state["open"]:
            raise RuntimeError("Attempting to close database while transaction is open.")
        return original_query(**kwargs)

    def guarded_insert(cls, data):
        if transaction_state["open"]:
            raise RuntimeError("Attempting to close database while transaction is open.")
        return original_insert(data)

    monkeypatch.setattr(knowledge_file_service_module.DB, "atomic", guarded_atomic)
    monkeypatch.setattr(FileService, "query", classmethod(guarded_query))
    monkeypatch.setattr(FileService, "insert", classmethod(guarded_insert))
    monkeypatch.setattr(knowledge_file_service_module, "get_uuid", lambda: "transaction-safe-folder")

    result = KnowledgeFileService.create_folder(kb, tenant_id, root.id, "事务安全目录")

    assert result["id"] == "transaction-safe-folder"
    assert result["parent_id"] == root.id


def test_move_document_changes_only_file_parent(service_fixture, monkeypatch):
    kb, tenant_id, root, _top_folder, _nested_folder, nested_file, _root_file = service_fixture
    original_location = nested_file.location
    monkeypatch.setattr(FileService, "move_file", classmethod(lambda cls, *_args: (_ for _ in ()).throw(AssertionError("generic storage move must not be used"))))

    result = KnowledgeFileService.move_entries(kb, tenant_id, [nested_file.id], root.id)

    assert result == {"moved": 1}
    assert nested_file.parent_id == root.id
    assert nested_file.location == original_location


def test_move_folder_rejects_descendant_destination(service_fixture):
    kb, tenant_id, _root, top_folder, nested_folder, *_ = service_fixture

    with pytest.raises(ValueError, match="own descendant"):
        KnowledgeFileService.move_entries(kb, tenant_id, [top_folder.id], nested_folder.id)


def test_rename_document_uses_title_only_update(service_fixture, monkeypatch):
    kb, tenant_id, _root, _top_folder, _nested_folder, nested_file, _root_file = service_fixture
    calls = []

    def rename_only(document_id, name):
        calls.append((document_id, name))
        nested_file.name = name
        kb._documents[document_id]["name"] = name
        return None

    monkeypatch.setattr(knowledge_file_service_module, "update_document_name_only", rename_only)

    result = KnowledgeFileService.rename_entry(kb, tenant_id, nested_file.id, "新名称.docx")

    assert result == {"id": nested_file.id, "name": "新名称.docx"}
    assert calls == [("doc-nested", "新名称.docx")]
    assert kb._documents["doc-nested"]["run"] == "3"
    assert kb._documents["doc-nested"]["chunk_num"] == 10


def test_delete_folder_removes_documents_before_folders(service_fixture, monkeypatch):
    kb, tenant_id, _root, _top_folder, nested_folder, nested_file, _root_file = service_fixture
    operations = []

    def delete_docs(document_ids, _tenant_id):
        operations.append(("document", document_ids[0]))
        kb._entries.pop(nested_file.id, None)
        return ""

    def delete_entry(entry_id):
        operations.append(("folder", entry_id))
        return kb._entries.pop(entry_id, None) is not None

    monkeypatch.setattr(FileService, "delete_docs", classmethod(lambda cls, ids, uid: delete_docs(ids, uid)))
    monkeypatch.setattr(FileService, "delete_by_id", classmethod(lambda cls, entry_id: delete_entry(entry_id)))

    result = KnowledgeFileService.delete_entries(kb, tenant_id, [nested_folder.id])

    assert result == {"deleted": 2, "failed": []}
    assert operations == [("document", "doc-nested"), ("folder", nested_folder.id)]


def test_delete_preflight_failure_makes_no_changes(service_fixture, monkeypatch):
    kb, tenant_id, *_ = service_fixture
    other_folder = _entry("other", "other-root", "其他目录", "folder", 1)
    original_get_by_id = FileService.get_by_id
    deletes = []
    monkeypatch.setattr(
        FileService,
        "get_by_id",
        classmethod(lambda cls, entry_id: (True, other_folder) if entry_id == other_folder.id else original_get_by_id(entry_id)),
    )
    monkeypatch.setattr(FileService, "delete_by_id", classmethod(lambda cls, entry_id: deletes.append(entry_id)))

    with pytest.raises(PermissionError):
        KnowledgeFileService.delete_entries(kb, tenant_id, [other_folder.id])
    assert deletes == []


def test_delete_reports_document_failure_with_path(service_fixture, monkeypatch):
    kb, tenant_id, _root, _top_folder, nested_folder, *_ = service_fixture
    monkeypatch.setattr(FileService, "delete_docs", classmethod(lambda cls, _ids, _uid: "storage unavailable"))

    result = KnowledgeFileService.delete_entries(kb, tenant_id, [nested_folder.id])

    assert result["deleted"] == 0
    assert result["failed"] == [
        {
            "id": "nested-file",
            "path": "2、二级文件/制度文件/A.docx",
            "message": "storage unavailable",
        }
    ]
