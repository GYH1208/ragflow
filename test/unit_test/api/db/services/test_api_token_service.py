import asyncio
from datetime import datetime, timezone
import logging
from types import SimpleNamespace

import pytest
from peewee import OperationalError, SqliteDatabase
from quart import Quart, g, jsonify
from werkzeug.exceptions import ServiceUnavailable, TooManyRequests

from api.db import APIKeyLastResult, APIKeyType
from api.db.db_models import APIToken, ScopedAPIToken
from api.db.services import api_service


class TrackingSqliteDatabase(SqliteDatabase):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.statements = []

    def execute_sql(self, sql, params=None, commit=None):
        self.statements.append(sql)
        return super().execute_sql(sql, params, commit)


class FailingUpdateSqliteDatabase(SqliteDatabase):
    fail_updates = False

    def execute_sql(self, sql, params=None, commit=None):
        if self.fail_updates and sql.lstrip().upper().startswith("UPDATE"):
            raise OperationalError("telemetry write failed")
        return super().execute_sql(sql, params, commit)


class SensitiveFailingUpdateSqliteDatabase(SqliteDatabase):
    fail_updates = False
    sensitive_token = "ragflow-full-sensitive-telemetry-token"

    def execute_sql(self, sql, params=None, commit=None):
        if self.fail_updates and sql.lstrip().upper().startswith("UPDATE"):
            raise OperationalError(f"telemetry write failed for {self.sensitive_token}")
        return super().execute_sql(sql, params, commit)


@pytest.fixture(params=[APIKeyType.FULL_ACCESS, APIKeyType.RETRIEVAL])
def token_case(request):
    if request.param is APIKeyType.FULL_ACCESS:
        return request.param, APIToken, {"tenant_id": "tenant-1", "token": "ragflow-full-1"}
    return request.param, ScopedAPIToken, {
        "tenant_id": "tenant-1",
        "token": "ragflow-rk-1",
        "name": "WorkBuddy",
        "allowed_dataset_ids": ["dataset-1"],
    }


