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
import subprocess

import pytest


def _load_svn_module():
    try:
        return importlib.import_module("common.data_source.svn_connector")
    except ModuleNotFoundError:
        pytest.fail("SVN connector is not implemented")


def _config(*, repository_url: str = "https://svn.example.test/svn/company") -> dict:
    return {
        "repository_url": repository_url,
        "base_path": "00_公用文件/00_体系文件",
        "include_roots": [
            "1、一级文件",
            "2、二级文件",
            "3、三级文件",
            "4、四级文件",
        ],
        "exclude_name_contains": ["旧版"],
        "credentials": {"username": "reader", "password": "top-secret"},
    }


class _UnexpectedRunner:
    def run(self, *args, **kwargs):
        del args, kwargs
        pytest.fail("SVN must not be called for invalid configuration")


class _SnapshotRunner:
    def __init__(self) -> None:
        self.calls = []

    def run(self, args, *, username, password, timeout):
        self.calls.append(
            {
                "args": list(args),
                "username": username,
                "password": password,
                "timeout": timeout,
            }
        )
        if args[0] == "info":
            return """<?xml version="1.0" encoding="UTF-8"?>
<info>
  <entry kind="dir" path="00_体系文件" revision="72089">
    <url>https://svn.example.test/svn/company/00_system</url>
    <repository>
      <root>https://svn.example.test/svn/company</root>
      <uuid>repository-uuid</uuid>
    </repository>
  </entry>
</info>
""".encode()

        if args[0] == "cat":
            return b"docx-bytes"

        root_name = args[-1].rsplit("/", 1)[-1]
        return f"""<?xml version="1.0" encoding="UTF-8"?>
<lists>
  <list path="{args[-1]}">
    <entry kind="file">
      <name>A/{root_name}.docx</name>
      <size>100</size>
      <commit revision="72080">
        <author>reader</author>
        <date>2026-08-28T00:00:00.000000Z</date>
      </commit>
    </entry>
  </list>
</lists>
""".encode()


def test_command_runner_passes_password_only_through_stdin():
    svn_connector = _load_svn_module()
    captured = {}

    def execute(command, **kwargs):
        captured["command"] = command
        captured["kwargs"] = kwargs
        return subprocess.CompletedProcess(command, 0, stdout=b"<info/>", stderr=b"")

    runner = svn_connector.SVNCommandRunner(execute=execute)

    runner.run(
        ["info", "--xml", "https://svn.example.test/svn/company"],
        username="reader",
        password="top-secret",
        timeout=15,
    )

    assert "top-secret" not in captured["command"]
    assert captured["command"] == [
        "svn",
        "info",
        "--xml",
        "https://svn.example.test/svn/company",
        "--username",
        "reader",
        "--non-interactive",
        "--no-auth-cache",
        "--password-from-stdin",
    ]
    assert captured["kwargs"]["input"] == b"top-secret\n"
    assert captured["kwargs"]["shell"] is False
    assert captured["kwargs"]["timeout"] == 15


def test_command_runner_redacts_process_error_output():
    svn_connector = _load_svn_module()

    def execute(command, **kwargs):
        del kwargs
        return subprocess.CompletedProcess(
            command,
            1,
            stdout=b"",
            stderr=b"Authentication failed: top-secret",
        )

    runner = svn_connector.SVNCommandRunner(execute=execute)

    with pytest.raises(svn_connector.ConnectorValidationError) as exc_info:
        runner.run(
            ["info", "--xml", "https://svn.example.test/svn/company"],
            username="reader",
            password="top-secret",
            timeout=15,
        )

    assert str(exc_info.value) == "SVN command failed with exit code 1."
    assert "top-secret" not in str(exc_info.value)


def test_connector_rejects_non_https_repository_before_running_svn():
    svn_connector = _load_svn_module()

    with pytest.raises(
        svn_connector.ConnectorValidationError,
        match="SVN repository URL must use HTTPS",
    ):
        svn_connector.SVNConnector(
            _config(repository_url="http://svn.example.test/svn/company"),
            runner=_UnexpectedRunner(),
        )


@pytest.mark.parametrize(
    ("username", "password"),
    [("", "top-secret"), ("reader", "")],
)
def test_connector_rejects_missing_credentials_before_running_svn(username, password):
    svn_connector = _load_svn_module()
    config = _config()
    config["credentials"] = {"username": username, "password": password}

    with pytest.raises(
        svn_connector.ConnectorMissingCredentialError,
        match="Missing credentials for SVN",
    ):
        svn_connector.SVNConnector(config, runner=_UnexpectedRunner())


def test_connector_rejects_roots_outside_the_approved_four():
    svn_connector = _load_svn_module()
    config = _config()
    config["include_roots"].append("5、范围外")

    with pytest.raises(
        svn_connector.ConnectorValidationError,
        match="SVN include roots must match the approved hierarchy roots",
    ):
        svn_connector.SVNConnector(config, runner=_UnexpectedRunner())


@pytest.mark.parametrize(
    ("field", "unsafe_value"),
    [
        ("base_path", "../00_体系文件"),
        ("base_path", "/00_公用文件/00_体系文件"),
        ("include_roots", ["1、一级文件", "2、二级文件", "3、三级文件", "../4、四级文件"]),
    ],
)
def test_connector_rejects_unsafe_configured_paths(field, unsafe_value):
    svn_connector = _load_svn_module()
    config = _config()
    config[field] = unsafe_value

    with pytest.raises(
        svn_connector.ConnectorValidationError,
        match="SVN configured paths must be safe relative paths",
    ):
        svn_connector.SVNConnector(config, runner=_UnexpectedRunner())


