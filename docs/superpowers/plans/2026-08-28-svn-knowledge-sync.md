# SVN Knowledge Sync Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a native read-only SVN connector that mirrors the four approved hierarchy branches into a RAGFlow knowledge base, incrementally reparses changes, and safely removes files after two confirmed missing snapshots.

**Architecture:** A focused `SVNConnector` shells out to the installed Subversion 1.14 client with argument arrays and stdin-only credentials, emits fingerprinted documents carrying validated relative paths, and plugs into the existing connector scheduler. The shared ingestion path gains optional relative-path propagation and content-aware parsing, while per-connector/KB sync state tracks missing documents and connector-created folders.

**Tech Stack:** Python 3.10+, Pydantic, Subversion CLI/XML, Peewee/MySQL, Quart, pytest, React/TypeScript, UmiJS/Jest.

**Spec:** `docs/superpowers/specs/2026-08-28-svn-knowledge-sync-design.md`

## Global Constraints

- SVN access is read-only; no VisualSVN server development or repository mutation is allowed.
- Only `1、一级文件`, `2、二级文件`, `3、三级文件`, and `4、四级文件` below `00_公用文件/00_体系文件` are in scope.
- Exclude every file or directory whose name contains `旧版`; a matching directory excludes its subtree.
- Prefer all DOC/DOCX files in a directory; use PDFs only when that directory has no DOC/DOCX.
- Preserve valid Unicode relative paths and do not mirror empty directories.
- Delete a missing RAGFlow document only after two consecutive complete successful snapshots.
- Default automatic sync and prune frequencies are both 60 minutes; a non-destructive manual sync action must be available separately from rebuild.
- Passwords travel through stdin only and must never appear in argv, logs, errors, tests, or committed fixtures.
- Every shared-service extension is opt-in: omitted optional parameters must preserve byte-for-byte task digests and first-snapshot prune behavior for non-SVN callers.

---

### Task 1: SVN listing, selection, and secure download connector

**Files:**
- Create: `common/data_source/svn_connector.py`
- Create: `test/unit_test/data_source/test_svn_connector_unit.py`
- Modify: `common/data_source/config.py`

**Interfaces:**
- Produces: `SVNConnector(config: dict)`, `validate_connector_settings()`, `list_keys() -> Iterator[KeyRecord]`, `get_value(key: str) -> Document`, and `retrieve_all_slim_docs_perm_sync() -> Iterator[list[SlimDocument]]`.
- Produces: `DocumentSource.SVN = "svn"`.
- Uses: existing `Document`, `KeyRecord`, `SlimDocument`, `ConnectorValidationError`, and `hash128`/xxhash conventions.

- [ ] **Step 1: Write failing tests for a fixed revision and secure command invocation**

```python
def _config(password: str) -> dict:
    return {
        "repository_url": "https://svn.example.test/svn/company",
        "base_path": "00_公用文件/00_体系文件",
        "include_roots": ["1、一级文件", "2、二级文件", "3、三级文件", "4、四级文件"],
        "exclude_name_contains": ["旧版"],
        "credentials": {"username": "reader", "password": password},
    }

def test_snapshot_uses_one_revision_and_password_only_on_stdin(fake_runner):
    connector = SVNConnector(_config(password="s3cret"), runner=fake_runner)
    keys = list(connector.list_keys())

    assert keys
    assert {call.revision for call in fake_runner.calls if call.action in {"list", "cat"}} == {"72089"}
    assert all("s3cret" not in arg for call in fake_runner.calls for arg in call.argv)
    assert all(call.stdin == "s3cret\n" for call in fake_runner.authenticated_calls)
```

- [ ] **Step 2: Run the security test and verify RED**

Run: `uv run pytest test/unit_test/data_source/test_svn_connector_unit.py::test_snapshot_uses_one_revision_and_password_only_on_stdin -v`

Expected: FAIL because `common.data_source.svn_connector` does not exist.

