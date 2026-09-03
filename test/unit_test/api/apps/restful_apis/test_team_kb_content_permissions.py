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
import inspect
from pathlib import Path
from types import SimpleNamespace

import pytest
from peewee import ConnectionContext, SqliteDatabase

import api.apps
import api.utils.api_utils
from api.apps.services.dataset_api_service import delete_datasets, update_dataset
from api.common.check_team_permission import check_kb_team_permission
from api.db import FileType, TeamMemberState, TenantPermission
from api.db.db_models import Document, File, File2Document, Knowledgebase, Task, Team, TeamMember
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
        def call(self, operation, user_id, *, document_id="doc-1"):
            document = SimpleNamespace(
                id="doc-1",
                kb_id=kb.id,
                name="A.txt",
                parser_id=ParserType.NAIVE.value,
                pipeline_id=None,
                parser_config={},
                type=FileType.DOC.value,
                status=StatusEnum.VALID.value,
                chunk_num=0,
                token_num=0,
                progress=0,
                process_duration=0,
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
                    lambda: _AwaitableValue({"document_ids": [document_id]}),
                )
                monkeypatch.setattr(document_api, "thread_pool_exec", _run_inline)
                monkeypatch.setattr(
                    document_api.DocumentService,
                    "query",
                    lambda **kwargs: [document]
                    if kwargs.get("id") == document.id and kwargs.get("kb_id") == document.kb_id
                    else [],
                )
                monkeypatch.setattr(
                    document_api.DocumentService,
                    "get_by_id",
                    lambda doc_id: (doc_id == document.id, document if doc_id == document.id else None),
                )
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

            if operation == "update_document":
                monkeypatch.setattr(
                    document_api.KnowledgebaseService,
                    "query",
                    lambda **kwargs: [kb] if kwargs.get("tenant_id") == kb.tenant_id else [],
                )
                monkeypatch.setattr(
                    document_api,
                    "get_request_json",
                    lambda: _AwaitableValue({"name": "B.txt"}),
                )
                monkeypatch.setattr(
                    document_api.DocumentService,
                    "query",
                    lambda **kwargs: [document]
                    if kwargs.get("id") == document.id and kwargs.get("kb_id") == document.kb_id
                    else [],
                )
                monkeypatch.setattr(document_api, "validate_document_update_fields", lambda *_args: (None, None))
                monkeypatch.setattr(document_api, "update_document_name_only", lambda *_args: None)
                monkeypatch.setattr(
                    document_api.DocumentService,
                    "get_by_id",
                    lambda doc_id: (doc_id == document.id, document if doc_id == document.id else None),
                )
                monkeypatch.setattr(document_api, "map_doc_keys", lambda doc: doc.to_dict())
                return _run(document_api.update_document(user_id, kb.id, document_id))

            if operation == "update_metadata_config":
                monkeypatch.setattr(
                    document_api.KnowledgebaseService,
                    "query",
                    lambda **kwargs: [kb] if kwargs.get("tenant_id") == kb.tenant_id else [],
                )
                monkeypatch.setattr(
                    document_api,
                    "get_request_json",
                    lambda: _AwaitableValue({"metadata": {"department": "HR"}}),
                )
                monkeypatch.setattr(
                    document_api.DocumentService,
                    "query",
                    lambda **kwargs: [document]
                    if kwargs.get("id") == document.id and kwargs.get("kb_id") == document.kb_id
                    else [],
                )
                monkeypatch.setattr(document_api.DocumentService, "update_parser_config", lambda *_args: None)
                monkeypatch.setattr(
                    document_api.DocumentService,
                    "get_by_id",
                    lambda doc_id: (doc_id == document.id, document if doc_id == document.id else None),
                )
                return _run(document_api.update_metadata_config(user_id, kb.id, document_id))

            if operation == "batch_update_status":
                monkeypatch.setattr(
                    document_api.KnowledgebaseService,
                    "query",
                    lambda **kwargs: [kb] if kwargs.get("tenant_id") == kb.tenant_id else [],
                )
                monkeypatch.setattr(
                    document_api,
                    "get_request_json",
                    lambda: _AwaitableValue({"doc_ids": [document_id], "status": "0"}),
                )
                monkeypatch.setattr(
                    document_api.DocumentService,
                    "get_by_id",
                    lambda doc_id: (doc_id == document.id, document if doc_id == document.id else None),
                )
                monkeypatch.setattr(document_api.DocumentService, "update_by_id", lambda *_args, **_kwargs: True)
                return _run(document_api.batch_update_document_status(user_id, kb.id))

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


@pytest.mark.parametrize("operation", ["update_document", "update_metadata_config", "batch_update_status"])
@pytest.mark.parametrize(
    ("user_id", "expected_code"),
    [
        ("owner-1", 0),
        ("member-active", 0),
        ("member-invited", "error"),
        ("member-removed", "error"),
        ("member-other", "error"),
    ],
)
def test_document_maintenance_uses_team_permission_matrix(operation, user_id, expected_code, permission_harness):
    result = permission_harness.call(operation, user_id=user_id)

    if expected_code == 0:
        assert result["code"] == 0
    else:
        assert result["code"] != 0


