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
import importlib
import importlib.util
import sys
import types
from types import SimpleNamespace

import pytest


def _install_optional_dependency_stubs():
    try:
        importlib.import_module("cv2")
    except Exception:  # noqa: BLE001 - optional native dependency can fail during import
        cv2_stub = types.ModuleType("cv2")
        cv2_stub.INTER_LINEAR = 1
        cv2_stub.INTER_CUBIC = 2
        cv2_stub.BORDER_CONSTANT = 0
        cv2_stub.BORDER_REPLICATE = 1
        cv2_stub.__getattr__ = lambda _name: 0
        sys.modules["cv2"] = cv2_stub

    if "xgboost" not in sys.modules and importlib.util.find_spec("xgboost") is None:
        sys.modules["xgboost"] = types.ModuleType("xgboost")


_install_optional_dependency_stubs()


@pytest.mark.parametrize("freq_field", ["refresh_freq", "prune_freq"])
def test_mysql_due_task_cutoff_uses_utc_clock(monkeypatch, freq_field):
    from api.db.services.connector_service import SyncLogsService

    monkeypatch.setenv("DB_TYPE", "mysql")

    cutoff = SyncLogsService._due_task_cutoff_sql(freq_field)

    assert cutoff.sql == f"UTC_TIMESTAMP() - INTERVAL `t2`.`{freq_field}` MINUTE"


@pytest.fixture
def prune_fixture(monkeypatch):
    from api.db.services.connector_service import Connector2KbService, ConnectorService, SyncLogsService
    from api.db.services.document_service import DocumentService
    from api.db.services.file_service import FileService
    from api.db.services.knowledgebase_service import KnowledgebaseService

    link = SimpleNamespace(id="link-1", sync_state={})
    existing_ids = {"doc-1"}
    deleted = []

    monkeypatch.setattr(Connector2KbService, "query", classmethod(lambda cls, **_kwargs: [link]))

    def update_link(_link_id, values):
        link.sync_state = values["sync_state"]
        return True

    monkeypatch.setattr(Connector2KbService, "update_by_id", update_link)
    monkeypatch.setattr(
        ConnectorService,
        "get_by_id",
        classmethod(lambda cls, _connector_id: (True, SimpleNamespace(id="connector-1", source="svn", tenant_id="tenant-1"))),
    )
    monkeypatch.setattr(
        KnowledgebaseService,
        "get_by_id",
        classmethod(lambda cls, _kb_id: (True, SimpleNamespace(id="kb-1", tenant_id="tenant-1"))),
    )
    monkeypatch.setattr(
        DocumentService,
        "list_doc_headers_by_kb_and_source_type",
        classmethod(lambda cls, *_args: [{"id": doc_id} for doc_id in sorted(existing_ids)]),
    )

    def delete_docs(doc_ids, _tenant_id):
        deleted.extend(doc_ids)
        existing_ids.difference_update(doc_ids)
        return ""

    monkeypatch.setattr(FileService, "delete_docs", delete_docs)
    monkeypatch.setattr(SyncLogsService, "increase_removed_docs", classmethod(lambda cls, *_args: None))
    return SimpleNamespace(link=link, existing_ids=existing_ids, deleted=deleted)


def test_first_complete_snapshot_records_missing_without_delete(prune_fixture):
    from api.db.services.connector_service import ConnectorService

    removed, errors = ConnectorService.cleanup_stale_documents_for_task(
        "task-1",
        "connector-1",
        "kb-1",
        "tenant-1",
        [],
        snapshot_revision="11",
        confirmation_scans=2,
    )

    assert (removed, errors) == (0, [])
    assert prune_fixture.deleted == []
    assert prune_fixture.link.sync_state["missing_counts"] == {"doc-1": 1}
    assert prune_fixture.link.sync_state["last_successful_revision"] == "11"


def test_second_complete_snapshot_deletes_confirmed_missing(prune_fixture):
    from api.db.services.connector_service import ConnectorService

    prune_fixture.link.sync_state = {"missing_counts": {"doc-1": 1}}

    removed, errors = ConnectorService.cleanup_stale_documents_for_task(
        "task-2",
        "connector-1",
        "kb-1",
        "tenant-1",
        [],
        snapshot_revision="12",
        confirmation_scans=2,
    )

    assert (removed, errors) == (1, [])
    assert prune_fixture.deleted == ["doc-1"]
    assert prune_fixture.link.sync_state["missing_counts"] == {}


def test_reappearing_document_clears_missing_count(prune_fixture):
    from api.db.services.connector_service import ConnectorService
    from api.utils.common import hash128
    from common.data_source.models import SlimDocument

    source_key = "repository:path"
    retained_id = hash128(f"kb-1:connector-1:{source_key}")
    prune_fixture.existing_ids.clear()
    prune_fixture.existing_ids.add(retained_id)
    prune_fixture.link.sync_state = {"missing_counts": {retained_id: 1}}

    ConnectorService.cleanup_stale_documents_for_task(
        "task-2",
        "connector-1",
        "kb-1",
        "tenant-1",
        [SlimDocument(id=source_key)],
        snapshot_revision="12",
        confirmation_scans=2,
    )

    assert prune_fixture.link.sync_state["missing_counts"] == {}


def test_default_cleanup_keeps_first_snapshot_behavior(prune_fixture):
    from api.db.services.connector_service import ConnectorService

    removed, errors = ConnectorService.cleanup_stale_documents_for_task(
        "task-1",
        "connector-1",
        "kb-1",
        "tenant-1",
        [],
    )

    assert (removed, errors) == (1, [])
    assert prune_fixture.deleted == ["doc-1"]
