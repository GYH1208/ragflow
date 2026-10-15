from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
from test.unit_test.api.apps.restful_apis.test_workbuddy_dataset_documents import readonly_client, KB_A, KB_B


async def test_retrieval_defaults_to_exact_allowlist(readonly_client):
    client,scope,_=readonly_client;headers={'x-oneid-access-token':'valid'}
    res=await client.post('/api/v1/retrieval',json={'question':''},headers=headers)
    assert res.status_code==200
    assert (await res.get_json())['data']['chunks']==[]
    scope['ids']=frozenset()
    res=await client.post('/api/v1/retrieval',json={'question':'test'},headers=headers)
    assert '暂未配置可访问知识库' in str(await res.get_json())


async def test_mixed_allowed_and_forbidden_ids_fail_before_search(readonly_client,monkeypatch):
    from api.apps.restful_apis import chunk_api as route
    spy=AsyncMock()
    monkeypatch.setattr(route.settings,'retriever',SimpleNamespace(retrieval=spy))
    client,_,_=readonly_client
    res=await client.post('/api/v1/retrieval',json={'question':'test','dataset_ids':[KB_A,KB_B]},headers={'x-oneid-access-token':'valid'})
    assert res.status_code==403
    spy.assert_not_called()


async def test_cross_owner_uses_kb_tenant_not_employee(readonly_client,monkeypatch):
    from api.apps.restful_apis import chunk_api as route
    from api.db.db_models import Knowledgebase
    client,_,_=readonly_client;owners=[]
    monkeypatch.setattr(route.KnowledgebaseService,'get_by_ids',lambda ids:list(Knowledgebase.select().where(Knowledgebase.id.in_(ids))))
    monkeypatch.setattr(route.KnowledgebaseService,'get_by_id',lambda id:(True,Knowledgebase.get_by_id(id)))
    monkeypatch.setattr(route,'get_model_config_from_provider_instance',lambda tenant,*args: owners.append(tenant) or {})
    monkeypatch.setattr(route,'LLMBundle',lambda *args:object())
    monkeypatch.setattr(route,'label_question',lambda *args:None)
    retriever=SimpleNamespace(retrieval=AsyncMock(return_value={'total':0,'chunks':[],'doc_aggs':[]}),retrieval_by_children=lambda chunks,tenants:chunks)
    monkeypatch.setattr(route.settings,'retriever',retriever)
    res=await client.post('/api/v1/retrieval',json={'question':'test'},headers={'x-oneid-access-token':'valid'})
    assert res.status_code==200
    assert (await res.get_json())['code']==0
    assert owners==['owner-a']
    assert retriever.retrieval.call_args.args[2]==['owner-a']
    assert retriever.retrieval.call_args.args[3]==[KB_A]


@pytest.mark.parametrize('suffix',['/chunks','/chunks/chunk-a','/chunks/chunk-a/image'])
async def test_chunk_and_image_cannot_cross_dataset_or_document(readonly_client,suffix):
    client,_,_=readonly_client
    res=await client.get(f'/api/v1/datasets/{KB_A}/documents/doc-b'+suffix,headers={'x-oneid-access-token':'valid'})
    assert res.status_code in (403,404)
    assert 'b.pdf' not in str(await res.get_json())


async def test_incompatible_embedding_models_keep_existing_error(readonly_client):
    from api.db.db_models import Knowledgebase
    client,scope,_=readonly_client;scope['ids']=frozenset({KB_A,KB_B})
    Knowledgebase.update(embd_id='different-model').where(Knowledgebase.id==KB_B).execute()
    res=await client.post('/api/v1/retrieval',json={'question':'test'},headers={'x-oneid-access-token':'valid'})
    assert 'different embedding models' in str(await res.get_json())


async def test_authorized_chunks_and_images_use_original_owner(readonly_client,monkeypatch):
    from api.apps.restful_apis import chunk_api as route
    from io import BytesIO
    from PIL import Image
    client,_,_=readonly_client;seen=[]
    image=BytesIO();Image.new('RGB',(1,1)).save(image,format='PNG')
    chunk={'id':'chunk-a','kb_id':KB_A,'doc_id':'doc-a','content_with_weight':'approved content','docnm_kwd':'a.pdf','img_id':KB_A+'-image','position_int':[]}
    def get(id,index,ids):
        seen.append((index,ids));return chunk.copy()
    monkeypatch.setattr(route.settings,'docStoreConn',SimpleNamespace(get=get))
    monkeypatch.setattr(route.settings,'STORAGE_IMPL',SimpleNamespace(get=lambda bucket,key:image.getvalue()))
    base=f'/api/v1/datasets/{KB_A}/documents/doc-a/chunks'
    res=await client.get(base+'?id=chunk-a',headers={'x-oneid-access-token':'valid'})
    assert (await res.get_json())['data']['chunks'][0]['content']=='approved content'
    res=await client.get(base+'/chunk-a/image',headers={'x-oneid-access-token':'valid'})
    assert res.status_code==200 and res.content_type=='image/png'
    assert await res.get_data()==image.getvalue()
    assert all(index.endswith('owner-a') and ids==[KB_A] for index,ids in seen)
    chunk['kb_id']=KB_B
    res=await client.get(base+'?id=chunk-a',headers={'x-oneid-access-token':'valid'})
    assert res.status_code==404
