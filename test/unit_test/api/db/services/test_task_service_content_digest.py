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

import xxhash


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


def _legacy_digest(chunking_config: dict, task: dict) -> str:
    hasher = xxhash.xxh64()
    for field in sorted(chunking_config):
        hasher.update(str(chunking_config[field]).encode("utf-8"))
    for field in ["doc_id", "from_page", "to_page"]:
        hasher.update(str(task.get(field, "")).encode("utf-8"))
    return hasher.hexdigest()


def test_omitted_content_version_preserves_existing_digest():
    from api.db.services.task_service import build_document_task_digest

    config = {"parser_id": "naive", "tenant_id": "tenant-1"}
    task = {"doc_id": "doc-1", "from_page": 0, "to_page": 1_000_000}

    assert build_document_task_digest(config.copy(), task, content_version=None) == _legacy_digest(config, task)


def test_changed_content_version_produces_a_fresh_digest():
    from api.db.services.task_service import build_document_task_digest

    config = {"parser_id": "naive", "tenant_id": "tenant-1"}
    task = {"doc_id": "doc-1", "from_page": 0, "to_page": 1_000_000}

    old_digest = build_document_task_digest(config.copy(), task, content_version="old-fingerprint")
    new_digest = build_document_task_digest(config.copy(), task, content_version="new-fingerprint")

    assert new_digest != old_digest
