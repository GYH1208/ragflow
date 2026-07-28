import numpy as np
import pytest

from api.channels.bootstrap import _images_for_used_chunks
from api.channels.core.base import OutgoingImage
from rag.nlp.evidence import (
    EvidenceChunk,
    EvidenceConfig,
    resolve_evidence,
    split_evidence_segments,
)


class FakeEmbedding:
    def __init__(self, vectors):
        self.vectors = vectors
        self.calls = []

    def encode(self, texts):
        self.calls.append(list(texts))
        return np.asarray([self.vectors[text] for text in texts]), 0


class FakeReranker:
    def __init__(self, scores):
        self.scores = scores
        self.calls = []

    def similarity(self, query, documents):
        self.calls.append((query, list(documents)))
        return np.asarray([self.scores[(query, document)] for document in documents]), 0


@pytest.mark.parametrize(
    ("question", "answer", "expected_segments"),
    [
        (
            "忘记打卡了怎么办",
            "进入考勤日历，点击异常记录后提交补签。",
            ["进入考勤日历，点击异常记录后提交补签。"],
        ),
        (
            "补卡路径是什么",
            "企业微信 → 工作台 → 半岛 EHR 系统 → 考勤日历。",
            ["企业微信 → 工作台 → 半岛 EHR 系统 → 考勤日历。"],
        ),
        (
            "有没有处理考勤异常的流程图",
            "知识库未找到流程图。处理入口位于考勤日历的异常记录。",
            ["处理入口位于考勤日历的异常记录。"],
        ),
        (
            "考勤异常在哪里处理",
            "在考勤日历中点击异常记录即可处理。",
            ["在考勤日历中点击异常记录即可处理。"],
        ),
    ],
)
def test_approved_queries_produce_only_business_fact_segments(
    question,
    answer,
    expected_segments,
):
    assert [segment.text for segment in split_evidence_segments(question, answer)] == expected_segments


def test_split_segments_removes_display_citations_from_copy_only():
    answer = "处理路径：进入考勤日历。[ID:2]\n点击异常记录后补签。[0]"

    segments = split_evidence_segments("考勤异常怎么处理？", answer)

    assert [segment.text for segment in segments] == [
        "处理路径：进入考勤日历。",
        "点击异常记录后补签。",
    ]
    assert answer.endswith("[0]")


def test_split_segments_ignores_knowledge_availability_but_keeps_business_negation():
    answer = "知识库未找到流程图，建议联系人事获取截图。\n请假期间不存在迟到情况。\n迟到记录不能通过请假自动补卡。"

    segments = split_evidence_segments("请假后还算迟到吗？", answer)

    assert [segment.text for segment in segments] == [
        "请假期间不存在迟到情况。",
        "迟到记录不能通过请假自动补卡。",
    ]


def test_split_segments_merges_short_heading_with_following_fact():
    segments = split_evidence_segments(
        "怎么补卡？",
        "操作步骤\n1. 打开工作台并进入考勤日历。\n2. 点击异常记录后提交补签。",
    )

    assert [segment.text for segment in segments] == [
        "操作步骤：打开工作台并进入考勤日历。",
        "点击异常记录后提交补签。",
    ]
    assert [segment.index for segment in segments] == [0, 1]


def test_split_segments_uses_question_only_to_contextualize_short_fact():
    segments = split_evidence_segments("忘记打卡怎么办？", "需补签。")

    assert [segment.text for segment in segments] == [
        "忘记打卡怎么办？ 需补签。",
    ]


@pytest.mark.asyncio
async def test_resolver_reuses_vectors_and_reranker_selects_non_first_candidate():
    answer = "进入考勤日历后点击异常记录。"
    chunks = [
        EvidenceChunk("c-unrelated", "考勤统计报表说明", "img-wrong"),
        EvidenceChunk(
            "c-correct",
            "工作台进入考勤日历，点击异常记录补签",
            "img-right",
        ),
    ]
    embedding = FakeEmbedding({answer: [1.0, 0.0]})
    reranker = FakeReranker(
        {
            (answer, chunks[0].content): 0.30,
            (answer, chunks[1].content): 0.92,
        }
    )
    loader_calls = []

    async def load_vectors(chunk_ids, dim):
        loader_calls.append((chunk_ids, dim))
        return {
            "c-unrelated": [0.95, 0.05],
            "c-correct": [0.80, 0.20],
        }

    result = await resolve_evidence(
        "考勤异常怎么处理？",
        answer,
        chunks,
        embedding,
        reranker,
        load_vectors,
        vector_similarity_weight=0.7,
        config=EvidenceConfig(min_hybrid_score=0.20),
    )

    assert embedding.calls == [[answer]]
    assert loader_calls == [(["c-unrelated", "c-correct"], 2)]
    assert result.status == "resolved"
    assert result.used_chunk_ids == ["c-correct"]


