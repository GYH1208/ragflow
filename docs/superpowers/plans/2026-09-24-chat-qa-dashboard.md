# Chat Q&A Dashboard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a tenant-scoped dashboard that shows total, last-30-day, and today Q&A counts, a day/week/month trend, and total counts by chat assistant across all chat sources.

**Architecture:** A new backend service reads tenant-owned `Dialog` rows and batches through both `Conversation` and `API4Conversation`, treating each valid `role == "user"` message as one Q&A. A protected REST endpoint returns already-aggregated data; a new React route consumes it through a focused service/hook and renders existing RAGFlow cards, controls, Recharts, and table primitives.

**Tech Stack:** Python 3.10+, Quart, Peewee, pytest, React 18, TypeScript, React Query, Recharts, Tailwind CSS, Jest/Testing Library.

**Spec:** `docs/superpowers/specs/2026-09-24-chat-qa-dashboard-design.md`

## Global Constraints

- Count only chat messages whose `role` is exactly `user`; exclude assistant prologues, assistant answers, system messages, tool messages, malformed items, and non-list message payloads.
- Merge `Conversation` and `API4Conversation` for `Dialog` records owned by the current tenant; do not expose source-specific totals or message text.
- Do not add a statistics/event table, scheduled aggregation job, export, answer-rate, accuracy-rate, token, latency, user-ranking, or question-detail feature.
- Use message `created_at` when valid; otherwise use conversation `update_time`, then `create_time`, for trend and date-filter placement.
- Add no new runtime dependency; reuse existing UI controls, Recharts, React Query, and i18n infrastructure.
- The route is `/chat-analytics`; the initial query defaults to all assistants and the latest 30 inclusive calendar days.
- The summary's lifetime count is not restricted by the selected date range; the trend and assistant table are restricted by it. Assistant selection applies to all summary cards, the trend, and the table.
- Add Simplified Chinese and English copy. Other locales may fall back to English under the existing i18n behavior.

## Review Focus

- `created_at` may be Unix seconds, Unix milliseconds, an ISO-8601 string, missing, or malformed; valid values are normalized and invalid values use the conversation fallback timestamp.
- Start and end dates are inclusive calendar-day boundaries; a question exactly at either boundary must be counted once.
- An unknown or other-tenant `dialog_id` must be rejected and must never return another tenant's count.
- Empty tenants, empty date buckets, malformed message arrays, and malformed message entries must return stable zero/empty results rather than failing the dashboard.
- A week or month spanning a year boundary must use stable ISO-week/month bucket labels, preserve chronological ordering, and fill missing buckets with zero.

---

### Task 1: Pure Q&A counting and time bucketing

**Files:**
- Create: `api/db/services/chat_analytics_service.py`
- Create: `test/unit_test/api/db/services/test_chat_analytics_service.py`

**Interfaces:**
- Consumes: conversation-like dictionaries with `dialog_id`, `message`, `create_time`, and `update_time` fields.
- Produces: `normalize_message_time(value: object, fallback: datetime) -> datetime`, `iter_user_questions(row: Mapping[str, Any]) -> Iterator[datetime]`, and `aggregate_question_rows(rows, assistants, from_date, to_date, granularity, now) -> dict`.

- [ ] **Step 1: Write failing timestamp and user-message tests**

```python
from datetime import datetime

from api.db.services.chat_analytics_service import (
    aggregate_question_rows,
    iter_user_questions,
    normalize_message_time,
)


def test_normalize_message_time_accepts_seconds_milliseconds_and_iso():
    fallback = datetime(2026, 9, 24, 12)
    expected = datetime(2026, 9, 1, 0)
    assert normalize_message_time(1788220800, fallback) == expected
    assert normalize_message_time(1788220800000, fallback) == expected
    assert normalize_message_time("2026-09-01T00:00:00Z", fallback) == expected


def test_iter_user_questions_counts_only_valid_user_messages_and_falls_back():
    fallback_ms = 1788264000000
    row = {
        "dialog_id": "d1",
        "create_time": fallback_ms - 1000,
        "update_time": fallback_ms,
        "message": [
            {"role": "assistant", "content": "hello"},
            {"role": "user", "content": "one", "created_at": 1788220800},
            {"role": "system", "content": "hidden"},
            {"role": "user", "content": "two", "created_at": "bad"},
            "broken",
        ],
    }
    assert list(iter_user_questions(row)) == [
        datetime(2026, 9, 1, 0),
        datetime(2026, 9, 1, 12),
    ]
    assert list(iter_user_questions({**row, "message": {}})) == []
```

