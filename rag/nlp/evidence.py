from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field
from timeit import default_timer as timer
from typing import Literal

import numpy as np

from common.misc_utils import thread_pool_exec

_CITATION_TOKEN = r"\[(?:ID:)?[0-9\u0660-\u0669\u06F0-\u06F9]+\]"
_CITATION_PATTERN = re.compile(
    r"\[(?:ID:)?([0-9\u0660-\u0669\u06F0-\u06F9]+)\]"
)
_THINK_TOKEN_PATTERN = re.compile(r"</?think>", re.IGNORECASE)
_EVIDENCE_SENTENCE_PATTERN = re.compile(
    rf".*?[。！？!?；;](?:\s*{_CITATION_TOKEN})*|.+$",
    re.DOTALL,
)
_LIST_PREFIX = re.compile(r"^\s*(?:[-*+]|\d+[.)、])\s*")
_MARKDOWN_ONLY = re.compile(r"^\s*(?:#{1,6}|[-*_`>|])+\s*$")
_META_ONLY = (
    re.compile(r"知识库.{0,12}(?:未找到|没有).{0,12}(?:图片|截图|流程图)"),
    re.compile(r"建议.{0,12}(?:联系|咨询).{0,12}(?:人事|管理员).{0,12}(?:截图|图片)"),
)


class EvidenceParseError(ValueError):
    pass


@dataclass(frozen=True)
class EvidenceChunk:
    chunk_id: str
    content: str
    image_id: str | None
    similarity: float
    vector_similarity: float
    term_similarity: float
    retrieval_rank: int


@dataclass(frozen=True)
class EvidenceUnit:
    index: int
    text: str
    citation_indexes: tuple[int, ...]


@dataclass(frozen=True)
class EvidenceMatch:
    segment_index: int
    chunk_id: str
    retrieval_score: float
    rerank_score: float
    rerank_margin: float | None


@dataclass(frozen=True)
class EvidenceDecision:
    unit_index: int
    cited_chunk_ids: list[str]
    candidate_chunk_ids: list[str]
    selected_chunk_id: str | None
    rerank_scores: list[tuple[str, float]]
    margin: float | None
    reason: str


@dataclass(frozen=True)
class EvidenceResolution:
    used_chunk_ids: list[str]
    matches: list[EvidenceMatch]
    unmatched_segments: list[int]
    status: Literal["resolved", "no_match", "error"]
    duration_ms: float
    reason: str | None = None
    decisions: list[EvidenceDecision] = field(default_factory=list)


@dataclass(frozen=True)
class EvidenceConfig:
    min_rerank_score: float = 0.75
    min_score_margin: float = 0.10
    shortlist_size: int = 3
    max_evidence_units: int = 2
    max_images: int = 2
    timeout_seconds: float = 0.9


def _clean_piece(piece: str) -> str:
    piece = _CITATION_PATTERN.sub("", piece)
    piece = _LIST_PREFIX.sub("", piece).strip()
    return re.sub(r"[ \t]+", " ", piece)


def _is_meta_only(piece: str) -> bool:
    return any(pattern.search(piece) for pattern in _META_ONLY)


def _strip_hidden_reasoning(answer: str) -> str:
    text = answer or ""
    depth = 0
    cursor = 0
    visible: list[str] = []
    for match in _THINK_TOKEN_PATTERN.finditer(text):
        token = match.group(0).lower()
        if token == "<think>":
            if depth != 0:
                raise EvidenceParseError("malformed_think_markup")
            visible.append(text[cursor : match.start()])
            depth = 1
        else:
            if depth != 1:
                raise EvidenceParseError("malformed_think_markup")
            depth = 0
        cursor = match.end()
    if depth != 0:
        raise EvidenceParseError("malformed_think_markup")
    if cursor:
        visible.append(text[cursor:])
        return "".join(visible)
    return text


def _citation_indexes(piece: str) -> tuple[int, ...]:
    indexes: list[int] = []
    for value in _CITATION_PATTERN.findall(piece):
        index = int(value)
        if index not in indexes:
            indexes.append(index)
    return tuple(indexes)


def split_evidence_units(answer: str) -> list[EvidenceUnit]:
    visible = _strip_hidden_reasoning(answer)
    raw_pieces = [
        match.group(0)
        for block in re.split(r"\n+", visible)
        for match in _EVIDENCE_SENTENCE_PATTERN.finditer(block)
    ]
    units: list[EvidenceUnit] = []
    seen_texts: set[str] = set()
    pending_heading = ""

    for raw_piece in raw_pieces:
        citations = _citation_indexes(raw_piece)
        piece = _clean_piece(raw_piece)
        if not piece or _MARKDOWN_ONLY.fullmatch(piece) or _is_meta_only(piece):
            continue

        is_heading = (
            not citations
            and len(piece) <= 12
            and not re.search(r"[。！？!?；;：:]", piece)
        )
        if is_heading:
            pending_heading = piece
            continue
        if pending_heading:
            piece = f"{pending_heading}：{piece}"
            pending_heading = ""
        if not citations or len(piece) < 5 or piece in seen_texts:
            continue

        seen_texts.add(piece)
        units.append(
            EvidenceUnit(
                index=len(units),
                text=piece,
                citation_indexes=citations,
            )
        )
    return units


