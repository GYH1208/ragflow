import dataclasses

import numpy as np
import pytest

from rag.nlp import evidence as evidence_module
from rag.nlp.evidence import (
    EvidenceConfig,
    RerankBusyError,
    resolve_evidence,
)


class FakeReranker:
    def __init__(self, scores):
        self.scores = scores
        self.calls = []

    async def __call__(self, query, documents):
        self.calls.append((query, list(documents)))
        return np.asarray(
            [self.scores[(query, document)] for document in documents]
        ), 0


def _candidate_chunk(
    chunk_id,
    image_id,
    rank,
    similarity=0.8,
    vector_similarity=0.8,
    term_similarity=0.8,
):
    return evidence_module.EvidenceChunk(
        chunk_id=chunk_id,
        content=f"{chunk_id} 内容",
        image_id=image_id,
        similarity=similarity,
        vector_similarity=vector_similarity,
        term_similarity=term_similarity,
        retrieval_rank=rank,
    )


def _rerank_query(question, unit_text):
    return (
        f"用户原始问题：\n{question}\n\n"
        f"回答中的相关回答点：\n{unit_text}"
    )


def test_split_evidence_units_removes_think_and_preserves_citations():
    answer = (
        "<think>内部分析引用 [ID:9]</think>"
        "1. 查看审批进度。[ID:0]\n"
        "2. 设置审批代理人。[1]"
    )

    units = evidence_module.split_evidence_units(answer)

    assert [(unit.index, unit.text, unit.citation_indexes) for unit in units] == [
        (0, "查看审批进度。", (0,)),
        (1, "设置审批代理人。", (1,)),
    ]


@pytest.mark.parametrize(
    "answer",
    [
        "<think>未闭合的内部分析",
        "正文</think>",
        "<think>第一层<think>嵌套</think></think>正文。[ID:0]",
    ],
)
def test_split_evidence_units_rejects_malformed_think_markup(answer):
    with pytest.raises(ValueError, match="malformed_think_markup"):
        evidence_module.split_evidence_units(answer)


def test_split_evidence_units_ignores_citation_only_and_duplicate_units():
    answer = (
        "[ID:0]\n"
        "查看审批进度。[ID:0]\n"
        "查看审批进度。[ID:0]\n"
        "知识库未找到截图。[ID:1]"
    )

    units = evidence_module.split_evidence_units(answer)

    assert [(unit.text, unit.citation_indexes) for unit in units] == [
        ("查看审批进度。", (0,)),
    ]


def test_split_evidence_units_reads_non_ascii_digits():
    units = evidence_module.split_evidence_units(
        "查看审批进度。[ID:٢]\n设置代理人。[ID:۱]"
    )

    assert [unit.citation_indexes for unit in units] == [(2,), (1,)]


def test_shortlist_keeps_cited_image_then_adds_top_ranked_competitors():
    chunks = [
        _candidate_chunk("c0", "img0", 0),
        _candidate_chunk("c1", "img1", 1),
        _candidate_chunk("c2", "img2", 2),
        _candidate_chunk("c3", "img3", 3),
    ]
    unit = evidence_module.EvidenceUnit(0, "回答点", (2,))

    shortlist = evidence_module.build_unit_shortlist(unit, chunks, 0.2, 3)

    assert [chunk.chunk_id for chunk in shortlist] == ["c2", "c0", "c1"]


def test_shortlist_rejects_chunks_outside_hard_gates():
    chunks = [
        _candidate_chunk("no-image", "", 0),
        _candidate_chunk("low", "img-low", 1, similarity=0.1),
        _candidate_chunk("nan", "img-nan", 2, vector_similarity=float("nan")),
        _candidate_chunk("valid", "img-valid", 3),
    ]
    unit = evidence_module.EvidenceUnit(0, "回答点", (0, 1, 2, 3))

    shortlist = evidence_module.build_unit_shortlist(unit, chunks, 0.2, 3)

    assert [chunk.chunk_id for chunk in shortlist] == ["valid"]


def test_shortlist_ignores_out_of_range_citation_without_substitution():
    chunks = [_candidate_chunk("competitor", "img", 0)]
    unit = evidence_module.EvidenceUnit(0, "回答点", (9,))

    shortlist = evidence_module.build_unit_shortlist(unit, chunks, 0.2, 3)

    assert [chunk.chunk_id for chunk in shortlist] == ["competitor"]
    assert unit.citation_indexes == (9,)


def test_evidence_config_uses_precision_first_defaults():
    config = EvidenceConfig()

    assert config.max_evidence_units == 2
    assert config.shortlist_size == 3
    assert config.max_images == 2
    assert config.min_rerank_score == 0.75
    assert config.min_score_margin == 0.10
    assert config.timeout_seconds == 0.9