@pytest.mark.parametrize("operation", ["update_document", "update_metadata_config", "batch_update_status"])
def test_document_maintenance_rejects_document_from_another_dataset(operation, permission_harness):
    result = permission_harness.call(operation, user_id="owner-1", document_id="doc-other")

    assert result["code"] != 0


def test_active_member_document_reparse_uses_owner_tenant(monkeypatch):
    module = _load_route_module(monkeypatch, "document_api")
    kb = _team_kb()
    document = SimpleNamespace(
        id="doc-1",
        kb_id=kb.id,
        name="A.txt",
        parser_id=ParserType.NAIVE.value,
        pipeline_id=None,
        parser_config={},
        type=FileType.DOC.value,
        status=StatusEnum.VALID.value,
        chunk_num=0,
        token_num=0,
        progress=0,
        process_duration=0,
        run=TaskStatus.UNSTART.value,
        to_dict=lambda: {"id": "doc-1", "kb_id": kb.id},
    )
    memberships = {"member-active": [kb.team_id]}
    monkeypatch.setattr(TeamMemberService, "active_team_ids", lambda user_id: memberships.get(user_id, []))
    monkeypatch.setattr(
        TeamService,
        "get_owned_team",
        lambda team_id, owner_id: SimpleNamespace(id=team_id)
        if (team_id, owner_id) == (kb.team_id, kb.tenant_id)
        else None,
    )
    monkeypatch.setattr(module.KnowledgebaseService, "get_by_id", lambda _dataset_id: (True, kb))
    monkeypatch.setattr(module.KnowledgebaseService, "query", lambda **_kwargs: [])
    monkeypatch.setattr(module, "get_request_json", lambda: _AwaitableValue({"pipeline_id": "a" * 32}))
    monkeypatch.setattr(module.DocumentService, "query", lambda **_kwargs: [document])
    monkeypatch.setattr(module.DocumentService, "get_by_id", lambda _doc_id: (True, document))
    monkeypatch.setattr(module, "validate_document_update_fields", lambda *_args: (None, None))
    monkeypatch.setattr(module, "map_doc_keys", lambda doc: doc.to_dict())
    reset_tenants = []
    monkeypatch.setattr(
        module,
        "reset_document_for_reparse",
        lambda _doc, tenant_id, **_kwargs: reset_tenants.append(tenant_id),
    )

    result = _run(module.update_document("member-active", kb.id, document.id))

    assert result["code"] == 0
    assert reset_tenants == [kb.tenant_id]


class _QueryArgs(dict):
    def getlist(self, key):
        return self.get(key, [])


class _ResponseHeaders(dict):
    def set(self, key, value):
        self[key] = value


def _prepare_raw_read_route(monkeypatch, *, user_id, existing=True):
    module = _load_route_module(monkeypatch, "document_api")
    module.current_user = SimpleNamespace(id=user_id)
    kb = SimpleNamespace(
        id="kb1",
        tenant_id="owner-1",
        permission=TenantPermission.TEAM.value,
        team_id="team-hr",
        status=StatusEnum.VALID.value,
    )
    document = SimpleNamespace(
        id="doc-1",
        kb_id=kb.id,
        name="A.txt",
        type=FileType.DOC.value,
        status=StatusEnum.VALID.value,
        thumbnail="page-1.png",
    )
    allowed_users = {kb.tenant_id, "member-active"}

    def query_documents(**kwargs):
        if not existing:
            return []
        if "thumbnail" in kwargs:
            return [document] if (kwargs.get("kb_id"), kwargs.get("thumbnail")) == (kb.id, document.thumbnail) else []
        if kwargs.get("id") != document.id:
            return []
        if kwargs.get("kb_id") not in (None, document.kb_id):
            return []
        return [document]

    monkeypatch.setattr(module.DocumentService, "query", query_documents)
    monkeypatch.setattr(
        module.DocumentService,
        "get_by_id",
        lambda doc_id: (existing and doc_id == document.id, document if existing and doc_id == document.id else None),
    )
    monkeypatch.setattr(module.DocumentService, "get_by_ids", lambda doc_ids: [document] if existing and document.id in doc_ids else [])
    monkeypatch.setattr(
        module.DocumentService,
        "accessible",
        lambda doc_id, actor_id: existing and doc_id == document.id and actor_id in allowed_users,
    )
    monkeypatch.setattr(
        module.DocumentService,
        "get_thumbnails",
        lambda _doc_ids: [{"id": document.id, "kb_id": kb.id, "thumbnail": document.thumbnail}] if existing else [],
    )
    monkeypatch.setattr(module.File2DocumentService, "get_storage_address", lambda **_kwargs: (kb.id, document.name))
    storage_reads = []
    module.settings = SimpleNamespace(
        STORAGE_IMPL=SimpleNamespace(
            get=lambda bucket, object_name: storage_reads.append((bucket, object_name)) or b"content"
        )
    )
    module.request = SimpleNamespace(args=_QueryArgs(doc_ids=[document.id]))

    async def fake_send_file(_stream, **kwargs):
        return {"sent": kwargs["attachment_filename"]}

    async def fake_make_response(data):
        return SimpleNamespace(data=data, headers=_ResponseHeaders())

    monkeypatch.setattr(module, "send_file", fake_send_file)
    monkeypatch.setattr(module, "make_response", fake_make_response)
    return module, kb, document, storage_reads


