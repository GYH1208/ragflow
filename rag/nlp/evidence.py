from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from timeit import default_timer as timer
from typing import Literal

import numpy as np

from common.misc_utils import thread_pool_exec

_CITATION_PATTERN = re.compile(r"\[(?:ID:)?[0-9\u0660-\u0669\u06F0-\u06F9]+\]")
_LIST_PREFIX = re.compile(r"^\s*(?:[-*+]|\d+[.)、])\s*")
_SENTENCE_BOUNDARY = re.compile(r"(?<=[。！？!?；;])|\n+")
_MARKDOWN_ONLY = re.compile(r"^\s*(?:#{1,6}|[-*_`>|])+\s*$")
_META_ONLY = (
    re.compile(r"知识库.{0,12}(?:未找到|没有).{0,12}(?:图片|截图|流程图)"),
    re.compile(r"建议.{0,12}(?:联系|咨询).{0,12}(?:人事|管理员).{0,12}(?:截图|图片)"),
)

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class EvidenceChunk:
    chunk_id: str
    content: str
    image_id: str | None = None
    vector: list[float] | None = None


@dataclass(frozen=True)
class EvidenceSegment:
    index: int
    text: str


@dataclass(frozen=True)
class EvidenceMatch:
    segment_index: int
    chunk_id: str
    hybrid_score: float
    rerank_score: float


@dataclass(frozen=True)
class EvidenceResolution:
    used_chunk_ids: list[str]
    matches: list[EvidenceMatch]
    unmatched_segments: list[int]
    status: Literal["resolved", "no_match", "error"]
    duration_ms: float
    reason: str | None = None


@dataclass(frozen=True)
class EvidenceConfig:
    min_hybrid_score: float = 0.55
    min_rerank_score: float = 0.70
    min_score_margin: float = 0.08
    shortlist_size: int = 3
    timeout_seconds: float = 10.0


ChunkVectorLoader = Callable[
    [list[str], int],
    Awaitable[dict[str, list[float]]],
]
LexicalScorer = Callable[[str, list[str]], list[float] | np.ndarray]


def _clean_piece(piece: str) -> str:
    piece = _CITATION_PATTERN.sub("", piece)
    piece = _LIST_PREFIX.sub("", piece).strip()
    return re.sub(r"[ \t]+", " ", piece)


def _is_meta_only(piece: str) -> bool:
    return any(pattern.search(piece) for pattern in _META_ONLY)


def split_evidence_segments(question: str, answer: str) -> list[EvidenceSegment]:
    question_context = (question or "").strip()
    raw = [_clean_piece(piece) for piece in _SENTENCE_BOUNDARY.split(answer or "")]
    raw = [piece for piece in raw if piece and not _MARKDOWN_ONLY.fullmatch(piece) and not _is_meta_only(piece)]

    merged: list[str] = []
    pending_heading = ""
    for piece in raw:
        is_heading = len(piece) <= 12 and not re.search(r"[。！？!?；;：:]", piece)
        if is_heading:
            pending_heading = piece
            continue
        if pending_heading:
            piece = f"{pending_heading}：{piece}"
            pending_heading = ""
        if 2 <= len(piece) < 5 and question_context:
            piece = f"{question_context} {piece}"
        if len(piece) >= 5:
            merged.append(piece)
    return [EvidenceSegment(index=index, text=text) for index, text in enumerate(merged)]


def _usable_vector(vector: object, dim: int) -> bool:
    if not isinstance(vector, (list, tuple, np.ndarray)) or len(vector) != dim:
        return False
    try:
        return bool(np.any(np.asarray(vector, dtype=float)))
    except (TypeError, ValueError):
        return False


def _tokenize(text: str) -> list[str]:
    from rag.nlp import rag_tokenizer

    return [token for token in rag_tokenizer.tokenize(text or "").split() if token]


def _lexical_scores(
    query_tokens: list[str],
    document_tokens: list[list[str]],
) -> np.ndarray:
    query_set = set(query_tokens)
    if not query_set:
        return np.zeros(len(document_tokens), dtype=float)
    return np.asarray(
        [len(query_set.intersection(tokens)) / len(query_set) for tokens in document_tokens],
        dtype=float,
    )


def _cosine_scores(
    query_vector: np.ndarray,
    document_vectors: list[list[float]],
) -> np.ndarray:
    matrix = np.asarray(document_vectors, dtype=float)
    query_norm = float(np.linalg.norm(query_vector))
    document_norms = np.linalg.norm(matrix, axis=1)
    denominators = document_norms * query_norm
    scores = np.zeros(len(matrix), dtype=float)
    valid = denominators > 0
    scores[valid] = (matrix[valid] @ query_vector) / denominators[valid]
    return scores


def _empty_resolution(
    started_at: float,
    reason: str,
    unmatched_segments: list[int],
) -> EvidenceResolution:
    return EvidenceResolution(
        used_chunk_ids=[],
        matches=[],
        unmatched_segments=unmatched_segments,
        status="no_match",
        duration_ms=(timer() - started_at) * 1000,
        reason=reason,
    )