- [ ] **Step 2: Run the focused tests and verify the import fails**

Run: `uv run pytest test/unit_test/api/db/services/test_chat_analytics_service.py -q`

Expected: FAIL with `ModuleNotFoundError: api.db.services.chat_analytics_service`.

- [ ] **Step 3: Implement timestamp normalization and safe question iteration**

```python
from __future__ import annotations

from collections.abc import Iterable, Iterator, Mapping
from datetime import datetime, timezone
from typing import Any


def _timestamp_ms_to_datetime(value: object) -> datetime | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number > 10_000_000_000:
        number /= 1000
    try:
        return datetime.fromtimestamp(number, tz=timezone.utc).replace(tzinfo=None)
    except (OverflowError, OSError, ValueError):
        return None


def normalize_message_time(value: object, fallback: datetime) -> datetime:
    numeric = _timestamp_ms_to_datetime(value)
    if numeric is not None:
        return numeric
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc).replace(tzinfo=None)
        except ValueError:
            pass
    return fallback


def _conversation_fallback(row: Mapping[str, Any]) -> datetime:
    for key in ("update_time", "create_time"):
        value = _timestamp_ms_to_datetime(row.get(key))
        if value is not None:
            return value
    return datetime.fromtimestamp(0, tz=timezone.utc).replace(tzinfo=None)


def iter_user_questions(row: Mapping[str, Any]) -> Iterator[datetime]:
    messages = row.get("message")
    if not isinstance(messages, list):
        return
    fallback = _conversation_fallback(row)
    for message in messages:
        if isinstance(message, Mapping) and message.get("role") == "user":
            yield normalize_message_time(message.get("created_at"), fallback)
```

- [ ] **Step 4: Add failing aggregate tests for inclusive ranges, zero-filled buckets, assistant selection, and year boundaries**

```python
def test_aggregate_question_rows_builds_summary_trend_and_assistant_totals():
    assistants = [{"id": "d1", "name": "Support"}, {"id": "d2", "name": "Sales"}]
    rows = [
        {"dialog_id": "d1", "create_time": 1788220800000, "update_time": 1788220800000,
         "message": [{"role": "user", "created_at": "2026-09-01T00:00:00Z"},
                     {"role": "user", "created_at": "2026-09-03T23:59:59Z"}]},
        {"dialog_id": "d2", "create_time": 1788307200000, "update_time": 1788307200000,
         "message": [{"role": "user", "created_at": "2026-09-02T12:00:00Z"}]},
    ]
    result = aggregate_question_rows(
        rows, assistants, datetime(2026, 9, 1), datetime(2026, 9, 3, 23, 59, 59),
        "day", datetime(2026, 9, 3, 12),
    )
    assert result["total_count"] == 3
    assert result["last_30_days_count"] == 3
    assert result["today_count"] == 1
    assert result["trend"] == [
        {"date": "2026-09-01", "count": 1},
        {"date": "2026-09-02", "count": 1},
        {"date": "2026-09-03", "count": 1},
    ]
    assert result["assistants"] == [
        {"id": "d1", "name": "Support", "count": 2},
        {"id": "d2", "name": "Sales", "count": 1},
    ]


def test_aggregate_week_and_month_buckets_cross_year_boundaries():
    assistants = [{"id": "d1", "name": "Support"}]
    rows = [{"dialog_id": "d1", "create_time": 0, "update_time": 0, "message": [
        {"role": "user", "created_at": "2025-12-31T10:00:00Z"},
        {"role": "user", "created_at": "2026-01-02T10:00:00Z"},
    ]}]
    weekly = aggregate_question_rows(rows, assistants, datetime(2025, 12, 29), datetime(2026, 1, 11, 23, 59, 59), "week", datetime(2026, 1, 2))
    assert weekly["trend"] == [{"date": "2026-W01", "count": 2}, {"date": "2026-W02", "count": 0}]
    monthly = aggregate_question_rows(rows, assistants, datetime(2025, 12, 1), datetime(2026, 1, 31, 23, 59, 59), "month", datetime(2026, 1, 2))
    assert monthly["trend"] == [{"date": "2025-12", "count": 1}, {"date": "2026-01", "count": 1}]
```

