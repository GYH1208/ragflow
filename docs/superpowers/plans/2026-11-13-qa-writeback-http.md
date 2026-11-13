# HTTP Q&A Writeback Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox syntax for tracking.

**Goal:** Create the approved synchronous Q&A writeback HTTP endpoint with keyword extraction and recoverable idempotency.

**Architecture:** Separate REST route, application service and SQL idempotency store. Persist prepared vectors and atomically finalize counters, reusing existing model/tokenizer/index APIs.

**Tech Stack:** Quart, Python, Peewee, existing RAGFlow model and document-store abstractions.

**Spec:** docs/superpowers/specs/2026-11-13-qa-writeback-http-design.md

## Global Constraints

- No MCP tool or registration, no production writes or deployment.
- One Q&A per request; configured chat/embedding providers; keyword extraction defaults to five and degrades on failure.
- Preserve Q&A content; keyword/manual precedence; stable idempotency and accurate counters.

## Review Focus

- Concurrent duplicate requests and crash/timeout after an ambiguous index insert.
- SQL failure after the index accepted a chunk, including lost completion responses.
- Explicit keywords, malformed input, newline preservation, empty/invalid model output.
- Authentication, inaccessible documents, busy/deleted/non-QA targets and removed chunks.
- Production route discovery without any MCP exposure.

## Tasks

- [x] Add failing service tests for validation, extraction, embedding, idempotency, partial failure, cancellation and contention; implement `api/apps/services/qa_writeback_service.py`.
- [x] Add SQL-store tests using isolated SQLite, then implement `QAWriteback` in `api/db/db_models.py` and `QAWritebackStore` in `api/db/services/qa_writeback_service.py`.
- [x] Add HTTP tests using Quart and the existing authentication guard; implement `api/apps/restful_apis/qa_api.py`, including route/status/payload checks.
- [x] Document the runnable contract in `docs/references/http_qa_writeback.md`; run focused tests and Ruff, review changes and report limits.

## Execution ledger

- Clean starting checkout on main; created feature branch `feat/qa-writeback-http` in the shared checkout.
- Existing Markdown parser unit baseline: 10 passed.
- Existing chunk route unit baseline is already broken: its Quart stub omits `g`; parent test conftest additionally requires unrelated provider credentials. Keep new tests in test/unit_test with isolated dependencies.
- Ruling: use an ordinary feature branch in the existing clean checkout; no additional worktree or production-side effects are needed.
- Ruling: expose a separate qa_api module to avoid expanding the existing large chunk route module or its already-broken unit fixture.
- Service, SQL and HTTP tests were observed failing before their implementations; runtime test caught missing tenant-default chat fallback, then passed after correction.
- Independent review caught cancellation/lease races, unconfirmed ES writes and target changes during preparation. Added failing regressions, moved publication under ledger/document/KB row locks, shielded outstanding writes and required read-back confirmation. Reviewer confirmed no outstanding findings.
- Ruling: keep SQL target locks during external index publication so parse startup/takeover cannot interleave. This can delay concurrent mutations of that document during an index outage; read-only requests remain unaffected.
- No production database/table changes, provider calls, service restart, MCP changes, merge or push were performed.
- Final verification: 71 new tests passed, including application service + real SQLite ledger recovery, real Quart HTTP requests and configured-provider adapter boundaries. All seven new Python files pass Ruff lint and format checks. Compilation and git diff whitespace checks pass. db_models.py retains its 97 pre-existing lint findings with no new findings.
- Review follow-up confirmed dataset validation/locking is before publication; no outstanding review findings. Feature remains uncommitted on `feat/qa-writeback-http` for review; production model/ES/MySQL end-to-end execution was not performed.

## User-requested verification follow-up (2026-11-13)

- Re-ran the original 71 tests, then added 11 HTTP-to-service-to-SQLite integration cases in `test/unit_test/api/apps/restful_apis/test_qa_writeback_flow.py`. Providers and the vector index remain isolated test doubles.
- The integration cases verify preserved Q&A, question vectors, generated/manual/degraded keywords, duplicate/conflicting/concurrent requests, embedding failure, lost index responses, SQL rollback/recovery, removed chunks and rejected credentials. Fixed the new fixture's generator cleanup so SQLite connections close normally.
- Verified the running API on port 9380 with an existing enabled API Key: knowledge-base listing returns HTTP 200/code 0; the new Q&A route returns HTTP 404. The production database does not yet contain `qa_writeback`.
- Live checks used an intentionally invalid empty Q&A body, preventing content writes even if the route were loaded during the check. No deployment, service restart, production schema/content changes or real provider calls were performed. API authentication may update existing usage audit counters.
- Current verification command: `.venv/bin/python -m pytest test/unit_test/api/apps/services/test_qa_writeback_service.py test/unit_test/api/apps/services/test_qa_writeback_runtime.py test/unit_test/api/db/services/test_qa_writeback_store.py test/unit_test/api/apps/restful_apis/test_qa_writeback_http.py test/unit_test/api/apps/restful_apis/test_qa_writeback_flow.py -q`.