- [ ] **Step 3: Implement the minimal secure SVN command runner and XML parsers**

```python
class SVNCommandRunner:
    def run(self, args: list[str], password: str, timeout: int) -> bytes:
        command = ["svn", *args, "--non-interactive", "--no-auth-cache", "--password-from-stdin"]
        result = subprocess.run(
            command,
            input=(password + "\n").encode(),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            check=False,
        )
        if result.returncode:
            raise ConnectorValidationError(_safe_svn_error(result.stderr))
        return result.stdout
```

Implement `svn info --xml` parsing for UUID/revision and `svn list --xml --recursive -r <revision>` parsing for path, kind, size, changed revision, and date. Reject non-HTTPS repository URLs and unsafe relative paths before issuing commands.

- [ ] **Step 4: Run the secure runner tests and verify GREEN**

Run: `uv run pytest test/unit_test/data_source/test_svn_connector_unit.py -k 'snapshot or password or https or xml' -v`

Expected: PASS.

- [ ] **Step 5: Write failing table-driven tests for scope, old-version exclusion, and Word/PDF selection**

```python
def _svn_entries(paths: list[str]) -> list[SVNEntry]:
    return [
        SVNEntry(path=path, kind="file", size=100, changed_revision="72089", changed_at="2026-08-28T00:00:00Z")
        for path in paths
    ]

@pytest.mark.parametrize(
    ("entries", "expected"),
    [
        (["1、一级文件/A/A.docx", "1、一级文件/A/A.pdf"], ["1、一级文件/A/A.docx"]),
        (["2、二级文件/B/B.pdf"], ["2、二级文件/B/B.pdf"]),
        (["3、三级文件/旧版/C.docx", "4、四级文件/D旧版.docx"], []),
        (["5、范围外/E.docx"], []),
    ],
)
def test_selects_only_approved_formal_documents(entries, expected):
    assert select_formal_documents(_svn_entries(entries), DEFAULT_POLICY).paths == expected
```

- [ ] **Step 6: Run selection tests and verify RED**

Run: `uv run pytest test/unit_test/data_source/test_svn_connector_unit.py::test_selects_only_approved_formal_documents -v`

Expected: FAIL because `select_formal_documents` is missing.

- [ ] **Step 7: Implement deterministic selection, stable keys, fingerprints, and lazy cat**

Implement directory grouping with case-insensitive suffix checks. `list_keys()` must list metadata only and emit fingerprints derived from repository UUID, relative path, changed revision, and size. `get_value()` alone performs `svn cat -r <snapshot_revision>` and returns a `Document` whose `id` is `<uuid>:<relative_path>`, `semantic_identifier` is the leaf stem, and `relative_path` is the complete approved path.

- [ ] **Step 8: Run all connector unit tests**

Run: `uv run pytest test/unit_test/data_source/test_svn_connector_unit.py -v`

Expected: PASS.

- [ ] **Step 9: Commit the connector core**

```bash
git add common/data_source/svn_connector.py common/data_source/config.py test/unit_test/data_source/test_svn_connector_unit.py
git commit -m "feat: add read-only SVN document connector"
```

### Task 2: Propagate connector relative paths into knowledge folders

**Files:**
- Modify: `common/data_source/models.py`
- Modify: `rag/svr/sync_data_source.py`
- Modify: `api/db/services/connector_service.py`
- Modify: `api/db/services/file_service.py`
- Modify: `test/unit_test/rag/test_sync_data_source.py`
- Modify: `test/unit_test/api/db/services/test_file_service_upload_document.py`
- Create: `test/unit_test/api/db/services/test_connector_relative_paths.py`

**Interfaces:**
- Produces: `Document.relative_path: Optional[str] = None`.
- Produces: `SyncLogsService.duplicate_and_parse(kb, docs, tenant_id, src, auto_parse=True)` passing the ordered `relative_paths` derived from `docs` into `FileService.upload_document`.
- Produces: optional `managed_folder_ids: set[str]` output from `FileService.upload_document` containing only folder IDs created by that call.

