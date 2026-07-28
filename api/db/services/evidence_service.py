from __future__ import annotations

import asyncio
import logging
from timeit import default_timer as timer

from api.db.services.dialog_service import get_retrieval_models
from common import settings
from rag.nlp.evidence import (
    EvidenceChunk,
    EvidenceConfig,
    EvidenceResolution,
    resolve_evidence,
)

LOGGER = logging.getLogger(__name__)


def _error_resolution(
    started_at: float,
    reason: str,
) -> EvidenceResolution:
    return EvidenceResolution(
        used_chunk_ids=[],
        matches=[],
        unmatched_segments=[],
        status="error",
        duration_ms=(timer() - started_at) * 1000,
        reason=reason,
    )


class EvidenceService:
    @classmethod
    async def resolve_for_dialog(
        cls,
        dialog,
        question: str,
        answer: str,
        chunks: list[dict],
        config: EvidenceConfig | None = None,
    ) -> EvidenceResolution:
        started_at = timer()
        config = config or EvidenceConfig()
        embedding_model = None
        rerank_model = None
        try:
            evidence_chunks = [
                EvidenceChunk(
                    chunk_id=str(chunk.get("id") or ""),
                    content=str(chunk.get("content") or ""),
                    image_id=(str(chunk.get("image_id") or "") or None),
                )
                for chunk in chunks
                if isinstance(chunk, dict) and chunk.get("id") and chunk.get("content")
            ]
            if not evidence_chunks:
                return EvidenceResolution(
                    used_chunk_ids=[],
                    matches=[],
                    unmatched_segments=[],
                    status="no_match",
                    duration_ms=(timer() - started_at) * 1000,
                    reason="no_valid_chunks",
                )

            kbs, embedding_model, rerank_model = get_retrieval_models(dialog)
            if embedding_model is None or rerank_model is None:
                return _error_resolution(
                    started_at,
                    "model_unavailable",
                )

            tenant_ids = list(dict.fromkeys(kb.tenant_id for kb in kbs))

            async def load_vectors(
                chunk_ids: list[str],
                dim: int,
            ) -> dict[str, list[float]]:
                return await settings.retriever.fetch_chunk_vectors(
                    chunk_ids,
                    tenant_ids,
                    dialog.kb_ids,
                    dim,
                )

            def score_lexical(
                segment_text: str,
                documents: list[str],
            ):
                from rag.nlp import rag_tokenizer

                queryer = settings.retriever.qryr
                query_tokens = rag_tokenizer.tokenize(queryer.rmWWW(segment_text)).split()
                document_tokens = [rag_tokenizer.tokenize(queryer.rmWWW(document)).split() for document in documents]
                return queryer.token_similarity(
                    query_tokens,
                    document_tokens,
                )

            result = await asyncio.wait_for(
                resolve_evidence(
                    question=question,
                    answer=answer,
                    chunks=evidence_chunks,
                    embedding_model=embedding_model,
                    rerank_model=rerank_model,
                    chunk_vector_loader=load_vectors,
                    vector_similarity_weight=(dialog.vector_similarity_weight),
                    config=config,
                    lexical_scorer=score_lexical,
                ),
                timeout=config.timeout_seconds,
            )
            LOGGER.info(
                "evidence resolved status=%s candidates=%d used_chunk_ids=%s matches=%s unmatched_segments=%s duration_ms=%.1f",
                result.status,
                len(evidence_chunks),
                result.used_chunk_ids,
                [
                    {
                        "segment_index": match.segment_index,
                        "chunk_id": match.chunk_id,
                        "hybrid_score": round(
                            match.hybrid_score,
                            4,
                        ),
                        "rerank_score": round(
                            match.rerank_score,
                            4,
                        ),
                    }
                    for match in result.matches
                ],
                result.unmatched_segments,
                result.duration_ms,
            )
            return result
        except asyncio.TimeoutError:  # noqa: UP041 - distinct from TimeoutError on Python 3.10
            LOGGER.warning(
                "evidence resolution timed out after %.1fs",
                config.timeout_seconds,
            )
            return _error_resolution(started_at, "timeout")
        except Exception as exc:
            LOGGER.warning(
                "evidence service failed: %s",
                exc,
                exc_info=True,
            )
            return _error_resolution(
                started_at,
                type(exc).__name__,
            )
        finally:
            for model in (embedding_model, rerank_model):
                if model is not None and hasattr(model, "close"):
                    model.close()
