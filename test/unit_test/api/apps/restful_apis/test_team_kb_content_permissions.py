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
from api.apps.services.dataset_api_service import delete_datasets, update_dataset
from api.common.check_team_permission import check_kb_team_permission
from api.db import TenantPermission
from api.db.services.team_service import TeamMemberService, TeamService
from common.constants import ParserType, StatusEnum, TaskStatus


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


class _UploadFiles:
    def __init__(self, files):
        self.files = files

    def __contains__(self, key):
        return key == "file"

    def getlist(self, _key):
        return self.files


class _Vector:
    def __init__(self, values):
        self.values = list(values)

    def __rmul__(self, scalar):
        return _Vector([scalar * value for value in self.values])

    def __add__(self, other):
        return _Vector([left + right for left, right in zip(self.values, other.values, strict=True)])

    def __len__(self):
        return len(self.values)

    def tolist(self):
        return self.values


def _load_route_module(monkeypatch, module_name):
    def identity_login_required(function=None, **_kwargs):
        if function is None:
            return lambda decorated: decorated
        return function

    monkeypatch.setattr(api.apps, "login_required", identity_login_required)
    monkeypatch.setattr(api.utils.api_utils, "add_tenant_id_to_kwargs", lambda function: function)
    module_path = Path(__file__).resolve().parents[5] / "api" / "apps" / "restful_apis" / f"{module_name}.py"
    spec = importlib.util.spec_from_file_location(f"test_team_content_{module_name}", module_path)
    module = importlib.util.module_from_spec(spec)
    module.manager = _DummyManager()
    spec.loader.exec_module(module)
    return module


def _run(coro):
    return asyncio.run(coro)


async def _run_inline(function, *args, **kwargs):
    return function(*args, **kwargs)


def _team_kb():
    return SimpleNamespace(
        id="kb-hr",
        tenant_id="owner-1",
        name="HR",
        permission=TenantPermission.TEAM.value,
        team_id="team-hr",
        status=StatusEnum.VALID.value,
        parser_id=ParserType.NAIVE.value,
        pipeline_id=None,
        parser_config={},
    )