- [ ] **Step 1: Write a failing sync-pipeline test that observes the relative path**

```python
@pytest.mark.asyncio
async def test_sync_passes_connector_relative_path_to_upload(monkeypatch):
    doc = _make_fake_doc()
    doc.relative_path = "1、一级文件/制度/A.docx"
    captured = {}
    monkeypatch.setattr(SyncLogsService, "duplicate_and_parse", lambda *args, **kwargs: captured.update(docs=args[1]) or ([], ["id"]))

    await _FakeSync(iter(([doc],)))._run_task_logic(_make_task())

    assert captured["docs"][0]["relative_path"] == "1、一级文件/制度/A.docx"
```

- [ ] **Step 2: Run the pipeline test and verify RED**

Run: `uv run pytest test/unit_test/rag/test_sync_data_source.py::test_sync_passes_connector_relative_path_to_upload -v`

Expected: FAIL because the path is dropped.

- [ ] **Step 3: Add the optional model field and pass it through `SyncBase`**

```python
class Document(BaseModel):
    relative_path: Optional[str] = None
```

When building `d` in `_run_sync_task_logic`, copy `doc.relative_path` only when present so existing connector fixtures remain compatible.

- [ ] **Step 4: Write a failing service test for folder-aware upload**

```python
def test_duplicate_and_parse_supplies_relative_paths(monkeypatch, kb):
    captured = {}
    monkeypatch.setattr(FileService, "upload_document", lambda *args, **kwargs: captured.update(kwargs) or ([], []))

    SyncLogsService.duplicate_and_parse(
        kb,
        [{
            "id": "doc-1",
            "semantic_identifier": "A",
            "extension": ".docx",
            "relative_path": "1、一级文件/制度/A.docx",
            "blob": b"docx",
        }],
        "tenant",
        "svn/c1",
        False,
    )

    assert captured["relative_paths"] == ["1、一级文件/制度/A.docx"]
```

- [ ] **Step 5: Run the service test and verify RED**

Run: `uv run pytest test/unit_test/api/db/services/test_connector_relative_paths.py -v`

Expected: FAIL because `relative_paths` is not passed.

- [ ] **Step 6: Pass ordered paths into `FileService.upload_document` and expose created folder IDs**

Build filenames and relative paths in one loop to keep multipart-style ordering exact. Extend `upload_document` with `managed_folder_ids: set[str] | None = None`; after each successful create, add only `created_folder_ids` generated by `ensure_kb_folder_path`. Do not mark reused folders.

- [ ] **Step 7: Verify Unicode nesting and no empty folders**

Run: `uv run pytest test/unit_test/api/db/services/test_connector_relative_paths.py test/unit_test/api/db/services/test_file_service_upload_document.py -v`

Expected: PASS, including a real service test asserting the leaf file parent chain equals `1、一级文件/制度`.

- [ ] **Step 8: Commit hierarchy propagation**

```bash
git add common/data_source/models.py rag/svr/sync_data_source.py api/db/services/connector_service.py api/db/services/file_service.py test/unit_test/rag/test_sync_data_source.py test/unit_test/api/db/services/test_file_service_upload_document.py test/unit_test/api/db/services/test_connector_relative_paths.py
git commit -m "feat: preserve connector document hierarchy"
```

### Task 3: Force changed SVN content to reparse without changing other callers

**Files:**
- Modify: `api/db/services/task_service.py`
- Modify: `api/db/services/document_service.py`
- Modify: `api/db/services/connector_service.py`
- Modify: `test/unit_test/api/db/services/test_task_service_content_digest.py`

**Interfaces:**
- Produces: optional `content_version: str | None = None` on `DocumentService.run()` and `queue_tasks()`.
- Changes: a non-empty `content_version` is included in the task digest; omitting it preserves the existing digest exactly.
- Changes: `SyncLogsService.duplicate_and_parse()` supplies the document fingerprint only when `src.startswith("svn/")`.
- Guarantees: identical SVN content may reuse completed task chunks; changed SVN content cannot reuse them; local uploads and every existing connector keep current digest behavior.