def _call_raw_read_route(module, kb, document, route):
    if route == "dataset_download":
        return _run(module.download(kb.id, document.id))
    if route == "document_download":
        return _run(module.download_document(document.id))
    if route == "thumbnails":
        return module.list_thumbnails()
    raise AssertionError(f"Unknown route: {route}")


@pytest.mark.parametrize("route", ["dataset_download", "document_download", "thumbnails"])
@pytest.mark.parametrize(
    ("user_id", "allowed"),
    [
        ("owner-1", True),
        ("member-active", True),
        ("member-invited", False),
        ("member-removed", False),
        ("member-other", False),
    ],
)
def test_raw_document_reads_follow_team_permission_matrix(monkeypatch, route, user_id, allowed):
    module, kb, document, storage_reads = _prepare_raw_read_route(monkeypatch, user_id=user_id)

    result = _call_raw_read_route(module, kb, document, route)

    if allowed:
        if route == "thumbnails":
            assert result["code"] == 0
        else:
            assert result == {"sent": document.name}
        assert storage_reads or route == "thumbnails"
    else:
        assert result["code"] != 0
        assert storage_reads == []


@pytest.mark.parametrize("route", ["dataset_download", "document_download", "thumbnails"])
def test_raw_document_reads_hide_missing_and_unauthorized_resources(monkeypatch, route):
    missing_module, missing_kb, missing_document, missing_reads = _prepare_raw_read_route(
        monkeypatch,
        user_id="owner-1",
        existing=False,
    )
    missing = _call_raw_read_route(missing_module, missing_kb, missing_document, route)
    denied_module, denied_kb, denied_document, denied_reads = _prepare_raw_read_route(
        monkeypatch,
        user_id="member-invited",
        existing=True,
    )
    denied = _call_raw_read_route(denied_module, denied_kb, denied_document, route)

    assert (missing["code"], missing["message"]) == (denied["code"], denied["message"])
    assert missing_reads == []
    assert denied_reads == []


@pytest.mark.parametrize(
    "image_id",
    [
        "kb1-71d8d4f2bb8e4c87",
        "imagetemps-3f84af32d0f64d8e9f05f6b3981e8c28",
    ],
)
def test_generic_document_image_route_preserves_chunk_and_reference_image_compatibility(monkeypatch, image_id):
    module, _kb, _document, storage_reads = _prepare_raw_read_route(monkeypatch, user_id="member-other")

    result = _run(module.get_document_image(image_id))

    bucket, object_name = image_id.split("-", 1)
    assert getattr(result, "data", None) == b"content"
    assert storage_reads == [(bucket, object_name)]


def test_dataset_download_binds_document_to_requested_dataset(monkeypatch):
    module, _kb, document, storage_reads = _prepare_raw_read_route(monkeypatch, user_id="owner-1")

    result = _run(module.download("kb-other", document.id))

    assert result["code"] != 0
    assert storage_reads == []


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
    source_file = SimpleNamespace(
        id="file-1",
        tenant_id=kb.tenant_id,
        type="doc",
        name="A.txt",
        location="A.txt",
        size=3,
    )
    monkeypatch.setattr(module.FileService, "get_parser", lambda *_args: "naive")

    staged = module._stage_document_replacements([source_file], [kb], "member-active")

    assert staged[0][1]["kb_id"] == kb.id
    assert staged[0][1]["created_by"] == "member-active"