async def resolve_evidence(
    question: str,
    answer: str,
    chunks: list[EvidenceChunk],
    embedding_model,
    rerank_model,
    chunk_vector_loader: ChunkVectorLoader,
    vector_similarity_weight: float,
    config: EvidenceConfig | None = None,
    lexical_scorer: LexicalScorer | None = None,
) -> EvidenceResolution:
    started_at = timer()
    config = config or EvidenceConfig()
    segments = split_evidence_segments(question, answer)
    segment_indexes = [segment.index for segment in segments]
    if not segments or not chunks:
        return _empty_resolution(
            started_at,
            "no_segments_or_chunks",
            segment_indexes,
        )
    if embedding_model is None or rerank_model is None:
        return EvidenceResolution(
            used_chunk_ids=[],
            matches=[],
            unmatched_segments=segment_indexes,
            status="error",
            duration_ms=(timer() - started_at) * 1000,
            reason="model_unavailable",
        )

    try:
        segment_vectors, _ = await thread_pool_exec(
            embedding_model.encode,
            [segment.text for segment in segments],
        )
        segment_vectors = np.asarray(segment_vectors, dtype=float)
        if segment_vectors.ndim != 2 or segment_vectors.shape[0] != len(segments) or segment_vectors.shape[1] == 0:
            raise ValueError("embedding result shape does not match evidence segments")
        dim = int(segment_vectors.shape[1])

        missing_ids = list(dict.fromkeys(chunk.chunk_id for chunk in chunks if not _usable_vector(chunk.vector, dim)))
        loaded_vectors = await chunk_vector_loader(missing_ids, dim) if missing_ids else {}

        usable_chunks: list[EvidenceChunk] = []
        chunk_vectors: list[list[float]] = []
        for chunk in chunks:
            vector = chunk.vector if _usable_vector(chunk.vector, dim) else loaded_vectors.get(chunk.chunk_id)
            if not _usable_vector(vector, dim):
                continue
            usable_chunks.append(chunk)
            chunk_vectors.append(np.asarray(vector, dtype=float).tolist())
        if not usable_chunks:
            return _empty_resolution(
                started_at,
                "no_chunk_vectors",
                segment_indexes,
            )

        chunk_contents = [chunk.content for chunk in usable_chunks]
        chunk_tokens = None if lexical_scorer else [_tokenize(content) for content in chunk_contents]
        vector_weight = min(
            1.0,
            max(0.0, float(vector_similarity_weight)),
        )
        term_weight = 1.0 - vector_weight
        shortlists: list[tuple[EvidenceSegment, list[tuple[int, float]]]] = []
        for segment, segment_vector in zip(segments, segment_vectors):
            vector_scores = _cosine_scores(
                segment_vector,
                chunk_vectors,
            )
            term_scores = np.asarray(
                (
                    lexical_scorer(segment.text, chunk_contents)
                    if lexical_scorer
                    else _lexical_scores(
                        _tokenize(segment.text),
                        chunk_tokens or [],
                    )
                ),
                dtype=float,
            )
            if len(term_scores) != len(usable_chunks):
                raise ValueError("lexical scorer result shape does not match chunks")
            hybrid_scores = vector_scores * vector_weight + term_scores * term_weight
            hybrid_scores = np.asarray(hybrid_scores, dtype=float)
            order = np.argsort(hybrid_scores)[::-1]
            selected = [(int(index), float(hybrid_scores[index])) for index in order[: config.shortlist_size]]
            shortlists.append((segment, selected))

        async def rerank_one(
            segment: EvidenceSegment,
            selected: list[tuple[int, float]],
        ) -> np.ndarray:
            documents = [usable_chunks[index].content for index, _ in selected]
            scores, _ = await thread_pool_exec(
                rerank_model.similarity,
                segment.text,
                documents,
            )
            return np.asarray(scores, dtype=float)

        rerank_scores = await asyncio.gather(*[rerank_one(segment, selected) for segment, selected in shortlists])

        matches: list[EvidenceMatch] = []
        unmatched_segments: list[int] = []
        for (segment, selected), scores in zip(
            shortlists,
            rerank_scores,
        ):
            if len(scores) != len(selected):
                raise ValueError("reranker result shape does not match shortlist")
            ranked = sorted(
                (
                    (
                        chunk_index,
                        hybrid_score,
                        float(rerank_score),
                    )
                    for (chunk_index, hybrid_score), rerank_score in zip(selected, scores)
                ),
                key=lambda item: item[2],
                reverse=True,
            )
            qualified = [item for item in ranked if item[1] >= config.min_hybrid_score and item[2] >= config.min_rerank_score]
            if len(ranked) > 1 and ranked[0][2] - ranked[1][2] < config.min_score_margin:
                qualified = []
            if not qualified:
                unmatched_segments.append(segment.index)
                continue
            for chunk_index, hybrid_score, rerank_score in qualified:
                matches.append(
                    EvidenceMatch(
                        segment_index=segment.index,
                        chunk_id=usable_chunks[chunk_index].chunk_id,
                        hybrid_score=hybrid_score,
                        rerank_score=rerank_score,
                    )
                )

        used_chunk_ids = list(dict.fromkeys(match.chunk_id for match in matches))
        return EvidenceResolution(
            used_chunk_ids=used_chunk_ids,
            matches=matches,
            unmatched_segments=unmatched_segments,
            status="resolved" if used_chunk_ids else "no_match",
            duration_ms=(timer() - started_at) * 1000,
            reason=(None if used_chunk_ids else "below_confidence_threshold"),
        )
    except Exception as exc:
        LOGGER.warning(
            "evidence resolution failed: %s",
            exc,
            exc_info=True,
        )
        return EvidenceResolution(
            used_chunk_ids=[],
            matches=[],
            unmatched_segments=segment_indexes,
            status="error",
            duration_ms=(timer() - started_at) * 1000,
            reason=type(exc).__name__,
        )