- [ ] **Step 5: Implement aggregation helpers and make all pure-service tests pass**

Implement `_bucket_key`, `_iter_bucket_keys`, and `aggregate_question_rows` so that it:

```python
{
    "total_count": int,
    "last_30_days_count": int,
    "today_count": int,
    "trend": list[dict[str, str | int]],
    "assistants": list[dict[str, str | int]],
}
```

uses inclusive boundaries, fills absent buckets with zero, includes zero-count assistants, sorts assistants by `(-count, name, id)`, and uses ISO labels `YYYY-MM-DD`, `YYYY-Www`, and `YYYY-MM`.

Run: `uv run pytest test/unit_test/api/db/services/test_chat_analytics_service.py -q`

Expected: PASS.

- [ ] **Step 6: Commit the pure aggregation unit**

```bash
git add api/db/services/chat_analytics_service.py test/unit_test/api/db/services/test_chat_analytics_service.py
git commit -m "feat: add chat question aggregation service"
```

### Task 2: Tenant-scoped database loading and REST endpoint

**Files:**
- Modify: `api/db/services/chat_analytics_service.py`
- Create: `api/apps/restful_apis/chat_analytics_api.py`
- Modify: `test/unit_test/api/db/services/test_chat_analytics_service.py`
- Create: `test/unit_test/api/apps/restful_apis/test_chat_analytics_api.py`

**Interfaces:**
- Consumes: Task 1's `aggregate_question_rows`; query params `dialog_id?: str`, `from_date: YYYY-MM-DD`, `to_date: YYYY-MM-DD`, `granularity: day|week|month`.
- Produces: `ChatAnalyticsService.dashboard(tenant_id: str, dialog_id: str | None, from_date: datetime, to_date: datetime, granularity: str, now: datetime | None = None) -> dict` and authenticated `GET /api/v1/chat-analytics`.

- [ ] **Step 1: Write failing database-service tests**

Use monkeypatched Peewee model queries to assert that `dashboard`:

```python
def test_dashboard_merges_both_conversation_models(monkeypatch):
    monkeypatch.setattr(Dialog, "select", fake_dialog_select([{"id": "d1", "name": "Support"}]))
    monkeypatch.setattr(Conversation, "select", fake_conversation_select([
        {"dialog_id": "d1", "message": [{"role": "user", "created_at": 1788220800}], "create_time": 1788220800000, "update_time": 1788220800000}
    ]))
    monkeypatch.setattr(API4Conversation, "select", fake_conversation_select([
        {"dialog_id": "d1", "message": [{"role": "user", "created_at": 1788307200}], "create_time": 1788307200000, "update_time": 1788307200000}
    ]))
    result = ChatAnalyticsService.dashboard("tenant-1", None, datetime(2026, 9, 1), datetime(2026, 9, 30, 23, 59, 59), "day", datetime(2026, 9, 24))
    assert result["total_count"] == 2


def test_dashboard_rejects_dialog_outside_tenant(monkeypatch):
    monkeypatch.setattr(Dialog, "select", fake_dialog_select([]))
    with pytest.raises(ValueError, match="Chat assistant not found"):
        ChatAnalyticsService.dashboard("tenant-1", "other-tenant-dialog", datetime(2026, 9, 1), datetime(2026, 9, 30, 23, 59, 59), "day")
```

