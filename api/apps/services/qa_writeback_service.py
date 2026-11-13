"""Synchronous HTTP Q&A ingestion, with durable retries across index/SQL writes."""

import asyncio
import hashlib
import json
import logging
import math
import re
from datetime import UTC, datetime

KEYWORD_TIMEOUT_SECONDS = 10
logger = logging.getLogger(__name__)


class QAWritebackError(Exception):
    def __init__(self, status, code, error_code, message, retryable=False):
        super().__init__(message)
        self.status = status
        self.code = code
        self.error_code = error_code
        self.retryable = retryable


def normalize_request(raw):
    def invalid(message):
        raise QAWritebackError(400, 101, "INVALID_ARGUMENT", message)

    if not isinstance(raw, dict):
        invalid("Request body must be a JSON object.")
    result = {}
    for field, limit in (("question", 2000), ("answer", 20000), ("source_system", 64), ("external_id", 128)):
        value = raw.get(field)
        if not isinstance(value, str):
            invalid(f"`{field}` must be a non-empty string.")
        value = value.replace("\r\n", "\n").replace("\r", "\n").strip()
        if not value or len(value) > limit or "\x00" in value:
            invalid(f"`{field}` must contain 1 to {limit} characters without NUL.")
        result[field] = value
    if not re.fullmatch(r"[A-Za-z0-9_-]+", result["source_system"]):
        invalid("`source_system` must use ASCII letters, digits, underscores or hyphens.")
    if re.search(r"\t(?=(?:回答|Answer)\s*[:：])", result["question"], re.IGNORECASE):
        invalid("`question` contains the reserved Q&A answer separator.")
    top_n = raw.get("keyword_top_n", 5)
    if type(top_n) is not int or not 1 <= top_n <= 10:
        invalid("`keyword_top_n` must be an integer between 1 and 10.")
    keywords = raw.get("important_keywords", [])
    if not isinstance(keywords, list) or len(keywords) > 20:
        invalid("`important_keywords` must be a list of at most 20 strings.")
    cleaned = []
    for keyword in keywords:
        if not isinstance(keyword, str) or len(keyword.strip()) > 100 or "\x00" in keyword:
            invalid("Each keyword must be a string of at most 100 characters without NUL.")
        if keyword.strip() and keyword.strip() not in cleaned:
            cleaned.append(keyword.strip())
    result["important_keywords"] = cleaned
    result["keyword_top_n"] = 0 if cleaned else top_n
    return result


def _digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