def test_listing_pins_all_roots_to_one_repository_revision():
    svn_connector = _load_svn_module()
    runner = _SnapshotRunner()
    connector = svn_connector.SVNConnector(_config(), runner=runner)

    keys = list(connector.list_keys())

    assert len(keys) == 4
    assert connector.snapshot_revision == "72089"
    list_calls = [call for call in runner.calls if call["args"][0] == "list"]
    assert len(list_calls) == 4
    assert all("--recursive" in call["args"] for call in list_calls)
    assert all(call["args"][call["args"].index("-r") + 1] == "72089" for call in list_calls)


def test_get_value_downloads_selected_file_at_snapshot_revision():
    svn_connector = _load_svn_module()
    runner = _SnapshotRunner()
    connector = svn_connector.SVNConnector(_config(), runner=runner)
    key_record = next(connector.list_keys())
    assert not [call for call in runner.calls if call["args"][0] == "cat"]

    document = connector.get_value(key_record.key)

    cat_calls = [call for call in runner.calls if call["args"][0] == "cat"]
    assert len(cat_calls) == 1
    assert cat_calls[0]["args"][:3] == ["cat", "-r", "72089"]
    assert cat_calls[0]["args"][3].endswith("/00_公用文件/00_体系文件/1、一级文件/A/1、一级文件.docx")
    assert document.id == key_record.key
    assert document.blob == b"docx-bytes"
    assert document.source == "svn"
    assert document.semantic_identifier == "1、一级文件.docx"
    assert document.extension == ".docx"
    assert document.size_bytes == len(b"docx-bytes")
    assert document.fingerprint == key_record.fingerprint


def test_slim_snapshot_lists_selected_ids_without_downloading_content():
    svn_connector = _load_svn_module()
    runner = _SnapshotRunner()
    config = _config()
    config["batch_size"] = 3
    connector = svn_connector.SVNConnector(config, runner=runner)

    batches = list(connector.retrieve_all_slim_docs_perm_sync())

    assert [len(batch) for batch in batches] == [3, 1]
    assert [doc.id for batch in batches for doc in batch] == [
        "repository-uuid:1、一级文件/A/1、一级文件.docx",
        "repository-uuid:2、二级文件/A/2、二级文件.docx",
        "repository-uuid:3、三级文件/A/3、三级文件.docx",
        "repository-uuid:4、四级文件/A/4、四级文件.docx",
    ]
    assert not [call for call in runner.calls if call["args"][0] == "cat"]


def test_validate_connector_settings_returns_snapshot_summary_without_cat():
    svn_connector = _load_svn_module()
    runner = _SnapshotRunner()
    connector = svn_connector.SVNConnector(_config(), runner=runner)

    summary = connector.validate_connector_settings()

    assert summary == {
        "repository_uuid": "repository-uuid",
        "revision": "72089",
        "roots": 4,
        "documents": 4,
    }
    assert not [call for call in runner.calls if call["args"][0] == "cat"]


def _entries(svn_connector, paths):
    return [
        svn_connector.SVNEntry(
            relative_path=path,
            size=100,
            changed_revision="72080",
            changed_at="2026-08-28T00:00:00.000000Z",
        )
        for path in paths
    ]


def test_formal_document_selection_applies_scope_exclusions_and_word_preference():
    svn_connector = _load_svn_module()
    entries = _entries(
        svn_connector,
        [
            "1、一级文件/A/A.docx",
            "1、一级文件/A/A.pdf",
            "1、一级文件/B/B.PDF",
            "2、二级文件/C/C.doc",
            "2、二级文件/C/C.docx",
            "2、二级文件/C/C.pdf",
            "3、三级文件/旧版/D.docx",
            "3、三级文件/E旧版.docx",
            "4、四级文件/F/说明.txt",
            "5、范围外/G.docx",
        ],
    )

    selected = svn_connector.select_formal_documents(
        entries,
        include_roots={"1、一级文件", "2、二级文件", "3、三级文件", "4、四级文件"},
        excluded_terms=("旧版",),
    )

    assert [entry.relative_path for entry in selected] == [
        "1、一级文件/A/A.docx",
        "1、一级文件/B/B.PDF",
        "2、二级文件/C/C.doc",
        "2、二级文件/C/C.docx",
    ]


@pytest.mark.parametrize(
    "unsafe_path",
    [
        "/1、一级文件/A.docx",
        "1、一级文件/../A.docx",
        "1、一级文件\\A.docx",
        "1、一级文件/A\x00.docx",
    ],
)
def test_formal_document_selection_rejects_unsafe_relative_paths(unsafe_path):
    svn_connector = _load_svn_module()

    with pytest.raises(
        svn_connector.ConnectorValidationError,
        match="SVN returned an unsafe relative path",
    ):
        svn_connector.select_formal_documents(
            _entries(svn_connector, [unsafe_path]),
            include_roots={"1、一级文件", "2、二级文件", "3、三级文件", "4、四级文件"},
            excluded_terms=("旧版",),
        )
