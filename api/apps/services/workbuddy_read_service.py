"""Readonly authorization resolves datasets before their original data owners."""
from werkzeug.exceptions import Forbidden, NotFound, BadRequest
from api.db.db_models import Knowledgebase, Document


def require_workbuddy_datasets(context,dataset_ids):
    ids=list(dict.fromkeys(dataset_ids))
    if any(id not in context.allowed_dataset_ids for id in ids):
        raise Forbidden('Center does not allow access to this dataset')
    records={kb.id:kb for kb in Knowledgebase.select().where(Knowledgebase.id.in_(ids)&(Knowledgebase.status=='1'))}
    if len(records)!=len(ids):
        raise NotFound('Dataset unavailable')
    return records


def require_workbuddy_document(context,dataset_id,document_id):
    require_workbuddy_datasets(context,[dataset_id])
    doc=Document.get_or_none(id=document_id,kb_id=dataset_id,status='1')
    if doc is None:
        raise NotFound('Document unavailable')
    return doc


def serialize_dataset(kb):
    return {'id':kb.id,'name':kb.name,'description':kb.description or '', 'document_count':kb.doc_num,'chunk_count':kb.chunk_num,'embedding_model':kb.embd_id,'create_time':kb.create_time,'update_time':kb.update_time,'update_date':kb.update_date}


def list_workbuddy_datasets(context,args):
    selected=args.get('id')
    if selected:
        require_workbuddy_datasets(context,[selected])
    query=Knowledgebase.select().where(Knowledgebase.id.in_(context.allowed_dataset_ids)&(Knowledgebase.status=='1'))
    if selected: query=query.where(Knowledgebase.id==selected)
    if args.get('name'): query=query.where(Knowledgebase.name==args['name'])
    ext=args.get('ext') or {}
    if ext.get('keywords'): query=query.where(Knowledgebase.name.contains(ext['keywords']))
    if ext.get('parser_id'): query=query.where(Knowledgebase.parser_id==ext['parser_id'])
    if ext.get('category_id'):
        category=ext['category_id']
        query=query.where(Knowledgebase.category_id.is_null() if category=='uncategorized' else Knowledgebase.category_id==category)
    if ext.get('owner_ids'): query=query.where(Knowledgebase.tenant_id.in_(ext['owner_ids']))
    orderby=args.get('orderby','create_time')
    if orderby not in ('create_time','update_time','name','doc_num','chunk_num'):
        raise BadRequest('Invalid dataset sort field')
    order=getattr(Knowledgebase,orderby)
    desc=args.get('desc',True)
    query=query.order_by(order.desc() if desc is True or str(desc).lower()=='true' else order.asc(),Knowledgebase.id)
    total=query.count()
    page=int(args.get('page',1));size=int(args.get('page_size',30))
    if page<1 or not 1<=size<=100:
        raise BadRequest('Invalid pagination')
    return {'data':[serialize_dataset(kb) for kb in query.paginate(page,size)],'total':total}