def _prepare_file_conversion_route(monkeypatch, *, authorized_kb_ids, target_owner_id="owner-source"):
    module = _load_route_module(monkeypatch, "file2document_api")
    module.current_user = SimpleNamespace(id="member-active")
    source_file = SimpleNamespace(
        id="file-1",
        tenant_id="owner-source",
        type="doc",
        name="A.txt",
        location="A.txt",
        size=3,
    )
    old_documents = {
        "doc-team-a": SimpleNamespace(id="doc-team-a", kb_id="kb-team-a"),
        "doc-private-b": SimpleNamespace(id="doc-private-b", kb_id="kb-private-b"),
    }
    knowledge_bases = {
        "kb-target": SimpleNamespace(
            id="kb-target",
            tenant_id=target_owner_id,
            parser_id="naive",
            pipeline_id=None,
            parser_config={},
        ),
        "kb-team-a": SimpleNamespace(id="kb-team-a", tenant_id="owner-a"),
        "kb-private-b": SimpleNamespace(id="kb-private-b", tenant_id="owner-b"),
    }
    old_links = [
        SimpleNamespace(file_id=source_file.id, document_id="doc-team-a"),
        SimpleNamespace(file_id=source_file.id, document_id="doc-private-b"),
    ]
    conversions = []
    removed = []

    async def capture_conversion(function, *args):
        conversions.append((function, args))

    monkeypatch.setattr(module, "thread_pool_exec", capture_conversion, raising=False)
    monkeypatch.setattr(
        module,
        "get_request_json",
        lambda: _AwaitableValue({"file_ids": [source_file.id], "kb_ids": ["kb-target"]}),
    )
    monkeypatch.setattr(module.FileService, "get_by_ids", lambda _file_ids: [source_file])
    monkeypatch.setattr(module.FileService, "get_by_id", lambda _file_id: (True, source_file))
    monkeypatch.setattr(module.File2DocumentService, "get_by_file_id", lambda _file_id: list(old_links))
    monkeypatch.setattr(
        module.DocumentService,
        "get_by_id",
        lambda document_id: (document_id in old_documents, old_documents.get(document_id)),
    )
    monkeypatch.setattr(
        module.DocumentService,
        "remove_document",
        lambda document, owner_tenant_id: removed.append((document.id, owner_tenant_id)),
    )
    monkeypatch.setattr(
        module.KnowledgebaseService,
        "get_by_id",
        lambda kb_id: (kb_id in knowledge_bases, knowledge_bases.get(kb_id)),
    )
    monkeypatch.setattr(module, "check_file_team_permission", lambda _file, _user_id: True)
    monkeypatch.setattr(module, "check_kb_team_permission", lambda kb, _user_id: kb.id in authorized_kb_ids)
    return module, conversions, removed


def test_file_conversion_rejects_when_any_existing_linked_kb_is_unauthorized(monkeypatch):
    module, conversions, removed = _prepare_file_conversion_route(
        monkeypatch,
        authorized_kb_ids={"kb-target", "kb-team-a"},
    )

    result = _run(inspect.unwrap(module.convert)())

    assert result["code"] != 0
    assert conversions == []
    assert removed == []


def test_file_conversion_runs_only_preauthorized_existing_documents_synchronously(monkeypatch):
    module, conversions, _removed = _prepare_file_conversion_route(
        monkeypatch,
        authorized_kb_ids={"kb-target", "kb-team-a", "kb-private-b"},
    )

    result = _run(inspect.unwrap(module.convert)())

    assert result["code"] == 0
    assert result["data"] is True
    assert len(conversions) == 1
    function, args = conversions[0]
    assert function is module._convert_files
    assert args[0] == ["file-1"]
    assert [(doc.id, owner_id) for doc, owner_id in args[1]["file-1"]] == [
        ("doc-team-a", "owner-a"),
        ("doc-private-b", "owner-b"),
    ]
    assert [kb.id for kb in args[2]] == ["kb-target"]
    assert args[3] == "member-active"


def test_file_conversion_rejects_cross_owner_targets_without_mutation(monkeypatch):
    module, conversions, removed = _prepare_file_conversion_route(
        monkeypatch,
        authorized_kb_ids={"kb-target", "kb-team-a", "kb-private-b"},
        target_owner_id="owner-other",
    )

    result = _run(inspect.unwrap(module.convert)())

    assert result["code"] != 0
    assert conversions == []
    assert removed == []


def test_file_conversion_returns_error_when_synchronous_worker_fails(monkeypatch):
    module, _conversions, removed = _prepare_file_conversion_route(
        monkeypatch,
        authorized_kb_ids={"kb-target", "kb-team-a", "kb-private-b"},
    )

    async def fail_conversion(_function, *_args):
        raise RuntimeError("late conversion failure")

    monkeypatch.setattr(module, "thread_pool_exec", fail_conversion, raising=False)

    result = _run(inspect.unwrap(module.convert)())

    assert result["code"] != 0
    assert removed == []


def test_file_conversion_route_deduplicates_overlapping_file_and_folder_selection(monkeypatch):
    module = _load_route_module(monkeypatch, "file2document_api")
    module.current_user = SimpleNamespace(id="member-active")
    folder = SimpleNamespace(id="folder-1", type="folder")
    files = {
        "file-1": SimpleNamespace(
            id="file-1", tenant_id="owner-target", type="doc", name="A.txt", location="A.txt", size=3
        ),
        "file-2": SimpleNamespace(
            id="file-2", tenant_id="owner-target", type="doc", name="B.txt", location="B.txt", size=5
        ),
    }
    target_kb = SimpleNamespace(
        id="kb-target",
        tenant_id="owner-target",
        parser_id="naive",
        pipeline_id=None,
        parser_config={},
    )
    conversions = []

    async def capture_conversion(function, *args):
        conversions.append((function, args))

    monkeypatch.setattr(module, "thread_pool_exec", capture_conversion, raising=False)
    monkeypatch.setattr(
        module,
        "get_request_json",
        lambda: _AwaitableValue(
            {
                "file_ids": ["file-1", folder.id, "file-1"],
                "kb_ids": [target_kb.id],
            }
        ),
    )
    monkeypatch.setattr(module.FileService, "get_by_ids", lambda _file_ids: [files["file-1"], folder])
    monkeypatch.setattr(module.FileService, "get_all_innermost_file_ids", lambda _folder_id, _ids: ["file-1", "file-2"])
    monkeypatch.setattr(module.FileService, "get_by_id", lambda file_id: (file_id in files, files.get(file_id)))
    monkeypatch.setattr(module.File2DocumentService, "get_by_file_id", lambda _file_id: [])
    monkeypatch.setattr(module.KnowledgebaseService, "get_by_id", lambda _kb_id: (True, target_kb))
    monkeypatch.setattr(module, "check_file_team_permission", lambda _file, _user_id: True)
    monkeypatch.setattr(module, "check_kb_team_permission", lambda _kb, _user_id: True)

    result = _run(inspect.unwrap(module.convert)())

    assert result["code"] == 0
    assert len(conversions) == 1
    function, args = conversions[0]
    assert function is module._convert_files
    assert args[0] == ["file-1", "file-2"]
    assert list(args[1]) == args[0]


