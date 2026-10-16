from peewee import SqliteDatabase
from playhouse.migrate import SqliteMigrator
from api.db import db_models as m


def test_migration_is_idempotent_and_preserves_existing_data(monkeypatch):
    from api.db.workbuddy_migration import migrate_workbuddy_schema

    models = [m.User, m.WorkBuddyEmployeeIdentity, m.EnterpriseDepartment, m.CenterDatasetGrant]
    db = SqliteDatabase(":memory:")
    with db.bind_ctx(models), db.connection_context():
        db.execute_sql("CREATE TABLE user (id TEXT PRIMARY KEY, email TEXT, nickname TEXT, update_time BIGINT, update_date DATETIME)")
        db.execute_sql("INSERT INTO user (id,email,nickname) VALUES ('old','old@example.com','Old')")
        monkeypatch.setenv("RAGFLOW_WORKBUDDY_TENANT_ID", "1494560257098951656")
        migrate_workbuddy_schema(SqliteMigrator(db))
        migrate_workbuddy_schema(SqliteMigrator(db))
        assert db.execute_sql("SELECT account_source FROM user").fetchone() == ("local",)
        assert m.CenterDatasetGrant.select().count() == 0
        assert m.EnterpriseDepartment.select().count() == 1
        assert db.execute_sql("SELECT id,email,nickname FROM user").fetchone() == ("old", "old@example.com", "Old")
