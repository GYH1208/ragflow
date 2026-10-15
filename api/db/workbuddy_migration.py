"""Additive WorkBuddy account migration; no automatic dataset grants."""
import os
from peewee import CharField
from playhouse.migrate import migrate
from api.db.db_models import User, WorkBuddyEmployeeIdentity, EnterpriseDepartment, CenterDatasetGrant
from common.misc_utils import get_uuid


def migrate_workbuddy_schema(migrator) -> None:
    database = migrator.database
    if "account_source" not in {column.name for column in database.get_columns("user")}:
        migrate(migrator.add_column("user", "account_source", CharField(max_length=16, default="local", index=True)))
    database.create_tables([WorkBuddyEmployeeIdentity, EnterpriseDepartment, CenterDatasetGrant], safe=True)
    User.update(account_source="local").where((User.account_source.is_null()) | (User.account_source == "")).execute()
    enterprise = os.environ.get("RAGFLOW_WORKBUDDY_TENANT_ID")
    if enterprise == "1494560257098951656":
        EnterpriseDepartment.get_or_create(enterprise_id=enterprise, department_id="1494587270954266260", defaults={"id":get_uuid(), "name":"后勤服务中心", "is_center":True, "updated_by":"migration"})
