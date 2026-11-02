# MCP Catalog Export Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Let WorkBuddy request and download complete Excel catalogs without filling model context.
**Architecture:** One start/poll MCP tool, bounded in-memory background jobs, existing REST authorization, and short-lived file-scoped HTTP download capabilities.
**Tech Stack:** Python, MCP, Starlette, httpx, openpyxl (existing dependency), pytest.
**Spec:** docs/superpowers/specs/2026-11-02-mcp-catalog-export-design.md

## Global Constraints
- Defaults and bounds: 15-minute file lifetime, 50,000 documents, 120-second job timeout, two active jobs, 16 retained jobs, 64 MiB total and 10 MiB per file.
- No backend permission changes, employee tickets in output/storage, full catalog in tool context, or guessed paths.
- Preserve earlier uncommitted directory-tool work; use the existing isolated worktree, deliver reviewed changes, and restart only the MCP service.

## Review Focus
- Worker ownership/lifecycle when stateless request connectors close.
- Export/poll authorization, bearer URL scope/expiry, and secret-safe access logs.
- Concurrent library mutations, duplicates, early empty pages, and explicit partial-export errors.
- Spreadsheet formula injection and Chinese filenames/content.
- Slow exports, bounded memory, clean shutdown, and no loss of previous counters.

## Task 1: Export worker and workbook
- [x] Write failing tests for full XLSX contents and large paginated catalogs, status/paths, literal strings, totals, access failures, and capacity/expiry.
- [x] Implement bounded job storage, owned worker connector, workbook builder and safe status responses in mcp/server/server.py.

## Task 2: MCP/HTTP integration and delivery
- [x] Write failing protocol tests for start/poll/download, identity isolation, required parameters, download expiry, and discovery/count migration.
- [x] Add the tool schema/dispatch, lifespan cleanup, download route and token-log redaction. Update browsing descriptions and docs with WorkBuddy start/poll/download workflow.
- [x] Run complete MCP tests, format/lint and independent review; address material findings.
- [x] Deliver verified changes, restart service, verify live discovery and counters. Report desktop validation limits explicitly.

## Execution record
- User explicitly requested direct implementation; approved Excel catalog scope comes from preceding conversation.

- Initial protocol/file tests: 161 MCP tests passed, including complete 5,000-row XLSX and real HTTP MCP SDK start/poll/download (181,445-byte workbook using isolated synthetic metadata).
- Independent review reproduced workbook threads outliving asyncio timeout and releasing active slots. Two regression tests failed for timeout-during-build and shutdown-during-build. Fix: cooperative stop signal plus shielded join retains the active slot until the builder exits, including during repeated cancellation.
- Runtime dependency declaration: openpyxl was previously only explicitly in the test group; promoted it to project dependencies and refreshed uv.lock offline (two lockfile lines).

- Final verification: 163 MCP tests passed; fresh real HTTP SDK export/download verified 5,000 rows; formatting, diff checks and offline lock consistency passed; no new Ruff findings. Independent review lifecycle issue fixed with both previously failing regressions green.
- WorkBuddy desktop UI, external proxies/TLS and multi-worker routing were not exercised; current service is single process. Those deployment limits and download capability semantics are documented.

- Deployment verified on 2026-11-02 06:39 UTC: existing WorkBuddy MCP service restarted and active; live discovery returns all seven tools including export_document_catalog; unknown download tokens return 404 and unauthenticated export returns 401. Existing tool counters were preserved and the new export counter initialized to zero.