The fake query helper must record `.where(...)` calls and return `.dicts()` data, so the tests also assert that tenant filtering occurs before conversation loading.

- [ ] **Step 2: Run database-service tests and verify failure**

Run: `uv run pytest test/unit_test/api/db/services/test_chat_analytics_service.py -q`

Expected: FAIL because `ChatAnalyticsService` does not exist.

- [ ] **Step 3: Implement batched tenant-scoped loading**

Add imports for `DB`, `Dialog`, `Conversation`, and `API4Conversation`, then implement:

```python
class ChatAnalyticsService:
    BATCH_SIZE = 500

    @classmethod
    @DB.connection_context()
    def dashboard(cls, tenant_id, dialog_id, from_date, to_date, granularity, now=None):
        # Select the full id/name option list from Dialog where tenant_id matches and status is valid.
        # Validate dialog_id against that list, then use either all assistants or the selected one
        # for row loading and aggregation.
        # Raise ValueError("Chat assistant not found") if a requested id is absent.
        # Read id/dialog_id/message/create_time/update_time from both conversation models
        # in BATCH_SIZE pages, concatenate the dictionaries, and call
        # aggregate_question_rows(...). Add assistant_options (the unfiltered option list,
        # sorted by name) to the returned dictionary.
```

Do not read or return message content beyond the in-process role/timestamp inspection.

- [ ] **Step 4: Write failing route tests for defaults, validation, and tenant isolation**

```python
@pytest.mark.asyncio
async def test_chat_analytics_route_uses_current_tenant_and_defaults(monkeypatch):
    monkeypatch.setattr(UserTenantService, "query", lambda **kwargs: [SimpleNamespace(tenant_id="tenant-1")])
    dashboard = Mock(return_value={"total_count": 0, "last_30_days_count": 0, "today_count": 0, "trend": [], "assistants": []})
    monkeypatch.setattr(ChatAnalyticsService, "dashboard", dashboard)
    response = await authenticated_client.get("/api/v1/chat-analytics")
    assert response.status_code == 200
    assert (await response.get_json())["data"]["total_count"] == 0
    assert dashboard.call_args.kwargs["tenant_id"] == "tenant-1"
    assert dashboard.call_args.kwargs["granularity"] == "day"


@pytest.mark.asyncio
@pytest.mark.parametrize("query", [
    "?granularity=hour",
    "?from_date=bad",
    "?from_date=2026-09-25&to_date=2026-09-24",
])
async def test_chat_analytics_route_rejects_invalid_params(authenticated_client, query):
    response = await authenticated_client.get(f"/api/v1/chat-analytics{query}")
    assert response.status_code == 200
    assert (await response.get_json())["code"] == RetCode.DATA_ERROR
```

Also test that `ValueError("Chat assistant not found")` becomes a 400 data error without returning dashboard data.

- [ ] **Step 5: Implement the protected route and parameter parser**

```python
@manager.route("/chat-analytics", methods=["GET"])
@login_required
async def chat_analytics():
    # Resolve the current user's first tenant through UserTenantService.
    # Parse YYYY-MM-DD dates; default from_date to today - 29 days and to_date to today.
    # Convert to inclusive 00:00:00 and 23:59:59 boundaries.
    # Accept only day/week/month and pass the optional dialog_id.
    # Return get_json_result(data=result); return get_data_error_result(...)
    # for invalid client parameters and server_error_response for unexpected errors.
```

- [ ] **Step 6: Run backend tests and lint the new Python files**

Run: `uv run pytest test/unit_test/api/db/services/test_chat_analytics_service.py test/unit_test/api/apps/restful_apis/test_chat_analytics_api.py -q`

Expected: PASS.

Run: `uv run ruff check api/db/services/chat_analytics_service.py api/apps/restful_apis/chat_analytics_api.py test/unit_test/api/db/services/test_chat_analytics_service.py test/unit_test/api/apps/restful_apis/test_chat_analytics_api.py`

Expected: PASS.

- [ ] **Step 7: Commit the endpoint**

