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
import importlib.util
import inspect
import socket
import sys
import threading
import time
import types
import warnings
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

warnings.filterwarnings(
    "ignore",
    message="pkg_resources is deprecated as an API.*",
    category=UserWarning,
)


def _install_cv2_stub_if_unavailable():
    try:
        importlib.import_module("cv2")
        return
    except Exception:
        pass

    stub = types.ModuleType("cv2")
    stub.INTER_LINEAR = 1
    stub.INTER_CUBIC = 2
    stub.BORDER_CONSTANT = 0
    stub.BORDER_REPLICATE = 1

    def _missing(*_args, **_kwargs):
        raise RuntimeError("cv2 runtime call is unavailable in this test environment")

    def _module_getattr(name):
        if name.isupper():
            return 0
        return _missing

    stub.__getattr__ = _module_getattr
    sys.modules["cv2"] = stub


def _install_xgboost_stub_if_unavailable():
    if "xgboost" in sys.modules:
        return
    if importlib.util.find_spec("xgboost") is not None:
        return
    sys.modules["xgboost"] = types.ModuleType("xgboost")


_install_cv2_stub_if_unavailable()
_install_xgboost_stub_if_unavailable()

from api.db.services import file_service as file_service_module  # noqa: E402
from api.db.services.file_service import FileService  # noqa: E402


class _DummyUploadFile:
    def __init__(self, filename, doc_id):
        self.filename = filename
        self.id = doc_id

    def read(self):
        raise AssertionError("read() should not be called for cross-KB collision path")


def _unwrapped_upload_document():
    return FileService.upload_document.__func__.__wrapped__


def _mock_folder_parent_lock(monkeypatch, folders):
    @contextmanager
    def locked_parent(cls, parent_id):
        parent = folders.get(parent_id)
        if parent is None:
            raise RuntimeError("Cannot find the knowledge base folder.")
        yield parent

    monkeypatch.setattr(FileService, "_locked_kb_folder_parent", classmethod(locked_parent))


def test_connector_upload_call_uses_owner_context_and_audit_keyword(monkeypatch):
    from api.db.services.connector_service import SyncLogsService

    kb = SimpleNamespace(id="kb-1")
    seen = []

    def upload_document(_kb, files, owner_tenant_id, *, created_by, src):
        seen.append((owner_tenant_id, created_by, src, files[0].filename))
        return [], []

    monkeypatch.setattr(FileService, "upload_document", upload_document)

    errors, document_ids = SyncLogsService.duplicate_and_parse(
        kb,
        [
            {
                "id": "doc-1",
                "semantic_identifier": "Policy",
                "extension": ".txt",
                "blob": b"policy",
            }
        ],
        "owner-1",
        "connector/source-1",
        auto_parse=False,
    )

    assert errors == []
    assert document_ids == []
    assert seen == [("owner-1", "owner-1", "connector/source-1", "Policy.txt")]


@pytest.mark.p2
def test_upload_document_skips_cross_kb_document_id_collision(monkeypatch):
    kb = SimpleNamespace(
        id="kb-target",
        tenant_id="tenant-1",
        name="Target KB",
        parser_id="default",
        pipeline_id=None,
        parser_config={},
    )
    existing_doc = SimpleNamespace(
        id="doc-1",
        kb_id="kb-other",
        location="old-location.txt",
        content_hash="old-hash",
        to_dict=lambda: {"id": "doc-1"},
    )

    monkeypatch.setattr(FileService, "get_root_folder", classmethod(lambda cls, _uid: {"id": "root"}))
    monkeypatch.setattr(FileService, "init_knowledgebase_docs", classmethod(lambda cls, _pf_id, _uid: None))
    monkeypatch.setattr(FileService, "get_kb_folder", classmethod(lambda cls, _uid: {"id": "kb-root"}))
    monkeypatch.setattr(
        FileService,
        "new_a_file_from_kb",
        classmethod(lambda cls, _tenant_id, _name, _parent_id: {"id": "kb-folder"}),
    )
    monkeypatch.setattr(file_service_module.DocumentService, "get_by_id", lambda _doc_id: (True, existing_doc))

    err, files = _unwrapped_upload_document()(
        FileService,
        kb,
        [_DummyUploadFile(filename="collision.txt", doc_id="doc-1")],
        "tenant-1",
        created_by="user-1",
    )

    assert files == []
    assert len(err) == 1
    assert err[0].startswith("collision.txt: ")
    assert "Existing document id collision with another knowledge base; skipping update." in err[0]


