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

import pytest

from api.utils.file_utils import normalize_knowledge_upload_path, validate_knowledge_upload_paths


def test_preserves_unicode_folder_names():
    assert normalize_knowledge_upload_path(
        "2、二级文件/制度文件/审批流程.docx",
        "审批流程.docx",
    ) == ["2、二级文件", "制度文件", "审批流程.docx"]


def test_normalizes_backslashes_and_duplicate_separators():
    assert normalize_knowledge_upload_path(
        r"一级\二级//file.txt",
        "file.txt",
    ) == ["一级", "二级", "file.txt"]


@pytest.mark.parametrize(
    "raw_path",
    [
        "/etc/passwd",
        r"C:\secret\file.txt",
        "../file.txt",
        "safe/../file.txt",
        "safe/./file.txt",
        "safe/fi\x00le.txt",
    ],
)
def test_rejects_unsafe_paths(raw_path):
    with pytest.raises(ValueError):
        normalize_knowledge_upload_path(raw_path, "file.txt")


def test_rejects_basename_mismatch():
    with pytest.raises(ValueError, match="does not match"):
        normalize_knowledge_upload_path("folder/other.txt", "file.txt")


def test_rejects_excessive_depth():
    raw_path = "/".join(["folder"] * 33 + ["file.txt"])
    with pytest.raises(ValueError, match="depth"):
        normalize_knowledge_upload_path(raw_path, "file.txt", max_depth=32)


def test_rejects_path_segments_over_database_limit():
    segment = "知" * 86
    with pytest.raises(ValueError, match="255 bytes"):
        normalize_knowledge_upload_path(f"{segment}/file.txt", "file.txt")


def test_defaults_missing_relative_paths_to_dataset_root():
    assert validate_knowledge_upload_paths([], ["one.txt", "two.txt"]) == ["", ""]


def test_requires_one_relative_path_per_uploaded_file():
    with pytest.raises(ValueError, match="one relative_path"):
        validate_knowledge_upload_paths(["folder/one.txt"], ["one.txt", "two.txt"])


def test_validates_every_relative_path_against_its_filename():
    with pytest.raises(ValueError, match="does not match"):
        validate_knowledge_upload_paths(["folder/other.txt"], ["one.txt"])