@pytest.mark.asyncio
async def test_resolver_never_maps_unrelated_first_candidate_image():
    answer = "进入考勤日历并处理异常记录。"
    chunks = [
        EvidenceChunk(
            "unrelated-image",
            "员工餐厅菜单",
            "wrong-image",
        ),
        EvidenceChunk(
            "correct-image",
            "进入考勤日历并处理异常记录",
            "correct-process-image",
        ),
        EvidenceChunk(
            "supporting-text",
            "异常记录支持提交补签",
            None,
        ),
    ]
    embedding = FakeEmbedding({answer: [1.0, 0.0]})
    reranker = FakeReranker(
        {
            (answer, chunks[0].content): 0.22,
            (answer, chunks[1].content): 0.93,
            (answer, chunks[2].content): 0.81,
        }
    )

    async def load_vectors(chunk_ids, dim):
        return {chunk_id: [1.0, 0.0] for chunk_id in chunk_ids}

    result = await resolve_evidence(
        "怎么处理考勤异常？",
        answer,
        chunks,
        embedding,
        reranker,
        load_vectors,
        vector_similarity_weight=1.0,
    )

    formatted_chunks = [{"id": chunk.chunk_id, "image_id": chunk.image_id} for chunk in chunks]
    assert result.used_chunk_ids == ["correct-image", "supporting-text"]
    assert _images_for_used_chunks(
        formatted_chunks,
        result.used_chunk_ids,
    ) == [OutgoingImage("correct-process-image")]


@pytest.mark.asyncio
async def test_resolver_rejects_two_qualified_candidates_inside_margin():
    answer = "点击异常记录进行处理。"
    chunks = [
        EvidenceChunk("c-a", "点击异常记录补签", "img-a"),
        EvidenceChunk("c-b", "点击异常记录请假", "img-b"),
    ]
    embedding = FakeEmbedding({answer: [1.0, 0.0]})
    reranker = FakeReranker(
        {
            (answer, chunks[0].content): 0.91,
            (answer, chunks[1].content): 0.90,
        }
    )

    async def load_vectors(chunk_ids, dim):
        return {"c-a": [1.0, 0.0], "c-b": [0.99, 0.01]}

    result = await resolve_evidence(
        "怎么处理？",
        answer,
        chunks,
        embedding,
        reranker,
        load_vectors,
        vector_similarity_weight=1.0,
    )

    assert result.status == "no_match"
    assert result.used_chunk_ids == []


@pytest.mark.asyncio
async def test_resolver_uses_injected_lexical_scorer_for_production_weights():
    answer = "执行操作。"
    chunks = [
        EvidenceChunk("c-a", "候选甲", "img-a"),
        EvidenceChunk("c-b", "候选乙", "img-b"),
    ]
    embedding = FakeEmbedding({answer: [1.0, 0.0]})
    reranker = FakeReranker(
        {
            (answer, chunks[0].content): 0.85,
            (answer, chunks[1].content): 0.95,
        }
    )
    lexical_calls = []

    async def load_vectors(chunk_ids, dim):
        return {"c-a": [1.0, 0.0], "c-b": [1.0, 0.0]}

    def lexical_scorer(query_text, documents):
        lexical_calls.append((query_text, list(documents)))
        return [0.0, 1.0]

    result = await resolve_evidence(
        "怎么操作？",
        answer,
        chunks,
        embedding,
        reranker,
        load_vectors,
        vector_similarity_weight=0.0,
        lexical_scorer=lexical_scorer,
    )

    assert lexical_calls == [
        (
            answer,
            [chunks[0].content, chunks[1].content],
        )
    ]
    assert result.used_chunk_ids == ["c-b"]


