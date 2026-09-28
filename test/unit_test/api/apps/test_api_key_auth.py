import asyncio
from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from peewee import SqliteDatabase
from quart import Quart, g
from werkzeug.exceptions import Forbidden, ServiceUnavailable, TooManyRequests

from api.db import APIKeyType
from api.db.db_models import APIToken, ScopedAPIToken


@pytest.fixture
def token_database():
    database = SqliteDatabase(":memory:")
    with database.bind_ctx([APIToken, ScopedAPIToken]), database.connection_context():
        database.create_tables([APIToken, ScopedAPIToken])
        yield database


def test_full_access_resolution_returns_immutable_context_with_unrestricted_datasets(token_database):
    from api.apps.api_key_auth import APIKeyContext, resolve_api_key

    APIToken.create(tenant_id="tenant-full", token="ragflow-full-1")

    context = resolve_api_key("ragflow-full-1")

    assert isinstance(context, APIKeyContext)
    assert context.key_type is APIKeyType.FULL_ACCESS
    assert context.tenant_id == "tenant-full"
    assert context.token == "ragflow-full-1"
    assert context.record.token == "ragflow-full-1"
    assert context.allowed_dataset_ids is None
    with pytest.raises(FrozenInstanceError):
        context.tenant_id = "different-tenant"


def test_api_key_context_representation_never_exposes_plaintext_token(token_database):
    from api.apps.api_key_auth import resolve_api_key

    plaintext_token = "ragflow-full-sensitive-credential"
    APIToken.create(tenant_id="tenant-full", token=plaintext_token)

    context = resolve_api_key(plaintext_token)

    assert plaintext_token not in repr(context)


def test_full_access_resolution_rejects_disabled_record(token_database):
    from api.apps.api_key_auth import resolve_api_key

    APIToken.create(tenant_id="tenant-full", token="ragflow-full-disabled", enabled=False)

    assert resolve_api_key("ragflow-full-disabled") is None