- [ ] **Step 1: Write a failing digest behavior test**

```python
def test_changed_content_hash_does_not_reuse_previous_chunks(monkeypatch):
    first = _queue_and_capture_task(monkeypatch, content_version="old")
    previous = [{**first, "progress": 1.0, "chunk_ids": "old-chunk"}]

    second = _queue_and_capture_task(monkeypatch, content_version="new", previous=previous)

    assert second["digest"] != first["digest"]
    assert second["progress"] == 0.0
    assert second.get("chunk_ids", "") == ""
```

- [ ] **Step 2: Run the digest test and verify RED**

Run: `uv run pytest test/unit_test/api/db/services/test_task_service_content_digest.py -v`

Expected: FAIL because the current digest ignores document content.

- [ ] **Step 3: Write a regression test proving omitted versions preserve the old digest**

```python
def test_omitted_content_version_preserves_existing_digest(monkeypatch):
    before = _legacy_digest_for_naive_document(doc_id="doc-1", parser_id="naive")
    task = _queue_and_capture_task(monkeypatch, content_version=None)
    assert task["digest"] == before
```

Derive `before` in the test from the current pre-change literal fixture fields, not by calling the production digest helper.

- [ ] **Step 4: Add the optional version to the digest input**

```python
if content_version is not None:
    hasher.update(content_version.encode("utf-8"))
```

Place this after chunking configuration and before page-range fields. Thread the optional parameter through `DocumentService.run`; in `duplicate_and_parse`, pass `doc["content_hash"]` only for an `svn/` source and omit it for every other source.

- [ ] **Step 5: Verify SVN changed/unchanged cases and non-SVN compatibility**

Run: `uv run pytest test/unit_test/api/db/services/test_task_service_content_digest.py test/unit_test/rag/test_sync_data_source.py -v`

Expected: PASS; unchanged SVN hashes reuse chunks, changed SVN hashes queue fresh parsing, and omitted versions retain the legacy digest.

- [ ] **Step 6: Commit content-aware reparsing**

```bash
git add api/db/services/task_service.py api/db/services/document_service.py api/db/services/connector_service.py test/unit_test/api/db/services/test_task_service_content_digest.py
git commit -m "fix: reparse connector documents when content changes"
```

### Task 4: Persist two-snapshot deletion state and clean managed empty folders

**Files:**
- Modify: `api/db/db_models.py`
- Modify: `api/db/services/connector_service.py`
- Modify: `api/db/services/file_service.py`
- Create: `test/unit_test/api/db/services/test_connector_safe_prune.py`
- Modify: `test/unit_test/rag/test_sync_data_source.py`

**Interfaces:**
- Produces: `Connector2Kb.sync_state: JSONField` with `missing_counts`, `managed_folder_ids`, and `last_successful_revision`.
- Changes: `cleanup_stale_documents_for_task(task_id, connector_id, kb_id, tenant_id, file_list, delete_batch_size=100, snapshot_revision=None, confirmation_scans=1)` retains current first-snapshot deletion by default and increments misses only when SVN passes `confirmation_scans=2`.
- Produces: `FileService.remove_empty_managed_folders(folder_ids, kb_root_id, tenant_id) -> set[str]`.
- Produces: `Connector2KbService.add_managed_folder_ids(connector_id, kb_id, folder_ids)`; connector uploads call it only with IDs actually created by `FileService.upload_document`.

- [ ] **Step 1: Write failing tests for first miss, second miss, recovery, and failed snapshot**