@pytest.mark.asyncio
async def test_resolver_keeps_chunks_for_different_segments_in_answer_order():
    answer = "先进入考勤日历。然后点击异常记录补签。"
    chunks = [
        EvidenceChunk(
            "c-calendar",
            "从工作台进入考勤日历",
            "img-calendar",
        ),
        EvidenceChunk(
            "c-repair",
            "选择异常记录并提交补签",
            "img-repair",
        ),
    ]
    embedding = FakeEmbedding(
        {
            "先进入考勤日历。": [1.0, 0.0],
            "然后点击异常记录补签。": [0.0, 1.0],
        }
    )
    reranker = FakeReranker(
        {
            ("先进入考勤日历。", chunks[0].content): 0.95,
            ("先进入考勤日历。", chunks[1].content): 0.20,
            ("然后点击异常记录补签。", chunks[0].content): 0.15,
            ("然后点击异常记录补签。", chunks[1].content): 0.94,
        }
    )

    async def load_vectors(chunk_ids, dim):
        return {
            "c-calendar": [1.0, 0.0],
            "c-repair": [0.0, 1.0],
        }

    result = await resolve_evidence(
        "怎么补卡？",
        answer,
        chunks,
        embedding,
        reranker,
        load_vectors,
        0.7,
    )

    assert result.used_chunk_ids == ["c-calendar", "c-repair"]


@pytest.mark.asyncio
async def test_resolver_rejects_lone_winner_when_runner_up_is_inside_margin():
    answer = "点击异常记录进行处理。"
    chunks = [
        EvidenceChunk("c-a", "点击异常记录补签", "img-a"),
        EvidenceChunk("c-b", "点击异常记录请假", "img-b"),
    ]
    embedding = FakeEmbedding({answer: [1.0, 0.0]})
    reranker = FakeReranker(
        {
            (answer, chunks[0].content): 0.74,
            (answer, chunks[1].content): 0.68,
        }
    )

    async def load_vectors(chunk_ids, dim):
        return {"c-a": [1.0, 0.0], "c-b": [0.99, 0.01]}

    result = await resolve_evidence(
        "怎么处理？",
        answer,
        chunks,
        embedding,
        reranker,
        load_vectors,
        0.7,
    )

    assert result.status == "no_match"
    assert result.used_chunk_ids == []


@pytest.mark.asyncio
async def test_resolver_drops_zero_and_wrong_dimension_vectors():
    answer = "有效事实。"
    chunks = [
        EvidenceChunk("zero", "零向量"),
        EvidenceChunk("wrong-dim", "错误维度"),
        EvidenceChunk("valid", "有效事实证据", "img-valid"),
    ]
    embedding = FakeEmbedding({answer: [1.0, 0.0]})
    reranker = FakeReranker({(answer, "有效事实证据"): 0.95})

    async def load_vectors(chunk_ids, dim):
        assert dim == 2
        return {
            "zero": [0.0, 0.0],
            "wrong-dim": [1.0],
            "valid": [1.0, 0.0],
        }

    result = await resolve_evidence(
        "问题",
        answer,
        chunks,
        embedding,
        reranker,
        load_vectors,
        0.7,
    )

    assert result.used_chunk_ids == ["valid"]


@pytest.mark.asyncio
async def test_resolver_does_not_fallback_when_reranker_raises():
    answer = "有效事实。"
    embedding = FakeEmbedding({answer: [1.0, 0.0]})

    class BrokenReranker:
        def similarity(self, query, documents):
            raise RuntimeError("reranker down")

    async def load_vectors(chunk_ids, dim):
        return {"c1": [1.0, 0.0]}

    result = await resolve_evidence(
        "问题",
        answer,
        [EvidenceChunk("c1", "有效事实证据", "img-1")],
        embedding,
        BrokenReranker(),
        load_vectors,
        0.7,
    )

    assert result.status == "error"
    assert result.used_chunk_ids == []


@pytest.mark.asyncio
async def test_resolver_deduplicates_chunk_across_segments_by_first_use():
    answer = "第一条事实。第二条事实。"
    embedding = FakeEmbedding(
        {
            "第一条事实。": [1.0, 0.0],
            "第二条事实。": [1.0, 0.0],
        }
    )
    chunk = EvidenceChunk("shared", "同时支撑两条事实", "img-shared")
    reranker = FakeReranker(
        {
            ("第一条事实。", chunk.content): 0.95,
            ("第二条事实。", chunk.content): 0.91,
        }
    )

    async def load_vectors(chunk_ids, dim):
        return {"shared": [1.0, 0.0]}

    result = await resolve_evidence(
        "问题",
        answer,
        [chunk],
        embedding,
        reranker,
        load_vectors,
        0.7,
    )

    assert result.used_chunk_ids == ["shared"]
