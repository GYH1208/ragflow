"""Full-access HTTP ingestion of external Q&A records (not an MCP tool)."""

import asyncio
import logging

from quart import g, jsonify, request
from werkzeug.exceptions import HTTPException

from api.apps import AUTH_API, login_required
from api.apps.services.qa_writeback_service import QAWritebackError, QAWritebackService, normalize_request
from api.common.check_team_permission import check_kb_team_permission
from api.db.services.document_service import DocumentService
from api.db.services.knowledgebase_service import KnowledgebaseService
from common.constants import ParserType, StatusEnum, TaskStatus

logger = logging.getLogger(__name__)


def _load_target(dataset_id, document_id, user_id):
    ok, kb = KnowledgebaseService.get_by_id(dataset_id)
    if not ok or str(kb.status) != StatusEnum.VALID.value or not check_kb_team_permission(kb, user_id):
        raise QAWritebackError(404, 404, "TARGET_NOT_FOUND", "Target dataset or document is unavailable.")
    docs = DocumentService.query(id=document_id, kb_id=dataset_id, status=StatusEnum.VALID.value)
    if not docs:
        raise QAWritebackError(404, 404, "TARGET_NOT_FOUND", "Target dataset or document is unavailable.")
    doc = docs[0]
    if doc.parser_id != ParserType.QA.value:
        raise QAWritebackError(400, 101, "INVALID_DOCUMENT_TYPE", "Target document must use Q&A parsing.")
    if str(doc.run) == TaskStatus.RUNNING.value:
        raise QAWritebackError(409, 106, "DOCUMENT_BUSY", "Target document is being parsed. Retry later.", True)
    return kb, doc


def _error_response(error):
    return jsonify(code=error.code, message=str(error), data={"error_code": error.error_code, "retryable": error.retryable}), error.status


@manager.route("/datasets/<dataset_id>/documents/<document_id>/qa", methods=["POST"])  # noqa: F821
@login_required(auth_types=[AUTH_API])
async def create_qa(dataset_id, document_id):
    try:
        try:
            payload = await request.get_json()
        except HTTPException:
            raise QAWritebackError(400, 101, "INVALID_ARGUMENT", "A valid application/json object is required.") from None
        normalize_request(payload)
        kb, doc = await asyncio.to_thread(_load_target, dataset_id, document_id, g.user.id)
        result = await QAWritebackService().create(kb, doc, payload)
        return jsonify(code=0, data=result)
    except QAWritebackError as error:
        return _error_response(error)
    except Exception as exc:  # noqa: BLE001 - return a stable error without internal details
        logger.warning("Q&A writeback request failed (%s)", type(exc).__name__)
        return _error_response(QAWritebackError(503, 503, "STORAGE_UNAVAILABLE", "Writeback storage is temporarily unavailable. Retry with the same IDs and payload.", True))
