# Full-key Dataset Scope Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox syntax for tracking.

**Goal:** Configure and enforce dataset ranges for full-access API keys, including uploads and Q&A writes.

**Architecture:** Add an allowlist and migration to the existing full-key model; apply a fail-closed dataset policy from the common authentication guard; expose scope selection and display in the current key UI.

**Tech Stack:** Quart, Peewee, Pydantic, React/TypeScript, pytest, Jest.

**Spec:** docs/superpowers/specs/2026-11-13-full-key-dataset-scope.md

## Global Constraints

- Empty full-key scope means all account-accessible datasets; nonempty means selected datasets. Retrieval scope cannot be empty.
- Retain ownership/team checks and legacy behavior for unrestricted keys and JWT users.
- Initial implementation excluded production writes/restarts and Git publication. Follow-up user authorization: “Ok，提交，部署” permits local commit and deployment; push/merge are outside this request.
- Work in the clean shared checkout on feature branch `feat/full-key-dataset-scope`, following the established session workflow.

## Review Focus

- Selected scopes must apply to multipart uploads and Q&A before any write/model call.
- Mixed authorized/unauthorized bulk operations must reject the whole request before side effects.
- Unhandled/global/management routes and Beta aliases must not broaden a scoped full key.
- Missing/malformed persisted scope, startup migration reruns and empty retrieval scope must fail safely.
- Editing or changing key types must preserve explicit selections and distinguish selected from all-accessible scope.

### Task 1: Model, validation and key management

Files: `api/db/db_models.py`, `api/utils/validation_utils.py`, `api/apps/restful_apis/system_api.py`; model/auth/management tests.

- [x] Add failing tests for full-key scope persistence, migration, validation, serialization and ownership checks.
- [x] Add `APIToken.allowed_dataset_ids`, migration and management create/update support.
- [x] Resolve immutable full-key allowlists and keep unrestricted/retrieval compatibility; run focused tests.

### Task 2: Common dataset authorization

Files: `api/apps/api_key_auth.py`, `api/apps/__init__.py`, new `api/apps/full_key_scope.py`; scoped route tests.

- [x] Add failing HTTP tests for upload/Q&A/path/bulk/alias access and denied global/management/Beta routes.
- [x] Implement the dataset policy, integrate the common guard and scope helper, and validate all targeted IDs before handlers.
- [x] Run policy and Q&A regressions; attempt existing retrieval/auth checks and record environment limits.

### Task 3: Key settings UI and documentation

Files: `web/src/components/api-service/chat-api-key-modal/{api-key-form,index}.tsx`, their tests, `web/src/locales/{en,zh}.ts`, tracked Q&A and local HTTP reference docs.

- [x] Add failing tests for full-key scope selection, empty selection, edit preservation, payloads and list display.
- [x] Implement all/selected controls and labels; retain retrieval expiry behavior.
- [x] Update docs, run relevant Jest tests and changed-file checks, inspect the final diff and report verification limits.

## Verification and review

- Backend focused regression command: `.venv/bin/python -m pytest test/unit_test/api/apps/test_full_key_scope.py test/unit_test/api/apps/test_full_key_resource_boundaries.py test/unit_test/api/apps/services/test_delete_datasets.py test/unit_test/api/apps/services/test_qa_writeback_service.py test/unit_test/api/apps/services/test_qa_writeback_runtime.py test/unit_test/api/db/services/test_qa_writeback_store.py test/unit_test/api/apps/restful_apis/test_qa_writeback_http.py test/unit_test/api/apps/restful_apis/test_qa_writeback_flow.py -q` — **134 passed**. Includes real Quart authorization, SQLite model/migration/management persistence and storage-boundary regressions; external providers and production storage are isolated.
- Frontend: API key form, modal and API-key utility Jest suites — **3 suites, 32 passed**. Changed TS/TSX/locales pass Prettier; changed modal/form TSX and tests pass ESLint.
- New policy and isolated tests pass Ruff. Existing backend files were compared against HEAD; no new lint findings remain. Python compilation and final diff whitespace checks pass.
- The preexisting full-app auth/model/retrieval test collection is blocked by the installed Polars CPU-feature warning (`sse4.1`, `popcnt`) under warnings-as-errors. No packages or warning policies were changed to bypass it; the focused suite avoids heavy imports with trusted source definitions.
- Read-only review found four isolation issues in existing operations: ES/OS single-ID chunk updates, folder deletion by name, parent expansion by colliding content IDs and partially executed mixed-document batches. All were reproduced with regression tests and fixed. Follow-up review: all four resolved, no new Critical/Important findings; reviewer independently reran 50 policy/resource and 2 dataset-deletion tests.
- Full-key empty scopes preserve existing privileges. Existing keys are not automatically restricted; an administrator must select their intended datasets after deployment. Restricted keys cannot widen their own scope or use global/management/Beta routes.
- Updated the tracked Q&A reference and the ignored local `docs/references/http_knowledge_read_api.md`; verified that the combined guide remains ignored. Production services, schema and keys were untouched. Changes remain uncommitted on `feat/full-key-dataset-scope`.

## Deployment authorization (2026-11-13)

The user explicitly requested committing and deploying this implementation. Reverification before commit: 134 backend and 32 frontend tests passed. Deployment preserves the existing Vite 9222 hosting and user-systemd API service, adds the full-key scope column using startup migration, initializes the previously merged Q&A table, and verifies live authorized/denied API behavior using a temporary key that is removed afterwards. Existing user key selections are not changed. Backups and live evidence belong in the private `/home/qaadmin/backups/full-key-scope-*` directory rather than Git. No push or merge is included.