def test_file_conversion_worker_deduplicates_files_before_staging(monkeypatch):
    module = _load_route_module(monkeypatch, "file2document_api")
    source_files = {
        "file-1": SimpleNamespace(
            id="file-1", tenant_id="owner-target", type="doc", name="A.txt", location="A.txt", size=3
        ),
        "file-2": SimpleNamespace(
            id="file-2", tenant_id="owner-target", type="doc", name="B.txt", location="B.txt", size=5
        ),
    }
    old_documents = {
        "file-1": SimpleNamespace(id="doc-old-1", kb_id="kb-old"),
        "file-2": SimpleNamespace(id="doc-old-2", kb_id="kb-old"),
    }
    target_kbs = [
        SimpleNamespace(
            id="kb-target-1",
            tenant_id="owner-target",
            parser_id="naive",
            pipeline_id=None,
            parser_config={},
        ),
        SimpleNamespace(
            id="kb-target-2",
            tenant_id="owner-target",
            parser_id="naive",
            pipeline_id=None,
            parser_config={},
        ),
    ]
    monkeypatch.setattr(module.FileService, "get_by_id", lambda file_id: (True, source_files[file_id]))
    monkeypatch.setattr(
        module.KnowledgebaseService,
        "get_by_id",
        lambda kb_id: (True, next(kb for kb in target_kbs if kb.id == kb_id)),
    )
    monkeypatch.setattr(module, "check_file_team_permission", lambda *_args: True)
    monkeypatch.setattr(module, "check_kb_team_permission", lambda *_args: True)
    monkeypatch.setattr(
        module,
        "_authorize_existing_documents",
        lambda _file_ids, _actor_id: {
            "file-1": [(old_documents["file-1"], "owner-old")],
            "file-2": [(old_documents["file-2"], "owner-old")],
        },
    )
    monkeypatch.setattr(module.FileService, "get_parser", lambda *_args: "naive")

    files, kbs, _existing = module._load_conversion_state(
        ["file-1", "file-2", "file-1", "file-2"],
        ["kb-target-1", "kb-target-2", "kb-target-1"],
        "member-active",
    )
    staged = module._stage_document_replacements(files, kbs, "member-active")

    assert [file.id for file in files] == ["file-1", "file-2"]
    assert [kb.id for kb in kbs] == ["kb-target-1", "kb-target-2"]
    assert [(document["name"], document["kb_id"]) for _file_id, document in staged] == [
        ("A.txt", "kb-target-1"),
        ("A.txt", "kb-target-2"),
        ("B.txt", "kb-target-1"),
        ("B.txt", "kb-target-2"),
    ]
    assert [file_id for file_id, _document in staged] == ["file-1", "file-1", "file-2", "file-2"]


def test_file_conversion_worker_rejects_relationship_changes_before_replacement(monkeypatch):
    module = _load_route_module(monkeypatch, "file2document_api")
    old_document = SimpleNamespace(id="doc-team-a", kb_id="kb-team-a")
    target_kb = _team_kb()
    source_file = SimpleNamespace(
        id="file-1",
        tenant_id=target_kb.tenant_id,
        type="doc",
        name="A.txt",
        location="A.txt",
        size=3,
    )
    changed_document = SimpleNamespace(id="doc-changed", kb_id="kb-team-a")
    monkeypatch.setattr(
        module,
        "_load_conversion_state",
        lambda *_args: (
            [source_file],
            [target_kb],
            {source_file.id: [(changed_document, "owner-a")]},
        ),
    )
    monkeypatch.setattr(
        module,
        "_replace_document_rows",
        lambda *_args: pytest.fail("changed relationships must be rejected before replacement"),
    )

    with pytest.raises(RuntimeError, match="relationships changed concurrently"):
        module._convert_files(
            [source_file.id],
            {source_file.id: [(old_document, "owner-a")]},
            [target_kb],
            "member-active",
        )



