# MCP Directory Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Browse authorized knowledge bases and documents through two paginated MCP tools.
**Architecture:** Wrap existing REST list endpoints in the MCP connector, reuse per-request credentials, static schemas, and durable tool counters.
**Tech Stack:** Python, MCP, httpx, pytest.
**Spec:** docs/superpowers/specs/2026-11-02-mcp-directory-design.md

## Global Constraints
- Defaults page=1, page_size=20; page_size 1–100.
- No guessed directory paths, new dependencies, listing caches, or backend permission changes.
- Keep implementation isolated until verification; copy reviewed changes into the shared checkout for delivery, without merging or committing unrelated work.

## Review Focus
- Missing total must produce a tool error, not a misleading complete list.
- Empty and past-end pages preserve total and has_more semantics.
- Caller identity and revoked access must be checked per request.
- Malformed dataset IDs must not alter backend URL paths.
- Existing counters must survive the addition of two tools.

## Task 1: Directory tools
**Files:** mcp/server/server.py; test/unit_test/mcp/test_server_directory_tools.py; existing discovery, auth, and count tests; docs/develop/mcp/mcp_tools.md.
**Interfaces:** RAGFlowConnector.list_knowledge_bases(*, api_key, page=1, page_size=20, name=None); list_documents(*, api_key, dataset_id, page=1, page_size=20). Both return MCP TextContent containing compact JSON.
- [x] Add tests for real MCP dispatch and schemas, pagination, compact responses, explicit versus absent paths, malformed responses, identity isolation, revoked access, and counter migration.
- [x] Run tests and confirm failures reflect missing tools.
- [x] Implement wrappers, schemas, dispatch, descriptions and counter registration.
- [x] Update existing four-tool assertions and documentation.
- [x] Run MCP tests and relevant backend authorization tests; run Ruff and diff checks.
- [x] Review final diff independently, fix material findings, and deliver reviewed changes to shared checkout.

## Execution record
- User authorized direct implementation; no further design/plan confirmation requested.
- Baseline and implementation use the existing virtual environment; no dependency install required.

- Baseline: 122 MCP tests passed. New directory tests: 20 failed for missing tools, then all 142 MCP tests passed.
- Independent review found document creation-time ties could repeat/omit entries. Regression failed with create_time ordering; list_documents now requests unique ID ordering.
- Ruling: preserve ordinary API-key knowledge-base pagination behavior and document its existing timestamp-tie limitation. WorkBuddy already has the required ID tie-breaker; changing unrelated backend pagination would broaden this task.
- Backend authorization regression collection blocked by existing Polars CPU-feature warning (sse4.1/popcnt); reproduced with importing Polars in the original environment. No dependency or CPU-check overrides applied.
- Ruff baseline has 47 pre-existing findings in touched files; verify no newly introduced findings.

- Final verification: 142 MCP tests passed; formatting check passed; no new Ruff findings; independent review completed and document-ordering finding fixed.
- Runtime verification: restarted ragflow-workbuddy-mcp.service; local port 9396 tools/list returned HTTP 200 with all six tools. Existing counters 69/76/53/2 preserved, new counters initialized to zero. No real employee content request was made during the smoke check.
