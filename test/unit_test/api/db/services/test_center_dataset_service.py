from dataclasses import replace
import pytest
from test.unit_test.api.db.services.test_workbuddy_employee_service import employee_db, profile
from api.db import db_models as m


def setup_center(profile):
    from api.db.services.center_dataset_service import CenterDatasetService as S
    S.sync_departments(profile.enterprise_id,profile.paths)
    S.set_center(profile.enterprise_id,'2',True,'admin')
    return S


def add_kb(id,status='1'):
    return m.Knowledgebase.create(id=id,tenant_id='owner',name=id,embd_id='embedding',created_by='owner',status=status)


def test_center_resolution_uses_unique_primary_path_and_ids(employee_db,profile):
    S=setup_center(profile)
    assert S.resolve_center('123',profile.paths)=='2'
    for paths in [(),(replace(profile.paths[0],complete=False),),(profile.paths[0],profile.paths[0]),(replace(profile.paths[0],is_main=False),)]:
        assert S.resolve_center('123',paths) is None
    m.EnterpriseDepartment.update(name='新中心名称').where(m.EnterpriseDepartment.department_id=='2').execute()
    assert S.resolve_center('123',profile.paths)=='2'
    with pytest.raises(ValueError): S.set_center('123','3',True,'admin')
    m.EnterpriseDepartment.update(is_center=True).where(m.EnterpriseDepartment.department_id=='3').execute()
    assert S.resolve_center('123',profile.paths) is None


def test_replacing_grants_is_atomic_and_empty_revokes_all(employee_db,profile):
    S=setup_center(profile)
    add_kb('kb-a');add_kb('kb-b')
    S.replace_grants('123','2',['kb-a'],'admin')
    with pytest.raises(ValueError): S.replace_grants('123','2',['kb-b','missing'],'admin')
    assert S.allowed_datasets('123','2')==frozenset({'kb-a'})
    S.replace_grants('123','2',[],'admin-2')
    assert S.allowed_datasets('123','2')==frozenset()
    assert m.CenterDatasetGrant.get().updated_by=='admin-2'


def test_inactive_or_foreign_center_cannot_be_granted(employee_db,profile):
    S=setup_center(profile);add_kb('kb-a')
    with pytest.raises(ValueError): S.replace_grants('other','2',['kb-a'],'admin')
    with pytest.raises(ValueError): S.replace_grants('123','3',['kb-a'],'admin')
    S.replace_grants('123','2',['kb-a'],'admin')
    m.Knowledgebase.update(status='0').where(m.Knowledgebase.id=='kb-a').execute()
    assert S.allowed_datasets('123','2')==frozenset()


def test_department_sync_preserves_admin_center_flags(employee_db,profile):
    S=setup_center(profile)
    S.sync_departments('123',profile.paths)
    assert S.resolve_center('123',profile.paths)=='2'
    assert m.EnterpriseDepartment.get(department_id='2').updated_by=='admin'
    assert S.allowed_datasets('123',None)==frozenset()
