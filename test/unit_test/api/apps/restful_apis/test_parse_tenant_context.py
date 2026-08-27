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

import asyncio
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

import api.apps
import api.utils.api_utils


class _DummyManager:
    def route(self, *_args, **_kwargs):
        return lambda function: function


class _AwaitableValue:
    def __init__(self, value):
        self.value = value

    def __await__(self):
        async def resolve():
            return self.value

        return resolve().__await__()


class _Document:
    def __init__(self, task_status, *, document_id="doc-1", kb_id="kb-1"):
        self.id = document_id
        self.kb_id = kb_id
        self.run = task_status

    def to_dict(self):
        return {"id": self.id, "kb_id": self.kb_id, "run": self.run}


def _load_route_module(monkeypatch, module_name):
    def identity_login_required(function=None, **_kwargs):
        if function is None:
            return lambda decorated: decorated
        return function

    monkeypatch.setattr(api.apps, "login_required", identity_login_required)
    monkeypatch.setattr(api.utils.api_utils, "add_tenant_id_to_kwargs", lambda function: function)
    module_path = Path(__file__).resolve().parents[5] / "api" / "apps" / "restful_apis" / f"{module_name}.py"
    spec = importlib.util.spec_from_file_location(f"test_{module_name}_tenant_context", module_path)
    module = importlib.util.module_from_spec(spec)
    module.manager = _DummyManager()
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def document_api_module(monkeypatch):
    return _load_route_module(monkeypatch, "document_api")


@pytest.fixture()
def chunk_api_module(monkeypatch):
    return _load_route_module(monkeypatch, "chunk_api")


def _run(coro):
    return asyncio.run(coro)


async def _run_inline(function, *args, **kwargs):
    return function(*args, **kwargs)


def _allow_team_member(monkeypatch, module, *, owner_id="owner-1"):
    monkeypatch.setattr(module.KnowledgebaseService, "accessible", lambda **_kwargs: True)
    monkeypatch.setattr(
        module.KnowledgebaseService,
        "get_by_id",
        lambda _dataset_id: (True, SimpleNamespace(id="kb-1", tenant_id=owner_id)),
    )
    monkeypatch.setattr(module, "get_request_json", lambda: _AwaitableValue({"document_ids": ["doc-1"]}))
    monkeypatch.setattr(module, "check_duplicate_ids", lambda ids, _kind: (ids, []))


def test_team_member_parse_documents_uses_dataset_owner_tenant(document_api_module, monkeypatch):
    module = document_api_module
    document = _Document(module.TaskStatus.UNSTART.value)
    deleted = []
    run_tenants = []

    _allow_team_member(monkeypatch, module)
    monkeypatch.setattr(module, "thread_pool_exec", _run_inline)
    monkeypatch.setattr(module.DocumentService, "query", lambda **_kwargs: [document])
    monkeypatch.setattr(module.DocumentService, "get_by_id", lambda _doc_id: (True, document))
    monkeypatch.setattr(module.DocumentService, "try_start_parse", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(module.DocumentService, "update_by_id", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(module.DocumentService, "run", lambda tenant_id, *_args: run_tenants.append(tenant_id))
    monkeypatch.setattr(module.TaskService, "filter_delete", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(module.search, "index_name", lambda tenant_id: f"idx-{tenant_id}")
    monkeypatch.setattr(
        module.settings,
        "docStoreConn",
        SimpleNamespace(
            index_exist=lambda *_args: True,
            delete=lambda condition, index, kb_id: deleted.append((condition, index, kb_id)),
        ),
    )

    result = _run(module.parse_documents("member-2", "kb-1"))

    assert result["code"] == 0
    assert run_tenants == ["owner-1"]
    assert deleted == [({"doc_id": "doc-1"}, "idx-owner-1", "kb-1")]


def test_team_member_stop_parse_documents_cleans_owner_index(document_api_module, monkeypatch):
    module = document_api_module
    document = _Document(module.TaskStatus.RUNNING.value)
    deleted = []

    _allow_team_member(monkeypatch, module)
    monkeypatch.setattr(module, "thread_pool_exec", _run_inline)
    monkeypatch.setattr(module.DocumentService, "query", lambda **_kwargs: [document])
    monkeypatch.setattr(module.DocumentService, "get_by_id", lambda _doc_id: (True, document))
    monkeypatch.setattr(module.DocumentService, "update_by_id", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(module.TaskService, "query", lambda **_kwargs: [SimpleNamespace(progress=0.5)])
    monkeypatch.setattr(module, "cancel_all_task_of", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(module.search, "index_name", lambda tenant_id: f"idx-{tenant_id}")
    monkeypatch.setattr(
        module.settings,
        "docStoreConn",
        SimpleNamespace(
            index_exist=lambda *_args: True,
            delete=lambda condition, index, kb_id: deleted.append((condition, index, kb_id)),
        ),
    )

    result = _run(module.stop_parse_documents("member-2", "kb-1"))

    assert result["code"] == 0
    assert deleted == [({"doc_id": "doc-1"}, "idx-owner-1", "kb-1")]


def test_team_member_sdk_parse_uses_dataset_owner_tenant(chunk_api_module, monkeypatch):
    module = chunk_api_module
    document = _Document(module.TaskStatus.UNSTART.value)
    deleted = []
    queued_tenants = []

    _allow_team_member(monkeypatch, module)
    monkeypatch.setattr(module.DocumentService, "query", lambda **_kwargs: [document])
    monkeypatch.setattr(module.DocumentService, "get_by_id", lambda _doc_id: (True, document))
    monkeypatch.setattr(module.DocumentService, "try_start_parse", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(module.DocumentService, "update_by_id", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(module.TaskService, "filter_delete", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(module.File2DocumentService, "get_storage_address", lambda **_kwargs: ("bucket", "name"))
    monkeypatch.setattr(module, "queue_tasks", lambda doc, *_args: queued_tenants.append(doc["tenant_id"]))
    monkeypatch.setattr(module.search, "index_name", lambda tenant_id: f"idx-{tenant_id}")
    monkeypatch.setattr(
        module.settings,
        "docStoreConn",
        SimpleNamespace(
            index_exist=lambda *_args: True,
            delete=lambda condition, index, kb_id: deleted.append((condition, index, kb_id)),
        ),
    )

    result = _run(module.parse("member-2", "kb-1"))

    assert result["code"] == 0
    assert queued_tenants == ["owner-1"]
    assert deleted == [({"doc_id": "doc-1"}, "idx-owner-1", "kb-1")]


def test_team_member_sdk_stop_parsing_cleans_owner_index(chunk_api_module, monkeypatch):
    module = chunk_api_module
    document = _Document(module.TaskStatus.RUNNING.value)
    deleted = []

    _allow_team_member(monkeypatch, module)
    monkeypatch.setattr(module.DocumentService, "query", lambda **_kwargs: [document])
    monkeypatch.setattr(module.DocumentService, "update_by_id", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(module, "cancel_all_task_of", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(module.search, "index_name", lambda tenant_id: f"idx-{tenant_id}")
    monkeypatch.setattr(
        module.settings,
        "docStoreConn",
        SimpleNamespace(
            index_exist=lambda *_args: True,
            delete=lambda condition, index, kb_id: deleted.append((condition, index, kb_id)),
        ),
    )

    result = _run(module.stop_parsing("member-2", "kb-1"))

    assert result["code"] == 0
    assert deleted == [({"doc_id": "doc-1"}, "idx-owner-1", "kb-1")]