@pytest.fixture()
def permission_harness(monkeypatch):
    document_api = _load_route_module(monkeypatch, "document_api")
    chunk_api = _load_route_module(monkeypatch, "chunk_api")
    kb = _team_kb()
    memberships = {
        "member-active": ["team-hr"],
        "member-invited": [],
        "member-removed": [],
        "member-other": ["team-finance"],
    }
    monkeypatch.setattr(TeamMemberService, "active_team_ids", lambda user_id: memberships.get(user_id, []))
    monkeypatch.setattr(
        TeamService,
        "get_owned_team",
        lambda team_id, owner_id: SimpleNamespace(id=team_id)
        if (team_id, owner_id) == ("team-hr", "owner-1")
        else None,
    )

    for module in (document_api, chunk_api):
        monkeypatch.setattr(module.KnowledgebaseService, "get_by_id", lambda _dataset_id: (True, kb))
        monkeypatch.setattr(
            module.KnowledgebaseService,
            "accessible",
            lambda kb_id, user_id: check_kb_team_permission(kb, user_id),
        )

    class PermissionHarness:
        def call(self, operation, user_id):
            document = SimpleNamespace(
                id="doc-1",
                kb_id=kb.id,
                name="A.txt",
                parser_id=ParserType.NAIVE.value,
                run=TaskStatus.RUNNING.value if operation == "stop_parse" else TaskStatus.UNSTART.value,
                to_dict=lambda: {"id": "doc-1", "kb_id": kb.id, "run": TaskStatus.UNSTART.value},
            )
            if operation == "upload":
                document_api.request = SimpleNamespace(args={"type": "empty"})

                async def upload_empty(*_args):
                    return document_api.get_result(data=True)

                monkeypatch.setattr(document_api, "_upload_empty_document", upload_empty)
                return _run(document_api.upload_document(kb.id, user_id))

            if operation == "delete_document":
                monkeypatch.setattr(
                    document_api,
                    "validate_and_parse_json_request",
                    lambda *_args, **_kwargs: _AwaitableValue(({"ids": [document.id]}, None)),
                )
                monkeypatch.setattr(document_api.DocumentService, "query", lambda **_kwargs: [document])
                monkeypatch.setattr(document_api, "check_duplicate_ids", lambda ids, _kind: (ids, []))
                monkeypatch.setattr(document_api, "thread_pool_exec", _run_inline)
                monkeypatch.setattr(document_api.FileService, "delete_docs", lambda *_args: "")
                return _run(document_api.delete_documents(user_id, kb.id))

            if operation in {"parse", "stop_parse"}:
                monkeypatch.setattr(
                    document_api,
                    "get_request_json",
                    lambda: _AwaitableValue({"document_ids": [document.id]}),
                )
                monkeypatch.setattr(document_api, "thread_pool_exec", _run_inline)
                monkeypatch.setattr(document_api.DocumentService, "query", lambda **_kwargs: [document])
                monkeypatch.setattr(document_api.DocumentService, "get_by_id", lambda _doc_id: (True, document))
                monkeypatch.setattr(document_api.DocumentService, "try_start_parse", lambda *_args, **_kwargs: True)
                monkeypatch.setattr(document_api.DocumentService, "update_by_id", lambda *_args, **_kwargs: True)
                monkeypatch.setattr(document_api.DocumentService, "run", lambda *_args, **_kwargs: None)
                monkeypatch.setattr(document_api.TaskService, "filter_delete", lambda *_args, **_kwargs: None)
                monkeypatch.setattr(document_api.TaskService, "query", lambda **_kwargs: [SimpleNamespace(progress=0.5)])
                monkeypatch.setattr(document_api, "cancel_all_task_of", lambda *_args, **_kwargs: None)
                monkeypatch.setattr(
                    document_api.settings,
                    "docStoreConn",
                    SimpleNamespace(index_exist=lambda *_args: False, delete=lambda *_args: 0),
                )
                route = document_api.parse_documents if operation == "parse" else document_api.stop_parse_documents
                return _run(route(user_id, kb.id))

            monkeypatch.setattr(chunk_api.DocumentService, "query", lambda **_kwargs: [document])
            monkeypatch.setattr(chunk_api, "get_request_json", lambda: _AwaitableValue({"delete_all": True}))
            monkeypatch.setattr(
                chunk_api.settings,
                "docStoreConn",
                SimpleNamespace(
                    get=lambda *_args: {
                        "id": "chunk-1",
                        "doc_id": document.id,
                        "content_with_weight": "existing content",
                    },
                    delete=lambda *_args: 0,
                    update=lambda *_args: True,
                ),
            )
            monkeypatch.setattr(chunk_api.DocumentService, "delete_chunk_images", lambda *_args: None)
            monkeypatch.setattr(chunk_api.DocumentService, "get_embd_id", lambda _doc_id: "embedding")
            monkeypatch.setattr(chunk_api, "get_model_config_from_provider_instance", lambda *_args: {})
            monkeypatch.setattr(
                chunk_api.TenantLLMService,
                "model_instance",
                lambda _config: SimpleNamespace(encode=lambda _texts: ([_Vector([1.0]), _Vector([1.0])], 1)),
            )
            if operation == "edit_chunk":
                monkeypatch.setattr(
                    chunk_api,
                    "get_request_json",
                    lambda: _AwaitableValue({"content": "updated content"}),
                )
                return _run(chunk_api.update_chunk(user_id, kb.id, document.id, "chunk-1"))
            return _run(chunk_api.rm_chunk(user_id, kb.id, document.id))

    return PermissionHarness()


@pytest.mark.parametrize(
    "operation",
    ["upload", "delete_document", "parse", "stop_parse", "edit_chunk", "delete_chunk"],
)
def test_active_member_can_maintain_assigned_team_dataset(operation, permission_harness):
    result = permission_harness.call(operation, user_id="member-active")

    assert result["code"] == 0


@pytest.mark.parametrize("user_id", ["member-invited", "member-removed", "member-other"])
def test_non_active_or_other_team_member_is_rejected(user_id, permission_harness):
    result = permission_harness.call("upload", user_id=user_id)

    assert result["code"] != 0


