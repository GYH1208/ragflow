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

    module._convert_files([source_file.id], {source_file.id: []}, [kb], "member-active")

    assert inserted[0]["kb_id"] == kb.id
    assert inserted[0]["created_by"] == "member-active"


def _prepare_file_conversion_route(monkeypatch, *, authorized_kb_ids):
    module = _load_route_module(monkeypatch, "file2document_api")
    module.current_user = SimpleNamespace(id="member-active")
    source_file = SimpleNamespace(id="file-1", type="doc", name="A.txt", location="A.txt", size=3)
    old_documents = {
        "doc-team-a": SimpleNamespace(id="doc-team-a", kb_id="kb-team-a"),
        "doc-private-b": SimpleNamespace(id="doc-private-b", kb_id="kb-private-b"),
    }
    knowledge_bases = {
        "kb-target": SimpleNamespace(
            id="kb-target",
            tenant_id="owner-target",
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
    scheduled = []
    removed = []

    class _Future:
        @staticmethod
        def add_done_callback(_callback):
            return None

    class _Loop:
        @staticmethod
        def run_in_executor(_executor, function, *args):
            scheduled.append((function, args))
            return _Future()

    module.asyncio = SimpleNamespace(get_running_loop=lambda: _Loop())
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
    return module, scheduled, removed


def test_file_conversion_rejects_when_any_existing_linked_kb_is_unauthorized(monkeypatch):
    module, scheduled, removed = _prepare_file_conversion_route(
        monkeypatch,
        authorized_kb_ids={"kb-target", "kb-team-a"},
    )

    result = _run(inspect.unwrap(module.convert)())

    assert result["code"] != 0
    assert scheduled == []
    assert removed == []


def test_file_conversion_schedules_only_preauthorized_existing_documents(monkeypatch):
    module, scheduled, _removed = _prepare_file_conversion_route(
        monkeypatch,
        authorized_kb_ids={"kb-target", "kb-team-a", "kb-private-b"},
    )

    result = _run(inspect.unwrap(module.convert)())

    assert result["code"] == 0
    assert len(scheduled) == 1
    function, args = scheduled[0]
    assert function is module._convert_files
    assert args[0] == ["file-1"]
    assert [(doc.id, owner_id) for doc, owner_id in args[1]["file-1"]] == [
        ("doc-team-a", "owner-a"),
        ("doc-private-b", "owner-b"),
    ]
    assert [kb.id for kb in args[2]] == ["kb-target"]
    assert args[3] == "member-active"


def test_file_conversion_route_deduplicates_overlapping_file_and_folder_selection(monkeypatch):
    module = _load_route_module(monkeypatch, "file2document_api")
    module.current_user = SimpleNamespace(id="member-active")
    folder = SimpleNamespace(id="folder-1", type="folder")
    files = {
        "file-1": SimpleNamespace(id="file-1", type="doc", name="A.txt", location="A.txt", size=3),
        "file-2": SimpleNamespace(id="file-2", type="doc", name="B.txt", location="B.txt", size=5),
    }
    target_kb = SimpleNamespace(
        id="kb-target",
        tenant_id="owner-target",
        parser_id="naive",
        pipeline_id=None,
        parser_config={},
    )
    scheduled = []

    class _Future:
        @staticmethod
        def add_done_callback(_callback):
            return None

    class _Loop:
        @staticmethod
        def run_in_executor(_executor, function, *args):
            scheduled.append((function, args))
            return _Future()

    module.asyncio = SimpleNamespace(get_running_loop=lambda: _Loop())
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
    assert len(scheduled) == 1
    function, args = scheduled[0]
    assert function is module._convert_files
    assert args[0] == ["file-1", "file-2"]
    assert list(args[1]) == args[0]


def test_file_conversion_worker_deduplicates_files_before_deleting_and_creating(monkeypatch):
    module = _load_route_module(monkeypatch, "file2document_api")
    source_files = {
        "file-1": SimpleNamespace(id="file-1", type="doc", name="A.txt", location="A.txt", size=3),
        "file-2": SimpleNamespace(id="file-2", type="doc", name="B.txt", location="B.txt", size=5),
    }
    old_documents = {
        "file-1": SimpleNamespace(id="doc-old-1", kb_id="kb-old"),
        "file-2": SimpleNamespace(id="doc-old-2", kb_id="kb-old"),
    }
    target_kbs = [
        SimpleNamespace(
            id="kb-target-1",
            tenant_id="owner-target-1",
            parser_id="naive",
            pipeline_id=None,
            parser_config={},
        ),
        SimpleNamespace(
            id="kb-target-2",
            tenant_id="owner-target-2",
            parser_id="naive",
            pipeline_id=None,
            parser_config={},
        ),
    ]
    removed = []
    deleted_links = []
    inserted_documents = []
    inserted_links = []

    monkeypatch.setattr(
        module.DocumentService,
        "remove_document",
        lambda document, owner_id: removed.append((document.id, owner_id)),
    )
    monkeypatch.setattr(
        module.DocumentService,
        "insert",
        lambda payload: inserted_documents.append(payload) or SimpleNamespace(id=payload["id"]),
    )
    monkeypatch.setattr(
        module.File2DocumentService,
        "delete_by_document_id",
        lambda document_id: deleted_links.append(document_id),
    )
    monkeypatch.setattr(module.File2DocumentService, "insert", lambda payload: inserted_links.append(payload))
    monkeypatch.setattr(module.FileService, "get_by_id", lambda file_id: (True, source_files[file_id]))
    monkeypatch.setattr(module.FileService, "get_parser", lambda *_args: "naive")

    module._convert_files(
        ["file-1", "file-2", "file-1", "file-2"],
        {
            "file-1": [(old_documents["file-1"], "owner-old")],
            "file-2": [(old_documents["file-2"], "owner-old")],
        },
        target_kbs,
        "member-active",
    )

    assert removed == [("doc-old-1", "owner-old"), ("doc-old-2", "owner-old")]
    assert deleted_links == ["doc-old-1", "doc-old-2"]
    assert [(document["name"], document["kb_id"]) for document in inserted_documents] == [
        ("A.txt", "kb-target-1"),
        ("A.txt", "kb-target-2"),
        ("B.txt", "kb-target-1"),
        ("B.txt", "kb-target-2"),
    ]
    assert [link["file_id"] for link in inserted_links] == ["file-1", "file-1", "file-2", "file-2"]


def test_file_conversion_worker_never_expands_deletion_from_bare_file_id(monkeypatch):
    module = _load_route_module(monkeypatch, "file2document_api")
    old_document = SimpleNamespace(id="doc-team-a", kb_id="kb-team-a")
    target_kb = _team_kb()
    source_file = SimpleNamespace(id="file-1", type="doc", name="A.txt", location="A.txt", size=3)
    removed = []
    deleted_links = []

    monkeypatch.setattr(
        module.File2DocumentService,
        "get_by_file_id",
        lambda _file_id: pytest.fail("worker must not rediscover links from a bare file id"),
    )
    monkeypatch.setattr(
        module.File2DocumentService,
        "delete_by_file_id",
        lambda _file_id: pytest.fail("worker must not delete links by a bare file id"),
    )
    monkeypatch.setattr(module.File2DocumentService, "delete_by_document_id", lambda doc_id: deleted_links.append(doc_id))
    monkeypatch.setattr(module.File2DocumentService, "insert", lambda payload: payload)
    monkeypatch.setattr(module.FileService, "get_by_id", lambda _file_id: (True, source_file))
    monkeypatch.setattr(module.FileService, "get_parser", lambda *_args: "naive")
    monkeypatch.setattr(
        module.DocumentService,
        "remove_document",
        lambda doc, owner_id: removed.append((doc.id, owner_id)),
    )
    monkeypatch.setattr(module.DocumentService, "insert", lambda payload: SimpleNamespace(id=payload["id"]))

    module._convert_files(
        [source_file.id],
        {source_file.id: [(old_document, "owner-a")]},
        [target_kb],
        "member-active",
    )

    assert removed == [(old_document.id, "owner-a")]
    assert deleted_links == [old_document.id]


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
    monkeypatch.setattr(module.FileService, "get_kb_folder", lambda _owner_id: {"id": "kb-root"})
    monkeypatch.setattr(module.FileService, "new_a_file_from_kb", lambda *_args: {"id": "kb-folder"})
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