def test_retrieval_resolution_returns_exact_deduplicated_allowlist(token_database):
    from api.apps.api_key_auth import resolve_api_key

    now = datetime(2035, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
    ScopedAPIToken.create(
        tenant_id="tenant-scoped",
        token="ragflow-rk-valid",
        name="WorkBuddy",
        allowed_dataset_ids=["dataset-1", "dataset-2", "dataset-1"],
        expires_at=now + timedelta(seconds=1),
    )

    context = resolve_api_key("ragflow-rk-valid", now=now)

    assert context.key_type is APIKeyType.RETRIEVAL
    assert context.tenant_id == "tenant-scoped"
    assert context.record.token == "ragflow-rk-valid"
    assert context.allowed_dataset_ids == frozenset({"dataset-1", "dataset-2"})


@pytest.mark.parametrize(
    ("enabled", "expires_at"),
    [
        (False, None),
        (True, datetime(2035, 1, 2, 3, 4, 5, tzinfo=timezone.utc)),
    ],
)
def test_retrieval_resolution_rejects_disabled_or_expired_record(token_database, enabled, expires_at):
    from api.apps.api_key_auth import resolve_api_key

    now = datetime(2035, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
    ScopedAPIToken.create(
        tenant_id="tenant-scoped",
        token="ragflow-rk-inactive",
        name="Inactive WorkBuddy",
        allowed_dataset_ids=["dataset-1"],
        enabled=enabled,
        expires_at=expires_at,
    )

    assert resolve_api_key("ragflow-rk-inactive", now=now) is None


@pytest.mark.parametrize(
    "malformed_allowlist",
    [
        "dataset-1",
        {"dataset-1": True},
        ["dataset-1", 7],
        ["dataset-1", ""],
    ],
)
def test_retrieval_resolution_fails_closed_for_malformed_allowlist(monkeypatch, malformed_allowlist):
    from api.apps import api_key_auth

    record = SimpleNamespace(
        tenant_id="tenant-scoped",
        token="ragflow-rk-malformed",
        enabled=True,
        expires_at=None,
        allowed_dataset_ids=malformed_allowlist,
    )
    monkeypatch.setattr(api_key_auth.ScopedAPIToken, "query", lambda **_kwargs: [record])

    assert api_key_auth.resolve_api_key("ragflow-rk-malformed") is None


def test_scoped_prefix_without_scoped_row_falls_back_to_historical_full_access_record(token_database):
    from api.apps.api_key_auth import resolve_api_key

    APIToken.create(tenant_id="tenant-legacy", token="ragflow-rk-prefix-collision")

    context = resolve_api_key("ragflow-rk-prefix-collision")

    assert context.key_type is APIKeyType.FULL_ACCESS
    assert context.tenant_id == "tenant-legacy"


def test_real_invalid_scoped_row_never_falls_through_to_broader_full_access_record(token_database):
    from api.apps.api_key_auth import resolve_api_key

    token = "ragflow-rk-shared-value"
    ScopedAPIToken.create(
        tenant_id="tenant-scoped",
        token=token,
        name="Disabled WorkBuddy",
        allowed_dataset_ids=["dataset-1"],
        enabled=False,
    )
    APIToken.create(tenant_id="tenant-full", token=token, enabled=True)

    assert resolve_api_key(token) is None


def test_dataset_helpers_preserve_first_seen_order_and_deny_any_out_of_scope_id():
    from api.apps.api_key_auth import APIKeyContext, allowed_dataset_ids, require_dataset_access

    app = Quart(__name__)
    context = APIKeyContext(
        key_type=APIKeyType.RETRIEVAL,
        tenant_id="tenant-scoped",
        token="ragflow-rk-valid",
        record=SimpleNamespace(token="ragflow-rk-valid"),
        allowed_dataset_ids=frozenset({"dataset-1", "dataset-2"}),
    )

    async def exercise_policy():
        async with app.test_request_context("/"):
            g.api_key_context = context
            assert allowed_dataset_ids() == frozenset({"dataset-1", "dataset-2"})
            assert require_dataset_access(["dataset-2", "dataset-1", "dataset-2"]) == [
                "dataset-2",
                "dataset-1",
            ]
            with pytest.raises(Forbidden, match="requested dataset"):
                require_dataset_access(["dataset-1", "dataset-outside-scope"])

        async with app.test_request_context("/"):
            assert allowed_dataset_ids() is None
            assert require_dataset_access(["dataset-2", "dataset-2", "dataset-1"]) == [
                "dataset-2",
                "dataset-1",
            ]

    asyncio.run(exercise_policy())


def _api_key_context(key_type, token):
    from api.apps.api_key_auth import APIKeyContext

    return APIKeyContext(
        key_type=key_type,
        tenant_id="tenant-quota",
        token=token,
        record=SimpleNamespace(token=token),
        allowed_dataset_ids=frozenset({"dataset-1"}) if key_type is APIKeyType.RETRIEVAL else None,
    )


def test_retrieval_quota_uses_exact_bucket_policy_and_sha256_namespaced_key(monkeypatch):
    from api.apps import api_key_auth

    plaintext_token = "ragflow-rk-sensitive-quota-token"
    calls = []

    def token_bucket(**kwargs):
        calls.append(kwargs)
        return [1, 59]

    monkeypatch.setattr(api_key_auth.REDIS_CONN, "lua_token_bucket", token_bucket)

    remaining = api_key_auth.consume_retrieval_quota(
        _api_key_context(APIKeyType.RETRIEVAL, plaintext_token),
        now=1234.5,
    )

    assert remaining == 59
    assert calls == [
        {
            "keys": ["api-key:retrieval-rate:f92c4c8837afce28d3212d3c85669778d6046fed6d9a8bf04d9e400ff46de4ab"],
            "args": [60, 1, 1234.5, 1],
            "client": api_key_auth.REDIS_CONN.REDIS,
        }
    ]
    assert plaintext_token not in calls[0]["keys"][0]


def test_retrieval_quota_isolates_distinct_api_keys(monkeypatch):
    from api.apps import api_key_auth

    bucket_keys = []

    def token_bucket(**kwargs):
        bucket_keys.append(kwargs["keys"][0])
        return [1, 58]

    monkeypatch.setattr(api_key_auth.REDIS_CONN, "lua_token_bucket", token_bucket)

    api_key_auth.consume_retrieval_quota(_api_key_context(APIKeyType.RETRIEVAL, "ragflow-rk-alpha"), now=10.0)
    api_key_auth.consume_retrieval_quota(_api_key_context(APIKeyType.RETRIEVAL, "ragflow-rk-beta"), now=10.0)

    assert bucket_keys == [
        "api-key:retrieval-rate:fbafb5dfa69501616573be0ffd2bea062219850e572cec88e640d399a43cfc5b",
        "api-key:retrieval-rate:7bc8dc4711f7ea3ff40f36b48dd72fe826961cbe6c42e724aee73ad305e0b06f",
    ]


def test_sixty_first_immediate_retrieval_returns_429_without_start_marker(monkeypatch):
    from api.apps import api_key_auth

    attempts = 0

    def token_bucket(**_kwargs):
        nonlocal attempts
        attempts += 1
        return [1, 60 - attempts] if attempts <= 60 else [0, 0]

    monkeypatch.setattr(api_key_auth.REDIS_CONN, "lua_token_bucket", token_bucket)
    app = Quart(__name__)
    context = _api_key_context(APIKeyType.RETRIEVAL, "ragflow-rk-sixty-one")

    async def exercise_quota():
        async with app.test_request_context("/"):
            for expected_remaining in range(59, -1, -1):
                assert api_key_auth.consume_retrieval_quota(context) == expected_remaining

            with pytest.raises(TooManyRequests) as raised:
                api_key_auth.consume_retrieval_quota(context)

            assert raised.value.code == 429
            assert raised.value.retry_after == 1
            assert getattr(g, "retrieval_started", False) is False

    asyncio.run(exercise_quota())


def test_retrieval_quota_redis_failure_maps_to_503_without_exposing_token(monkeypatch):
    from api.apps import api_key_auth

    plaintext_token = "ragflow-rk-never-log-this"

    def unavailable_bucket(**_kwargs):
        raise ConnectionError(f"redis failed for {plaintext_token}")

    monkeypatch.setattr(api_key_auth.REDIS_CONN, "lua_token_bucket", unavailable_bucket)

    with pytest.raises(ServiceUnavailable) as raised:
        api_key_auth.consume_retrieval_quota(
            _api_key_context(APIKeyType.RETRIEVAL, plaintext_token),
            now=10.0,
        )

    assert raised.value.code == 503
    assert plaintext_token not in repr(raised.value)


def test_full_access_key_skips_retrieval_quota(monkeypatch):
    from api.apps import api_key_auth

    def unexpected_bucket(**_kwargs):
        raise AssertionError("full-access API keys must not consume retrieval quota")

    monkeypatch.setattr(api_key_auth.REDIS_CONN, "lua_token_bucket", unexpected_bucket)

    assert api_key_auth.consume_retrieval_quota(
        _api_key_context(APIKeyType.FULL_ACCESS, "ragflow-full-unlimited"),
        now=10.0,
    ) == 60


def test_knowledge_retrieve_scope_is_permission_only_and_does_not_auto_consume_quota(monkeypatch):
    from api.apps import api_key_auth
    from api.apps import KNOWLEDGE_RETRIEVE_SCOPE, login_required

    bucket_calls = []

    def token_bucket(**kwargs):
        bucket_calls.append(kwargs)
        return [1, 59]

    monkeypatch.setattr(api_key_auth.REDIS_CONN, "lua_token_bucket", token_bucket)

    app = Quart(__name__)

    @login_required(api_scope=KNOWLEDGE_RETRIEVE_SCOPE)
    async def dataset_metadata():
        return "metadata"

    async def exercise_metadata_scope():
        async with app.test_request_context("/"):
            g.user = SimpleNamespace(id="tenant-retrieval")
            g.api_key_context = _api_key_context(APIKeyType.RETRIEVAL, "ragflow-rk-metadata-only")
            assert await dataset_metadata() == "metadata"
            assert getattr(g, "retrieval_started", False) is False

        async with app.test_request_context("/"):
            g.user = SimpleNamespace(id="tenant-jwt")
            assert await dataset_metadata() == "metadata"
            assert getattr(g, "retrieval_started", False) is False

    asyncio.run(exercise_metadata_scope())
    assert bucket_calls == []