def test_local_upload_uses_owner_resources_and_actor_audit(monkeypatch):
    module = _load_route_module(monkeypatch, "document_api")
    kb = _team_kb()
    upload_file = SimpleNamespace(filename="A.txt")
    module.request = SimpleNamespace(
        form=_AwaitableValue({}),
        files=_AwaitableValue(_UploadFiles([upload_file])),
        args={},
    )
    captured = {}

    async def capture_upload(_function, *args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        return ["stop after capture"], []

    monkeypatch.setattr(module, "thread_pool_exec", capture_upload)

    _run(module._upload_local_documents(kb, "member-active"))

    assert captured["args"][2] == "owner-1"
    assert captured["kwargs"]["created_by"] == "member-active"


def test_delete_documents_uses_owner_resource_context(monkeypatch):
    module = _load_route_module(monkeypatch, "document_api")
    kb = _team_kb()
    document = SimpleNamespace(id="doc-1")
    deleted_for_tenants = []
    monkeypatch.setattr(module.KnowledgebaseService, "get_by_id", lambda _dataset_id: (True, kb))
    monkeypatch.setattr(module, "check_kb_team_permission", lambda _kb, _actor_id: True)
    monkeypatch.setattr(
        module,
        "validate_and_parse_json_request",
        lambda *_args, **_kwargs: _AwaitableValue(({"ids": [document.id]}, None)),
    )
    monkeypatch.setattr(module.DocumentService, "query", lambda **_kwargs: [document])
    monkeypatch.setattr(module, "check_duplicate_ids", lambda ids, _kind: (ids, []))

    async def capture_delete(_function, _doc_ids, owner_tenant_id):
        deleted_for_tenants.append(owner_tenant_id)
        return ""

    monkeypatch.setattr(module, "thread_pool_exec", capture_delete)

    result = _run(module.delete_documents("member-active", kb.id))

    assert result["code"] == 0
    assert deleted_for_tenants == ["owner-1"]


def test_knowledge_file_create_folder_uses_owner_context_and_actor_audit(monkeypatch):
    module = _load_route_module(monkeypatch, "knowledge_file_api")
    kb = _team_kb()
    seen = []
    monkeypatch.setattr(module.KnowledgebaseService, "get_by_id", lambda _dataset_id: (True, kb))
    monkeypatch.setattr(module, "check_kb_team_permission", lambda _kb, _actor_id: True)
    monkeypatch.setattr(module, "get_request_json", lambda: _AwaitableValue({"parent_id": "root", "name": "Policy"}))
    monkeypatch.setattr(
        module.KnowledgeFileService,
        "create_folder",
        lambda _kb, owner_tenant_id, parent_id, name, *, created_by: seen.append(
            (owner_tenant_id, parent_id, name, created_by)
        )
        or {"id": "folder-1"},
    )

    result = _run(module.create_folder(kb.id, "member-active"))

    assert result["code"] == 0
    assert seen == [("owner-1", "root", "Policy", "member-active")]


def test_file_conversion_keeps_target_owner_context_and_actor_audit(monkeypatch):
    module = _load_route_module(monkeypatch, "file2document_api")
    kb = _team_kb()
    source_file = SimpleNamespace(id="file-1", type="doc", name="A.txt", location="A.txt", size=3)
    inserted = []
    monkeypatch.setattr(module.File2DocumentService, "get_by_file_id", lambda _file_id: [])
    monkeypatch.setattr(module.File2DocumentService, "delete_by_file_id", lambda _file_id: None)
    monkeypatch.setattr(module.File2DocumentService, "insert", lambda payload: payload)
    monkeypatch.setattr(module.FileService, "get_by_id", lambda _file_id: (True, source_file))
    monkeypatch.setattr(module.FileService, "get_parser", lambda *_args: "naive")
    monkeypatch.setattr(module.DocumentService, "insert", lambda payload: inserted.append(payload) or SimpleNamespace(id=payload["id"]))

    module._convert_files([source_file.id], [kb], "member-active")

    assert inserted[0]["kb_id"] == kb.id
    assert inserted[0]["created_by"] == "member-active"


@pytest.mark.asyncio
async def test_team_member_cannot_update_or_delete_dataset_core_configuration(monkeypatch):
    monkeypatch.setattr(
        "api.apps.services.dataset_api_service.KnowledgebaseService.get_or_none",
        lambda **_kwargs: None,
    )

    update_ok, update_error = await update_dataset("member-active", "kb-hr", {"parser_id": "table"})
    delete_ok, delete_error = await delete_datasets("member-active", ids=["kb-hr"])

    assert update_ok is False
    assert update_error == "User 'member-active' lacks permission for dataset 'kb-hr'"
    assert delete_ok is False
    assert "lacks permission" in delete_error
