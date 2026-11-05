# MCP Image Download Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Deliver authorized chunk images as directly downloadable files while retaining native MCP images.
**Architecture:** Add a bounded in-memory image capability store and HTTP route to the MCP app. Enrich `get_chunk_image`'s text metadata after successful backend validation. Follow the existing catalog delivery pattern.
**Tech Stack:** Python, Starlette, HTTPX, MCP, pytest.
**Spec:** `docs/superpowers/specs/2026-11-05-mcp-image-download-design.md`.

## Global Constraints

- TTL 15 minutes; at most 128 images / 64 MiB per process; no new dependencies.
- Preserve native image behavior and backend authorization; no credentials in links/cache.
- Preserve main and production; Windows Skill update is a documented handoff.

## Review Focus

- Proxy prefixes and SSE without a usable public endpoint must not produce broken links.
- Denied, oversized or unsupported backend responses must never issue capabilities.
- Full cache must preserve existing links and native image responses.
- Expired/shutdown links fail; capability lifetime after permission revocation is documented.
- Concurrent clients must receive their authorized bytes; access logs must not expose tokens.

### Task 1: Image delivery and lifecycle

**Files:** modify `mcp/server/server.py`; add `test/unit_test/mcp/test_server_image_download.py`.
**Interfaces:** `ImageDownloads.publish(data, mime_type, source, base_url) -> dict`; async `download(request) -> Response`; managed by application lifespan. Existing tool arguments unchanged.

- [x] Write protocol tests covering downloadable bytes, native blocks, auth, expiry, bounds, shutdown, prefixes and logs.
- [x] Run new tests; confirm failure because download metadata/route is absent.
- [x] Implement capability cache, route, lifecycle, redaction and metadata enrichment with graceful native fallback.
- [x] Run `PYTHONPATH=. /home/qaadmin/ragflow/.venv/bin/python -m pytest -q -W ignore::RuntimeWarning test/unit_test/mcp`; expect all passing.

### Task 2: Client handoff and final verification

**Files:** modify `docs/develop/mcp/mcp_tools.md`; add `docs/guides/workbuddy-image-download.md`.
**Interfaces:** document exact Task 1 metadata and download route.

- [x] Document WorkBuddy download/checksum/present flow, deployment routing and capability limits; replace stale cache advice.
- [x] Run focused backend image authorization tests plus MCP suite and Ruff/diff checks.
- [x] Obtain whole-branch review, address important findings, commit on feature branch.
- [x] Report implementation, validation and remaining deployment/client steps.

## Final validation

- Whole-branch review identified a proxy-prefix middleware mismatch; reproduced with
  `/prefix` and `/mcp-gateway`, then fixed using Starlette route-path normalization.
- Concurrent callers now use distinct image bytes and an asynchronous backend;
  tests verify each caller's native image, downloaded bytes, MIME type and SHA-256.
- MCP suite plus backend retrieval/image authorization tests: **196 passed**.
- New test file passes Ruff lint and formatting; server fatal checks and diff checks pass.
  Full server lint retains the 45 pre-existing findings.
- Deployment and WorkBuddy desktop acceptance remain pending; follow
  `docs/guides/workbuddy-image-download.md`. Main and production were preserved.