def _finite_score(value: object) -> bool:
    try:
        return bool(np.isfinite(float(value)))
    except (TypeError, ValueError):
        return False


def _passes_hard_gates(
    chunk: EvidenceChunk,
    retrieval_similarity_threshold: float,
) -> bool:
    return (
        bool(chunk.chunk_id)
        and bool(chunk.content.strip())
        and bool(chunk.image_id)
        and _finite_score(chunk.similarity)
        and _finite_score(chunk.vector_similarity)
        and _finite_score(chunk.term_similarity)
        and chunk.similarity >= retrieval_similarity_threshold
    )


def build_unit_shortlist(
    unit: EvidenceUnit,
    chunks: list[EvidenceChunk],
    retrieval_similarity_threshold: float,
    shortlist_size: int,
) -> list[EvidenceChunk]:
    eligible = [
        chunk
        for chunk in chunks
        if _passes_hard_gates(
            chunk,
            retrieval_similarity_threshold,
        )
    ]
    eligible_ids = {id(chunk) for chunk in eligible}
    cited = [
        chunks[index]
        for index in unit.citation_indexes
        if 0 <= index < len(chunks)
        and id(chunks[index]) in eligible_ids
    ]
    ordered: list[EvidenceChunk] = []
    seen_chunk_ids: set[str] = set()
    for chunk in cited + sorted(
        eligible,
        key=lambda item: item.retrieval_rank,
    ):
        if chunk.chunk_id not in seen_chunk_ids:
            seen_chunk_ids.add(chunk.chunk_id)
            ordered.append(chunk)
        if len(ordered) == shortlist_size:
            break
    return ordered


def _empty_resolution(
    started_at: float,
    reason: str,
    unmatched_segments: list[int],
    decisions: list[EvidenceDecision] | None = None,
) -> EvidenceResolution:
    return EvidenceResolution(
        used_chunk_ids=[],
        matches=[],
        unmatched_segments=unmatched_segments,
        status="no_match",
        duration_ms=(timer() - started_at) * 1000,
        reason=reason,
        decisions=decisions or [],
    )


def _rerank_query(question: str, unit: EvidenceUnit) -> str:
    return (
        f"用户原始问题：\n{question.strip()}\n\n"
        f"回答中的相关回答点：\n{unit.text}"
    )


async def _rerank_unit(
    question: str,
    unit: EvidenceUnit,
    shortlist: list[EvidenceChunk],
    rerank_model,
) -> np.ndarray:
    scores, _ = await thread_pool_exec(
        rerank_model.similarity,
        _rerank_query(question, unit),
        [chunk.content for chunk in shortlist],
    )
    values = np.asarray(scores, dtype=float)
    if values.ndim != 1 or len(values) != len(shortlist):
        raise ValueError(
            "reranker result shape does not match shortlist"
        )
    if not np.all(np.isfinite(values)):
        raise ValueError("reranker returned non-finite scores")
    return values


