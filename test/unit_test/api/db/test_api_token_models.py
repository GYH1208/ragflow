from datetime import datetime, timedelta, timezone

import pytest
from peewee import CompositeKey, DateTimeField, OperationalError, SqliteDatabase
from playhouse.migrate import SqliteMigrator

from api import db
from api.db import db_models


def test_api_key_enum_values_remain_stable_for_persistence_and_api_serialization():
    assert hasattr(db, "APIKeyType")
    assert hasattr(db, "APIKeyLastResult")
    assert [member.value for member in db.APIKeyType] == ["full_access", "retrieval"]
    assert [member.value for member in db.APIKeyLastResult] == ["success", "denied", "rate_limited", "error"]


def test_api_token_metadata_defaults_protect_new_full_access_keys_from_legacy_labeling():
    expected_fields = {
        "name",
        "is_legacy",
        "enabled",
        "total_calls",
        "retrieval_calls",
        "last_used_at",
        "last_result",
    }

    assert expected_fields <= set(db_models.APIToken._meta.fields)

    token = db_models.APIToken(tenant_id="tenant-1", token="ragflow-full-1")
    assert token.name == "旧版 API Key"
    assert token.is_legacy is False
    assert token.enabled is True
    assert token.total_calls == 0
    assert token.retrieval_calls == 0
    assert token.last_used_at is None
    assert token.last_result is None


def test_scoped_api_token_schema_preserves_dataset_scope_expiry_and_standard_timestamps():
    assert hasattr(db_models, "ScopedAPIToken")
    scoped_model = db_models.ScopedAPIToken

    assert scoped_model._meta.table_name == "scoped_api_token"
    assert isinstance(scoped_model._meta.primary_key, CompositeKey)
    assert scoped_model._meta.primary_key.field_names == ("tenant_id", "token")
    assert isinstance(scoped_model.allowed_dataset_ids, db_models.JSONField)
    assert scoped_model.allowed_dataset_ids.null is False
    assert isinstance(scoped_model.expires_at, DateTimeField)
    assert scoped_model.expires_at.null is True
    assert {
        "name",
        "enabled",
        "total_calls",
        "retrieval_calls",
        "last_used_at",
        "last_result",
        "create_time",
        "create_date",
        "update_time",
        "update_date",
    } <= set(scoped_model._meta.fields)

    database = SqliteDatabase(":memory:")
    expires_at = datetime(2030, 4, 5, 14, 7, 8, tzinfo=timezone(timedelta(hours=8)))
    with database.bind_ctx([scoped_model]), database.connection_context():
        database.create_tables([scoped_model])
        scoped_model.create(
            tenant_id="tenant-1",
            token="ragflow-rk-1",
            name="WorkBuddy",
            allowed_dataset_ids=["dataset-1", "dataset-2"],
            expires_at=expires_at,
        )
        stored = scoped_model.get_by_id(("tenant-1", "ragflow-rk-1"))

    assert stored.allowed_dataset_ids == ["dataset-1", "dataset-2"]
    assert stored.expires_at == datetime(2030, 4, 5, 6, 7, 8, tzinfo=timezone.utc)
    assert stored.expires_at.tzinfo is timezone.utc
    assert stored.enabled is True
    assert stored.total_calls == 0
    assert stored.retrieval_calls == 0
    assert stored.last_used_at is None
    assert stored.last_result is None


def test_api_token_metadata_migration_is_idempotent_and_marks_existing_rows_legacy():
    assert hasattr(db_models, "migrate_api_token_metadata")
    database = SqliteDatabase(":memory:")
    migrator = SqliteMigrator(database)

    with database.connection_context():
        database.execute_sql(
            "CREATE TABLE api_token (tenant_id TEXT NOT NULL, token TEXT NOT NULL, "
            "PRIMARY KEY (tenant_id, token))"
        )
        database.execute_sql(
            "INSERT INTO api_token (tenant_id, token) VALUES (?, ?)",
            ("tenant-legacy", "ragflow-legacy-1"),
        )

        db_models.migrate_api_token_metadata(migrator)
        db_models.migrate_api_token_metadata(migrator)
        row = database.execute_sql(
            "SELECT name, is_legacy, enabled, total_calls, retrieval_calls, "
            "last_used_at, last_result FROM api_token WHERE token = ?",
            ("ragflow-legacy-1",),
        ).fetchone()

    assert row == ("旧版 API Key", 1, 1, 0, 0, None, None)


@pytest.mark.parametrize(
    "failure",
    [
        OperationalError("permission denied while altering api_token"),
        RuntimeError("migration driver interrupted"),
    ],
)
def test_api_token_metadata_migration_propagates_non_duplicate_ddl_failures(monkeypatch, failure):
    class FailingMigrator:
        @staticmethod
        def add_column(table_name, column_name, column_type):
            return table_name, column_name, column_type

    def raise_migration_error(*_operations):
        raise failure

    monkeypatch.setattr(db_models, "migrate", raise_migration_error)

    with pytest.raises(type(failure), match=str(failure)):
        db_models.migrate_api_token_metadata(FailingMigrator())