class QAWritebackService:
    def __init__(self, store=None, runtime=None):
        if store is None:
            from api.db.services.qa_writeback_service import QAWritebackStore

            store = QAWritebackStore()
        self.store = store
        self.runtime = runtime or QAWritebackRuntime()

    async def create(self, kb, doc, raw):
        payload = normalize_request(raw)
        identity = {"tenant_id": kb.tenant_id, "dataset_id": kb.id, "document_id": doc.id, "source_system": payload["source_system"], "external_id": payload["external_id"]}
        key = _digest(identity)
        identity["id"] = key
        fingerprint = _digest({**payload, "important_keywords": sorted(payload["important_keywords"])})
        owner = ""
        try:
            record, owner = await asyncio.to_thread(self.store.claim, identity, fingerprint, key[:32])
            prepared = record["prepared"]
            if not record["completed"] and prepared is None:
                keywords, keyword_status = await self._keywords(kb, doc, payload)
                try:
                    vector, tokens = await self.runtime.embed(kb, doc, payload["question"])
                    if not vector or not all(math.isfinite(float(v)) for v in vector):
                        raise ValueError("Invalid embedding vector")
                except QAWritebackError:
                    raise
                except Exception:  # noqa: BLE001 - normalize errors from configured providers
                    raise QAWritebackError(503, 503, "EMBEDDING_UNAVAILABLE", "Embedding service is temporarily unavailable.", True) from None
                chunk = await asyncio.to_thread(self.runtime.build_chunk, kb, doc, payload, record["chunk_id"], keywords, vector)
                prepared = {"chunk": chunk, "keyword_status": keyword_status, "token_num": int(tokens)}
                await asyncio.to_thread(self.store.prepare, key, owner, prepared)

            if record["completed"]:
                existing = await asyncio.to_thread(self.runtime.get_chunk, kb, record["chunk_id"])
                if existing is None:
                    raise QAWritebackError(409, 409, "CHUNK_REMOVED", "The previously created chunk was removed. Use a new external ID to create another.")
                created = False
            else:

                def publish():
                    # Runs while the SQL ledger and document rows are locked. A
                    # lease takeover or reparse must wait until publication ends.
                    existing = self.runtime.get_chunk(kb, record["chunk_id"])
                    if existing is not None:
                        if str(existing.get("doc_id")) != str(doc.id) or existing.get("content_with_weight") != prepared["chunk"]["content_with_weight"]:
                            raise QAWritebackError(409, 409, "IDEMPOTENCY_CONFLICT", "The chunk has been changed outside this writeback request.")
                        return False
                    self.runtime.insert_chunk(kb, doc, prepared["chunk"])
                    confirmed = self.runtime.get_chunk(kb, record["chunk_id"])
                    if confirmed is None or str(confirmed.get("doc_id")) != str(doc.id) or confirmed.get("content_with_weight") != prepared["chunk"]["content_with_weight"]:
                        raise QAWritebackError(503, 503, "STORAGE_UNAVAILABLE", "Index write could not be confirmed. Retry the same request.", True)
                    return True

                created = await self._finish(key, owner, publish)
            return {
                "chunk_id": record["chunk_id"],
                "dataset_id": kb.id,
                "document_id": doc.id,
                "source_system": payload["source_system"],
                "external_id": payload["external_id"],
                "created": created,
                "important_keywords": prepared["chunk"]["important_kwd"],
                "keyword_status": prepared["keyword_status"],
            }
        except QAWritebackError:
            raise
        except Exception as exc:  # noqa: BLE001 - stable public error, without provider details
            logger.warning("Q&A writeback persistence failed (%s)", type(exc).__name__)
            raise QAWritebackError(503, 503, "STORAGE_UNAVAILABLE", "Writeback storage is temporarily unavailable. Retry with the same IDs and payload.", True) from None
        finally:
            if owner:
                try:
                    await asyncio.shield(asyncio.to_thread(self.store.release, key, owner))
                except Exception as exc:  # noqa: BLE001 - preserve the original result/error
                    logger.warning("Q&A writeback lease release failed (%s)", type(exc).__name__)

    async def _finish(self, key, owner, publish):
        worker = asyncio.create_task(asyncio.to_thread(self.store.finish, key, owner, publish))
        try:
            return await asyncio.shield(worker)
        except asyncio.CancelledError:
            # Cancelling to_thread cannot stop its worker. Keep the lease until
            # publication/commit settles; otherwise a retry can race a live writer.
            while not worker.done():
                try:
                    await asyncio.shield(worker)
                except asyncio.CancelledError:
                    continue
                except Exception:  # noqa: BLE001 - caller cancellation takes precedence
                    break
            if not worker.cancelled():
                worker.exception()
            raise

    async def _keywords(self, kb, doc, payload):
        if payload["important_keywords"]:
            return payload["important_keywords"], "provided"
        try:
            async with asyncio.timeout(KEYWORD_TIMEOUT_SECONDS):
                output = await self.runtime.extract_keywords(kb, doc, f"问题：{payload['question']}\n回答：{payload['answer']}", payload["keyword_top_n"])
            if not isinstance(output, str) or "**ERROR**" in output:
                return [], "failed"
            keywords = []
            for item in re.split(r"[,，;；、\r\n]+", output):
                item = item.strip().strip("`\"' ")
                if item and len(item) <= 100 and item not in keywords:
                    keywords.append(item)
            return (keywords[: payload["keyword_top_n"]], "generated") if keywords else ([], "failed")
        except Exception as exc:  # noqa: BLE001 - optional enrichment must fail softly
            logger.warning("Q&A keyword extraction degraded (%s)", type(exc).__name__)
            return [], "failed"