```bash
git add api/db/services/chat_analytics_service.py api/apps/restful_apis/chat_analytics_api.py test/unit_test/api/db/services/test_chat_analytics_service.py test/unit_test/api/apps/restful_apis/test_chat_analytics_api.py
git commit -m "feat: expose tenant chat question analytics"
```

### Task 3: Frontend analytics client, types, and query hook

**Files:**
- Modify: `web/src/utils/api.ts`
- Create: `web/src/interfaces/chat-analytics.ts`
- Create: `web/src/services/chat-analytics-service.ts`
- Create: `web/src/hooks/use-chat-analytics.ts`
- Create: `web/src/hooks/__tests__/use-chat-analytics.test.tsx`

**Interfaces:**
- Consumes: `GET /api/v1/chat-analytics` from Task 2.
- Produces: `ChatAnalyticsGranularity`, `ChatAnalyticsResponse`, `ChatAnalyticsParams`, `chatAnalyticsService.getDashboard`, and `useChatAnalytics(params)` returning `{ data, loading, error, refetch }`.

- [ ] **Step 1: Write the failing hook test**

```tsx
jest.mock('@/services/chat-analytics-service', () => ({
  __esModule: true,
  default: { getDashboard: jest.fn() },
}));

it('keys and sends all dashboard filters', async () => {
  mockedService.getDashboard.mockResolvedValue({ data: { code: 0, data: fixture } });
  const params = { dialogId: 'd1', fromDate: '2026-09-01', toDate: '2026-09-24', granularity: 'week' as const };
  const { result } = renderHook(() => useChatAnalytics(params), { wrapper: createQueryWrapper() });
  await waitFor(() => expect(result.current.loading).toBe(false));
  expect(mockedService.getDashboard).toHaveBeenCalledWith({ params: {
    dialog_id: 'd1', from_date: '2026-09-01', to_date: '2026-09-24', granularity: 'week',
  }});
  expect(result.current.data).toEqual(fixture);
});
```

Add a second test asserting a rejected request exposes `error` and `refetch` without replacing filters.

- [ ] **Step 2: Run the hook test and verify missing-module failure**

Run: `cd web && npm test -- --runInBand src/hooks/__tests__/use-chat-analytics.test.tsx`

Expected: FAIL because the hook/service modules do not exist.

- [ ] **Step 3: Add the API constant, exact TypeScript contracts, service, and hook**

```ts
export type ChatAnalyticsGranularity = 'day' | 'week' | 'month';

export interface ChatAnalyticsParams {
  dialogId?: string;
  fromDate: string;
  toDate: string;
  granularity: ChatAnalyticsGranularity;
}

export interface ChatAnalyticsResponse {
  total_count: number;
  last_30_days_count: number;
  today_count: number;
  trend: Array<{ date: string; count: number }>;
  assistants: Array<{ id: string; name: string; count: number }>;
  assistant_options: Array<{ id: string; name: string }>;
}
```

Add `chatAnalytics: `${restAPIv1}/chat-analytics`` to `api.ts`. Register a GET method in `chat-analytics-service.ts`. In the hook, map camelCase UI params to snake_case query params, use the full params object in the React Query key, disable window-focus refetch, and provide a zero-valued initial response.

- [ ] **Step 4: Run the hook test and TypeScript check**

Run: `cd web && npm test -- --runInBand src/hooks/__tests__/use-chat-analytics.test.tsx`

Expected: PASS.

Run: `cd web && npm run type-check`

Expected: PASS.

- [ ] **Step 5: Commit the frontend data layer**

```bash
git add web/src/utils/api.ts web/src/interfaces/chat-analytics.ts web/src/services/chat-analytics-service.ts web/src/hooks/use-chat-analytics.ts web/src/hooks/__tests__/use-chat-analytics.test.tsx
git commit -m "feat: add chat analytics frontend client"
```

### Task 4: Dashboard route, navigation, translations, and page behavior