class _ReadableUploadFile:
    def __init__(self, filename: str, payload: bytes):
        self.filename = filename
        self._payload = payload

    def read(self):
        return self._payload


@pytest.mark.p2
def test_upload_document_places_relative_folders_under_explicit_parent(monkeypatch):
    kb = SimpleNamespace(
        id="kb-1",
        tenant_id="tenant-1",
        name="Knowledge Base",
        parser_id="naive",
        pipeline_id=None,
        parser_config={},
    )
    files = [
        _ReadableUploadFile("A.txt", b"first"),
        _ReadableUploadFile("A.txt", b"second"),
    ]
    folders = {
        "kb-folder": SimpleNamespace(
            id="kb-folder",
            parent_id="kb-root",
            tenant_id="tenant-1",
            name="Knowledge Base",
            type="folder",
        ),
        "current-folder": SimpleNamespace(
            id="current-folder",
            parent_id="kb-folder",
            tenant_id="tenant-1",
            name="Current Folder",
            type="folder",
        ),
    }
    leaf_files = []
    inserted_documents = []
    generated_ids = iter(["doc-1", "folder-1", "folder-2", "doc-2", "folder-3"])

    class _Storage:
        def __init__(self):
            self.objects = {}

        def obj_exist(self, bucket, location):
            return (bucket, location) in self.objects

        def put(self, bucket, location, blob, *_args):
            self.objects[(bucket, location)] = blob

    def query_files(**kwargs):
        parent_id = kwargs.get("parent_id")
        name = kwargs.get("name")
        file_type = kwargs.get("type")
        folder_matches = [
            folder
            for folder in folders.values()
            if folder.parent_id == parent_id and folder.name == name and (file_type is None or folder.type == file_type)
        ]
        leaf_matches = [
            file
            for file in leaf_files
            if file["parent_id"] == parent_id and file["name"] == name and file_type is None
        ]
        return folder_matches + leaf_matches

    def insert_folder(data):
        folder = SimpleNamespace(**data)
        folders[folder.id] = folder
        return folder

    def add_file_from_kb(doc, parent_id, tenant_id, *, created_by=None):
        leaf_files.append({
            "parent_id": parent_id,
            "tenant_id": tenant_id,
            "created_by": created_by,
            "name": doc["name"],
            "location": doc["location"],
        })

    monkeypatch.setattr(FileService, "get_root_folder", classmethod(lambda cls, _uid: {"id": "root"}))
    monkeypatch.setattr(FileService, "init_knowledgebase_docs", classmethod(lambda cls, _pf_id, _uid: None))
    monkeypatch.setattr(FileService, "get_kb_folder", classmethod(lambda cls, _uid: {"id": "kb-root"}))
    monkeypatch.setattr(
        FileService,
        "new_a_file_from_kb",
        classmethod(lambda cls, _tenant_id, _name, _parent_id: {"id": "kb-folder"}),
    )
    monkeypatch.setattr(FileService, "get_by_id", classmethod(lambda cls, file_id: (file_id in folders, folders.get(file_id))))
    _mock_folder_parent_lock(monkeypatch, folders)
    monkeypatch.setattr(FileService, "query", classmethod(lambda cls, **kwargs: query_files(**kwargs)))
    monkeypatch.setattr(
        FileService,
        "_query_kb_folder_entries_locked",
        classmethod(lambda cls, tenant_id, parent_id, name: query_files(tenant_id=tenant_id, parent_id=parent_id, name=name)),
    )
    monkeypatch.setattr(FileService, "_insert_kb_folder_locked", classmethod(lambda cls, data: insert_folder(data)))
    monkeypatch.setattr(
        FileService,
        "add_file_from_kb",
        classmethod(
            lambda cls, doc, parent_id, tenant_id, *, created_by=None: add_file_from_kb(
                doc,
                parent_id,
                tenant_id,
                created_by=created_by,
            )
        ),
    )
    monkeypatch.setattr(FileService, "get_parser", classmethod(lambda cls, _type, _name, parser_id: parser_id))
    monkeypatch.setattr(file_service_module, "get_uuid", lambda: next(generated_ids))
    monkeypatch.setattr(file_service_module.DocumentService, "get_by_id", lambda _doc_id: (False, None))
    monkeypatch.setattr(file_service_module.DocumentService, "check_doc_health", lambda *_args: True)
    monkeypatch.setattr(file_service_module.DocumentService, "insert", lambda doc: inserted_documents.append(doc.copy()))
    monkeypatch.setattr(file_service_module, "thumbnail_img", lambda *_args: None)
    monkeypatch.setattr(file_service_module.settings, "STORAGE_IMPL", _Storage())

    upload_kwargs = {
        "relative_paths": [
            "2、二级文件/制度文件/A.txt",
            "2、二级文件/表单/A.txt",
        ],
        "parent_folder_id": "current-folder",
    }
    supported_kwargs = {
        key: value
        for key, value in upload_kwargs.items()
        if key in inspect.signature(_unwrapped_upload_document()).parameters
    }
    err, uploaded = _unwrapped_upload_document()(
        FileService,
        kb,
        files,
        "tenant-1",
        created_by="member-1",
        **supported_kwargs,
    )

    assert err == []
    assert [item[0]["name"] for item in uploaded] == ["A.txt", "A.txt"]
    assert {item[0]["location"] for item in uploaded} == {
        "2、二级文件/制度文件/A.txt",
        "2、二级文件/表单/A.txt",
    }
    top_folder = next(folder for folder in folders.values() if folder.name == "2、二级文件")
    assert top_folder.parent_id == "current-folder"
    child_folders = {folder.name: folder for folder in folders.values() if folder.parent_id == top_folder.id}
    assert set(child_folders) == {"制度文件", "表单"}
    assert {file["parent_id"] for file in leaf_files} == {
        child_folders["制度文件"].id,
        child_folders["表单"].id,
    }
    assert {file["tenant_id"] for file in leaf_files} == {"tenant-1"}
    assert {file["created_by"] for file in leaf_files} == {"member-1"}
    assert len(inserted_documents) == 2
    assert {document["created_by"] for document in inserted_documents} == {"member-1"}