@pytest.mark.parametrize("failure_point", ["second_target", "after_original_delete"])
def test_file_conversion_rolls_back_staged_targets_and_preserves_original_on_late_failure(monkeypatch, failure_point):
    module = _load_route_module(monkeypatch, "file2document_api")
    database = SqliteDatabase(":memory:")
    models = [File, Knowledgebase, Document, File2Document, Task]

    with database.bind_ctx(models), database.connection_context():
        database.create_tables(models)
        monkeypatch.setattr(module, "DB", database, raising=False)
        source_file = File.create(
            id="file-1",
            parent_id="root",
            tenant_id="owner-1",
            created_by="owner-1",
            name="A.txt",
            type="doc",
            location="A.txt",
            size=3,
        )
        old_kb = Knowledgebase.create(
            id="kb-old",
            tenant_id="owner-1",
            name="Old",
            embd_id="embedding-1",
            created_by="owner-1",
            doc_num=1,
        )
        targets = [
            Knowledgebase.create(
                id=f"kb-target-{index}",
                tenant_id="owner-1",
                name=f"Target {index}",
                embd_id="embedding-1",
                created_by="owner-1",
            )
            for index in (1, 2)
        ]
        old_document = Document.create(
            id="doc-old",
            kb_id=old_kb.id,
            parser_id="naive",
            type="doc",
            created_by="owner-1",
            name="A.txt",
            location="A.txt",
            size=3,
            suffix="txt",
        )
        File2Document.create(id="link-old", file_id=source_file.id, document_id=old_document.id)

        if failure_point == "second_target":
            original_insert = module._insert_staged_document
            insert_count = 0

            def fail_after_second_insert(*args):
                nonlocal insert_count
                result = original_insert(*args)
                insert_count += 1
                if insert_count == 2:
                    raise RuntimeError("late insert failure")
                return result

            monkeypatch.setattr(module, "_insert_staged_document", fail_after_second_insert)
        else:
            original_delete = module._delete_original_document

            def fail_after_original_delete(*args):
                original_delete(*args)
                raise RuntimeError("late delete failure")

            monkeypatch.setattr(module, "_delete_original_document", fail_after_original_delete)

        monkeypatch.setattr(
            module.DocumentService,
            "cleanup_document_resources",
            lambda *_args: pytest.fail("external cleanup must happen only after a durable replacement"),
            raising=False,
        )

        with pytest.raises(RuntimeError, match="late (insert|delete) failure"):
            module._replace_document_rows(
                [source_file],
                targets,
                {source_file.id: [(old_document, "owner-1")]},
                "owner-1",
            )

        assert [document.id for document in Document.select()] == [old_document.id]
        assert [(link.file_id, link.document_id) for link in File2Document.select()] == [
            (source_file.id, old_document.id)
        ]
        assert Knowledgebase.get_by_id(old_kb.id).doc_num == 1
        assert [Knowledgebase.get_by_id(target.id).doc_num for target in targets] == [0, 0]


def test_cross_owner_conversion_has_zero_mutations_and_source_deletion_cannot_touch_target(monkeypatch):
    module = _load_route_module(monkeypatch, "file2document_api")
    database = SqliteDatabase(":memory:")
    models = [File, Knowledgebase, Document, File2Document, Task]

    with database.bind_ctx(models), database.connection_context():
        database.create_tables(models)
        monkeypatch.setattr(module, "DB", database, raising=False)
        monkeypatch.setattr(module, "check_file_team_permission", lambda *_args: True)
        monkeypatch.setattr(module, "check_kb_team_permission", lambda *_args: True)
        source_file = File.create(
            id="file-owner-a",
            parent_id="root",
            tenant_id="owner-a",
            created_by="owner-a",
            name="A.txt",
            type="doc",
            location="A.txt",
            size=3,
        )
        target_kb = Knowledgebase.create(
            id="kb-owner-b",
            tenant_id="owner-b",
            name="Owner B",
            embd_id="embedding-1",
            created_by="owner-b",
            doc_num=1,
        )
        target_document = Document.create(
            id="doc-owner-b",
            kb_id=target_kb.id,
            parser_id="naive",
            type="doc",
            created_by="owner-b",
            name="Existing.txt",
            location="Existing.txt",
            size=4,
            suffix="txt",
        )

        with pytest.raises(PermissionError, match="same owner"):
            module._replace_document_rows([source_file], [target_kb], {source_file.id: []}, "authorized-actor")

        assert [document.id for document in Document.select()] == [target_document.id]
        assert File2Document.select().count() == 0
        source_file.delete_instance()
        assert Document.get_by_id(target_document.id).kb_id == target_kb.id
        assert Knowledgebase.get_by_id(target_kb.id).doc_num == 1


