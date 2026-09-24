from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Iterator, Mapping
from datetime import UTC, datetime, timedelta
from typing import Any

from api.db.db_models import DB, API4Conversation, Conversation, Dialog
from common.constants import StatusEnum


def _timestamp_to_datetime(value: object) -> datetime | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None

    if number > 10_000_000_000:
        number /= 1000

    try:
        return datetime.fromtimestamp(number, tz=UTC)
    except (OverflowError, OSError, ValueError):
        return None


def normalize_message_time(value: object, fallback: datetime) -> datetime:
    numeric = _timestamp_to_datetime(value)
    if numeric is not None:
        return numeric

    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value)
            if parsed.tzinfo is None:
                return parsed.replace(tzinfo=UTC)
            return parsed.astimezone(UTC)
        except ValueError:
            pass

    return fallback


def _conversation_fallback(row: Mapping[str, Any]) -> datetime:
    for key in ("update_time", "create_time"):
        value = _timestamp_to_datetime(row.get(key))
        if value is not None:
            return value
    return datetime.fromtimestamp(0, tz=UTC)


def iter_user_questions(row: Mapping[str, Any]) -> Iterator[datetime]:
    messages = row.get("message")
    if not isinstance(messages, list):
        return

    fallback = _conversation_fallback(row)
    for message in messages:
        if isinstance(message, Mapping) and message.get("role") == "user":
            yield normalize_message_time(message.get("created_at"), fallback)


def _bucket_key(value: datetime, granularity: str) -> str:
    if granularity == "day":
        return value.strftime("%Y-%m-%d")
    if granularity == "week":
        year, week, _ = value.isocalendar()
        return f"{year}-W{week:02d}"
    if granularity == "month":
        return value.strftime("%Y-%m")
    raise ValueError(f"Unsupported granularity: {granularity}")


def _bucket_start(value: datetime, granularity: str) -> datetime:
    day = value.replace(hour=0, minute=0, second=0, microsecond=0)
    if granularity == "day":
        return day
    if granularity == "week":
        return day - timedelta(days=day.weekday())
    if granularity == "month":
        return day.replace(day=1)
    raise ValueError(f"Unsupported granularity: {granularity}")


def _next_bucket(value: datetime, granularity: str) -> datetime:
    if granularity == "day":
        return value + timedelta(days=1)
    if granularity == "week":
        return value + timedelta(weeks=1)
    if granularity == "month":
        if value.month == 12:
            return value.replace(year=value.year + 1, month=1)
        return value.replace(month=value.month + 1)
    raise ValueError(f"Unsupported granularity: {granularity}")


def _iter_bucket_keys(
    from_date: datetime, to_date: datetime, granularity: str
) -> Iterator[str]:
    cursor = _bucket_start(from_date, granularity)
    last = _bucket_start(to_date, granularity)
    while cursor <= last:
        yield _bucket_key(cursor, granularity)
        cursor = _next_bucket(cursor, granularity)


def aggregate_question_rows(
    rows: Iterable[Mapping[str, Any]],
    assistants: Iterable[Mapping[str, str]],
    from_date: datetime,
    to_date: datetime,
    granularity: str,
    now: datetime,
) -> dict[str, Any]:
    assistant_list = list(assistants)
    assistant_ids = {assistant["id"] for assistant in assistant_list}
    assistant_counts = Counter({assistant_id: 0 for assistant_id in assistant_ids})
    bucket_counts: Counter[str] = Counter()
    total_count = 0
    last_30_days_count = 0
    today_count = 0
    today = now.date()
    last_30_days_start = today - timedelta(days=29)

    for row in rows:
        dialog_id = row.get("dialog_id")
        if dialog_id not in assistant_ids:
            continue
        for question_time in iter_user_questions(row):
            total_count += 1
            question_day = question_time.date()
            if last_30_days_start <= question_day <= today:
                last_30_days_count += 1
            if question_day == today:
                today_count += 1
            if from_date <= question_time <= to_date:
                assistant_counts[dialog_id] += 1
                bucket_counts[_bucket_key(question_time, granularity)] += 1

    assistant_results = [
        {
            "id": assistant["id"],
            "name": assistant["name"],
            "count": assistant_counts[assistant["id"]],
        }
        for assistant in assistant_list
    ]
    assistant_results.sort(key=lambda item: (-item["count"], item["name"], item["id"]))

    return {
        "total_count": total_count,
        "last_30_days_count": last_30_days_count,
        "today_count": today_count,
        "trend": [
            {"date": key, "count": bucket_counts[key]}
            for key in _iter_bucket_keys(from_date, to_date, granularity)
        ],
        "assistants": assistant_results,
    }


class ChatAnalyticsService:
    BATCH_SIZE = 500

    @classmethod
    def _load_assistants(cls, tenant_id: str) -> list[dict[str, str]]:
        query = (
            Dialog.select(Dialog.id, Dialog.name)
            .where(
                Dialog.tenant_id == tenant_id,
                Dialog.status == StatusEnum.VALID.value,
            )
            .order_by(Dialog.name.asc(), Dialog.id.asc())
        )
        return list(query.dicts())

    @classmethod
    def _load_conversation_rows(
        cls, model, dialog_ids: list[str]
    ) -> list[dict[str, Any]]:
        if not dialog_ids:
            return []

        query = model.select(
            model.dialog_id,
            model.message,
            model.create_time,
            model.update_time,
        ).where(model.dialog_id.in_(dialog_ids)).order_by(model.id.asc())
        rows = []
        offset = 0
        while True:
            batch = list(
                query.offset(offset).limit(cls.BATCH_SIZE).dicts()
            )
            rows.extend(batch)
            if len(batch) < cls.BATCH_SIZE:
                break
            offset += cls.BATCH_SIZE
        return rows

    @classmethod
    @DB.connection_context()
    def dashboard(
        cls,
        tenant_id: str,
        dialog_id: str | None,
        from_date: datetime,
        to_date: datetime,
        granularity: str,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        assistant_options = cls._load_assistants(tenant_id)
        assistant_by_id = {
            assistant["id"]: assistant for assistant in assistant_options
        }
        if dialog_id and dialog_id not in assistant_by_id:
            raise ValueError("Chat assistant not found")

        selected_assistants = (
            [assistant_by_id[dialog_id]] if dialog_id else assistant_options
        )
        selected_ids = [assistant["id"] for assistant in selected_assistants]
        rows = cls._load_conversation_rows(Conversation, selected_ids)
        rows.extend(
            cls._load_conversation_rows(API4Conversation, selected_ids)
        )
        result = aggregate_question_rows(
            rows,
            selected_assistants,
            from_date,
            to_date,
            granularity,
            now or datetime.now(UTC),
        )
        result["assistant_options"] = assistant_options
        return result