@pytest.mark.asyncio
async def test_resolver_rejects_wrong_citation_when_uncited_competitor_wins():
    question = "怎么查看报销申请的进度？"
    answer = "进入审批页面查看当前进度。[ID:1]"
    chunks = [
        dataclasses.replace(
            _candidate_chunk("approval", "approval-image", 0),
            content="进入报销审批页面，查看审批进度和当前节点",
        ),
        dataclasses.replace(
            _candidate_chunk("proxy", "proxy-image", 1),
            content="设置我的代理人，代理报销或审批",
        ),
    ]
    query = _rerank_query(question, "进入审批页面查看当前进度。")
    reranker = FakeReranker(
        {
            (query, chunks[1].content): 0.62,
            (query, chunks[0].content): 0.80,
        }
    )

    result = await resolve_evidence(
        question=question,
        answer=answer,
        chunks=chunks,
        rerank_similarity=reranker,
        retrieval_similarity_threshold=0.2,
    )

    assert result.status == "no_match"
    assert result.used_chunk_ids == []
    assert result.decisions[0].reason == "cited_candidate_not_top1"


@pytest.mark.asyncio
async def test_resolver_rejects_top1_inside_margin():
    question = "怎么处理审批？"
    answer = "打开审批记录处理。[ID:0]"
    chunks = [
        dataclasses.replace(
            _candidate_chunk("a", "img-a", 0),
            content="打开审批记录",
        ),
        dataclasses.replace(
            _candidate_chunk("b", "img-b", 1),
            content="打开审批流程",
        ),
    ]
    query = _rerank_query(question, "打开审批记录处理。")
    reranker = FakeReranker(
        {
            (query, chunks[0].content): 0.86,
            (query, chunks[1].content): 0.80,
        }
    )

    result = await resolve_evidence(question, answer, chunks, reranker, 0.2)

    assert result.used_chunk_ids == []
    assert result.decisions[0].reason == "below_score_margin"


@pytest.mark.asyncio
async def test_two_distinct_units_can_select_two_images_in_answer_order():
    question = "怎么查审批进度，怎么设置代理人？"
    answer = "查看审批记录。[ID:0]\n设置我的代理人。[ID:1]"
    chunks = [
        dataclasses.replace(
            _candidate_chunk("approval", "img-approval", 0),
            content="查看审批进度",
        ),
        dataclasses.replace(
            _candidate_chunk("proxy", "img-proxy", 1),
            content="设置我的代理人",
        ),
    ]
    first_query = _rerank_query(question, "查看审批记录。")
    second_query = _rerank_query(question, "设置我的代理人。")
    reranker = FakeReranker(
        {
            (first_query, chunks[0].content): 0.93,
            (first_query, chunks[1].content): 0.30,
            (second_query, chunks[1].content): 0.94,
            (second_query, chunks[0].content): 0.25,
        }
    )

    result = await resolve_evidence(question, answer, chunks, reranker, 0.2)

    assert result.used_chunk_ids == ["approval", "proxy"]
    assert [decision.reason for decision in result.decisions] == [
        "accepted",
        "accepted",
    ]


@pytest.mark.asyncio
async def test_one_unit_never_selects_two_images():
    question = "怎么查审批进度？"
    answer = "查看审批记录和当前节点。[ID:0][ID:1]"
    chunks = [
        dataclasses.replace(
            _candidate_chunk("a", "img-a", 0),
            content="查看审批记录",
        ),
        dataclasses.replace(
            _candidate_chunk("b", "img-b", 1),
            content="查看审批当前节点",
        ),
    ]
    query = _rerank_query(question, "查看审批记录和当前节点。")
    reranker = FakeReranker(
        {
            (query, chunks[0].content): 0.92,
            (query, chunks[1].content): 0.70,
        }
    )

    result = await resolve_evidence(question, answer, chunks, reranker, 0.2)

    assert result.used_chunk_ids == ["a"]


@pytest.mark.asyncio
async def test_single_candidate_still_requires_absolute_threshold():
    question = "怎么查审批进度？"
    answer = "查看审批记录。[ID:0]"
    chunk = dataclasses.replace(
        _candidate_chunk("approval", "img-approval", 0),
        content="查看审批记录",
    )
    query = _rerank_query(question, "查看审批记录。")
    reranker = FakeReranker({(query, chunk.content): 0.74})

    result = await resolve_evidence(
        question,
        answer,
        [chunk],
        reranker,
        0.2,
    )

    assert result.used_chunk_ids == []
    assert result.decisions[0].margin is None
    assert result.decisions[0].reason == "below_rerank_threshold"


@pytest.mark.asyncio
async def test_two_units_with_same_image_id_are_deduplicated():
    question = "怎么查进度和节点？"
    answer = "查看审批进度。[ID:0]\n查看审批节点。[ID:1]"
    chunks = [
        dataclasses.replace(
            _candidate_chunk("progress", "shared-image", 0),
            content="查看审批进度",
        ),
        dataclasses.replace(
            _candidate_chunk("node", "shared-image", 1),
            content="查看审批节点",
        ),
    ]
    first_query = _rerank_query(question, "查看审批进度。")
    second_query = _rerank_query(question, "查看审批节点。")
    reranker = FakeReranker(
        {
            (first_query, chunks[0].content): 0.95,
            (first_query, chunks[1].content): 0.40,
            (second_query, chunks[0].content): 0.30,
            (second_query, chunks[1].content): 0.94,
        }
    )

    result = await resolve_evidence(question, answer, chunks, reranker, 0.2)

    assert result.used_chunk_ids == ["progress"]
    assert [decision.reason for decision in result.decisions] == [
        "accepted",
        "duplicate_image",
    ]