def test_file_conversion_cleans_external_state_only_after_durable_replacement(monkeypatch):
    module = _load_route_module(monkeypatch, "file2document_api")
    database = SqliteDatabase(":memory:")
    models = [File, Knowledgebase, Document, File2Document, Task]

    with database.bind_ctx(models), database.connection_context():
        database.create_tables(models)
        monkeypatch.setattr(module, "DB", database, raising=False)
        source_file = File.create(
            id="file-1",
            parent_id="root",
            tenant_id="owner-1",
            created_by="owner-1",
            name="A.txt",
            type="doc",
            location="A.txt",
            size=3,
        )
        old_kb = Knowledgebase.create(
            id="kb-old",
            tenant_id="owner-1",
            name="Old",
            embd_id="embedding-1",
            created_by="owner-1",
            doc_num=1,
        )
        target_kb = Knowledgebase.create(
            id="kb-target",
            tenant_id="owner-1",
            name="Target",
            embd_id="embedding-1",
            created_by="owner-1",
        )
        old_document = Document.create(
            id="doc-old",
            kb_id=old_kb.id,
            parser_id="naive",
            type="doc",
            created_by="owner-1",
            name="A.txt",
            location="A.txt",
            size=3,
            suffix="txt",
        )
        File2Document.create(id="link-old", file_id=source_file.id, document_id=old_document.id)
        cleanup_calls = []

        def verify_durable_cleanup(document, owner_id):
            assert database.in_transaction() is False
            assert Document.get_or_none(Document.id == document.id) is None
            assert Document.select().where(Document.kb_id == target_kb.id).count() == 1
            cleanup_calls.append((document.id, owner_id))

        monkeypatch.setattr(module.DocumentService, "cleanup_document_resources", verify_durable_cleanup)

        module._replace_document_rows(
            [source_file],
            [target_kb],
            {source_file.id: [(old_document, "owner-1")]},
            "owner-1",
        )

        assert cleanup_calls == [(old_document.id, "owner-1")]
        assert Knowledgebase.get_by_id(old_kb.id).doc_num == 0
        assert Knowledgebase.get_by_id(target_kb.id).doc_num == 1
        links = list(File2Document.select())
        assert len(links) == 1
        assert links[0].file_id == source_file.id
        assert links[0].document_id != old_document.id


def _bind_real_service_connection_context(monkeypatch, method, database):
    wrapper = method.__func__ if hasattr(method, "__func__") else method
    contexts = [
        cell.cell_contents
        for cell in (wrapper.__closure__ or ())
        if isinstance(cell.cell_contents, ConnectionContext)
    ]
    assert len(contexts) == 1
    monkeypatch.setattr(contexts[0], "db", database)


def _prepare_real_active_member_conversion(monkeypatch, tmp_path, *, member_status="1", team_status="1"):
    module = _load_route_module(monkeypatch, "file2document_api")
    database = SqliteDatabase(tmp_path / "active-member-conversion.db")
    models = [Team, TeamMember, File, Knowledgebase, Document, File2Document, Task]
    with database.bind_ctx(models):
        database.create_tables(models)
        team = Team.create(
            id="team-hr",
            tenant_id="owner-1",
            name="HR",
            created_by="owner-1",
            status=team_status,
        )
        TeamMember.create(
            id="membership-1",
            team_id=team.id,
            user_id="member-active",
            state=TeamMemberState.ACTIVE.value,
            invited_by="owner-1",
            status=member_status,
        )
        old_kb = Knowledgebase.create(
            id="kb-old",
            tenant_id="owner-1",
            name="Old",
            embd_id="embedding-1",
            permission=TenantPermission.TEAM.value,
            team_id=team.id,
            created_by="owner-1",
            doc_num=1,
        )
        target_kb = Knowledgebase.create(
            id="kb-target",
            tenant_id="owner-1",
            name="Target",
            embd_id="embedding-1",
            permission=TenantPermission.TEAM.value,
            team_id=team.id,
            created_by="owner-1",
        )
        source_file = File.create(
            id="file-1",
            parent_id="root",
            tenant_id="owner-1",
            created_by="owner-1",
            name="A.txt",
            type=FileType.DOC.value,
            location="A.txt",
            size=3,
        )
        old_document = Document.create(
            id="doc-old",
            kb_id=old_kb.id,
            parser_id=ParserType.NAIVE.value,
            type=FileType.DOC.value,
            created_by="owner-1",
            name="A.txt",
            location="A.txt",
            size=3,
            suffix="txt",
        )
        File2Document.create(id="link-old", file_id=source_file.id, document_id=old_document.id)

    monkeypatch.setattr(module, "DB", database, raising=False)
    for method in (
        module.FileService.get_kb_id_by_file_id,
        module.KnowledgebaseService.get_by_id,
        TeamMemberService.active_team_ids,
        TeamService.get_owned_team,
    ):
        _bind_real_service_connection_context(monkeypatch, method, database)
    monkeypatch.setattr(module.DocumentService, "cleanup_document_resources", lambda *_args: None)
    return module, database, models, source_file, old_kb, target_kb, old_document