```python
def test_first_complete_snapshot_keeps_missing_document(state_fixture):
    removed, errors = ConnectorService.cleanup_stale_documents_for_task(
        "task-1", "connector-1", "kb-1", "tenant-1", [], snapshot_revision="11", confirmation_scans=2
    )
    assert removed == 0
    assert state_fixture.missing_counts == {"doc-1": 1}

def test_second_complete_snapshot_deletes_missing_document(state_fixture):
    state_fixture.missing_counts = {"doc-1": 1}
    removed, errors = ConnectorService.cleanup_stale_documents_for_task(
        "task-2", "connector-1", "kb-1", "tenant-1", [], snapshot_revision="12", confirmation_scans=2
    )
    assert removed == 1

def test_reappearing_document_clears_missing_count(state_fixture):
    state_fixture.missing_counts = {"doc-1": 1}
    ConnectorService.cleanup_stale_documents_for_task(
        "task-2",
        "connector-1",
        "kb-1",
        "tenant-1",
        [SlimDocument(id="source-key")],
        snapshot_revision="12",
        confirmation_scans=2,
    )
    assert state_fixture.missing_counts == {}
```

The failed-snapshot test must call `_collect_prune_snapshot()` with one root raising and assert cleanup is never invoked.

Add a compatibility test calling the service without `confirmation_scans`; it must delete a stale non-SVN document on the first successful snapshot exactly as it does today.

- [ ] **Step 2: Run safe-prune tests and verify RED**

Run: `uv run pytest test/unit_test/api/db/services/test_connector_safe_prune.py test/unit_test/rag/test_sync_data_source.py -k 'prune or snapshot' -v`

Expected: FAIL because stale documents are currently deleted on the first successful prune.

- [ ] **Step 3: Add the JSON state column and idempotent migration**

Add `sync_state = JSONField(null=False, default={})` to `Connector2Kb` and an `alter_db_add_column` entry following the existing `auto_parse` migration style. Always copy the dict before mutation to avoid shared mutable defaults in application code.

- [ ] **Step 4: Implement two-snapshot state transitions**

Compute stable RAGFlow document IDs from the snapshot, clear recovered IDs, increment only current stale IDs, and delete only IDs whose previous count was at least one. Persist state only after the full snapshot and deletion attempt complete; keep count `2` for a failed deletion so it retries.

- [ ] **Step 5: Record newly created connector folders in the per-KB state**

In `duplicate_and_parse`, only when `src.startswith("svn/")`, create a local `managed_folder_ids` set, pass it to `FileService.upload_document`, and after a successful upload call `Connector2KbService.add_managed_folder_ids(docs[0]["connector_id"], kb.id, managed_folder_ids)`. The service must union IDs with existing state under a row lock; an empty set performs no write. Add tests showing that a reused folder is absent, a newly created child folder is persisted, and a non-SVN source never writes managed-folder state.

- [ ] **Step 6: Write and run failing managed-folder cleanup tests**

```python
def test_cleanup_removes_only_empty_managed_ancestors(file_tree):
    remaining = FileService.remove_empty_managed_folders(
        {file_tree.managed_leaf, file_tree.reused_parent},
        file_tree.kb_root,
        file_tree.tenant,
    )
    assert not FileService.get_by_id(file_tree.managed_leaf)[0]
    assert FileService.get_by_id(file_tree.reused_parent)[0]
    assert file_tree.reused_parent in remaining
```

Expected initial result: FAIL because the cleanup helper is missing.

- [ ] **Step 7: Implement bottom-up managed-folder cleanup**

Delete only folders recorded in `managed_folder_ids`, only when they have no children, and never delete the KB root. Remove successfully deleted IDs from sync state; preserve non-empty or missing IDs without touching untracked directories.

- [ ] **Step 8: Run prune and file-service tests**

Run: `uv run pytest test/unit_test/api/db/services/test_connector_safe_prune.py test/unit_test/api/db/services/test_file_service_upload_document.py test/unit_test/rag/test_sync_data_source.py -v`

Expected: PASS.

- [ ] **Step 9: Commit safe pruning**

