import asyncio
import logging
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from api.db.services import evidence_service
from rag.nlp.evidence import (
    EvidenceConfig,
    EvidenceDecision,
    EvidenceResolution,
)


def _chunk(chunk_id="c1", content="证据正文", image_id="img-1"):
    return {
        "id": chunk_id,
        "content": content,
        "image_id": image_id,
        "similarity": 0.80,
        "vector_similarity": 0.70,
        "term_similarity": 0.90,
    }


@pytest.mark.asyncio
async def test_service_uses_only_reranker_and_preserves_reference_order(
    monkeypatch,
):
    reranker = Mock()
    reranker.close = Mock()
    captured = {}

    async def fake_resolve(**kwargs):
        captured.update(kwargs)
        return EvidenceResolution(["c2"], [], [], "resolved", 1.0)

    monkeypatch.setattr(
        evidence_service,
        "get_rerank_model",
        lambda dialog: reranker,
    )
    monkeypatch.setattr(evidence_service, "resolve_evidence", fake_resolve)

    dialog = SimpleNamespace(
        similarity_threshold=0.42,
        rerank_id="reranker-1",
    )
    chunks = [
        _chunk("c1", "第一条", "img-1"),
        {
            **_chunk("c2", "第二条", "img-2"),
            "similarity": 0.75,
            "vector_similarity": 0.65,
            "term_similarity": 0.85,
        },
    ]

    result = await evidence_service.EvidenceService.resolve_for_dialog(
        dialog,
        "问题",
        "回答。[ID:1]",
        chunks,
    )

    assert result.used_chunk_ids == ["c2"]
    assert [chunk.retrieval_rank for chunk in captured["chunks"]] == [0, 1]
    assert captured["chunks"][1].similarity == 0.75
    assert captured["retrieval_similarity_threshold"] == 0.42
    assert captured["rerank_model"] is reranker
    assert "embedding_model" not in captured
    assert "chunk_vector_loader" not in captured
    reranker.close.assert_called_once()


@pytest.mark.asyncio
async def test_service_timeout_is_fail_closed_and_closes_reranker(monkeypatch):
    reranker = Mock()
    reranker.close = Mock()

    async def never_finishes(**kwargs):
        await asyncio.sleep(1)

    monkeypatch.setattr(
        evidence_service,
        "get_rerank_model",
        lambda dialog: reranker,
    )
    monkeypatch.setattr(evidence_service, "resolve_evidence", never_finishes)

    result = await evidence_service.EvidenceService.resolve_for_dialog(
        SimpleNamespace(similarity_threshold=0.2, rerank_id="r1"),
        "问题",
        "回答。[ID:0]",
        [_chunk()],
        EvidenceConfig(timeout_seconds=0.01),
    )

    assert result.status == "error"
    assert result.reason == "rerank_timeout"
    assert result.used_chunk_ids == []
    reranker.close.assert_called_once()


@pytest.mark.asyncio
async def test_service_without_reranker_fails_closed(monkeypatch):
    monkeypatch.setattr(
        evidence_service,
        "get_rerank_model",
        lambda dialog: None,
    )

    result = await evidence_service.EvidenceService.resolve_for_dialog(
        SimpleNamespace(similarity_threshold=0.2, rerank_id=""),
        "问题",
        "回答。[ID:0]",
        [_chunk()],
    )

    assert result.status == "error"
    assert result.reason == "model_unavailable"


@pytest.mark.asyncio
async def test_service_log_contains_decisions_but_not_full_text(
    monkeypatch,
    caplog,
):
    reranker = Mock()
    reranker.close = Mock()
    decision = EvidenceDecision(
        unit_index=0,
        cited_chunk_ids=["c1"],
        candidate_chunk_ids=["c1", "c2"],
        selected_chunk_id="c1",
        rerank_scores=[("c1", 0.91), ("c2", 0.40)],
        margin=0.51,
        reason="accepted",
    )

    async def fake_resolve(**kwargs):
        return EvidenceResolution(
            ["c1"],
            [],
            [],
            "resolved",
            12.5,
            decisions=[decision],
        )

    monkeypatch.setattr(
        evidence_service,
        "get_rerank_model",
        lambda dialog: reranker,
    )
    monkeypatch.setattr(evidence_service, "resolve_evidence", fake_resolve)
    caplog.set_level(logging.INFO, logger=evidence_service.__name__)

    await evidence_service.EvidenceService.resolve_for_dialog(
        SimpleNamespace(
            id="dialog-1",
            similarity_threshold=0.2,
            rerank_id="r1",
        ),
        "不得写入日志的完整问题",
        "不得写入日志的完整回答。[ID:0]",
        [_chunk()],
    )

    assert "candidate_chunk_ids" in caplog.text
    assert "accepted" in caplog.text
    assert "12.5" in caplog.text
    assert "不得写入日志的完整问题" not in caplog.text
    assert "不得写入日志的完整回答" not in caplog.text
    assert "证据正文" not in caplog.text