def test_usage_record_atomically_increments_and_replaces_latest_status_for_each_key_type(token_case):
    key_type, model, create_values = token_case
    database = TrackingSqliteDatabase(":memory:")
    first_used_at = datetime(2031, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
    second_used_at = datetime(2031, 1, 2, 3, 5, 6, tzinfo=timezone.utc)

    with database.bind_ctx([APIToken, ScopedAPIToken]), database.connection_context():
        database.create_tables([APIToken, ScopedAPIToken])
        model.create(**create_values)
        database.statements.clear()

        result = api_service.APIKeyUsageService.record(
            token=create_values["token"],
            key_type=key_type,
            result=APIKeyLastResult.SUCCESS,
            retrieval_started=True,
            used_at=first_used_at,
        )
        update_statements = [sql for sql in database.statements if sql.lstrip().upper().startswith("UPDATE")]
        non_update_statements = [sql for sql in database.statements if not sql.lstrip().upper().startswith("UPDATE")]

        assert result is None
        assert len(update_statements) == 1
        assert non_update_statements == []
        assert '"total_calls" + ?' in update_statements[0]
        assert '"retrieval_calls" + ?' in update_statements[0]

        api_service.APIKeyUsageService.record(
            token=create_values["token"],
            key_type=key_type,
            result=APIKeyLastResult.DENIED,
            retrieval_started=False,
            used_at=second_used_at,
        )
        stored = model.get_by_id(("tenant-1", create_values["token"]))

    assert stored.total_calls == 2
    assert stored.retrieval_calls == 1
    assert stored.last_used_at == second_used_at
    assert stored.last_result == "denied"


def test_usage_record_for_missing_token_is_a_no_op(token_case):
    key_type, model, create_values = token_case
    database = SqliteDatabase(":memory:")

    with database.bind_ctx([APIToken, ScopedAPIToken]), database.connection_context():
        database.create_tables([APIToken, ScopedAPIToken])
        result = api_service.APIKeyUsageService.record(
            token="missing-token",
            key_type=key_type,
            result=APIKeyLastResult.ERROR,
            retrieval_started=True,
            used_at=datetime(2032, 2, 3, 4, 5, 6, tzinfo=timezone.utc),
        )

        assert result is None
        assert model.select().count() == 0
        other_model = ScopedAPIToken if model is APIToken else APIToken
        assert other_model.select().count() == 0


def test_usage_record_propagates_low_level_update_failures():
    database = FailingUpdateSqliteDatabase(":memory:")

    with database.bind_ctx([APIToken, ScopedAPIToken]), database.connection_context():
        database.create_tables([APIToken, ScopedAPIToken])
        APIToken.create(tenant_id="tenant-1", token="ragflow-full-1")
        database.fail_updates = True

        with pytest.raises(OperationalError, match="telemetry write failed"):
            api_service.APIKeyUsageService.record(
                token="ragflow-full-1",
                key_type=APIKeyType.FULL_ACCESS,
                result=APIKeyLastResult.ERROR,
                retrieval_started=False,
                used_at=datetime(2033, 3, 4, 5, 6, 7, tzinfo=timezone.utc),
            )


def test_scoped_api_token_service_targets_only_the_retrieval_key_table():
    assert api_service.ScopedAPITokenService.model is ScopedAPIToken


@pytest.mark.parametrize(
    ("status", "body", "expected_result"),
    [
        pytest.param(200, {"code": 0, "data": []}, "success", id="2xx-success"),
        pytest.param(403, {"code": 403}, "denied", id="403-denied"),
        pytest.param(429, {"code": 429}, "rate_limited", id="429-rate-limited"),
        pytest.param(503, {"code": 500}, "error", id="5xx-error"),
        pytest.param(200, {"code": 101}, "error", id="200-error-envelope"),
    ],
)
def test_request_usage_classifies_real_http_response_and_nonzero_success_envelope(status, body, expected_result):
    from api.apps import _record_api_key_usage
    from api.apps.api_key_auth import APIKeyContext

    database = SqliteDatabase(":memory:")
    token = f"ragflow-full-classify-{status}-{body['code']}"
    app = Quart(__name__)

    with database.bind_ctx([APIToken, ScopedAPIToken]), database.connection_context():
        database.create_tables([APIToken, ScopedAPIToken])
        record = APIToken.create(tenant_id="tenant-usage", token=token)
        context = APIKeyContext(
            key_type=APIKeyType.FULL_ACCESS,
            tenant_id="tenant-usage",
            token=token,
            record=record,
            allowed_dataset_ids=None,
        )

        async def exercise_hook():
            async with app.test_request_context(
                "/retrieval",
                method="POST",
                data=b"{malformed request json",
                headers={"Content-Type": "application/json"},
            ):
                g.api_key_context = context
                response = jsonify(body)
                response.status_code = status
                returned = await _record_api_key_usage(response)
                assert returned is response
                assert returned.status_code == status
                assert await returned.get_json() == body

        asyncio.run(exercise_hook())
        stored = APIToken.get_by_id(("tenant-usage", token))

    assert stored.total_calls == 1
    assert stored.retrieval_calls == 0
    assert stored.last_result == expected_result


def test_request_usage_increments_retrieval_only_after_real_start_marker():
    from api.apps import _record_api_key_usage
    from api.apps.api_key_auth import APIKeyContext, mark_retrieval_started

    database = SqliteDatabase(":memory:")
    token = "ragflow-rk-marker-gated"
    app = Quart(__name__)

    with database.bind_ctx([APIToken, ScopedAPIToken]), database.connection_context():
        database.create_tables([APIToken, ScopedAPIToken])
        record = ScopedAPIToken.create(
            tenant_id="tenant-usage",
            token=token,
            name="Marker gated",
            allowed_dataset_ids=["dataset-1"],
        )
        context = APIKeyContext(
            key_type=APIKeyType.RETRIEVAL,
            tenant_id="tenant-usage",
            token=token,
            record=record,
            allowed_dataset_ids=frozenset({"dataset-1"}),
        )

        async def exercise_hook():
            async with app.test_request_context("/retrieval"):
                g.api_key_context = context
                await _record_api_key_usage(jsonify({"code": 0}))

            async with app.test_request_context("/retrieval"):
                g.api_key_context = context
                mark_retrieval_started()
                await _record_api_key_usage(jsonify({"code": 0}))

        asyncio.run(exercise_hook())
        stored = ScopedAPIToken.get_by_id(("tenant-usage", token))

    assert stored.total_calls == 2
    assert stored.retrieval_calls == 1
    assert stored.last_result == "success"


def test_request_usage_without_resolved_context_does_not_write_statistics():
    from api.apps import _record_api_key_usage

    database = SqliteDatabase(":memory:")
    token = "ragflow-full-no-resolved-context"
    app = Quart(__name__)

    with database.bind_ctx([APIToken, ScopedAPIToken]), database.connection_context():
        database.create_tables([APIToken, ScopedAPIToken])
        APIToken.create(tenant_id="tenant-usage", token=token)

        async def exercise_hook():
            async with app.test_request_context("/retrieval"):
                response = jsonify({"code": 0})
                assert await _record_api_key_usage(response) is response

        asyncio.run(exercise_hook())
        stored = APIToken.get_by_id(("tenant-usage", token))

    assert stored.total_calls == 0
    assert stored.retrieval_calls == 0
    assert stored.last_result is None


def test_request_usage_failure_logs_generic_message_and_preserves_original_response(caplog):
    from api.apps import _record_api_key_usage
    from api.apps.api_key_auth import APIKeyContext

    database = SensitiveFailingUpdateSqliteDatabase(":memory:")
    token = database.sensitive_token
    app = Quart(__name__)

    with database.bind_ctx([APIToken, ScopedAPIToken]), database.connection_context():
        database.create_tables([APIToken, ScopedAPIToken])
        record = APIToken.create(tenant_id="tenant-usage", token=token)
        context = APIKeyContext(
            key_type=APIKeyType.FULL_ACCESS,
            tenant_id="tenant-usage",
            token=token,
            record=record,
            allowed_dataset_ids=None,
        )
        database.fail_updates = True

        async def exercise_hook():
            async with app.test_request_context("/retrieval"):
                g.api_key_context = context
                response = jsonify({"code": 0, "data": {"kept": True}})
                response.status_code = 202
                with caplog.at_level(logging.WARNING):
                    returned = await _record_api_key_usage(response)
                assert returned is response
                assert returned.status_code == 202
                assert await returned.get_json() == {"code": 0, "data": {"kept": True}}

        asyncio.run(exercise_hook())

    assert "API key usage telemetry update failed" in caplog.text
    assert token not in caplog.text
    assert "telemetry write failed for" not in caplog.text


def test_real_quart_quota_rejection_returns_429_and_records_rate_limit_without_retrieval(monkeypatch):
    from api.apps import KNOWLEDGE_RETRIEVE_SCOPE, _record_api_key_usage, login_required
    from api.apps import api_key_auth
    from api.apps.api_key_auth import APIKeyContext, consume_retrieval_quota, mark_retrieval_started

    database = SqliteDatabase(":memory:")
    token = "ragflow-rk-real-429"
    app = Quart(__name__)
    route_entered = False

    with database.bind_ctx([APIToken, ScopedAPIToken]), database.connection_context():
        database.create_tables([APIToken, ScopedAPIToken])
        record = ScopedAPIToken.create(
            tenant_id="tenant-usage",
            token=token,
            name="Real 429",
            allowed_dataset_ids=["dataset-1"],
        )
        context = APIKeyContext(
            key_type=APIKeyType.RETRIEVAL,
            tenant_id="tenant-usage",
            token=token,
            record=record,
            allowed_dataset_ids=frozenset({"dataset-1"}),
        )

        def exhausted_bucket(**_kwargs):
            assert route_entered, "quota was consumed before the actual retrieval boundary"
            return [0, 0]

        monkeypatch.setattr(api_key_auth.REDIS_CONN, "lua_token_bucket", exhausted_bucket)

        @app.before_request
        async def install_context():
            g.user = SimpleNamespace(id="tenant-usage")
            g.api_key_context = context

        app.after_request(_record_api_key_usage)

        @app.post("/retrieval")
        @login_required(api_scope=KNOWLEDGE_RETRIEVE_SCOPE)
        async def retrieve():
            nonlocal route_entered
            route_entered = True
            try:
                consume_retrieval_quota(context)
            except TooManyRequests as error:
                response = jsonify({"code": 429, "message": "Retrieval API rate limit exceeded.", "data": None})
                response.headers["Retry-After"] = str(error.retry_after)
                return response, 429
            mark_retrieval_started()
            return jsonify({"code": 0})

        async def exercise_request():
            response = await app.test_client().post("/retrieval")
            assert response.status_code == 429
            assert response.headers["Retry-After"] == "1"
            assert await response.get_json() == {
                "code": 429,
                "message": "Retrieval API rate limit exceeded.",
                "data": None,
            }

        asyncio.run(exercise_request())
        stored = ScopedAPIToken.get_by_id(("tenant-usage", token))

    assert route_entered is True
    assert stored.total_calls == 1
    assert stored.retrieval_calls == 0
    assert stored.last_result == "rate_limited"


def test_real_quart_quota_backend_failure_returns_503_after_boundary_without_retrieval_marker(monkeypatch):
    from api.apps import KNOWLEDGE_RETRIEVE_SCOPE, _record_api_key_usage, login_required
    from api.apps import api_key_auth
    from api.apps.api_key_auth import APIKeyContext, consume_retrieval_quota, mark_retrieval_started

    database = SqliteDatabase(":memory:")
    token = "ragflow-rk-real-503"
    app = Quart(__name__)
    route_entered = False

    with database.bind_ctx([APIToken, ScopedAPIToken]), database.connection_context():
        database.create_tables([APIToken, ScopedAPIToken])
        record = ScopedAPIToken.create(
            tenant_id="tenant-usage",
            token=token,
            name="Real 503",
            allowed_dataset_ids=["dataset-1"],
        )
        context = APIKeyContext(
            key_type=APIKeyType.RETRIEVAL,
            tenant_id="tenant-usage",
            token=token,
            record=record,
            allowed_dataset_ids=frozenset({"dataset-1"}),
        )

        def unavailable_bucket(**_kwargs):
            assert route_entered, "quota was consumed before the actual retrieval boundary"
            raise ConnectionError("redis unavailable")

        monkeypatch.setattr(api_key_auth.REDIS_CONN, "lua_token_bucket", unavailable_bucket)

        @app.before_request
        async def install_context():
            g.user = SimpleNamespace(id="tenant-usage")
            g.api_key_context = context

        app.after_request(_record_api_key_usage)

        @app.post("/retrieval")
        @login_required(api_scope=KNOWLEDGE_RETRIEVE_SCOPE)
        async def retrieve():
            nonlocal route_entered
            route_entered = True
            try:
                consume_retrieval_quota(context)
            except ServiceUnavailable:
                return jsonify({"code": 503, "message": "Retrieval quota service is unavailable.", "data": None}), 503
            mark_retrieval_started()
            return jsonify({"code": 0})

        async def exercise_request():
            response = await app.test_client().post("/retrieval")
            assert response.status_code == 503
            assert await response.get_json() == {
                "code": 503,
                "message": "Retrieval quota service is unavailable.",
                "data": None,
            }

        asyncio.run(exercise_request())
        stored = ScopedAPIToken.get_by_id(("tenant-usage", token))

    assert route_entered is True
    assert stored.total_calls == 1
    assert stored.retrieval_calls == 0
    assert stored.last_result == "error"