def test_active_member_file_conversion_uses_one_real_atomic_transaction(monkeypatch, tmp_path):
    module, database, models, source_file, old_kb, target_kb, old_document = _prepare_real_active_member_conversion(
        monkeypatch,
        tmp_path,
    )

    with database.bind_ctx(models):
        try:
            module._replace_document_rows(
                [source_file],
                [target_kb],
                {source_file.id: [(old_document, old_kb.tenant_id)]},
                "member-active",
            )

            documents = list(Document.select().order_by(Document.id))
            links = list(File2Document.select())
            assert len(documents) == 1
            assert documents[0].kb_id == target_kb.id
            assert documents[0].created_by == "member-active"
            assert [(link.file_id, link.document_id) for link in links] == [(source_file.id, documents[0].id)]
            assert Knowledgebase.get_by_id(old_kb.id).doc_num == 0
            assert Knowledgebase.get_by_id(target_kb.id).doc_num == 1
        finally:
            if not database.is_closed():
                database.close()


@pytest.mark.parametrize(
    ("member_status", "team_status"),
    [
        (StatusEnum.INVALID.value, StatusEnum.VALID.value),
        (StatusEnum.VALID.value, StatusEnum.INVALID.value),
    ],
)
def test_file_conversion_revalidates_membership_and_team_before_mutation(
    monkeypatch,
    tmp_path,
    member_status,
    team_status,
):
    module, database, models, source_file, old_kb, target_kb, old_document = _prepare_real_active_member_conversion(
        monkeypatch,
        tmp_path,
        member_status=member_status,
        team_status=team_status,
    )

    with database.bind_ctx(models):
        try:
            with pytest.raises(PermissionError, match="No authorization"):
                module._replace_document_rows(
                    [source_file],
                    [target_kb],
                    {source_file.id: [(old_document, old_kb.tenant_id)]},
                    "member-active",
                )

            assert [document.id for document in Document.select()] == [old_document.id]
            assert [(link.file_id, link.document_id) for link in File2Document.select()] == [
                (source_file.id, old_document.id)
            ]
            assert Knowledgebase.get_by_id(old_kb.id).doc_num == 1
            assert Knowledgebase.get_by_id(target_kb.id).doc_num == 0
        finally:
            if not database.is_closed():
                database.close()


def test_web_upload_storage_calls_use_kb_owner_context(monkeypatch):
    module = _load_route_module(monkeypatch, "document_api")
    kb = _team_kb()
    storage_calls = []

    class _Storage:
        @staticmethod
        def obj_exist(bucket, location, owner_tenant_id):
            storage_calls.append(("exists", bucket, location, owner_tenant_id))
            return False

        @staticmethod
        def put(bucket, location, blob, owner_tenant_id):
            storage_calls.append(("put", bucket, location, blob, owner_tenant_id))

    module.request = SimpleNamespace(form=_AwaitableValue({"name": "Policy", "url": "https://example.com"}))
    monkeypatch.setattr(module, "is_valid_url", lambda _url: True)
    monkeypatch.setattr(module, "html2pdf", lambda _url: b"pdf")
    monkeypatch.setattr(module, "duplicate_name", lambda *_args, **_kwargs: "Policy.pdf")
    monkeypatch.setattr(module, "filename_type", lambda _name: "pdf")
    monkeypatch.setattr(module, "thumbnail", lambda *_args: "")
    monkeypatch.setattr(module, "get_uuid", lambda: "doc-1")
    monkeypatch.setattr(module.FileService, "get_root_folder", lambda _owner_id: {"id": "root"})
    monkeypatch.setattr(module.FileService, "init_knowledgebase_docs", lambda *_args: None)
    monkeypatch.setattr(module.FileService, "get_or_create_kb_root", lambda *_args: {"id": "kb-folder"})
    monkeypatch.setattr(module.FileService, "add_file_from_kb", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(module.DocumentService, "insert", lambda _doc: None)
    monkeypatch.setattr(module.settings, "STORAGE_IMPL", _Storage())

    result = _run(module._upload_web_document(kb.id, kb, "member-active"))

    assert result["code"] == 0
    assert storage_calls == [
        ("exists", kb.id, "Policy.pdf", kb.tenant_id),
        ("put", kb.id, "Policy.pdf", b"pdf", kb.tenant_id),
    ]


def test_chunk_authorization_uses_the_first_fetched_kb_object(monkeypatch):
    module = _load_route_module(monkeypatch, "chunk_api")
    first_kb = _team_kb()
    second_kb = SimpleNamespace(**{**vars(first_kb), "team_id": "team-other"})
    fetched = iter([(True, first_kb), (True, second_kb)])
    checked = []

    monkeypatch.setattr(module.KnowledgebaseService, "get_by_id", lambda _kb_id: next(fetched))
    monkeypatch.setattr(module.KnowledgebaseService, "accessible", lambda **_kwargs: False)
    monkeypatch.setattr(
        module,
        "check_kb_team_permission",
        lambda kb, user_id: checked.append((kb, user_id)) or True,
        raising=False,
    )

    result = module._get_authorized_kb(first_kb.id, "member-active")

    assert result is first_kb
    assert checked == [(first_kb, "member-active")]


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