@pytest.mark.p2
def test_failed_upload_removes_only_request_created_empty_folders(monkeypatch):
    kb = SimpleNamespace(
        id="kb-1",
        tenant_id="tenant-1",
        name="Knowledge Base",
        parser_id="naive",
        pipeline_id=None,
        parser_config={},
    )
    broken_file = _ReadableUploadFile("A.txt", b"unused")
    broken_file.read = lambda: (_ for _ in ()).throw(RuntimeError("broken upload"))
    folders = {
        "kb-folder": SimpleNamespace(
            id="kb-folder",
            parent_id="kb-root",
            tenant_id="tenant-1",
            name="Knowledge Base",
            type="folder",
        )
    }
    generated_ids = iter(["doc-1", "folder-1", "folder-2"])

    class _Storage:
        @staticmethod
        def obj_exist(*_args):
            return False

        @staticmethod
        def put(*_args):
            raise AssertionError("storage must not be written after read failure")

    def query_files(**kwargs):
        return [
            folder
            for folder in folders.values()
            if folder.parent_id == kwargs.get("parent_id")
            and folder.name == kwargs.get("name")
            and (kwargs.get("type") is None or folder.type == kwargs.get("type"))
        ]

    def insert_folder(data):
        folder = SimpleNamespace(**data)
        folders[folder.id] = folder
        return folder

    monkeypatch.setattr(FileService, "get_root_folder", classmethod(lambda cls, _uid: {"id": "root"}))
    monkeypatch.setattr(FileService, "init_knowledgebase_docs", classmethod(lambda cls, _pf_id, _uid: None))
    monkeypatch.setattr(FileService, "get_kb_folder", classmethod(lambda cls, _uid: {"id": "kb-root"}))
    monkeypatch.setattr(FileService, "new_a_file_from_kb", classmethod(lambda cls, *_args: {"id": "kb-folder"}))
    monkeypatch.setattr(FileService, "get_by_id", classmethod(lambda cls, file_id: (file_id in folders, folders.get(file_id))))
    _mock_folder_parent_lock(monkeypatch, folders)
    monkeypatch.setattr(FileService, "query", classmethod(lambda cls, **kwargs: query_files(**kwargs)))
    monkeypatch.setattr(
        FileService,
        "_query_kb_folder_entries_locked",
        classmethod(lambda cls, tenant_id, parent_id, name: query_files(tenant_id=tenant_id, parent_id=parent_id, name=name)),
    )
    monkeypatch.setattr(FileService, "_insert_kb_folder_locked", classmethod(lambda cls, data: insert_folder(data)))
    monkeypatch.setattr(FileService, "delete", classmethod(lambda cls, folder: folders.pop(folder.id, None)))
    monkeypatch.setattr(FileService, "get_parser", classmethod(lambda cls, _type, _name, parser_id: parser_id))
    monkeypatch.setattr(file_service_module, "get_uuid", lambda: next(generated_ids))
    monkeypatch.setattr(file_service_module.DocumentService, "get_by_id", lambda _doc_id: (False, None))
    monkeypatch.setattr(file_service_module.DocumentService, "check_doc_health", lambda *_args: True)
    monkeypatch.setattr(file_service_module, "thumbnail_img", lambda *_args: None)
    monkeypatch.setattr(file_service_module.settings, "STORAGE_IMPL", _Storage())

    err, uploaded = _unwrapped_upload_document()(
        FileService,
        kb,
        [broken_file],
        "tenant-1",
        created_by="member-1",
        relative_paths=["顶层/空目录/A.txt"],
    )

    assert uploaded == []
    assert err == ["A.txt: broken upload"]
    assert set(folders) == {"kb-folder"}


