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


def _run(awaitable):
    return asyncio.run(awaitable)


@pytest.fixture()
def knowledge_file_api_module(monkeypatch):
    monkeypatch.setattr(api.apps, "login_required", lambda function: function)
    monkeypatch.setattr(api.utils.api_utils, "add_tenant_id_to_kwargs", lambda function: function)
    module_path = Path(__file__).resolve().parents[5] / "api" / "apps" / "restful_apis" / "knowledge_file_api.py"
    spec = importlib.util.spec_from_file_location("test_knowledge_file_api_unit", module_path)
    module = importlib.util.module_from_spec(spec)
    module.manager = _DummyManager()
    spec.loader.exec_module(module)
    return module


def _kb():
    return SimpleNamespace(id="kb-1", tenant_id="tenant-1", name="知识库", permission="me")


def test_list_entries_passes_validated_query(knowledge_file_api_module, monkeypatch):
    module = knowledge_file_api_module
    captured = {}
    monkeypatch.setattr(module, "request", SimpleNamespace(args={"parent_id": "folder-1", "keywords": "审批", "page": "2", "page_size": "10"}))
    monkeypatch.setattr(module.KnowledgebaseService, "get_by_id", lambda _kb_id: (True, _kb()))
    monkeypatch.setattr(module, "check_kb_team_permission", lambda _kb_record, _tenant_id: True)
    monkeypatch.setattr(
        module.KnowledgeFileService,
        "list_entries",
        lambda _kb_record, _tenant_id, **kwargs: captured.update(kwargs) or {"entries": [], "total": 0},
    )

    response = _run(module.list_entries("kb-1", "tenant-1"))

    assert response["code"] == 0
    assert captured["parent_id"] == "folder-1"
    assert captured["keywords"] == "审批"
    assert captured["page"] == 2
    assert captured["page_size"] == 10


def test_delete_entries_returns_partial_failures(knowledge_file_api_module, monkeypatch):
    module = knowledge_file_api_module
    monkeypatch.setattr(module.KnowledgebaseService, "get_by_id", lambda _kb_id: (True, _kb()))
    monkeypatch.setattr(module, "check_kb_team_permission", lambda _kb_record, _tenant_id: True)
    monkeypatch.setattr(module, "get_request_json", lambda: _async_value({"ids": ["folder-1"]}))
    monkeypatch.setattr(
        module.KnowledgeFileService,
        "delete_entries",
        lambda *_args: {
            "deleted": 2,
            "failed": [{"id": "f3", "path": "目录/A.docx", "message": "storage unavailable"}],
        },
    )

    response = _run(module.delete_entries("kb-1", "tenant-1"))

    assert response["code"] == 500
    assert response["data"]["deleted"] == 2
    assert response["data"]["failed"][0]["path"] == "目录/A.docx"


async def _async_value(value):
    return value
