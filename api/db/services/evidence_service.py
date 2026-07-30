from __future__ import annotations

import asyncio
import logging
from timeit import default_timer as timer

from api.db.services.dialog_service import get_rerank_model
from api.db.services.evidence_rerank_executor import EvidenceRerankLease
from rag.nlp.evidence import (
    EvidenceChunk,
    EvidenceConfig,
    EvidenceResolution,
    RerankBusyError,
    resolve_evidence,
)

LOGGER = logging.getLogger(__name__)


def _score(value: object) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float("nan")


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
        deadline = started_at + config.timeout_seconds
        rerank_lease = None
        try:
            evidence_chunks = [
                EvidenceChunk(
                    chunk_id=str(chunk.get("id") or ""),
                    content=str(chunk.get("content") or ""),
                    image_id=(
                        str(chunk.get("image_id") or "") or None
                    ),
                    similarity=_score(chunk.get("similarity")),
                    vector_similarity=_score(
                        chunk.get("vector_similarity")
                    ),
                    term_similarity=_score(
                        chunk.get("term_similarity")
                    ),
                    retrieval_rank=index,
                )
                for index, chunk in enumerate(chunks)
                if isinstance(chunk, dict)
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

            rerank_model = get_rerank_model(dialog)
            if rerank_model is None:
                return _error_resolution(
                    started_at,
                    "model_unavailable",
                )
            rerank_lease = EvidenceRerankLease(rerank_model)

            remaining_seconds = deadline - timer()
            if remaining_seconds <= 0:
                return _error_resolution(
                    started_at,
                    "rerank_timeout",
                )

            result = await asyncio.wait_for(
                resolve_evidence(
                    question=question,
                    answer=answer,
                    chunks=evidence_chunks,
                    rerank_similarity=rerank_lease.similarity,
                    retrieval_similarity_threshold=float(
                        dialog.similarity_threshold
                    ),
                    config=config,
                ),
                timeout=remaining_seconds,
            )
            score_by_id = {
                chunk.chunk_id: {
                    "similarity": round(chunk.similarity, 4),
                    "vector_similarity": round(
                        chunk.vector_similarity,
                        4,
                    ),
                    "term_similarity": round(
                        chunk.term_similarity,
                        4,
                    ),
                    "retrieval_rank": chunk.retrieval_rank,
                }
                for chunk in evidence_chunks
            }
            LOGGER.info(
                "evidence resolved dialog_id=%s status=%s "
                "candidates=%d used_chunk_ids=%s decisions=%s "
                "duration_ms=%.1f",
                getattr(dialog, "id", ""),
                result.status,
                len(evidence_chunks),
                result.used_chunk_ids,
                [
                    {
                        "unit_index": decision.unit_index,
                        "cited_chunk_ids": (
                            decision.cited_chunk_ids
                        ),
                        "candidate_chunk_ids": (
                            decision.candidate_chunk_ids
                        ),
                        "original_scores": {
                            chunk_id: score_by_id.get(chunk_id)
                            for chunk_id in (
                                decision.candidate_chunk_ids
                            )
                        },
                        "selected_chunk_id": (
                            decision.selected_chunk_id
                        ),
                        "rerank_scores": [
                            (chunk_id, round(score, 4))
                            for chunk_id, score in (
                                decision.rerank_scores
                            )
                        ],
                        "margin": (
                            round(decision.margin, 4)
                            if decision.margin is not None
                            else None
                        ),
                        "reason": decision.reason,
                    }
                    for decision in result.decisions
                ],
                result.duration_ms,
            )
            return result
        except asyncio.TimeoutError:  # noqa: UP041
            LOGGER.warning(
                "evidence resolution timed out "
                "reason=rerank_timeout timeout_seconds=%.3f",
                config.timeout_seconds,
            )
            return _error_resolution(
                started_at,
                "rerank_timeout",
            )
        except RerankBusyError:
            LOGGER.warning(
                "evidence reranker unavailable reason=rerank_busy"
            )
            return _error_resolution(
                started_at,
                "rerank_busy",
            )
        except Exception as exc:  # noqa: BLE001
            LOGGER.warning(
                "evidence service failed error_type=%s",
                type(exc).__name__,
            )
            return _error_resolution(
                started_at,
                "rerank_error",
            )
        finally:
            if rerank_lease is not None:
                rerank_lease.seal()
