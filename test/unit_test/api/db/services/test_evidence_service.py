import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import numpy as np
import pytest

from api.db.services import evidence_service
from rag.nlp.evidence import EvidenceConfig, EvidenceResolution


@pytest.mark.asyncio
async def test_service_passes_stable_chunk_ids_and_dialog_weight(monkeypatch):
    embedding = Mock()
    reranker = Mock()
    kbs = [SimpleNamespace(tenant_id="tenant-a")]

    class FakeQueryer:
        @staticmethod
        def rmWWW(text):
            return text

        @staticmethod
        def token_similarity(query_tokens, document_tokens):
            return [0.25 for _ in document_tokens]

    retriever = SimpleNamespace(
        fetch_chunk_vectors=AsyncMock(return_value={"c1": [1.0, 0.0]}),
        qryr=FakeQueryer(),
    )
    captured = {}

    async def fake_resolve(**kwargs):
        captured.update(kwargs)
        await kwargs["chunk_vector_loader"](["c1"], 2)
        return EvidenceResolution(["c1"], [], [], "resolved", 1.0)

    monkeypatch.setattr(
        evidence_service,
        "get_retrieval_models",
        lambda dialog: (kbs, embedding, reranker),
    )
    monkeypatch.setattr(evidence_service.settings, "retriever", retriever)
    monkeypatch.setattr(evidence_service, "resolve_evidence", fake_resolve)

    dialog = SimpleNamespace(
        kb_ids=["kb-a"],
        vector_similarity_weight=0.65,
    )
    result = await evidence_service.EvidenceService.resolve_for_dialog(
        dialog,
        "问题",
        "回答",
        [{"id": "c1", "content": "证据", "image_id": "img-1"}],
    )

    assert result.used_chunk_ids == ["c1"]
    assert captured["chunks"][0].chunk_id == "c1"
    assert captured["vector_similarity_weight"] == 0.65
    assert captured["lexical_scorer"](
        "考勤异常",
        ["考勤异常处理流程"],
    ) == [0.25]
    retriever.fetch_chunk_vectors.assert_awaited_once_with(
        ["c1"],
        ["tenant-a"],
        ["kb-a"],
        2,
    )


@pytest.mark.asyncio
async def test_service_timeout_returns_error_and_closes_models(monkeypatch):
    embedding = Mock()
    reranker = Mock()
    embedding.close = Mock()
    reranker.close = Mock()

    async def never_finishes(**kwargs):
        await asyncio.sleep(1)

    monkeypatch.setattr(
        evidence_service,
        "get_retrieval_models",
        lambda dialog: (
            [SimpleNamespace(tenant_id="tenant-a")],
            embedding,
            reranker,
        ),
    )
    monkeypatch.setattr(
        evidence_service,
        "resolve_evidence",
        never_finishes,
    )

    result = await evidence_service.EvidenceService.resolve_for_dialog(
        SimpleNamespace(
            kb_ids=["kb-a"],
            vector_similarity_weight=0.7,
        ),
        "问题",
        "回答",
        [{"id": "c1", "content": "证据", "image_id": "img"}],
        EvidenceConfig(timeout_seconds=0.01),
    )

    assert result.status == "error"
    assert result.reason == "timeout"
    embedding.close.assert_called_once()
    reranker.close.assert_called_once()


@pytest.mark.asyncio
async def test_service_without_reranker_does_not_embed(monkeypatch):
    embedding = Mock()
    embedding.close = Mock()
    monkeypatch.setattr(
        evidence_service,
        "get_retrieval_models",
        lambda dialog: (
            [SimpleNamespace(tenant_id="tenant-a")],
            embedding,
            None,
        ),
    )

    result = await evidence_service.EvidenceService.resolve_for_dialog(
        SimpleNamespace(
            kb_ids=["kb-a"],
            vector_similarity_weight=0.7,
        ),
        "问题",
        "回答",
        [{"id": "c1", "content": "证据", "image_id": "img"}],
    )

    assert result.status == "error"
    assert result.reason == "model_unavailable"
    embedding.encode.assert_not_called()
    embedding.close.assert_called_once()


@pytest.mark.asyncio
async def test_service_vector_fetch_failure_never_returns_chunks(monkeypatch):
    embedding = Mock()
    embedding.encode.return_value = (
        np.asarray([[1.0, 0.0]]),
        0,
    )
    reranker = Mock()
    reranker.similarity.return_value = (
        np.asarray([0.95]),
        0,
    )
    retriever = SimpleNamespace(fetch_chunk_vectors=AsyncMock(side_effect=RuntimeError("store down")))
    monkeypatch.setattr(
        evidence_service,
        "get_retrieval_models",
        lambda dialog: (
            [SimpleNamespace(tenant_id="tenant-a")],
            embedding,
            reranker,
        ),
    )
    monkeypatch.setattr(evidence_service.settings, "retriever", retriever)

    result = await evidence_service.EvidenceService.resolve_for_dialog(
        SimpleNamespace(
            kb_ids=["kb-a"],
            vector_similarity_weight=0.7,
        ),
        "问题",
        "回答事实。",
        [
            {
                "id": "c1",
                "content": "回答事实证据",
                "image_id": "img",
            }
        ],
    )

    assert result.status == "error"
    assert result.used_chunk_ids == []