async def resolve_evidence(
    question: str,
    answer: str,
    chunks: list[EvidenceChunk],
    rerank_model,
    retrieval_similarity_threshold: float,
    config: EvidenceConfig | None = None,
) -> EvidenceResolution:
    started_at = timer()
    config = config or EvidenceConfig()
    try:
        units = split_evidence_units(answer)
    except EvidenceParseError:
        return _empty_resolution(
            started_at,
            "malformed_think_markup",
            [],
        )

    pre_decisions: list[EvidenceDecision] = []
    pre_unmatched: list[int] = []
    work: list[
        tuple[EvidenceUnit, list[EvidenceChunk], list[str]]
    ] = []
    for unit in units:
        resolved_citations = [
            chunks[index]
            for index in unit.citation_indexes
            if 0 <= index < len(chunks)
        ]
        if not resolved_citations:
            pre_unmatched.append(unit.index)
            pre_decisions.append(
                EvidenceDecision(
                    unit_index=unit.index,
                    cited_chunk_ids=[],
                    candidate_chunk_ids=[],
                    selected_chunk_id=None,
                    rerank_scores=[],
                    margin=None,
                    reason="citation_not_found",
                )
            )
            continue

        shortlist = build_unit_shortlist(
            unit,
            chunks,
            retrieval_similarity_threshold,
            config.shortlist_size,
        )
        shortlist_ids = {
            chunk.chunk_id for chunk in shortlist
        }
        cited_chunk_ids = [
            chunk.chunk_id
            for chunk in resolved_citations
            if chunk.chunk_id in shortlist_ids
        ]
        if not cited_chunk_ids:
            pre_unmatched.append(unit.index)
            pre_decisions.append(
                EvidenceDecision(
                    unit_index=unit.index,
                    cited_chunk_ids=[
                        chunk.chunk_id
                        for chunk in resolved_citations
                    ],
                    candidate_chunk_ids=[
                        chunk.chunk_id for chunk in shortlist
                    ],
                    selected_chunk_id=None,
                    rerank_scores=[],
                    margin=None,
                    reason="no_image_candidates",
                )
            )
            continue

        work.append((unit, shortlist, cited_chunk_ids))
        if len(work) == config.max_evidence_units:
            break

    if not work:
        if pre_decisions:
            reason = pre_decisions[0].reason
            unmatched = pre_unmatched
        else:
            reason = "no_visible_evidence_units"
            unmatched = [unit.index for unit in units]
        return _empty_resolution(
            started_at,
            reason,
            unmatched,
            pre_decisions,
        )

    rerank_results = await asyncio.gather(
        *[
            _rerank_unit(
                question,
                unit,
                shortlist,
                rerank_model,
            )
            for unit, shortlist, _ in work
        ],
        return_exceptions=True,
    )

    used_chunk_ids: list[str] = []
    matches: list[EvidenceMatch] = []
    decisions: list[EvidenceDecision] = []
    unmatched_segments: list[int] = list(pre_unmatched)
    seen_image_ids: set[str] = set()
    saw_rerank_error = False

    for (unit, shortlist, cited_chunk_ids), result in zip(
        work,
        rerank_results,
    ):
        if isinstance(result, BaseException):
            saw_rerank_error = True
            unmatched_segments.append(unit.index)
            decisions.append(
                EvidenceDecision(
                    unit_index=unit.index,
                    cited_chunk_ids=cited_chunk_ids,
                    candidate_chunk_ids=[
                        chunk.chunk_id for chunk in shortlist
                    ],
                    selected_chunk_id=None,
                    rerank_scores=[],
                    margin=None,
                    reason="rerank_error",
                )
            )
            continue

        ranked = sorted(
            zip(shortlist, result),
            key=lambda item: float(item[1]),
            reverse=True,
        )
        winner, winner_score = ranked[0]
        margin = (
            float(winner_score) - float(ranked[1][1])
            if len(ranked) > 1
            else None
        )
        reason = "accepted"
        if winner.chunk_id not in cited_chunk_ids:
            reason = "cited_candidate_not_top1"
        elif float(winner_score) < config.min_rerank_score:
            reason = "below_rerank_threshold"
        elif (
            margin is not None
            and margin < config.min_score_margin
        ):
            reason = "below_score_margin"
        elif str(winner.image_id) in seen_image_ids:
            reason = "duplicate_image"

        selected_chunk_id = (
            winner.chunk_id if reason == "accepted" else None
        )
        decisions.append(
            EvidenceDecision(
                unit_index=unit.index,
                cited_chunk_ids=cited_chunk_ids,
                candidate_chunk_ids=[
                    chunk.chunk_id for chunk in shortlist
                ],
                selected_chunk_id=selected_chunk_id,
                rerank_scores=[
                    (chunk.chunk_id, float(score))
                    for chunk, score in ranked
                ],
                margin=margin,
                reason=reason,
            )
        )
        if reason != "accepted":
            unmatched_segments.append(unit.index)
            continue

        seen_image_ids.add(str(winner.image_id))
        used_chunk_ids.append(winner.chunk_id)
        matches.append(
            EvidenceMatch(
                segment_index=unit.index,
                chunk_id=winner.chunk_id,
                retrieval_score=winner.similarity,
                rerank_score=float(winner_score),
                rerank_margin=margin,
            )
        )
        if len(used_chunk_ids) == config.max_images:
            break

    all_decisions = sorted(
        pre_decisions + decisions,
        key=lambda decision: decision.unit_index,
    )
    if used_chunk_ids:
        status: Literal["resolved", "no_match", "error"] = (
            "resolved"
        )
        reason = None
    elif saw_rerank_error:
        status = "error"
        reason = "rerank_error"
    else:
        status = "no_match"
        reason = all_decisions[0].reason

    return EvidenceResolution(
        used_chunk_ids=used_chunk_ids,
        matches=matches,
        unmatched_segments=unmatched_segments,
        status=status,
        duration_ms=(timer() - started_at) * 1000,
        reason=reason,
        decisions=all_decisions,
    )
