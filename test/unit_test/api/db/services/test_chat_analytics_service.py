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
        datetime(2026, 9, 1),
        datetime(2026, 9, 3, 23, 59, 59),
        "day",
        datetime(2026, 9, 3, 12),
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
        datetime(2025, 12, 29),
        datetime(2026, 1, 11, 23, 59, 59),
        "week",
        datetime(2026, 1, 2),
    )
    assert weekly["trend"] == [
        {"date": "2026-W01", "count": 2},
        {"date": "2026-W02", "count": 0},
    ]

    monthly = aggregate_question_rows(
        rows,
        assistants,
        datetime(2025, 12, 1),
        datetime(2026, 1, 31, 23, 59, 59),
        "month",
        datetime(2026, 1, 2),
    )
    assert monthly["trend"] == [
        {"date": "2025-12", "count": 1},
        {"date": "2026-01", "count": 1},
    ]
