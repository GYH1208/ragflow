from datetime import UTC, datetime

import pytest

from api.db.db_models import API4Conversation, Conversation, Dialog
from api.db.services.chat_analytics_service import (
    ChatAnalyticsService,
    aggregate_question_rows,
    iter_user_questions,
    normalize_message_time,
)


def _utc(*args):
    return datetime(*args, tzinfo=UTC)


class _FakeQuery:
    def __init__(self, rows):
        self.rows = rows
        self.where_calls = []
        self.offset_value = 0
        self.limit_value = None

    def where(self, *conditions):
        self.where_calls.append(conditions)
        return self

    def order_by(self, *fields):
        return self

    def offset(self, value):
        self.offset_value = value
        return self

    def limit(self, value):
        self.limit_value = value
        return self

    def dicts(self):
        end = (
            self.offset_value + self.limit_value
            if self.limit_value is not None
            else None
        )
        return self.rows[self.offset_value : end]


def _patch_select(monkeypatch, model, rows):
    queries = []

    def select(*fields):
        query = _FakeQuery(rows)
        queries.append(query)
        return query

    monkeypatch.setattr(model, "select", select)
    return queries


def test_normalize_message_time_accepts_seconds_milliseconds_and_iso():
    fallback = datetime(2026, 9, 24, 12, tzinfo=UTC)
    expected = datetime(2026, 9, 1, 0, tzinfo=UTC)

    assert normalize_message_time(1788220800, fallback) == expected
    assert normalize_message_time(1788220800000, fallback) == expected
    assert normalize_message_time("2026-09-01T00:00:00Z", fallback) == expected


def test_iter_user_questions_keeps_legacy_questions_without_inventing_a_time():
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
        _utc(2026, 9, 1, 0),
        None,
    ]
    assert list(iter_user_questions({**row, "message": {}})) == []


def test_legacy_questions_only_contribute_to_the_lifetime_total():
    result = aggregate_question_rows(
        [
            {
                "dialog_id": "d1",
                "create_time": 1788220800000,
                "update_time": 1790208000000,
                "message": [
                    {"role": "user", "content": "legacy without time"},
                    {
                        "role": "user",
                        "content": "timestamped",
                        "created_at": "2026-09-24T08:00:00Z",
                    },
                ],
            }
        ],
        [{"id": "d1", "name": "Support"}],
        _utc(2026, 9, 24),
        _utc(2026, 9, 24, 23, 59, 59, 999999),
        "day",
        _utc(2026, 9, 24, 12),
    )

    assert result["total_count"] == 2
    assert result["last_30_days_count"] == 1
    assert result["today_count"] == 1
    assert result["trend"] == [{"date": "2026-09-24", "count": 1}]
    assert result["assistants"] == [{"id": "d1", "name": "Support", "count": 1}]


def test_aggregate_question_rows_builds_summary_trend_and_assistant_totals():
    assistants = [
        {"id": "d1", "name": "Support"},
        {"id": "d2", "name": "Sales"},
        {"id": "d3", "name": "Unused"},
    ]
    rows = [
        {
            "dialog_id": "d1",
            "create_time": 1788220800000,
            "update_time": 1788220800000,
            "message": [
                {"role": "user", "created_at": "2026-09-01T00:00:00Z"},
                {"role": "user", "created_at": "2026-09-03T23:59:59Z"},
            ],
        },
        {
            "dialog_id": "d2",
            "create_time": 1788307200000,
            "update_time": 1788307200000,
            "message": [
                {"role": "user", "created_at": "2026-09-02T12:00:00Z"}
            ],
        },
    ]

    result = aggregate_question_rows(
        rows,
        assistants,
        _utc(2026, 9, 1),
        _utc(2026, 9, 3, 23, 59, 59),
        "day",
        _utc(2026, 9, 3, 12),
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
        {"id": "d3", "name": "Unused", "count": 0},
    ]


def test_aggregate_week_and_month_buckets_cross_year_boundaries():
    assistants = [{"id": "d1", "name": "Support"}]
    rows = [
        {
            "dialog_id": "d1",
            "create_time": 0,
            "update_time": 0,
            "message": [
                {"role": "user", "created_at": "2025-12-31T10:00:00Z"},
                {"role": "user", "created_at": "2026-01-02T10:00:00Z"},
            ],
        }
    ]

    weekly = aggregate_question_rows(
        rows,
        assistants,
        _utc(2025, 12, 29),
        _utc(2026, 1, 11, 23, 59, 59),
        "week",
        _utc(2026, 1, 2),
    )
    assert weekly["trend"] == [
        {"date": "2026-W01", "count": 2},
        {"date": "2026-W02", "count": 0},
    ]

    monthly = aggregate_question_rows(
        rows,
        assistants,
        _utc(2025, 12, 1),
        _utc(2026, 1, 31, 23, 59, 59),
        "month",
        _utc(2026, 1, 2),
    )
    assert monthly["trend"] == [
        {"date": "2025-12", "count": 1},
        {"date": "2026-01", "count": 1},
    ]


def test_dashboard_merges_both_conversation_models(monkeypatch):
    dialog_queries = _patch_select(
        monkeypatch, Dialog, [{"id": "d1", "name": "Support"}]
    )
    _patch_select(
        monkeypatch,
        Conversation,
        [
            {
                "dialog_id": "d1",
                "message": [{"role": "user", "created_at": 1788220800}],
                "create_time": 1788220800000,
                "update_time": 1788220800000,
            }
        ],
    )
    _patch_select(
        monkeypatch,
        API4Conversation,
        [
            {
                "dialog_id": "d1",
                "message": [{"role": "user", "created_at": 1788307200}],
                "create_time": 1788307200000,
                "update_time": 1788307200000,
            }
        ],
    )

    result = ChatAnalyticsService.dashboard.__wrapped__(
        ChatAnalyticsService,
        "tenant-1",
        None,
        _utc(2026, 9, 1),
        _utc(2026, 9, 30, 23, 59, 59),
        "day",
        _utc(2026, 9, 24),
    )

    assert result["total_count"] == 2
    assert result["assistant_options"] == [{"id": "d1", "name": "Support"}]
    assert dialog_queries[0].where_calls


def test_dashboard_rejects_dialog_outside_tenant(monkeypatch):
    dialog_queries = _patch_select(monkeypatch, Dialog, [])
    conversation_queries = _patch_select(monkeypatch, Conversation, [])
    api_queries = _patch_select(monkeypatch, API4Conversation, [])

    with pytest.raises(ValueError, match="Chat assistant not found"):
        ChatAnalyticsService.dashboard.__wrapped__(
            ChatAnalyticsService,
            "tenant-1",
            "other-tenant-dialog",
            _utc(2026, 9, 1),
            _utc(2026, 9, 30, 23, 59, 59),
            "day",
        )

    assert dialog_queries[0].where_calls
    assert conversation_queries == []
    assert api_queries == []