**Files:**
- Modify: `web/src/routes.tsx`
- Modify: `web/src/layouts/components/global-navbar.tsx`
- Modify: `web/src/locales/zh.ts`
- Modify: `web/src/locales/en.ts`
- Create: `web/src/pages/chat-analytics/index.tsx`
- Create: `web/src/pages/chat-analytics/summary-cards.tsx`
- Create: `web/src/pages/chat-analytics/trend-chart.tsx`
- Create: `web/src/pages/chat-analytics/assistant-table.tsx`
- Create: `web/src/pages/chat-analytics/__tests__/index.test.tsx`

**Interfaces:**
- Consumes: Task 3's `useChatAnalytics`, `ChatAnalyticsGranularity`, and `ChatAnalyticsResponse`; existing `DatePickerWithRange`, select/button/card/table primitives, and Recharts.
- Produces: lazy route `/chat-analytics`, global navigation item `header.chatAnalytics`, and a responsive dashboard whose filters update the query params supplied to the hook.

- [ ] **Step 1: Write failing page behavior tests**

Mock `useChatAnalytics` with a fixture and assert:

```tsx
it('renders summary, trend, and one total column per assistant', () => {
  renderDashboard();
  expect(screen.getByText('1,149')).toBeInTheDocument();
  expect(screen.getByText('546')).toBeInTheDocument();
  expect(screen.getByText('21')).toBeInTheDocument();
  expect(screen.getByRole('columnheader', { name: 'Chat assistant' })).toBeInTheDocument();
  expect(screen.getByRole('columnheader', { name: 'Total Q&As' })).toBeInTheDocument();
  expect(screen.queryByText(/source/i)).not.toBeInTheDocument();
});

it('changes granularity and preserves assistant/date filters', async () => {
  renderDashboard();
  await user.click(screen.getByRole('button', { name: 'By week' }));
  expect(mockUseChatAnalytics).toHaveBeenLastCalledWith(expect.objectContaining({
    dialogId: undefined,
    granularity: 'week',
  }));
});

it('renders zero and empty states without crashing', () => {
  mockUseChatAnalytics.mockReturnValue({ data: emptyFixture, loading: false, error: null, refetch: jest.fn() });
  renderDashboard();
  expect(screen.getAllByText('0')).toHaveLength(3);
  expect(screen.getByText('No data available')).toBeInTheDocument();
});
```

Add tests for the loading skeleton and request-error retry button.

- [ ] **Step 2: Run page tests and verify the page is missing**

Run: `cd web && npm test -- --runInBand src/pages/chat-analytics/__tests__/index.test.tsx`

Expected: FAIL because `@/pages/chat-analytics` does not exist.

- [ ] **Step 3: Add route, navigation matching, and translations**

Add `ChatAnalytics = '/chat-analytics'` to `Routes`, a lazy child route under `root-layout`, and this path to `PathMap`/`menuItems`. Add `header.chatAnalytics` plus a `chatAnalytics` locale namespace containing:

```ts
{
  title, lifetimeCount, last30DaysCount, todayCount, trendTitle,
  allAssistants, assistant, dateRange, byDay, byWeek, byMonth,
  assistantName, totalQuestions, loadError, retry
}
```

with natural Simplified Chinese and English values. Add a route/nav assertion to the page test or a small navbar test confirming `/chat-analytics` renders the active navigation state.

- [ ] **Step 4: Implement the dashboard page and focused presentation components**

`index.tsx` owns filter state and defaults:

```tsx
const today = startOfDay(new Date());
const [range, setRange] = useState<DateRange>({ from: subDays(today, 29), to: today });
const [dialogId, setDialogId] = useState<string>();
const [granularity, setGranularity] = useState<ChatAnalyticsGranularity>('day');
const query = useChatAnalytics({
  dialogId,
  fromDate: format(range.from, 'yyyy-MM-dd'),
  toDate: format(range.to, 'yyyy-MM-dd'),
  granularity,
});
```

Presentation rules:

