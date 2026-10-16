import pytest
from peewee import IntegrityError, SqliteDatabase
from api.db import db_models as m


def test_external_identity_and_grant_keys_are_unique():
    assert hasattr(m.User, "account_source"), "employee account source must exist"
    models = [m.User, m.WorkBuddyEmployeeIdentity, m.EnterpriseDepartment, m.CenterDatasetGrant]
    db = SqliteDatabase(":memory:")
    with db.bind_ctx(models), db.connection_context():
        db.create_tables(models)
        local = m.User.create(id="local", nickname="Local", email="local@example.com")
        assert local.account_source == "local"
        identity = dict(user_id="u1", enterprise_id="e1", union_id="s1", issuer="issuer", application_id="app", name="Employee")
        m.WorkBuddyEmployeeIdentity.create(id="i1", **identity)
        with pytest.raises(IntegrityError):
            m.WorkBuddyEmployeeIdentity.create(id="i2", **identity)
        with pytest.raises(IntegrityError):
            m.WorkBuddyEmployeeIdentity.create(id="i3", **{**identity, "union_id": "s2"})
        grant = dict(enterprise_id="e1", center_department_id="d1", dataset_id="kb1", created_by="admin", updated_by="admin")
        m.CenterDatasetGrant.create(id="g1", **grant)
        with pytest.raises(IntegrityError):
            m.CenterDatasetGrant.create(id="g2", **grant)