```bash
git add api/db/db_models.py api/db/services/connector_service.py api/db/services/file_service.py test/unit_test/api/db/services/test_connector_safe_prune.py test/unit_test/rag/test_sync_data_source.py
git commit -m "feat: confirm connector deletions across two snapshots"
```

### Task 5: Register SVN in sync orchestration and connection testing

**Files:**
- Modify: `common/constants.py`
- Modify: `rag/svr/sync_data_source.py`
- Modify: `api/apps/restful_apis/connector_api.py`
- Modify: `test/unit_test/rag/test_sync_data_source.py`
- Modify: `test/testcases/restful_api/test_connector_routes_unit.py`

**Interfaces:**
- Produces: `FileSource.SVN = "svn"` and `func_factory[FileSource.SVN] = SVN`.
- Produces: `SVN(SyncBase)._generate(task)` using the shared fingerprint-filtered generator.
- Changes: `POST /connectors/<id>/test` supports SVN and returns repository UUID/revision/root validation without returning credentials.
- Produces: `POST /connectors/<id>/sync` immediately schedules SYNC and PRUNE for a linked KB without deleting existing documents.

- [ ] **Step 1: Write a failing factory and generation test**

```python
def _svn_config() -> dict:
    return {
        "repository_url": "https://svn.example.test/svn/company",
        "base_path": "00_公用文件/00_体系文件",
        "include_roots": ["1、一级文件", "2、二级文件", "3、三级文件", "4、四级文件"],
        "exclude_name_contains": ["旧版"],
        "credentials": {"username": "reader", "password": "secret"},
    }

def test_svn_is_registered_as_fingerprint_connector():
    assert sync_data_source.func_factory[FileSource.SVN] is sync_data_source.SVN

@pytest.mark.asyncio
async def test_svn_generate_uses_fingerprint_filter(monkeypatch):
    sync = sync_data_source.SVN(_svn_config())
    generator = await sync._generate(_make_task())
    assert [doc.id for batch in generator for doc in batch] == ["changed-key"]
```

- [ ] **Step 2: Run orchestration tests and verify RED**

Run: `uv run pytest test/unit_test/rag/test_sync_data_source.py -k svn -v`

Expected: FAIL because SVN is not registered.

- [ ] **Step 3: Implement the SVN sync adapter and factory registration**

Instantiate `SVNConnector`, load credentials, validate settings, and use `_fingerprint_filtered_generator(task)` for normal sync. For prune initialization, instantiate the same connector, require all four root listings to succeed before yielding the slim snapshot, and pass `confirmation_scans=2`. Every other connector continues to omit that argument and therefore retains the default value `1`.

- [ ] **Step 4: Write a failing API connection-test route case**

```python
def test_svn_test_route_returns_revision_without_password(client, connector):
    response = client.post(f"/connectors/{connector.id}/test")
    assert response.json["code"] == 0
    assert response.json["data"] == {"repository_uuid": "uuid-1", "revision": "72089", "roots": 4}
    assert "password" not in response.text
```

- [ ] **Step 5: Run the route test and verify RED**

Run: `uv run pytest test/testcases/restful_api/test_connector_routes_unit.py -k svn -v`

Expected: FAIL because the endpoint supports only REST API.

- [ ] **Step 6: Extend the endpoint with source-specific validation**

Dispatch REST API and SVN validation explicitly. Run blocking SVN validation via `asyncio.to_thread`; map expected credential/config errors to `RetCode.DATA_ERROR`, unexpected failures to a generic server message, and never include stderr containing supplied credentials.

- [ ] **Step 7: Run orchestration and route suites**

Run: `uv run pytest test/unit_test/rag/test_sync_data_source.py test/testcases/restful_api/test_connector_routes_unit.py -v`

Expected: PASS, including existing S3/WebDAV prune and sync cases without changed expectations.

- [ ] **Step 8: Write the failing non-destructive manual-sync route test**

