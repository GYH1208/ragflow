from types import SimpleNamespace
import pytest
from quart import Quart
from peewee import SqliteDatabase
from api.db import db_models as m
from api.auth.workbuddy.types import WorkBuddyAccessContext


KB_A="a"*12+"1aaa8"+"a"*15
KB_B="b"*12+"1bbb8"+"b"*15


@pytest.fixture
def readonly_client(monkeypatch):
    import api.apps as auth
    from api.apps.restful_apis import dataset_api,document_api,chunk_api
    models=[m.User,m.Knowledgebase,m.Document]
    db=SqliteDatabase(':memory:')
    with db.bind_ctx(models),db.connection_context():
        db.create_tables(models)
        for id in (KB_A,KB_B):
            m.Knowledgebase.create(id=id,tenant_id='owner-'+id[0],name='Visible' if id[0]=='a' else 'Hidden',description='description',embd_id='model',created_by='owner')
        m.Document.create(id='doc-a',kb_id=KB_A,parser_id='naive',type='pdf',created_by='owner',suffix='pdf',name='a.pdf',location='a.pdf')
        m.Document.create(id='doc-b',kb_id=KB_B,parser_id='naive',type='pdf',created_by='owner',suffix='pdf',name='b.pdf',location='b.pdf')
        from common.constants import RetCode
        monkeypatch.setattr(document_api.KnowledgebaseService,'get_by_id',lambda id: (m.Knowledgebase.get_or_none(id=id) is not None,m.Knowledgebase.get_or_none(id=id)))
        monkeypatch.setattr(document_api,'_get_docs_with_request',lambda req,id: (RetCode.SUCCESS,'',list(m.Document.select().where(m.Document.kb_id==id).dicts()),m.Document.select().where(m.Document.kb_id==id).count()))
        scope={'ids':frozenset({KB_A})}
        async def authenticate(token): return SimpleNamespace(id='employee',account_source='workbuddy'),WorkBuddyAccessContext('employee','123','2',scope['ids'])
        monkeypatch.setattr(auth,'authenticate_workbuddy',authenticate)
        app=Quart('workbuddy-read-test');app.secret_key='test'
        for mod in (dataset_api,document_api,chunk_api): app.register_blueprint(mod.manager,url_prefix='/api/v1')
        yield app.test_client(),scope,db


async def test_employee_sees_only_granted_dataset_names_and_counts(readonly_client):
    client,scope,_=readonly_client;headers={'x-oneid-access-token':'valid'}
    res=await client.get('/api/v1/datasets?page=1&page_size=1',headers=headers)
    assert res.status_code==200
    data=await res.get_json()
    assert data.get('total',data.get('total_datasets'))==1, data
    assert [kb['id'] for kb in data['data']]==[KB_A]
    assert 'Hidden' not in str(data)
    scope['ids']=frozenset()
    res=await client.get('/api/v1/datasets',headers=headers)
    assert (await res.get_json())['data']==[]
    res=await client.get('/api/v1/datasets?id='+KB_B,headers=headers)
    assert res.status_code==403


async def test_document_scope_checks_dataset_owner_and_document_binding(readonly_client):
    client,scope,_=readonly_client;headers={'x-oneid-access-token':'valid'}
    res=await client.get('/api/v1/datasets/'+KB_A+'/documents',headers=headers)
    assert res.status_code==200
    data=await res.get_json()
    assert data['data']['total']==1
    assert [doc['id'] for doc in data['data']['docs']]==['doc-a']
    res=await client.get('/api/v1/datasets/'+KB_B+'/documents',headers=headers)
    assert res.status_code==403
    assert 'b.pdf' not in str(await res.get_json())
    from api.apps.services.workbuddy_read_service import require_workbuddy_document
    with pytest.raises(Exception): require_workbuddy_document(WorkBuddyAccessContext('employee','123','2',scope['ids']),KB_A,'doc-b')
