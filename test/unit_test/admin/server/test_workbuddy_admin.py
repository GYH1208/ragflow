import importlib
from pathlib import Path
from types import SimpleNamespace
import pytest
from flask import Flask
from flask_login import LoginManager, UserMixin
from test.unit_test.api.db.services.test_workbuddy_employee_service import employee_db, profile
from test.unit_test.api.db.services.test_center_dataset_service import setup_center, add_kb
from api.db import db_models as m


@pytest.fixture
def admin_client(monkeypatch,employee_db):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[4]/'admin/server'))
    routes=importlib.import_module('routes')
    class Person(UserMixin):
        def __init__(self,admin):
            self.id='admin' if admin else 'ordinary';self.is_superuser=admin;self.email='test@example.com'
    app=Flask('workbuddy-admin-test');app.secret_key='test'
    manager=LoginManager(app)
    @manager.request_loader
    def user(req): return Person(req.headers.get('test-role')=='admin')
    auth=importlib.import_module('auth')
    monkeypatch.setattr(auth.UserService,'filter_by_id',lambda id: SimpleNamespace(id=id,is_superuser=id=='admin',is_active='1'))
    from api.common.exceptions import AdminException
    @app.errorhandler(AdminException)
    def failed(exc): return {'code':exc.code,'message':exc.message},403
    monkeypatch.setenv('RAGFLOW_WORKBUDDY_TENANT_ID','123')
    app.register_blueprint(routes.admin_bp)
    return app.test_client()


@pytest.mark.parametrize('method,path,body',[
    ('get','/employees',None),('get','/employees/missing',None),('put','/employees/missing/enabled',{'enabled':False}),
    ('get','/departments',None),('get','/centers',None),('put','/departments/2/center',{'enabled':True}),
    ('get','/datasets',None),('get','/centers/2/datasets',None),('put','/centers/2/datasets',{'dataset_ids':[]})])
def test_all_workbuddy_admin_routes_require_superadmin(admin_client,method,path,body):
    res=getattr(admin_client,method)('/api/v1/admin/workbuddy'+path,json=body)
    assert res.status_code in (400,401,403)
    assert m.CenterDatasetGrant.select().count()==0


def test_employee_filters_and_safe_fields(admin_client,employee_db,profile):
    from api.db.services.workbuddy_employee_service import WorkBuddyEmployeeService as S
    setup_center(profile);user=S.sync_employee(profile)
    res=admin_client.get('/api/v1/admin/workbuddy/employees?q=员工&center_id=2',headers={'test-role':'admin'})
    assert res.status_code==200
    data=res.json['data'];assert data['total']==1
    assert data['items'][0]['user_id']==user.id
    assert data['items'][0]['center_name']=='中心'
    assert 'password' not in str(data) and 'access_token' not in str(data)
    assert admin_client.put(f'/api/v1/admin/workbuddy/employees/{user.id}/enabled',json={'enabled':'false'},headers={'test-role':'admin'}).status_code==400


def test_bad_replacement_does_not_partially_update(admin_client,employee_db,profile):
    S=setup_center(profile);add_kb('kb-a')
    url='/api/v1/admin/workbuddy/centers/2/datasets';headers={'test-role':'admin'}
    assert admin_client.put(url,json={'dataset_ids':['kb-a']},headers=headers).status_code==200
    assert admin_client.put(url,json={'dataset_ids':['missing']},headers=headers).status_code==400
    assert S.allowed_datasets('123','2')==frozenset({'kb-a'})


def test_legacy_password_promotion_delete_and_key_creation_reject_employees(monkeypatch,employee_db,profile):
    from test.unit_test.admin.server.test_user_service_mgr import _load_admin_services
    from api.db.services.workbuddy_employee_service import WorkBuddyEmployeeService as S
    user=S.sync_employee(profile);services=_load_admin_services(monkeypatch)
    monkeypatch.setattr(services.UserService,'query_user_by_email',lambda _: [user])
    for fn,args in [(services.UserMgr.delete_user,(user.email,)),(services.UserMgr.update_user_password,(user.email,'anything')),(services.UserMgr.grant_admin,(user.email,)),(services.UserMgr.save_api_key,({'tenant_id':user.id,'token':'test-key'},))]:
        with pytest.raises(Exception,match='WorkBuddy'): fn(*args)