class QAWritebackRuntime:
    """Existing provider/tokenizer/index adapters; no MCP or file-parser dependency."""

    async def extract_keywords(self, kb, doc, content, top_n):
        from api.db.joint_services.tenant_model_service import get_model_config_from_provider_instance, get_tenant_default_model_by_type
        from api.db.services.llm_service import LLMBundle
        from common.constants import LLMType
        from rag.prompts.generator import keyword_extraction

        def model():
            name = (doc.parser_config or {}).get("llm_id") or (kb.parser_config or {}).get("llm_id")
            config = get_model_config_from_provider_instance(kb.tenant_id, LLMType.CHAT.value, name) if name else get_tenant_default_model_by_type(kb.tenant_id, LLMType.CHAT.value)
            return LLMBundle(kb.tenant_id, config, lang=kb.language or "Chinese")

        chat = await asyncio.to_thread(model)
        try:
            return await keyword_extraction(chat, content, top_n)
        finally:
            await asyncio.to_thread(chat.close)

    async def embed(self, kb, doc, question):
        from api.db.joint_services.tenant_model_service import get_model_config_from_provider_instance
        from api.db.services.document_service import DocumentService
        from api.db.services.llm_service import LLMBundle
        from common.constants import LLMType
        from common.token_utils import num_tokens_from_string

        def encode():
            model_id = DocumentService.get_embd_id(doc.id)
            config = get_model_config_from_provider_instance(kb.tenant_id, LLMType.EMBEDDING.value, model_id)
            with LLMBundle(kb.tenant_id, config, lang=kb.language or "Chinese") as model:
                if num_tokens_from_string(question) > int(model.max_length * 0.95):
                    raise QAWritebackError(400, 101, "INVALID_ARGUMENT", "Question exceeds the configured embedding model's input limit.")
                vectors, tokens = model.encode([question])
                return list(map(float, vectors[0])), tokens

        return await asyncio.to_thread(encode)

    def build_chunk(self, kb, doc, payload, chunk_id, keywords, vector):
        from rag.nlp import rag_tokenizer

        now = datetime.now(UTC)
        tokens = rag_tokenizer.tokenize(payload["question"])
        return {
            "id": chunk_id,
            "kb_id": kb.id,
            "doc_id": doc.id,
            "docnm_kwd": doc.name,
            "content_with_weight": f"问题：{payload['question']}\t回答：{payload['answer']}",
            "content_ltks": tokens,
            "content_sm_ltks": rag_tokenizer.fine_grained_tokenize(tokens),
            "question_kwd": [payload["question"]],
            "question_tks": tokens,
            "important_kwd": keywords,
            "important_tks": rag_tokenizer.tokenize(" ".join(keywords)),
            "available_int": 1,
            "create_time": now.strftime("%Y-%m-%d %H:%M:%S"),
            "create_timestamp_flt": now.timestamp(),
            f"q_{len(vector)}_vec": vector,
        }

    def get_chunk(self, kb, chunk_id):
        from common import settings
        from rag.nlp.search import index_name

        index = index_name(kb.tenant_id)
        if not settings.docStoreConn.index_exist(index, kb.id):
            return None
        return settings.docStoreConn.get(chunk_id, index, [kb.id])

    def insert_chunk(self, kb, doc, chunk):
        from common import settings
        from rag.nlp.search import index_name

        index = index_name(kb.tenant_id)
        if not settings.docStoreConn.index_exist(index, kb.id):
            size = next(len(v) for key, v in chunk.items() if re.fullmatch(r"q_\d+_vec", key))
            settings.docStoreConn.create_idx(index, kb.id, size, doc.parser_id)
        errors = settings.docStoreConn.insert([chunk], index, kb.id)
        if errors:
            raise RuntimeError("Document store rejected the chunk")
