#
#  Copyright 2026 The InfiniFlow Authors. All Rights Reserved.
#
#  Licensed under the Apache License, Version 2.0 (the "License");
#  you may not use this file except in compliance with the License.
#  You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
#  Unless required by applicable law or agreed to in writing, software
#  distributed under the License is distributed on an "AS IS" BASIS,
#  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#  See the License for the specific language governing permissions and
#  limitations under the License.
#
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import time

from quart import g, has_request_context
from werkzeug.exceptions import Forbidden, ServiceUnavailable, TooManyRequests

from api.db import APIKeyType
from api.db.db_models import APIToken, ScopedAPIToken
from rag.utils.redis_conn import REDIS_CONN


KNOWLEDGE_RETRIEVE_SCOPE = "knowledge:retrieve"
SCOPED_API_KEY_PREFIX = "ragflow-rk-"
RETRIEVAL_QUOTA_CAPACITY = 60
RETRIEVAL_QUOTA_REFILL_RATE = 1
RETRIEVAL_QUOTA_COST = 1


@dataclass(frozen=True)
class APIKeyContext:
    key_type: APIKeyType
    tenant_id: str
    token: str = field(repr=False)
    record: APIToken | ScopedAPIToken = field(repr=False)
    allowed_dataset_ids: frozenset[str] | None


def _first_record(model, token: str):
    records = model.query(token=token)
    return records[0] if records else None


def _is_enabled(record) -> bool:
    return bool(getattr(record, "enabled", True))


def _is_expired(record, now: datetime) -> bool:
    expires_at = getattr(record, "expires_at", None)
    if expires_at is None:
        return False
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    else:
        expires_at = expires_at.astimezone(timezone.utc)
    return expires_at <= now


def _validated_allowlist(record) -> frozenset[str] | None:
    values = getattr(record, "allowed_dataset_ids", None)
    if not isinstance(values, list):
        return None
    if any(not isinstance(value, str) or not value.strip() for value in values):
        return None
    return frozenset(values)


def resolve_api_key(token: str, now: datetime | None = None) -> APIKeyContext | None:
    if not isinstance(token, str) or not token:
        return None

    if token.startswith(SCOPED_API_KEY_PREFIX):
        scoped_record = _first_record(ScopedAPIToken, token)
        if scoped_record is not None:
            resolved_now = now or datetime.now(timezone.utc)
            if resolved_now.tzinfo is None:
                resolved_now = resolved_now.replace(tzinfo=timezone.utc)
            else:
                resolved_now = resolved_now.astimezone(timezone.utc)
            allowlist = _validated_allowlist(scoped_record)
            if not _is_enabled(scoped_record) or _is_expired(scoped_record, resolved_now) or allowlist is None:
                return None
            return APIKeyContext(
                key_type=APIKeyType.RETRIEVAL,
                tenant_id=scoped_record.tenant_id,
                token=token,
                record=scoped_record,
                allowed_dataset_ids=allowlist,
            )

    full_record = _first_record(APIToken, token)
    if full_record is None or not _is_enabled(full_record):
        return None
    return APIKeyContext(
        key_type=APIKeyType.FULL_ACCESS,
        tenant_id=full_record.tenant_id,
        token=token,
        record=full_record,
        allowed_dataset_ids=None,
    )


def _current_context() -> APIKeyContext | None:
    if not has_request_context():
        return None
    return getattr(g, "api_key_context", None)


def consume_retrieval_quota(context: APIKeyContext, now: float | None = None) -> int:
    if context.key_type is not APIKeyType.RETRIEVAL:
        return RETRIEVAL_QUOTA_CAPACITY

    token_hash = hashlib.sha256(context.token.encode()).hexdigest()
    key = f"api-key:retrieval-rate:{token_hash}"
    try:
        result = REDIS_CONN.lua_token_bucket(
            keys=[key],
            args=[
                RETRIEVAL_QUOTA_CAPACITY,
                RETRIEVAL_QUOTA_REFILL_RATE,
                time.time() if now is None else now,
                RETRIEVAL_QUOTA_COST,
            ],
            client=REDIS_CONN.REDIS,
        )
    except Exception:
        raise ServiceUnavailable("Retrieval quota service is unavailable.") from None

    remaining = max(0, int(float(result[1])))
    if int(result[0]) != 1:
        raise TooManyRequests("Retrieval API rate limit exceeded.", retry_after=1)
    return remaining


def mark_retrieval_started() -> None:
    if has_request_context():
        g.retrieval_started = True


def allowed_dataset_ids() -> frozenset[str] | None:
    context = _current_context()
    if context is None or context.key_type is APIKeyType.FULL_ACCESS:
        return None
    return context.allowed_dataset_ids


def require_dataset_access(dataset_ids: Iterable[str]) -> list[str]:
    requested_ids = list(dict.fromkeys(dataset_ids))
    allowlist = allowed_dataset_ids()
    if allowlist is not None and any(dataset_id not in allowlist for dataset_id in requested_ids):
        raise Forbidden("API key does not allow access to the requested dataset.")
    return requested_ids