@pytest.mark.asyncio
async def test_non_finite_rerank_output_is_fail_closed():
    class NonFiniteReranker:
        async def __call__(self, query, documents):
            return np.asarray([float("nan")]), 0

    result = await resolve_evidence(
        "怎么查审批？",
        "查看审批。[ID:0]",
        [_candidate_chunk("approval", "img", 0)],
        NonFiniteReranker(),
        0.2,
    )

    assert result.status == "error"
    assert result.used_chunk_ids == []
    assert result.decisions[0].reason == "rerank_error"


@pytest.mark.asyncio
async def test_busy_reranker_is_fail_closed_with_stable_reason():
    async def busy_similarity(query, documents):
        raise RerankBusyError("capacity detail")

    result = await resolve_evidence(
        "怎么查审批？",
        "查看审批。[ID:0]",
        [_candidate_chunk("approval", "img", 0)],
        busy_similarity,
        0.2,
    )

    assert result.status == "error"
    assert result.used_chunk_ids == []
    assert result.reason == "rerank_busy"
    assert result.decisions[0].reason == "rerank_busy"


@pytest.mark.asyncio
async def test_missing_citation_does_not_substitute_top_retrieval():
    result = await resolve_evidence(
        "怎么查审批？",
        "查看审批。[ID:9]",
        [_candidate_chunk("approval", "img", 0)],
        FakeReranker({}),
        0.2,
    )

    assert result.status == "no_match"
    assert result.used_chunk_ids == []
    assert result.reason == "citation_not_found"


@pytest.mark.asyncio
async def test_malformed_hidden_reasoning_fails_closed():
    result = await resolve_evidence(
        "怎么查审批？",
        "<think>未闭合",
        [_candidate_chunk("approval", "img", 0)],
        FakeReranker({}),
        0.2,
    )

    assert result.status == "no_match"
    assert result.reason == "malformed_think_markup"


@pytest.mark.asyncio
async def test_approval_progress_never_sends_wrong_proxy_citation():
    question = "怎么查看报销申请的进度？"
    answer = "打开企业微信审批页面查看当前审批进度。[ID:1]"
    chunks = [
        evidence_module.EvidenceChunk(
            chunk_id="eab6502820610ded",
            content="进入报销审批页面，查看审批进度、当前审批人和处理节点",
            image_id="approval-progress-image",
            similarity=0.82,
            vector_similarity=0.79,
            term_similarity=0.86,
            retrieval_rank=0,
        ),
        evidence_module.EvidenceChunk(
            chunk_id="dbb4bfdaf5619fa5",
            content="打开企业微信工作台，设置我的代理人，代理报销或审批",
            image_id="my-proxy-image",
            similarity=0.76,
            vector_similarity=0.74,
            term_similarity=0.80,
            retrieval_rank=1,
        ),
    ]
    query = _rerank_query(
        question,
        "打开企业微信审批页面查看当前审批进度。",
    )
    reranker = FakeReranker(
        {
            (query, chunks[1].content): 0.6262,
            (query, chunks[0].content): 0.7986,
        }
    )

    result = await resolve_evidence(
        question,
        answer,
        chunks,
        reranker,
        0.2,
    )

    assert result.status == "no_match"
    assert result.used_chunk_ids == []
    assert result.decisions[0].reason == "cited_candidate_not_top1"


@pytest.mark.asyncio
async def test_failed_second_unit_does_not_suppress_confirmed_first_unit():
    question = "怎么查审批进度，怎么设置代理人？"
    answer = "查看审批记录。[ID:0]\n设置代理人。[ID:1]"
    chunks = [
        dataclasses.replace(
            _candidate_chunk("approval", "img-approval", 0),
            content="查看审批进度",
        ),
        dataclasses.replace(
            _candidate_chunk("proxy", "img-proxy", 1),
            content="设置审批代理",
        ),
        dataclasses.replace(
            _candidate_chunk("other", "img-other", 2),
            content="审批代理说明",
        ),
    ]
    first_query = _rerank_query(question, "查看审批记录。")
    second_query = _rerank_query(question, "设置代理人。")
    reranker = FakeReranker(
        {
            (first_query, chunks[0].content): 0.95,
            (first_query, chunks[1].content): 0.40,
            (first_query, chunks[2].content): 0.30,
            (second_query, chunks[1].content): 0.82,
            (second_query, chunks[0].content): 0.20,
            (second_query, chunks[2].content): 0.78,
        }
    )

    result = await resolve_evidence(
        question,
        answer,
        chunks,
        reranker,
        0.2,
    )

    assert result.used_chunk_ids == ["approval"]
    assert [decision.reason for decision in result.decisions] == [
        "accepted",
        "below_score_margin",
    ]
