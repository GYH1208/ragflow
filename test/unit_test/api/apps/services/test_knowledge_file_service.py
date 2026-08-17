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

import pytest

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
