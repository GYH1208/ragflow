# MCP Call Analytics Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add the selected cumulative MCP dashboard to the existing chat analytics page.
**Architecture:** Read the persisted 9396 counters in a small independent Python service. Expose a login-protected, superuser-only aggregate through the existing analytics blueprint; query separately from chat data and render a second tab using existing UI components.
**Tech Stack:** Python/Quart, React/TypeScript, React Query, Recharts, pytest, Jest.
**Spec:** docs/superpowers/specs/2026-11-02-mcp-call-analytics-design.md

## Global Constraints
- Preserve real counters; read only, no historical reconstruction or merging with Q&A totals.
- Ordinary users must not receive process-wide counts; failed reads must not appear as zero.
- Use the existing frontend and backend; no dependencies or database migrations.
- Validate in isolation, then deliver reviewed files to the running checkout.

## Review Focus
- Unknown tools and classification totals reconcile.
- Missing, corrupt, boolean and negative counters produce unavailable status.
- Non-superusers cannot trigger file reads or receive counts.
- MCP errors and refreshes do not break original chat filters.
- UI displays cumulative scope, persisted time and all values on narrow screens.

### Task 1: Read-only MCP statistics API
**Files:** api/db/services/mcp_analytics_service.py; api/apps/restful_apis/chat_analytics_api.py; test/unit_test/api/db/services/test_mcp_analytics_service.py; test/unit_test/api/apps/restful_apis/test_chat_analytics_api.py.
**Interfaces:** MCPAnalyticsService.dashboard() -> dict with available, total_count, categories, tools, updated_at; GET /api/v1/mcp-analytics adds authorized. Reads only a server-configured path with the 9396 file as default.
- [x] Write and run failing tests for aggregation, all-zero, unknown tool and unavailable data; test superuser/ordinary-user API behavior.
- [x] Implement aggregation, strict nonnegative integer validation and unavailable results.
- [x] Run new and existing analytics backend tests and Ruff.

### Task 2: Existing-page MCP tab
**Files:** web/src/interfaces/chat-analytics.ts; web/src/services/chat-analytics-service.ts; web/src/hooks/use-chat-analytics.ts; web/src/utils/api.ts; web/src/pages/chat-analytics/index.tsx; web/src/pages/chat-analytics/mcp-dashboard.tsx; associated hook/page tests.
**Interfaces:** useMcpAnalytics() returns separate data/loading/error/refetch state; McpAnalyticsResponse carries API fields. Poll every 30 seconds; no date parameters.
- [x] Write and run failing tests for the MCP tab, cumulative values, refresh and unavailable state.
- [x] Add typed service/hook and accessible dashboard tab, summary cards and sorted per-tool bars with values.
- [x] Run analytics Jest tests, targeted lint/format and frontend build/type checks.

### Task 3: Review and runtime delivery
- [x] Review the full diff, permission checks and reconciliation.
- [x] Copy only task files into shared checkout; restart the existing backend service to register the new route. Existing Vite serves frontend source.
- [x] Verify unauthenticated access is rejected, superuser path returns real values and rendered dashboard works at the requested URL; confirm counters unchanged.
- [x] Record verification and any baseline limitations.


## Verification record
- Backend: 26 analytics tests passed; Ruff checks and formatting passed.
- Frontend: 16 analytics/navigation tests passed; targeted ESLint passed; production Vite build passed.
- Full TypeScript check reports 196 existing errors; normalized output matches unchanged main exactly. No errors in changed analytics files.
- Independent review found inherited object-property names could crash unknown-tool rendering. Added __proto__ regression, observed RED, fixed own-property lookup, and reran the analytics suite GREEN.
- Original counters are read only; genuine service traffic increased the live source total from the earlier chat snapshot.

- Runtime delivery complete: existing 9222 page renders the MCP tab using the new authenticated API; global totals reconcile to the persisted source. Desktop, 390px mobile, light and dark captures checked; refresh and tab switch work; no browser page errors.
- Verified authenticated /api/v1/chat-analytics and /api/v1/users/me both return HTTP 200 with code 0; all three API endpoints reject anonymous requests with HTTP 401.
- Deployment incident: backend restart required 30s termination timeout and 86.6s initialization; Vite proxied requests failed while the backend was unavailable. Backend is now listening on 9380 and normal traffic restored. No second restart performed.