```python
def test_manual_sync_schedules_without_rebuild_deletion(client, connector, monkeypatch):
    scheduled = []
    monkeypatch.setattr(SyncLogsService, "schedule", lambda *args, **kwargs: scheduled.append((args, kwargs)))
    monkeypatch.setattr(FileService, "delete_docs", lambda *_: pytest.fail("manual sync must not delete existing documents"))

    response = client.post(f"/connectors/{connector.id}/sync", json={"kb_id": "kb-1"})

    assert response.json["code"] == 0
    assert [call[1]["task_type"] for call in scheduled] == [ConnectorTaskType.SYNC, ConnectorTaskType.PRUNE]
```

- [ ] **Step 9: Implement the authorized linked-KB manual-sync endpoint**

Validate connector access and the `Connector2Kb` relationship, cancel only already scheduled/running tasks for that connector/KB if necessary to prevent duplicates, then schedule SYNC followed by PRUNE. Do not call `ConnectorService.rebuild` or `FileService.delete_docs`.

- [ ] **Step 10: Run route tests again**

Run: `uv run pytest test/testcases/restful_api/test_connector_routes_unit.py -k 'svn or manual_sync' -v`

Expected: PASS.

- [ ] **Step 11: Commit orchestration and testing support**

```bash
git add common/constants.py rag/svr/sync_data_source.py api/apps/restful_apis/connector_api.py test/unit_test/rag/test_sync_data_source.py test/testcases/restful_api/test_connector_routes_unit.py
git commit -m "feat: integrate SVN with connector scheduling"
```

### Task 6: Add the SVN data-source form and Chinese copy

**Files:**
- Create: `web/src/pages/user-setting/data-source/constant/svn-constant.tsx`
- Modify: `web/src/pages/user-setting/data-source/constant/index.tsx`
- Modify: `web/src/pages/user-setting/data-source/interface.ts`
- Modify: `web/src/locales/zh.ts`
- Modify: `web/src/locales/en.ts`
- Create: `web/src/pages/user-setting/data-source/__tests__/svn-data-source.test.tsx`

**Interfaces:**
- Produces: `DataSourceKey.SVN = "svn"`.
- Produces: form fields for repository URL, base path, four include roots, exclusion term, username, password, batch size, and delete sync.
- Produces: default `refresh_freq=60`, `prune_freq=60`, `sync_deleted_files=true`.
- Produces: a distinct “立即同步” action that invokes `/connectors/<id>/sync`; it must not invoke rebuild.

- [ ] **Step 1: Write a failing form contract test**

```tsx
it('builds a safe SVN connector configuration with hourly defaults', () => {
  const values = DataSourceFormDefaultValues[DataSourceKey.SVN];
  expect(values.refresh_freq).toBe(60);
  expect(values.prune_freq).toBe(60);
  expect(values.config.include_roots).toEqual([
    '1、一级文件', '2、二级文件', '3、三级文件', '4、四级文件',
  ]);
  expect(values.config.exclude_name_contains).toEqual(['旧版']);
  expect(field('config.credentials.password').type).toBe(FormFieldType.Password);
});
```

- [ ] **Step 2: Run the form test and verify RED**

Run: `cd web && npm test -- svn-data-source.test.tsx --runInBand`

Expected: FAIL because `DataSourceKey.SVN` and its fields are missing.

- [ ] **Step 3: Add SVN metadata, fields, defaults, and translations**

Use the existing dynamic-form pattern. Use a text/tag field for include roots, a tag field for exclusions, and a Password field for the password. Use a generic existing data-source icon if no SVN asset is available; do not add generated binary assets.

- [ ] **Step 4: Verify the UI contract and type checking**

Run: `cd web && npm test -- svn-data-source.test.tsx --runInBand && npm run lint -- --quiet`

Expected: PASS.

- [ ] **Step 5: Add and test the non-destructive “Sync now” action**

Add a detail-page action that calls `/connectors/<id>/sync` with the selected linked knowledge-base ID. Its test must spy on the HTTP service boundary and assert the sync URL is used while the rebuild URL is not used. Translate the label as `立即同步` / `Sync now` and keep the existing rebuild wording distinct.

