"""Readonly authorization resolves datasets before their original data owners."""

from werkzeug.exceptions import BadRequest, Forbidden, NotFound

from api.db.db_models import Document, Knowledgebase


def require_workbuddy_datasets(context, dataset_ids):
    ids = list(dict.fromkeys(dataset_ids))
    if any(id not in context.allowed_dataset_ids for id in ids):
        raise Forbidden("Center does not allow access to this dataset")
    records = {kb.id: kb for kb in Knowledgebase.select().where(Knowledgebase.id.in_(ids) & (Knowledgebase.status == "1"))}
    if len(records) != len(ids):
        raise NotFound("Dataset unavailable")
    return records


def require_workbuddy_document(context, dataset_id, document_id):
    require_workbuddy_datasets(context, [dataset_id])
    doc = Document.get_or_none(id=document_id, kb_id=dataset_id, status="1")
    if doc is None:
        raise NotFound("Document unavailable")
    return doc


def serialize_dataset(kb):
    return {
        "id": kb.id,
        "name": kb.name,
        "description": kb.description or "",
        "document_count": kb.doc_num,
        "chunk_count": kb.chunk_num,
        "embedding_model": kb.embd_id,
        "create_time": kb.create_time,
        "update_time": kb.update_time,
        "update_date": kb.update_date,
    }


def list_workbuddy_datasets(context, args):
    selected = args.get("id")
    if selected:
        require_workbuddy_datasets(context, [selected])
    query = Knowledgebase.select().where(Knowledgebase.id.in_(context.allowed_dataset_ids) & (Knowledgebase.status == "1"))
    if selected:
        query = query.where(Knowledgebase.id == selected)
    if args.get("name"):
        query = query.where(Knowledgebase.name == args["name"])
    ext = args.get("ext") or {}
    if ext.get("keywords"):
        query = query.where(Knowledgebase.name.contains(ext["keywords"]))
    if ext.get("parser_id"):
        query = query.where(Knowledgebase.parser_id == ext["parser_id"])
    if ext.get("category_id"):
        category = ext["category_id"]
        query = query.where(Knowledgebase.category_id.is_null() if category == "uncategorized" else Knowledgebase.category_id == category)
    if ext.get("owner_ids"):
        query = query.where(Knowledgebase.tenant_id.in_(ext["owner_ids"]))
    orderby = args.get("orderby", "create_time")
    if orderby not in ("create_time", "update_time", "name", "doc_num", "chunk_num"):
        raise BadRequest("Invalid dataset sort field")
    order = getattr(Knowledgebase, orderby)
    desc = args.get("desc", True)
    query = query.order_by(order.desc() if desc is True or str(desc).lower() == "true" else order.asc(), Knowledgebase.id)
    total = query.count()
    page = int(args.get("page", 1))
    size = int(args.get("page_size", 30))
    if page < 1 or not 1 <= size <= 100:
        raise BadRequest("Invalid pagination")
    return {"data": [serialize_dataset(kb) for kb in query.paginate(page, size)], "total": total}


def validate_workbuddy_chunk_binding(chunk, dataset_id, document_id):
    if not isinstance(chunk, dict):
        raise NotFound("Chunk unavailable")
    datasets = chunk.get("kb_id", chunk.get("dataset_id"))
    if isinstance(datasets, str):
        datasets = [datasets]
    if not isinstance(datasets, (list, tuple)) or dataset_id not in datasets or str(chunk.get("doc_id", chunk.get("document_id"))) != document_id:
        raise NotFound("Chunk unavailable")
    return chunk


def require_workbuddy_chunk(context, dataset_id, document_id, chunk_id):
    import re

    from common import settings
    from rag.nlp import search

    if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", chunk_id):
        raise BadRequest("Invalid chunk ID")
    kb = require_workbuddy_datasets(context, [dataset_id])[dataset_id]
    require_workbuddy_document(context, dataset_id, document_id)
    chunk = settings.docStoreConn.get(chunk_id, search.index_name(kb.tenant_id), [dataset_id])
    return validate_workbuddy_chunk_binding(chunk, dataset_id, document_id)


def _rank_dataset(chunk):
    dataset = chunk.get("kb_id", chunk.get("dataset_id"))
    if isinstance(dataset, (list, tuple)) and len(dataset) == 1:
        dataset = dataset[0]
    if not isinstance(dataset, str):
        raise Forbidden("Invalid retrieval dataset binding")
    return dataset


def validate_workbuddy_ranks(kbs, chunks, doc_aggs=(), document_ids=None):
    """Validate index results before optional enrichment can look up a document."""
    ids = [chunk.get("doc_id", chunk.get("document_id")) for chunk in chunks]
    ids.extend(agg.get("doc_id") for agg in doc_aggs)
    if any(not isinstance(value, str) for value in ids):
        raise Forbidden("Invalid retrieval document binding")
    documents = {d.id: d for d in Document.select().where(Document.id.in_(ids) & Document.kb_id.in_(list(kbs)) & (Document.status == "1"))}
    for chunk in chunks:
        kb_id = _rank_dataset(chunk)
        doc_id = chunk.get("doc_id", chunk.get("document_id"))
        doc = documents.get(doc_id)
        if kb_id not in kbs or doc is None or doc.kb_id != kb_id or document_ids and doc_id not in document_ids:
            raise Forbidden("Retrieval result is outside the allowed documents")
    for agg in doc_aggs:
        doc = documents.get(agg.get("doc_id"))
        if doc is None or document_ids and doc.id not in document_ids:
            raise Forbidden("Retrieval aggregation is outside the allowed documents")
        agg["doc_name"] = doc.name


def expand_workbuddy_parents(kbs, chunks):
    """Parent IDs are content hashes, so fetch and validate per KB and document."""
    import re
    from collections import defaultdict

    from common import settings
    from rag.nlp import search

    groups = defaultdict(list)
    output = []
    for chunk in chunks:
        parent = chunk.get("mom_id")
        if not isinstance(parent, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", parent):
            output.append(chunk)
            continue
        groups[(_rank_dataset(chunk), chunk["doc_id"], parent)].append(chunk)
    for (kb_id, doc_id, parent_id), children in groups.items():
        parent = settings.docStoreConn.get(parent_id, search.index_name(kbs[kb_id].tenant_id), [kb_id])
        try:
            validate_workbuddy_chunk_binding(parent, kb_id, doc_id)
        except NotFound:
            output.extend(children)
            continue
        score = sum(float(child["similarity"]) for child in children) / len(children)
        merged = {
            **children[0],
            "chunk_id": parent_id,
            "content_with_weight": parent.get("content_with_weight", ""),
            "content_ltks": " ".join(child.get("content_ltks", "") for child in children),
            "doc_id": doc_id,
            "kb_id": kb_id,
            "docnm_kwd": parent.get("docnm_kwd", children[0].get("docnm_kwd", "")),
            "important_kwd": [word for child in children for word in child.get("important_kwd", [])],
            "image_id": parent.get("img_id", ""),
            "positions": parent.get("position_int", []),
            "similarity": score,
            "vector_similarity": score,
            "term_similarity": score,
        }
        merged.pop("mom_id", None)
        output.append(merged)
    return sorted(output, key=lambda chunk: float(chunk["similarity"]), reverse=True)
