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
from werkzeug.datastructures import MultiDict

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


class _DummyFiles(dict):
    def getlist(self, key):
        value = self.get(key, [])
        return value if isinstance(value, list) else [value]


class _DummyRequest:
    def __init__(self, form, files):
        self._form = form
        self._files = files
        self.args = {}

    @property
    def form(self):
        return _AwaitableValue(self._form)

    @property
    def files(self):
        return _AwaitableValue(self._files)


@pytest.fixture()
def document_api_module(monkeypatch):
    def identity_login_required(function=None, **_kwargs):
        if function is None:
            return lambda decorated: decorated
        return function

    monkeypatch.setattr(api.apps, "login_required", identity_login_required)
    monkeypatch.setattr(api.utils.api_utils, "add_tenant_id_to_kwargs", lambda function: function)
    module_path = Path(__file__).resolve().parents[5] / "api" / "apps" / "restful_apis" / "document_api.py"
    spec = importlib.util.spec_from_file_location("test_document_upload_parent_unit", module_path)
    module = importlib.util.module_from_spec(spec)
    module.manager = _DummyManager()
    spec.loader.exec_module(module)
    return module


def test_local_upload_forwards_current_folder_to_file_service(document_api_module, monkeypatch):
    module = document_api_module
    captured = {}
    kb = SimpleNamespace(id="kb-1", tenant_id="tenant-1")
    upload_file = SimpleNamespace(filename="A.txt")
    monkeypatch.setattr(
        module,
        "request",
        _DummyRequest(
            form=MultiDict([("relative_path", "上传目录/A.txt"), ("parent_id", "current-folder")]),
            files=_DummyFiles({"file": [upload_file]}),
        ),
    )
    monkeypatch.setattr(
        module.KnowledgeFileService,
        "assert_folder_in_kb",
        lambda _kb, _tenant_id, folder_id: SimpleNamespace(id=folder_id),
    )

    async def capture_thread_pool(_function, *_args, **kwargs):
        captured.update(kwargs)
        return ["stop after capturing upload arguments"], []

    monkeypatch.setattr(module, "thread_pool_exec", capture_thread_pool)

    asyncio.run(module._upload_local_documents(kb, "tenant-1"))

    assert captured["parent_folder_id"] == "current-folder"