- [ ] **Step 6: Commit the UI**

```bash
git add web/src/pages/user-setting/data-source/constant/svn-constant.tsx web/src/pages/user-setting/data-source/constant/index.tsx web/src/pages/user-setting/data-source/interface.ts web/src/locales/zh.ts web/src/locales/en.ts web/src/pages/user-setting/data-source/__tests__/svn-data-source.test.tsx
git commit -m "feat: add SVN data source configuration UI"
```

### Task 7: End-to-end regression and server pilot

**Files:**
- Create: `test/integration/test_svn_connector_pipeline.py`
- Modify: `docs/superpowers/specs/2026-08-28-svn-knowledge-sync-design.md` only if observed behavior requires a documented correction approved by the user.

**Interfaces:**
- Validates: SVN fixture server/CLI -> selection -> relative-path upload -> parse scheduling -> two-snapshot prune.
- Validates: the designated real read-only pilot knowledge base only after unit tests pass and credentials are supplied interactively outside logs.

- [ ] **Step 1: Write the failing integration scenario with a fake SVN executable**

The fake executable must return deterministic XML/cat bytes for revision 10, then revision 11 with one changed file, then two successful revisions missing the old path. Assert the knowledge folder tree, content hashes, parsing submissions, first-miss retention, and second-miss deletion through real service boundaries while mocking only storage/search/Redis infrastructure.

- [ ] **Step 2: Run the integration scenario and verify RED**

Run: `uv run pytest test/integration/test_svn_connector_pipeline.py -v`

Expected: FAIL at the first unimplemented cross-component contract discovered by the scenario.

- [ ] **Step 3: Make only the minimal integration corrections**

Correct contract mismatches in the owning modules without broad refactors. For each discovered bug, add or tighten the nearest unit test before changing production code.

- [ ] **Step 4: Run all focused backend and frontend suites**

```bash
uv run pytest \
  test/unit_test/data_source/test_svn_connector_unit.py \
  test/unit_test/rag/test_sync_data_source.py \
  test/unit_test/api/db/services/test_connector_relative_paths.py \
  test/unit_test/api/db/services/test_task_service_content_digest.py \
  test/unit_test/api/db/services/test_connector_safe_prune.py \
  test/testcases/restful_api/test_connector_routes_unit.py \
  test/integration/test_svn_connector_pipeline.py -v
cd web && npm test -- svn-data-source.test.tsx --runInBand
```

Expected: PASS with no warnings containing credentials. Also run the existing local upload/folder upload and blob/WebDAV connector regression tests; their assertions must remain unchanged.

- [ ] **Step 5: Run formatting and static checks on changed files**

```bash
uv run ruff check common/data_source/svn_connector.py rag/svr/sync_data_source.py api/db/services/connector_service.py api/db/services/file_service.py api/db/services/task_service.py
uv run ruff format --check common/data_source/svn_connector.py test/unit_test/data_source/test_svn_connector_unit.py
cd web && npm run lint -- --quiet
```

Expected: PASS.

- [ ] **Step 6: Pilot the connector using interactive credentials**

Create the SVN connector in RAGFlow with the approved hostname URL, link it to the “SVN 试点” knowledge base, set both frequencies to 60, and trigger “立即同步”, not “重建”. Enter the password only in the RAGFlow password field or a no-echo terminal prompt; do not place it in shell history. Verify the four roots and one known nested DOCX in the knowledge file browser, then confirm parse status reaches DONE.

- [ ] **Step 7: Verify incremental behavior without mutating SVN**

Run a second sync against the same revision and assert zero downloads/uploads. Use the fake integration scenario—not the production repository—to exercise changed/deleted cases because the SVN account is read-only and the task does not authorize repository mutations.

- [ ] **Step 8: Commit integration coverage**

```bash
git add test/integration/test_svn_connector_pipeline.py
git commit -m "test: cover SVN knowledge sync lifecycle"
```