@pytest.mark.p2
def test_ensure_kb_folder_path_serializes_concurrent_creation(monkeypatch):
    parent = SimpleNamespace(
        id="parent-folder",
        parent_id="kb-folder",
        tenant_id="tenant-1",
        name="Current Folder",
        type="folder",
    )
    folders = []
    records_lock = threading.Lock()
    next_id = iter(["folder-1", "folder-2"])

    monkeypatch.setattr(
        FileService,
        "get_by_id",
        classmethod(lambda cls, file_id: (file_id == parent.id, parent if file_id == parent.id else None)),
    )
    _mock_folder_parent_lock(monkeypatch, {parent.id: parent})

    def query_files(**kwargs):
        with records_lock:
            matches = [
                folder
                for folder in folders
                if folder.tenant_id == kwargs.get("tenant_id")
                and folder.parent_id == kwargs.get("parent_id")
                and folder.name == kwargs.get("name")
            ]
        if not matches:
            # Open a deterministic race window for the second request.
            time.sleep(0.05)
        return matches

    def insert_folder(data):
        folder = SimpleNamespace(**{**data, "id": next(next_id)})
        with records_lock:
            folders.append(folder)
        return folder

    monkeypatch.setattr(
        FileService,
        "_query_kb_folder_entries_locked",
        classmethod(lambda cls, tenant_id, parent_id, name: query_files(tenant_id=tenant_id, parent_id=parent_id, name=name)),
    )
    monkeypatch.setattr(FileService, "_insert_kb_folder_locked", classmethod(lambda cls, data: insert_folder(data)))

    results = []

    def create_path():
        results.append(
            FileService.ensure_kb_folder_path(
                parent.id,
                ["重复目录"],
                "tenant-1",
            )
        )

    threads = [threading.Thread(target=create_path) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(folders) == 1
    assert len({folder.id for folder in results}) == 1


# ---------------------------------------------------------------------------
# Helpers shared by TestValidateUrlForCrawl
# ---------------------------------------------------------------------------

def _addrinfo(ip_str: str) -> list:
    """Build a minimal getaddrinfo-style result for a single address string."""
    family = socket.AF_INET6 if ":" in ip_str else socket.AF_INET
    return [(family, socket.SOCK_STREAM, 6, "", (ip_str, 0))]


# ---------------------------------------------------------------------------
# _validate_url_for_crawl SSRF-guard tests
# ---------------------------------------------------------------------------

@pytest.mark.p2
class TestValidateUrlForCrawl:
    """Focused regression suite for the SSRF guard on the URL-crawl path.

    All DNS lookups are monkeypatched so the tests are deterministic and
    require no network access.
    """

    # -- scheme checks -------------------------------------------------------

    def test_rejects_ftp_scheme(self):
        with pytest.raises(ValueError, match="scheme"):
            FileService._validate_url_for_crawl("ftp://example.com/file.txt")

    def test_rejects_file_scheme(self):
        with pytest.raises(ValueError, match="scheme"):
            FileService._validate_url_for_crawl("file:///etc/passwd")

    def test_rejects_javascript_scheme(self):
        with pytest.raises(ValueError, match="scheme"):
            FileService._validate_url_for_crawl("javascript:alert(1)")

    # -- host checks ---------------------------------------------------------

    def test_rejects_missing_host(self):
        with pytest.raises(ValueError, match="host"):
            FileService._validate_url_for_crawl("http:///path")

    def test_rejects_dns_resolution_failure(self, monkeypatch):
        def _raise(h, p):
            raise socket.gaierror("NXDOMAIN")

        monkeypatch.setattr(socket, "getaddrinfo", _raise)
        with pytest.raises(ValueError, match="Could not resolve"):
            FileService._validate_url_for_crawl("http://nxdomain.invalid/")

    # -- blocked address families --------------------------------------------

    def test_rejects_loopback_ipv4(self, monkeypatch):
        monkeypatch.setattr(socket, "getaddrinfo", lambda h, p: _addrinfo("127.0.0.1"))
        with pytest.raises(ValueError, match="non-public"):
            FileService._validate_url_for_crawl("http://localhost/")

    def test_rejects_private_class_a(self, monkeypatch):
        monkeypatch.setattr(socket, "getaddrinfo", lambda h, p: _addrinfo("10.0.0.1"))
        with pytest.raises(ValueError, match="non-public"):
            FileService._validate_url_for_crawl("http://internal.example/")

    def test_rejects_private_class_b(self, monkeypatch):
        monkeypatch.setattr(socket, "getaddrinfo", lambda h, p: _addrinfo("172.16.0.1"))
        with pytest.raises(ValueError, match="non-public"):
            FileService._validate_url_for_crawl("http://internal.example/")

    def test_rejects_private_class_c(self, monkeypatch):
        monkeypatch.setattr(socket, "getaddrinfo", lambda h, p: _addrinfo("192.168.1.100"))
        with pytest.raises(ValueError, match="non-public"):
            FileService._validate_url_for_crawl("http://internal.example/")

    def test_rejects_link_local_ipv4(self, monkeypatch):
        monkeypatch.setattr(socket, "getaddrinfo", lambda h, p: _addrinfo("169.254.0.1"))
        with pytest.raises(ValueError, match="non-public"):
            FileService._validate_url_for_crawl("http://link-local.example/")

    def test_rejects_reserved_ipv4(self, monkeypatch):
        # 240.0.0.0/4 is IANA reserved — not globally routable
        monkeypatch.setattr(socket, "getaddrinfo", lambda h, p: _addrinfo("240.0.0.1"))
        with pytest.raises(ValueError, match="non-public"):
            FileService._validate_url_for_crawl("http://reserved.example/")

    def test_rejects_ipv4_mapped_loopback(self, monkeypatch):
        """::ffff:127.0.0.1 must not bypass the loopback check."""
        monkeypatch.setattr(socket, "getaddrinfo", lambda h, p: _addrinfo("::ffff:127.0.0.1"))
        with pytest.raises(ValueError, match="non-public"):
            FileService._validate_url_for_crawl("http://mapped-loopback.example/")

    def test_rejects_ipv4_mapped_private(self, monkeypatch):
        """::ffff:192.168.1.1 must not bypass the private-range check."""
        monkeypatch.setattr(socket, "getaddrinfo", lambda h, p: _addrinfo("::ffff:192.168.1.1"))
        with pytest.raises(ValueError, match="non-public"):
            FileService._validate_url_for_crawl("http://mapped-private.example/")

    def test_rejects_when_any_record_is_private(self, monkeypatch):
        """All DNS records must pass; one private record is enough to block."""
        monkeypatch.setattr(
            socket,
            "getaddrinfo",
            lambda h, p: _addrinfo("93.184.216.34") + _addrinfo("10.0.0.1"),
        )
        with pytest.raises(ValueError, match="non-public"):
            FileService._validate_url_for_crawl("http://mixed.example/")

    # -- allowed cases -------------------------------------------------------

    def test_allows_public_ipv4(self, monkeypatch):
        monkeypatch.setattr(socket, "getaddrinfo", lambda h, p: _addrinfo("93.184.216.34"))
        hostname, resolved_ip = FileService._validate_url_for_crawl("https://example.com/doc.pdf")
        assert hostname == "example.com"
        assert resolved_ip == "93.184.216.34"

    def test_allows_public_ipv6(self, monkeypatch):
        monkeypatch.setattr(
            socket,
            "getaddrinfo",
            lambda h, p: _addrinfo("2606:2800:220:1:248:1893:25c8:1946"),
        )
        hostname, resolved_ip = FileService._validate_url_for_crawl("https://example.com/")
        assert hostname == "example.com"
        assert resolved_ip == "2606:2800:220:1:248:1893:25c8:1946"

    def test_allows_http_scheme(self, monkeypatch):
        monkeypatch.setattr(socket, "getaddrinfo", lambda h, p: _addrinfo("1.2.3.4"))
        hostname, _ = FileService._validate_url_for_crawl("http://example.com/")
        assert hostname == "example.com"

    # -- multi-record behaviour ----------------------------------------------

    def test_returns_first_ip_for_multi_record_host(self, monkeypatch):
        """The first public IP is returned as the DNS pin value."""
        monkeypatch.setattr(
            socket,
            "getaddrinfo",
            lambda h, p: _addrinfo("1.2.3.4") + _addrinfo("5.6.7.8"),
        )
        _, resolved_ip = FileService._validate_url_for_crawl("http://multi.example/")
        assert resolved_ip == "1.2.3.4"

    def test_allows_dual_stack_host(self, monkeypatch):
        """A host with both public IPv4 and public IPv6 records is allowed."""
        monkeypatch.setattr(
            socket,
            "getaddrinfo",
            lambda h, p: (
                _addrinfo("93.184.216.34")
                + _addrinfo("2606:2800:220:1:248:1893:25c8:1946")
            ),
        )
        hostname, resolved_ip = FileService._validate_url_for_crawl("https://example.com/")
        assert hostname == "example.com"
        assert resolved_ip == "93.184.216.34"