- `summary-cards.tsx`: three responsive cards, locale number formatting, existing icons from the installed icon libraries, skeletons while loading.
- `trend-chart.tsx`: `ResponsiveContainer`, `CartesianGrid`, `XAxis`, `YAxis`, `Tooltip`, and one blue monotone `Line` with dot markers; day/week/month segmented buttons have accessible selected state.
- `assistant-table.tsx`: exactly two columns (`assistantName`, `totalQuestions`), descending count order from the server, existing `TableEmpty` behavior.
- `index.tsx`: assistant select, range picker, loading/error/empty states, and retry action. Do not add source filters or raw question rows.

- [ ] **Step 5: Run page tests, type-check, and lint touched frontend files**

Run: `cd web && npm test -- --runInBand src/pages/chat-analytics/__tests__/index.test.tsx src/hooks/__tests__/use-chat-analytics.test.tsx`

Expected: PASS.

Run: `cd web && npm run type-check`

Expected: PASS.

Run: `cd web && npx eslint src/pages/chat-analytics src/hooks/use-chat-analytics.ts src/services/chat-analytics-service.ts src/interfaces/chat-analytics.ts src/routes.tsx src/layouts/components/global-navbar.tsx`

Expected: PASS.

- [ ] **Step 6: Commit the dashboard UI**

```bash
git add web/src/routes.tsx web/src/layouts/components/global-navbar.tsx web/src/locales/zh.ts web/src/locales/en.ts web/src/pages/chat-analytics web/src/hooks/use-chat-analytics.ts web/src/services/chat-analytics-service.ts web/src/interfaces/chat-analytics.ts
git commit -m "feat: add chat question analytics dashboard"
```

### Task 5: End-to-end verification and visual QA

**Files:**
- Create: `design-qa.md`
- Modify if defects are found: files from Tasks 1-4 only

**Interfaces:**
- Consumes: the completed endpoint and dashboard.
- Produces: passing backend/frontend verification and `design-qa.md` with `final result: passed` or an explicit blocked result if browser comparison cannot run.

- [ ] **Step 1: Run the complete focused backend suite**

Run: `uv run pytest test/unit_test/api/db/services/test_chat_analytics_service.py test/unit_test/api/apps/restful_apis/test_chat_analytics_api.py -q`

Expected: PASS.

- [ ] **Step 2: Run the complete focused frontend suite and static checks**

Run: `cd web && npm test -- --runInBand src/hooks/__tests__/use-chat-analytics.test.tsx src/pages/chat-analytics/__tests__/index.test.tsx`

Expected: PASS.

Run: `cd web && npm run type-check && npm run lint`

Expected: PASS.

- [ ] **Step 3: Start the application and inspect the real route**

Start required backend dependencies and the backend using the repository's documented commands, then run the frontend with `cd web && npm run dev -- --host 0.0.0.0 --port 4173 --strictPort`. Open `/chat-analytics` in the available browser at the same desktop viewport as the supplied reference image.

Verify all primary interactions: assistant selection, date-range change, day/week/month switching, tooltip display, retry behavior (using request blocking if supported), empty state, and responsive card/filter wrapping. Check the browser console for errors.

- [ ] **Step 4: Create and iterate the visual QA report**

Compare the supplied reference screenshot and the rendered dashboard at the same viewport. Write `design-qa.md` with sections for reference, captured viewport, hierarchy, spacing, typography, cards, chart, filters, table, responsive behavior, console status, and severity-ranked findings.

Fix all P0/P1/P2 findings, rerun the relevant tests, capture again, and update the report until it ends with:

```markdown
final result: passed
```

If a browser, authenticated local session, backend dependency, or reference capture is unavailable, document the exact blocker and end with `final result: blocked`; do not claim visual completion.

- [ ] **Step 5: Run final diff and regression checks**

Run: `git diff --check`

Expected: no output.

Run: `git status --short`

Expected: only intentional dashboard/QA files are modified.

- [ ] **Step 6: Commit verification fixes and QA evidence**

```bash
git add design-qa.md api/db/services/chat_analytics_service.py api/apps/restful_apis/chat_analytics_api.py test/unit_test/api/db/services/test_chat_analytics_service.py test/unit_test/api/apps/restful_apis/test_chat_analytics_api.py web/src
git commit -m "test: verify chat question analytics dashboard"
```
