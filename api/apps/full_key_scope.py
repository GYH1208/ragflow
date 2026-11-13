"""Dataset-only access for full API keys with an explicit allowlist.

Unknown/global endpoints are denied so aliases cannot silently escape the scope.
Resource handlers retain their existing ownership and document-binding checks.
"""

import asyncio

from quart import g, request
from werkzeug.exceptions import BadRequest, Forbidden, NotFound

from api.apps.api_key_auth import require_dataset_access


def _require_ids(values):
    if not isinstance(values, list) or not values or any(not isinstance(value, str) or not value for value in values):
        raise BadRequest("Explicit dataset IDs are required for this operation.")
    require_dataset_access(values)


async def _payload():
    payload = await request.get_json()
    if not isinstance(payload, dict):
        raise BadRequest("A JSON object is required.")
    return payload


async def _require_documents(values, dataset_id):
    if not isinstance(values, list) or any(not isinstance(value, str) or not value for value in values):
        raise BadRequest("Document IDs must be a list of nonempty strings.")
    if not values:
        return
    from api.db.services.document_service import DocumentService

    documents = await asyncio.to_thread(lambda: list(DocumentService.get_by_ids(values)))
    require_dataset_access([doc.kb_id for doc in documents])
    if {doc.id for doc in documents} != set(values):
        raise NotFound("Document not found.")
    if any(doc.kb_id != dataset_id for doc in documents):
        raise Forbidden("Documents must belong to the requested dataset.")


async def _require_chunks(values, dataset_id, document_id):
    _require_chunk_ids(values)
    from api.db.services.knowledgebase_service import KnowledgebaseService
    from common import settings
    from rag.nlp.search import index_name

    found, kb = await asyncio.to_thread(KnowledgebaseService.get_by_id, dataset_id)
    if not found or kb.tenant_id != g.api_key_context.tenant_id:
        raise Forbidden("Dataset is unavailable to this API key.")
    for chunk_id in values:
        chunk = await asyncio.to_thread(settings.docStoreConn.get, chunk_id, index_name(kb.tenant_id), [dataset_id])
        if not chunk:
            raise NotFound("Chunk not found.")
        datasets = chunk.get("kb_id", chunk.get("dataset_id"))
        datasets = [datasets] if isinstance(datasets, str) else datasets
        if (
            not isinstance(datasets, (list, tuple))
            or any(not isinstance(value, str) for value in datasets)
            or set(datasets) != {dataset_id}
            or chunk.get("doc_id", chunk.get("document_id")) != document_id
        ):
            raise Forbidden("Chunk is outside the requested dataset or document.")


def _require_chunk_ids(values):
    if not isinstance(values, list) or not values or any(not isinstance(value, str) or not value for value in values):
        raise BadRequest("Explicit chunk IDs are required.")


async def _check_dataset_batch(route, dataset_id, arguments):
    fields = {
        ("/datasets/<dataset_id>/chunks", "POST"): "document_ids",
        ("/datasets/<dataset_id>/chunks", "DELETE"): "document_ids",
        ("/datasets/<dataset_id>/documents", "DELETE"): "ids",
        ("/datasets/<dataset_id>/documents/parse", "POST"): "document_ids",
        ("/datasets/<dataset_id>/documents/stop", "POST"): "document_ids",
        ("/datasets/<dataset_id>/documents/batch-update-status", "POST"): "doc_ids",
    }
    field = fields.get((route, request.method))
    if field:
        payload = await _payload()
        if field in payload and payload[field] is not None:
            await _require_documents(payload[field], dataset_id)
    if route in ("/datasets/<dataset_id>/metadata/update", "/datasets/<dataset_id>/documents/metadatas") and request.method in ("POST", "PATCH"):
        selector = (await _payload()).get("selector") or {}
        if not isinstance(selector, dict):
            raise BadRequest("Document selector must be an object.")
        if selector.get("document_ids"):
            await _require_documents(selector["document_ids"], dataset_id)
    if route == "/datasets/<dataset_id>/documents/<document_id>/chunks" and request.method == "PATCH":
        await _require_chunks((await _payload()).get("chunk_ids"), dataset_id, arguments["document_id"])


async def require_full_key_scope():
    """Authorize only supported dataset REST routes before their side effects."""
    rule = request.url_rule.rule if request.url_rule is not None else ""
    arguments = request.view_args or {}
    prefix = "/api/v1"
    if not rule.startswith(prefix + "/"):
        raise Forbidden("This API key is limited to its selected datasets.")
    route = rule[len(prefix) :]

    if "dataset_id" in arguments and route.startswith("/datasets/<dataset_id>"):
        require_dataset_access([arguments["dataset_id"]])
        await _check_dataset_batch(route, arguments["dataset_id"], arguments)
        return
    if route == "/datasets" and request.method == "GET":
        # The dataset service applies the allowlist before count and pagination.
        return
    if route == "/datasets" and request.method == "DELETE":
        _require_ids((await _payload()).get("ids"))
        return
    if route in ("/retrieval", "/datasets/search") and request.method == "POST":
        payload = await _payload()
        ids = payload.get("dataset_ids")
        if route == "/retrieval" and not ids:
            # The retrieval handler resolves omitted IDs from the key's scope.
            return
        _require_ids(ids)
        return
    if route in ("/datasets/tags/aggregation", "/datasets/metadata/flattened") and request.method == "GET":
        _require_ids(request.args.get("dataset_ids", "").split(","))
        return
    if route in ("/documents/<doc_id>/preview", "/documents/<document_id>") and request.method == "GET":
        from api.db.services.document_service import DocumentService

        document_id = arguments.get("doc_id") or arguments.get("document_id")
        found, document = await asyncio.to_thread(DocumentService.get_by_id, document_id)
        if not found or not document:
            raise NotFound("Document not found.")
        require_dataset_access([document.kb_id])
        return
    raise Forbidden("This API key is limited to its selected datasets and cannot use this endpoint.")
