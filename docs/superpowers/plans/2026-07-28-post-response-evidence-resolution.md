# Post-response Evidence Resolution Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在企业微信文字回答成功发送后，从本轮已有检索候选中高精度解析真实证据 Chunk，持久化 `used_chunk_ids`，并单独发送所有可信图片。

**Architecture:** 新增不依赖渠道和数据库的 `rag/nlp/evidence.py`，负责答案分段、复用 Chunk 向量、混合初排和 reranker 复核；新增应用层 `EvidenceService` 负责取得当前对话的 embedding/reranker、读取现有 Chunk 向量和控制超时。企业微信首包只发送文字与原有来源文件，首包确认成功后再调用证据服务、定向保存结果并发送媒体-only 图片包。

**Tech Stack:** Python 3.10+、asyncio、NumPy、RAGFlow `FulltextQueryer`、`LLMBundle`、Peewee、pytest/pytest-asyncio、ruff。

## Global Constraints

- 证据解析只能在文字及现有来源文件发送成功后启动，不能增加文字首包等待时间。
- 第一版只使用当前 `reference.chunks`，不得补充检索。
- 必须复用索引中的 Chunk embedding；只批量计算新的答案片段 embedding。
- 必须使用当前对话配置的 reranker 复核，reranker 不可用或超时时不发送图片。
- 不使用生成式大模型、OCR、视觉模型或图片像素分析来判断图片。
- 企业微信图片选择只能读取稳定的 `used_chunk_ids`，不能读取 `[ID:n]`。
- 低置信度或分数接近的候选只发文字，不发送猜测图片。
- 多个可信带图 Chunk 的图片全部发送，按答案首次使用顺序排列，并按 `image_id` 去重。
- Web 引用展示、文字回答内容和现有来源文件选择逻辑保持不变。
- 不新增数据库表或列；`message_id` 和 `used_chunk_ids` 写入现有 `Conversation.reference` JSON。
- 第一版超时为 10 秒；任何证据解析异常不得撤回、替换或重复发送文字。
- 初始可信阈值固定为 `min_hybrid_score=0.55`、`min_rerank_score=0.70`、`min_score_margin=0.08`；上线前用人工标注回归样本复核，宁可漏发也不误发。

---

## File Map

- Create `rag/nlp/evidence.py`: 通用数据类型、答案事实分段、混合初排、reranker 复核和证据汇总。
- Create `api/db/services/evidence_service.py`: 模型绑定、Chunk 向量加载、10 秒超时和结构化日志。
- Modify `api/db/services/dialog_service.py`: 抽取只绑定 retrieval 所需模型的 `get_retrieval_models()`，避免证据阶段重复创建 chat/TTS 模型。
- Modify `api/db/services/conversation_service.py`: reference 写入 `message_id`，并用事务和行锁定向更新 `used_chunk_ids`。
- Modify `api/channels/core/base.py`: 允许渠道返回明确的发送成功状态。
- Modify `api/channels/wecom/channel.py`: WebSocket 支持媒体-only 消息，并返回文字首包是否成功。
- Modify `api/channels/bootstrap.py`: 文字优先编排、证据调用、持久化和后置图片发送。
- Create `test/unit_test/rag/test_evidence.py`: 通用引擎分段、排序、阈值、去重和异常测试。
- Create `test/unit_test/api/db/services/test_evidence_service.py`: 模型/向量适配、超时、关闭资源和日志测试。
- Create `test/unit_test/api/db/services/test_conversation_service_evidence.py`: `message_id` 绑定、定向更新和并发保护测试。
- Modify `test/unit_test/api/channels/test_wecom_channel.py`: 文字成功状态和媒体-only 发送测试。
- Modify `test/unit_test/api/channels/test_bootstrap.py`: 图片映射和完整时序回归测试。

---

### Task 1: Evidence data types and factual answer segmentation

**Files:**

- Create: `rag/nlp/evidence.py`
- Create: `test/unit_test/rag/test_evidence.py`

**Interfaces:**

- Consumes: 原始 `question: str`、`answer: str` 和候选 Chunk 的稳定 ID/正文/图片 ID。
- Produces: `EvidenceChunk`、`EvidenceSegment`、`EvidenceMatch`、`EvidenceResolution`、`EvidenceConfig` 和 `split_evidence_segments(question, answer)`.

- [ ] **Step 1: Write failing segmentation and immutability tests**

```python
# test/unit_test/rag/test_evidence.py
from rag.nlp.evidence import split_evidence_segments


def test_split_segments_removes_display_citations_from_copy_only():
    answer = "处理路径：进入考勤日历。[ID:2]\n点击异常记录后补签。[0]"

    segments = split_evidence_segments("考勤异常怎么处理？", answer)

    assert [segment.text for segment in segments] == [
        "处理路径：进入考勤日历。",
        "点击异常记录后补签。",
    ]
    assert answer.endswith("[0]")


def test_split_segments_ignores_knowledge_availability_but_keeps_business_negation():
    answer = (
        "知识库未找到流程图，建议联系人事获取截图。\n"
        "请假期间不存在迟到情况。\n"
        "迟到记录不能通过请假自动补卡。"
    )

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
```

- [ ] **Step 2: Run the tests and verify the module is missing**

Run:

```bash
uv run pytest test/unit_test/rag/test_evidence.py -q
```

Expected: collection fails with `ModuleNotFoundError: No module named 'rag.nlp.evidence'`.

- [ ] **Step 3: Add immutable types, conservative defaults, and segmentation**

```python
# rag/nlp/evidence.py
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Awaitable, Callable, Literal


_CITATION_PATTERN = re.compile(r"\[(?:ID:)?[0-9\u0660-\u0669\u06F0-\u06F9]+\]")
_LIST_PREFIX = re.compile(r"^\s*(?:[-*+]|\d+[.)、])\s*")
_SENTENCE_BOUNDARY = re.compile(r"(?<=[。！？!?；;])|\n+")
_MARKDOWN_ONLY = re.compile(r"^\s*(?:#{1,6}|[-*_`>|])+\s*$")
_META_ONLY = (
    re.compile(r"知识库.{0,12}(?:未找到|没有).{0,12}(?:图片|截图|流程图)"),
    re.compile(r"建议.{0,12}(?:联系|咨询).{0,12}(?:人事|管理员).{0,12}(?:截图|图片)"),
)


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


def _clean_piece(piece: str) -> str:
    piece = _CITATION_PATTERN.sub("", piece)
    piece = _LIST_PREFIX.sub("", piece).strip()
    return re.sub(r"[ \t]+", " ", piece)


def _is_meta_only(piece: str) -> bool:
    return any(pattern.search(piece) for pattern in _META_ONLY)


def split_evidence_segments(question: str, answer: str) -> list[EvidenceSegment]:
    question_context = (question or "").strip()
    raw = [_clean_piece(piece) for piece in _SENTENCE_BOUNDARY.split(answer or "")]
    raw = [
        piece
        for piece in raw
        if piece
        and not _MARKDOWN_ONLY.fullmatch(piece)
        and not _is_meta_only(piece)
    ]

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
```

- [ ] **Step 4: Run focused tests**

Run:

```bash
uv run pytest test/unit_test/rag/test_evidence.py -q
```

Expected: 4 tests pass.

- [ ] **Step 5: Commit the segmentation boundary**

```bash
git add rag/nlp/evidence.py test/unit_test/rag/test_evidence.py
git commit -m "feat: add evidence answer segmentation"
```

---

### Task 2: Hybrid shortlist and reranker evidence resolution

**Files:**

- Modify: `rag/nlp/evidence.py`
- Modify: `test/unit_test/rag/test_evidence.py`

**Interfaces:**

- Consumes: `EvidenceChunk`, `ChunkVectorLoader`, an embedding model exposing `encode(list[str])`, a reranker exposing `similarity(query, list[str])`, and `vector_similarity_weight`.
- Produces: `resolve_evidence(question: str, answer: str, chunks: list[EvidenceChunk], embedding_model, rerank_model, chunk_vector_loader: ChunkVectorLoader, vector_similarity_weight: float, config: EvidenceConfig = EvidenceConfig()) -> EvidenceResolution`.

- [ ] **Step 1: Write failing evidence-selection tests with deterministic models**

```python
# append to test/unit_test/rag/test_evidence.py
import numpy as np
import pytest

from rag.nlp.evidence import EvidenceChunk, EvidenceConfig, resolve_evidence


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


@pytest.mark.asyncio
async def test_resolver_reuses_chunk_vectors_and_reranker_can_select_non_first_candidate():
    answer = "进入考勤日历后点击异常记录。"
    chunks = [
        EvidenceChunk("c-unrelated", "考勤统计报表说明", "img-wrong"),
        EvidenceChunk("c-correct", "工作台进入考勤日历，点击异常记录补签", "img-right"),
    ]
    embedding = FakeEmbedding({answer: [1.0, 0.0]})
    reranker = FakeReranker({
        (answer, chunks[0].content): 0.30,
        (answer, chunks[1].content): 0.92,
    })
    loader_calls = []

    async def load_vectors(chunk_ids, dim):
        loader_calls.append((chunk_ids, dim))
        return {"c-unrelated": [0.95, 0.05], "c-correct": [0.80, 0.20]}

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
async def test_resolver_keeps_all_chunks_supporting_different_segments_in_answer_order():
    answer = "先进入考勤日历。然后点击异常记录补签。"
    chunks = [
        EvidenceChunk("c-calendar", "从工作台进入考勤日历", "img-calendar"),
        EvidenceChunk("c-repair", "选择异常记录并提交补签", "img-repair"),
    ]
    embedding = FakeEmbedding({
        "先进入考勤日历。": [1.0, 0.0],
        "然后点击异常记录补签。": [0.0, 1.0],
    })
    reranker = FakeReranker({
        ("先进入考勤日历。", chunks[0].content): 0.95,
        ("先进入考勤日历。", chunks[1].content): 0.20,
        ("然后点击异常记录补签。", chunks[0].content): 0.15,
        ("然后点击异常记录补签。", chunks[1].content): 0.94,
    })

    async def load_vectors(chunk_ids, dim):
        return {"c-calendar": [1.0, 0.0], "c-repair": [0.0, 1.0]}

    result = await resolve_evidence(
        "怎么补卡？", answer, chunks, embedding, reranker, load_vectors, 0.7
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
    reranker = FakeReranker({
        (answer, chunks[0].content): 0.74,
        (answer, chunks[1].content): 0.68,
    })

    async def load_vectors(chunk_ids, dim):
        return {"c-a": [1.0, 0.0], "c-b": [0.99, 0.01]}

    result = await resolve_evidence(
        "怎么处理？", answer, chunks, embedding, reranker, load_vectors, 0.7
    )

    assert result.status == "no_match"
    assert result.used_chunk_ids == []
```

- [ ] **Step 2: Run the new tests and verify `resolve_evidence` is missing**

Run:

```bash
uv run pytest test/unit_test/rag/test_evidence.py -q
```

Expected: collection fails because `resolve_evidence` is not exported.

- [ ] **Step 3: Implement one embedding batch, injected vector loading, hybrid shortlist, and per-segment reranker batches**

Add these imports and helpers to `rag/nlp/evidence.py`:

```python
import asyncio
import logging
from timeit import default_timer as timer

import numpy as np

from common.misc_utils import thread_pool_exec
from rag.nlp import query, rag_tokenizer


LOGGER = logging.getLogger(__name__)


def _tokens(text: str) -> list[str]:
    queryer = query.FulltextQueryer()
    return rag_tokenizer.tokenize(queryer.rmWWW(text)).split()


def _usable_vector(vector: object, dim: int) -> bool:
    return (
        isinstance(vector, (list, tuple, np.ndarray))
        and len(vector) == dim
        and bool(np.any(np.asarray(vector, dtype=float)))
    )


def _empty_resolution(started_at: float, reason: str) -> EvidenceResolution:
    return EvidenceResolution(
        used_chunk_ids=[],
        matches=[],
        unmatched_segments=[],
        status="no_match",
        duration_ms=(timer() - started_at) * 1000,
        reason=reason,
    )
```

Implement the resolver with the following concrete control flow:

```python
async def resolve_evidence(
    question: str,
    answer: str,
    chunks: list[EvidenceChunk],
    embedding_model,
    rerank_model,
    chunk_vector_loader: ChunkVectorLoader,
    vector_similarity_weight: float,
    config: EvidenceConfig = EvidenceConfig(),
) -> EvidenceResolution:
    started_at = timer()
    segments = split_evidence_segments(question, answer)
    if not segments or not chunks:
        return _empty_resolution(started_at, "no_segments_or_chunks")
    if embedding_model is None or rerank_model is None:
        return EvidenceResolution([], [], [s.index for s in segments], "error",
                                  (timer() - started_at) * 1000, "model_unavailable")

    try:
        segment_vectors, _ = await thread_pool_exec(
            embedding_model.encode,
            [segment.text for segment in segments],
        )
        segment_vectors = np.asarray(segment_vectors, dtype=float)
        if segment_vectors.ndim != 2 or segment_vectors.shape[0] != len(segments):
            raise ValueError("embedding result shape does not match evidence segments")
        dim = int(segment_vectors.shape[1])

        missing_ids = [
            chunk.chunk_id
            for chunk in chunks
            if not _usable_vector(chunk.vector, dim)
        ]
        loaded = await chunk_vector_loader(missing_ids, dim) if missing_ids else {}
        usable_chunks = []
        chunk_vectors = []
        for chunk in chunks:
            vector = chunk.vector if _usable_vector(chunk.vector, dim) else loaded.get(chunk.chunk_id)
            if not _usable_vector(vector, dim):
                continue
            usable_chunks.append(chunk)
            chunk_vectors.append(list(vector))
        if not usable_chunks:
            return _empty_resolution(started_at, "no_chunk_vectors")

        queryer = query.FulltextQueryer()
        chunk_tokens = [
            rag_tokenizer.tokenize(queryer.rmWWW(chunk.content)).split()
            for chunk in usable_chunks
        ]
        vector_weight = min(1.0, max(0.0, float(vector_similarity_weight)))
        term_weight = 1.0 - vector_weight
        shortlists: list[tuple[EvidenceSegment, list[tuple[int, float]]]] = []
        for segment, segment_vector in zip(segments, segment_vectors):
            hybrid, _, _ = queryer.hybrid_similarity(
                segment_vector,
                chunk_vectors,
                rag_tokenizer.tokenize(queryer.rmWWW(segment.text)).split(),
                chunk_tokens,
                tkweight=term_weight,
                vtweight=vector_weight,
            )
            order = np.argsort(np.asarray(hybrid, dtype=float))[::-1]
            selected = [
                (int(index), float(hybrid[index]))
                for index in order[:config.shortlist_size]
            ]
            shortlists.append((segment, selected))

        async def rerank_one(segment, selected):
            docs = [usable_chunks[index].content for index, _ in selected]
            scores, _ = await thread_pool_exec(
                rerank_model.similarity,
                segment.text,
                docs,
            )
            return np.asarray(scores, dtype=float)

        rerank_scores = await asyncio.gather(*[
            rerank_one(segment, selected)
            for segment, selected in shortlists
        ])

        matches: list[EvidenceMatch] = []
        unmatched: list[int] = []
        for (segment, selected), scores in zip(shortlists, rerank_scores):
            if len(scores) != len(selected):
                raise ValueError("reranker result shape does not match shortlist")
            ranked = sorted(
                [
                    (chunk_index, hybrid_score, float(score))
                    for (chunk_index, hybrid_score), score in zip(selected, scores)
                ],
                key=lambda item: item[2],
                reverse=True,
            )
            qualified = [
                item for item in ranked
                if item[1] >= config.min_hybrid_score
                and item[2] >= config.min_rerank_score
            ]
            if (
                len(qualified) == 1
                and len(ranked) > 1
                and qualified[0][2] - ranked[1][2] < config.min_score_margin
            ):
                qualified = []
            if not qualified:
                unmatched.append(segment.index)
                continue
            for chunk_index, hybrid_score, rerank_score in qualified:
                matches.append(EvidenceMatch(
                    segment_index=segment.index,
                    chunk_id=usable_chunks[chunk_index].chunk_id,
                    hybrid_score=hybrid_score,
                    rerank_score=rerank_score,
                ))

        used_chunk_ids = list(dict.fromkeys(match.chunk_id for match in matches))
        return EvidenceResolution(
            used_chunk_ids=used_chunk_ids,
            matches=matches,
            unmatched_segments=unmatched,
            status="resolved" if used_chunk_ids else "no_match",
            duration_ms=(timer() - started_at) * 1000,
            reason=None if used_chunk_ids else "below_confidence_threshold",
        )
    except Exception as exc:
        LOGGER.warning("evidence resolution failed: %s", exc, exc_info=True)
        return EvidenceResolution(
            used_chunk_ids=[],
            matches=[],
            unmatched_segments=[segment.index for segment in segments],
            status="error",
            duration_ms=(timer() - started_at) * 1000,
            reason=type(exc).__name__,
        )
```

The reranker interface accepts one query and multiple documents, so the implementation makes one batched document call per answer segment and schedules those segment batches together. It must not call the reranker once per Chunk.

- [ ] **Step 4: Add edge-case tests**

Add tests asserting:

```python
@pytest.mark.asyncio
async def test_resolver_drops_missing_zero_and_wrong_dimension_vectors():
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
        "问题", answer, chunks, embedding, reranker, load_vectors, 0.7
    )

    assert result.used_chunk_ids == ["valid"]


@pytest.mark.asyncio
async def test_resolver_returns_error_without_falling_back_when_reranker_raises():
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
async def test_resolver_deduplicates_same_chunk_across_segments_by_first_use():
    answer = "第一条事实。第二条事实。"
    embedding = FakeEmbedding({
        "第一条事实。": [1.0, 0.0],
        "第二条事实。": [1.0, 0.0],
    })
    chunk = EvidenceChunk("shared", "同时支撑两条事实", "img-shared")
    reranker = FakeReranker({
        ("第一条事实。", chunk.content): 0.95,
        ("第二条事实。", chunk.content): 0.91,
    })

    async def load_vectors(chunk_ids, dim):
        return {"shared": [1.0, 0.0]}

    result = await resolve_evidence(
        "问题", answer, [chunk], embedding, reranker, load_vectors, 0.7
    )

    assert result.used_chunk_ids == ["shared"]
```

Use the same deterministic fakes from Step 1; explicitly provide one zero vector, one wrong-dimension vector, and a `FakeReranker.similarity()` that raises `RuntimeError("reranker down")`.

- [ ] **Step 5: Run the complete engine unit tests**

Run:

```bash
uv run pytest test/unit_test/rag/test_evidence.py -q
```

Expected: all segmentation, ranking, ambiguity, vector validation, error and deduplication tests pass.

- [ ] **Step 6: Commit the generic engine**

```bash
git add rag/nlp/evidence.py test/unit_test/rag/test_evidence.py
git commit -m "feat: resolve answer evidence from retrieved chunks"
```

---

### Task 3: Retrieval model adapter, existing-vector loading, and timeout

**Files:**

- Modify: `api/db/services/dialog_service.py`
- Create: `api/db/services/evidence_service.py`
- Create: `test/unit_test/api/db/services/test_evidence_service.py`

**Interfaces:**

- Consumes: confirmed `dialog`, question, final answer and formatted `reference.chunks`.
- Produces:

- `get_retrieval_models(dialog, trace_context=None, langfuse_session_id=None) -> tuple[list, object | None, object | None]`
- `EvidenceService.resolve_for_dialog(dialog, question: str, answer: str, chunks: list[dict], config: EvidenceConfig = EvidenceConfig()) -> EvidenceResolution`

- [ ] **Step 1: Write failing adapter tests**

```python
# test/unit_test/api/db/services/test_evidence_service.py
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from rag.nlp.evidence import EvidenceConfig, EvidenceResolution


@pytest.mark.asyncio
async def test_service_passes_real_chunk_ids_and_dialog_weights(monkeypatch):
    embedding = Mock()
    reranker = Mock()
    kbs = [SimpleNamespace(tenant_id="tenant-a")]
    retriever = SimpleNamespace(fetch_chunk_vectors=AsyncMock(return_value={"c1": [1.0, 0.0]}))
    captured = {}

    async def fake_resolve(**kwargs):
        captured.update(kwargs)
        await kwargs["chunk_vector_loader"](["c1"], 2)
        return EvidenceResolution(["c1"], [], [], "resolved", 1.0)

    monkeypatch.setattr(
        "api.db.services.evidence_service.get_retrieval_models",
        lambda dialog: (kbs, embedding, reranker),
    )
    monkeypatch.setattr("api.db.services.evidence_service.settings.retriever", retriever)
    monkeypatch.setattr("api.db.services.evidence_service.resolve_evidence", fake_resolve)

    from api.db.services.evidence_service import EvidenceService

    dialog = SimpleNamespace(kb_ids=["kb-a"], vector_similarity_weight=0.65)
    result = await EvidenceService.resolve_for_dialog(
        dialog,
        "问题",
        "回答",
        [{"id": "c1", "content": "证据", "image_id": "img-1"}],
    )

    assert result.used_chunk_ids == ["c1"]
    assert captured["chunks"][0].chunk_id == "c1"
    assert captured["vector_similarity_weight"] == 0.65
    retriever.fetch_chunk_vectors.assert_awaited_once_with(
        ["c1"], ["tenant-a"], ["kb-a"], 2
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
        "api.db.services.evidence_service.get_retrieval_models",
        lambda dialog: ([SimpleNamespace(tenant_id="t")], embedding, reranker),
    )
    monkeypatch.setattr("api.db.services.evidence_service.resolve_evidence", never_finishes)

    from api.db.services.evidence_service import EvidenceService

    result = await EvidenceService.resolve_for_dialog(
        SimpleNamespace(kb_ids=["kb"], vector_similarity_weight=0.7),
        "问题",
        "回答",
        [{"id": "c1", "content": "证据", "image_id": "img"}],
        EvidenceConfig(timeout_seconds=0.01),
    )

    assert result.status == "error"
    assert result.reason == "timeout"
    embedding.close.assert_called_once()
    reranker.close.assert_called_once()
```

- [ ] **Step 2: Run tests and verify the service module is missing**

Run:

```bash
uv run pytest test/unit_test/api/db/services/test_evidence_service.py -q
```

Expected: collection fails with `ModuleNotFoundError`.

- [ ] **Step 3: Extract retrieval-only model binding**

Refactor `api/db/services/dialog_service.py` without changing `get_models()` return order:

```python
def get_retrieval_models(dialog, trace_context=None, langfuse_session_id=None):
    embd_mdl, rerank_mdl = None, None
    kbs = list(KnowledgebaseService.get_by_ids(dialog.kb_ids))
    embedding_list = list({kb.embd_id for kb in kbs})
    if len(embedding_list) > 1:
        raise Exception("**ERROR**: Knowledge bases use different embedding models.")
    if embedding_list:
        owner_tenant_id = kbs[0].tenant_id
        config = get_model_config_from_provider_instance(
            owner_tenant_id,
            LLMType.EMBEDDING,
            embedding_list[0],
        )
        embd_mdl = LLMBundle(
            owner_tenant_id,
            config,
            trace_context=trace_context,
            langfuse_session_id=langfuse_session_id,
        )
    if dialog.rerank_id:
        config = get_model_config_from_provider_instance(
            dialog.tenant_id,
            LLMType.RERANK,
            dialog.rerank_id,
        )
        rerank_mdl = LLMBundle(
            dialog.tenant_id,
            config,
            trace_context=trace_context,
            langfuse_session_id=langfuse_session_id,
        )
    return kbs, embd_mdl, rerank_mdl


def get_models(dialog, trace_context=None, langfuse_session_id=None):
    kbs, embd_mdl, rerank_mdl = get_retrieval_models(
        dialog,
        trace_context=trace_context,
        langfuse_session_id=langfuse_session_id,
    )
    chat_mdl = tts_mdl = None
    if dialog.llm_id:
        chat_model_config = get_model_config_from_provider_instance(
            dialog.tenant_id,
            LLMType.CHAT,
            dialog.llm_id,
        )
    else:
        chat_model_config = get_tenant_default_model_by_type(
            dialog.tenant_id,
            LLMType.CHAT,
        )
    chat_mdl = LLMBundle(
        dialog.tenant_id,
        chat_model_config,
        trace_context=trace_context,
        langfuse_session_id=langfuse_session_id,
    )
    if dialog.prompt_config.get("tts"):
        tts_config = get_tenant_default_model_by_type(
            dialog.tenant_id,
            LLMType.TTS,
        )
        tts_mdl = LLMBundle(
            dialog.tenant_id,
            tts_config,
            trace_context=trace_context,
            langfuse_session_id=langfuse_session_id,
        )
    return kbs, embd_mdl, rerank_mdl, chat_mdl, tts_mdl
```

Move the existing embedding and reranker construction into the helper rather than duplicating it. Preserve the existing exception text for mixed embedding models.

- [ ] **Step 4: Implement the application service**

```python
# api/db/services/evidence_service.py
from __future__ import annotations

import asyncio
import logging
from timeit import default_timer as timer

from common import settings
from api.db.services.dialog_service import get_retrieval_models
from rag.nlp.evidence import (
    EvidenceChunk,
    EvidenceConfig,
    EvidenceResolution,
    resolve_evidence,
)


LOGGER = logging.getLogger(__name__)


class EvidenceService:
    @classmethod
    async def resolve_for_dialog(
        cls,
        dialog,
        question: str,
        answer: str,
        chunks: list[dict],
        config: EvidenceConfig = EvidenceConfig(),
    ) -> EvidenceResolution:
        started_at = timer()
        embedding_model = rerank_model = None
        try:
            kbs, embedding_model, rerank_model = get_retrieval_models(dialog)
            if embedding_model is None or rerank_model is None:
                return EvidenceResolution(
                    [], [], [], "error",
                    (timer() - started_at) * 1000,
                    "model_unavailable",
                )
            tenant_ids = list(dict.fromkeys(kb.tenant_id for kb in kbs))
            evidence_chunks = [
                EvidenceChunk(
                    chunk_id=str(chunk.get("id") or ""),
                    content=str(chunk.get("content") or ""),
                    image_id=str(chunk.get("image_id") or "") or None,
                )
                for chunk in chunks
                if isinstance(chunk, dict)
                and chunk.get("id")
                and chunk.get("content")
            ]

            async def load_vectors(chunk_ids: list[str], dim: int):
                return await settings.retriever.fetch_chunk_vectors(
                    chunk_ids,
                    tenant_ids,
                    dialog.kb_ids,
                    dim,
                )

            result = await asyncio.wait_for(
                resolve_evidence(
                    question=question,
                    answer=answer,
                    chunks=evidence_chunks,
                    embedding_model=embedding_model,
                    rerank_model=rerank_model,
                    chunk_vector_loader=load_vectors,
                    vector_similarity_weight=dialog.vector_similarity_weight,
                    config=config,
                ),
                timeout=config.timeout_seconds,
            )
            LOGGER.info(
                "evidence resolved status=%s candidates=%d used_chunk_ids=%s "
                "matches=%s unmatched_segments=%s duration_ms=%.1f",
                result.status,
                len(evidence_chunks),
                result.used_chunk_ids,
                [
                    {
                        "segment_index": match.segment_index,
                        "chunk_id": match.chunk_id,
                        "hybrid_score": round(match.hybrid_score, 4),
                        "rerank_score": round(match.rerank_score, 4),
                    }
                    for match in result.matches
                ],
                result.unmatched_segments,
                result.duration_ms,
            )
            return result
        except asyncio.TimeoutError:
            LOGGER.warning("evidence resolution timed out after %.1fs", config.timeout_seconds)
            return EvidenceResolution(
                [], [], [], "error",
                (timer() - started_at) * 1000,
                "timeout",
            )
        except Exception as exc:
            LOGGER.warning("evidence service failed: %s", exc, exc_info=True)
            return EvidenceResolution(
                [], [], [], "error",
                (timer() - started_at) * 1000,
                type(exc).__name__,
            )
        finally:
            for model in (embedding_model, rerank_model):
                if model is not None and hasattr(model, "close"):
                    model.close()
```

- [ ] **Step 5: Add no-reranker and vector-fetch failure tests**

Add exact assertions:

```python
@pytest.mark.asyncio
async def test_service_without_reranker_returns_error_without_embedding_call(
    monkeypatch,
):
    embedding = Mock()
    embedding.close = Mock()
    monkeypatch.setattr(
        "api.db.services.evidence_service.get_retrieval_models",
        lambda dialog: ([SimpleNamespace(tenant_id="t")], embedding, None),
    )

    from api.db.services.evidence_service import EvidenceService

    result = await EvidenceService.resolve_for_dialog(
        SimpleNamespace(kb_ids=["kb"], vector_similarity_weight=0.7),
        "问题",
        "回答",
        [{"id": "c1", "content": "证据", "image_id": "img"}],
    )

    assert result.status == "error"
    assert result.reason == "model_unavailable"
    embedding.encode.assert_not_called()
    embedding.close.assert_called_once()


@pytest.mark.asyncio
async def test_service_vector_fetch_failure_never_returns_used_chunks(
    monkeypatch,
):
    embedding = Mock()
    embedding.encode.return_value = (np.asarray([[1.0, 0.0]]), 0)
    reranker = Mock()
    reranker.similarity.return_value = (np.asarray([0.95]), 0)
    retriever = SimpleNamespace(
        fetch_chunk_vectors=AsyncMock(side_effect=RuntimeError("store down"))
    )
    monkeypatch.setattr(
        "api.db.services.evidence_service.get_retrieval_models",
        lambda dialog: ([SimpleNamespace(tenant_id="t")], embedding, reranker),
    )
    monkeypatch.setattr(
        "api.db.services.evidence_service.settings.retriever",
        retriever,
    )

    from api.db.services.evidence_service import EvidenceService

    result = await EvidenceService.resolve_for_dialog(
        SimpleNamespace(kb_ids=["kb"], vector_similarity_weight=0.7),
        "问题",
        "回答事实。",
        [{"id": "c1", "content": "回答事实证据", "image_id": "img"}],
    )

    assert result.status == "error"
    assert result.used_chunk_ids == []
```

Add `import numpy as np` to this test module.

- [ ] **Step 6: Run adapter and existing dialog tests**

Run:

```bash
uv run pytest \
  test/unit_test/api/db/services/test_evidence_service.py \
  test/unit_test/api/db/services/test_dialog_service_final_answer.py \
  test/unit_test/api/db/services/test_dialog_service_use_sql_source_columns.py \
  -q
```

Expected: all tests pass and `get_models()` retains its original five-element return contract.

- [ ] **Step 7: Commit the service boundary**

```bash
git add \
  api/db/services/dialog_service.py \
  api/db/services/evidence_service.py \
  test/unit_test/api/db/services/test_evidence_service.py
git commit -m "feat: adapt retrieval models for evidence resolution"
```

---

### Task 4: Message-bound reference persistence with row locking

**Files:**

- Modify: `api/db/services/conversation_service.py`
- Create: `test/unit_test/api/db/services/test_conversation_service_evidence.py`

**Interfaces:**

- Consumes: `conversation_id: str`, assistant `message_id: str`, ordered `used_chunk_ids: list[str]`.
- Produces: `ConversationService.update_reference_evidence(conversation_id: str, message_id: str, used_chunk_ids: list[str]) -> bool`.

- [ ] **Step 1: Write failing tests for reference identity and targeted updates**

```python
# test/unit_test/api/db/services/test_conversation_service_evidence.py
from types import SimpleNamespace

from api.db.services.conversation_service import (
    ConversationService,
    structure_answer,
)


def test_structure_answer_records_message_id_on_reference():
    conv = SimpleNamespace(message=[], reference=[{"chunks": [], "doc_aggs": []}])
    ans = {
        "answer": "回答",
        "reference": {"chunks": [{"id": "c1"}], "doc_aggs": []},
        "final": True,
    }

    structure_answer(conv, ans, "message-2", "conversation-1")

    assert conv.reference[-1]["message_id"] == "message-2"


def test_merge_updates_only_matching_reference():
    references = [
        {"message_id": "message-1", "chunks": [{"id": "old"}]},
        {"message_id": "message-2", "chunks": [{"id": "new"}]},
    ]

    updated, found = ConversationService._merge_reference_evidence(
        references,
        "message-1",
        ["old"],
    )

    assert found is True
    assert updated[0]["used_chunk_ids"] == ["old"]
    assert "used_chunk_ids" not in updated[1]
    assert "used_chunk_ids" not in references[0]


def test_merge_never_guesses_last_reference_when_message_is_missing():
    updated, found = ConversationService._merge_reference_evidence(
        [{"chunks": [{"id": "legacy"}]}],
        "missing",
        ["legacy"],
    )

    assert found is False
    assert updated == [{"chunks": [{"id": "legacy"}]}]
```

- [ ] **Step 2: Run tests and verify they fail**

Run:

```bash
uv run pytest test/unit_test/api/db/services/test_conversation_service_evidence.py -q
```

Expected: failure because `message_id` is absent and `_merge_reference_evidence` is undefined.

- [ ] **Step 3: Add `message_id` and a non-mutating merge helper**

In `structure_answer()` add the identity before assigning the reference:

```python
reference["message_id"] = message_id
reference["chunks"] = chunk_list
```

Add this class method:

```python
@staticmethod
def _merge_reference_evidence(references, message_id, used_chunk_ids):
    merged = [
        dict(reference) if isinstance(reference, dict) else reference
        for reference in (references or [])
    ]
    stable_ids = list(dict.fromkeys(str(chunk_id) for chunk_id in used_chunk_ids if chunk_id))
    for index, reference in enumerate(merged):
        if not isinstance(reference, dict):
            continue
        if str(reference.get("message_id") or "") != str(message_id):
            continue
        reference["used_chunk_ids"] = stable_ids
        merged[index] = reference
        return merged, True
    return merged, False
```

- [ ] **Step 4: Write a failing transaction test that proves the latest row is re-read**

Add this fake Peewee query chain and transaction test:

```python
from contextlib import nullcontext

import api.db.services.conversation_service as conversation_module


class _IdField:
    def __eq__(self, value):
        return value


class _Select:
    def __init__(self, model):
        self.model = model

    def where(self, conversation_id):
        self.conversation_id = conversation_id
        return self

    def for_update(self):
        self.model.for_update_called = True
        return self

    def first(self):
        return self.model.row


class _Update:
    def __init__(self, model, payload):
        self.model = model
        self.payload = payload

    def where(self, conversation_id):
        self.conversation_id = conversation_id
        return self

    def execute(self):
        self.model.saved_reference = self.payload["reference"]
        return 1


class _ConversationModel:
    id = _IdField()
    row = None
    saved_reference = None
    for_update_called = False

    @classmethod
    def select(cls):
        return _Select(cls)

    @classmethod
    def update(cls, **payload):
        return _Update(cls, payload)


class _ConversationService(ConversationService):
    model = _ConversationModel


def _update_reference_evidence(conversation_id, message_id, used_chunk_ids):
    method = ConversationService.update_reference_evidence.__wrapped__
    return method(
        _ConversationService,
        conversation_id,
        message_id,
        used_chunk_ids,
    )


def test_update_reference_evidence_locks_and_preserves_concurrent_reference(
    monkeypatch,
):
    monkeypatch.setattr(conversation_module.DB, "atomic", nullcontext)
    _ConversationModel.row = SimpleNamespace(reference=[
        {"message_id": "message-1", "chunks": [{"id": "c1"}]},
        {"message_id": "message-2", "chunks": [{"id": "c2"}]},
        {"message_id": "message-3", "chunks": [{"id": "concurrent"}]},
    ])
    _ConversationModel.saved_reference = None
    _ConversationModel.for_update_called = False

    assert _update_reference_evidence(
        "conversation-1",
        "message-1",
        ["c1"],
    ) is True

    assert _ConversationModel.for_update_called is True
    assert _ConversationModel.saved_reference[2] == {
        "message_id": "message-3",
        "chunks": [{"id": "concurrent"}],
    }
    assert _ConversationModel.saved_reference[0]["used_chunk_ids"] == ["c1"]


def test_update_reference_evidence_does_not_write_when_message_is_missing(
    monkeypatch,
):
    monkeypatch.setattr(conversation_module.DB, "atomic", nullcontext)
    _ConversationModel.row = SimpleNamespace(reference=[
        {"message_id": "message-1", "chunks": [{"id": "c1"}]},
    ])
    _ConversationModel.saved_reference = None

    assert _update_reference_evidence(
        "conversation-1",
        "missing",
        ["c1"],
    ) is False
    assert _ConversationModel.saved_reference is None
```

- [ ] **Step 5: Implement the transaction and row lock**

Add `datetime` plus existing timestamp helpers to the imports, then implement:

```python
@classmethod
@DB.connection_context()
def update_reference_evidence(cls, conversation_id, message_id, used_chunk_ids):
    with DB.atomic():
        conversation = (
            cls.model
            .select()
            .where(cls.model.id == conversation_id)
            .for_update()
            .first()
        )
        if conversation is None:
            return False
        references, found = cls._merge_reference_evidence(
            conversation.reference,
            message_id,
            used_chunk_ids,
        )
        if not found:
            return False
        updated = (
            cls.model
            .update(
                reference=references,
                update_time=current_timestamp(),
                update_date=datetime_format(datetime.now()),
            )
            .where(cls.model.id == conversation_id)
            .execute()
        )
        return updated == 1
```

Do not use `reference[-1]` in this method.

- [ ] **Step 6: Run persistence and channel-binding regression tests**

Run:

```bash
uv run pytest \
  test/unit_test/api/db/services/test_conversation_service_evidence.py \
  test/unit_test/api/db/services/test_conversation_service_channel_binding.py \
  -q
```

Expected: all tests pass.

- [ ] **Step 7: Commit targeted persistence**

```bash
git add \
  api/db/services/conversation_service.py \
  test/unit_test/api/db/services/test_conversation_service_evidence.py
git commit -m "feat: persist evidence by conversation message"
```

---

### Task 5: Confirmed text sends and WeCom media-only follow-ups

**Files:**

- Modify: `api/channels/core/base.py`
- Modify: `api/channels/wecom/channel.py`
- Modify: `test/unit_test/api/channels/test_wecom_channel.py`

**Interfaces:**

- Consumes: existing `OutgoingMessage`.
- Produces: `Channel.send(OutgoingMessage) -> bool | None`; WeCom returns `True` only after the requested text/media send has a successful WebSocket acknowledgement, and accepts `text=""` when images or files are present.

- [ ] **Step 1: Write failing WeCom send-contract tests**

```python
# append to test/unit_test/api/channels/test_wecom_channel.py
@pytest.mark.asyncio
async def test_websocket_text_send_returns_true_after_ack(monkeypatch):
    channel = make_channel()
    monkeypatch.setattr(channel, "_ws_request", AsyncMock(return_value={"body": {}}))

    result = await channel.send(OutgoingMessage(chat_id="chat-1", text="answer"))

    assert result is True


@pytest.mark.asyncio
async def test_websocket_text_send_returns_false_when_ack_fails(monkeypatch):
    channel = make_channel()
    monkeypatch.setattr(
        channel,
        "_ws_request",
        AsyncMock(side_effect=RuntimeError("send failed")),
    )

    result = await channel.send(OutgoingMessage(chat_id="chat-1", text="answer"))

    assert result is False


@pytest.mark.asyncio
async def test_websocket_media_only_message_skips_markdown_and_sends_all_images(monkeypatch):
    channel = make_channel()
    request = AsyncMock(return_value={"body": {}})
    send_image = AsyncMock()
    monkeypatch.setattr(channel, "_ws_request", request)
    monkeypatch.setattr(channel, "_load_stored_image", lambda image_id: image_id.encode())
    monkeypatch.setattr(
        channel,
        "_upload_websocket_image",
        AsyncMock(side_effect=["media-a", "media-b"]),
    )
    monkeypatch.setattr(channel, "_send_websocket_image", send_image)

    result = await channel.send(OutgoingMessage(
        chat_id="chat-1",
        text="",
        images=[OutgoingImage("image-a"), OutgoingImage("image-b")],
    ))

    request.assert_not_awaited()
    assert send_image.await_args_list == [
        call("chat-1", "media-a"),
        call("chat-1", "media-b"),
    ]
    assert result is True
```

Change the import to `from unittest.mock import AsyncMock, call`.

- [ ] **Step 2: Run the focused tests and verify current behavior fails**

Run:

```bash
uv run pytest \
  test/unit_test/api/channels/test_wecom_channel.py::test_websocket_text_send_returns_true_after_ack \
  test/unit_test/api/channels/test_wecom_channel.py::test_websocket_text_send_returns_false_when_ack_fails \
  test/unit_test/api/channels/test_wecom_channel.py::test_websocket_media_only_message_skips_markdown_and_sends_all_images \
  -q
```

Expected: return-value assertions fail and media-only send is rejected.

- [ ] **Step 3: Widen the base contract without forcing unrelated channels to change**

```python
# api/channels/core/base.py
@abstractmethod
async def send(self, message: OutgoingMessage) -> bool | None:
    """Send a message.

    Channels that can confirm delivery acknowledgement return True/False.
    Legacy channel adapters may return None.
    """
```

Only a channel with both `supports_reference_images=True` and an explicit `True` result can trigger post-response evidence work.

- [ ] **Step 4: Make WeCom WebSocket sends acknowledge text and allow media-only messages**

Change `send()` to return the delegated result:

```python
async def send(self, message: OutgoingMessage) -> bool:
    if self.connection_type == "websocket":
        return await self._send_websocket_message(message)
    # Preserve the webhook implementation, returning False on each existing
    # error branch and data.get("errcode", 0) == 0 after the POST.
```

Change the beginning and state tracking of `_send_websocket_message()`:

```python
async def _send_websocket_message(self, message: OutgoingMessage) -> bool:
    if not message.text and not message.images and not message.files:
        LOGGER.error("[%s:%s] empty websocket message", self.channel_id, self.account_id)
        return False
    if self._ws is None or self._ws.closed:
        LOGGER.error("[wecom:%s] websocket is not connected", self.account_id)
        return False

    sent_any = False
    if message.text:
        try:
            await self._ws_request(
                "aibot_send_msg",
                {
                    "chatid": message.chat_id,
                    "msgtype": "markdown",
                    "markdown": {"content": message.text},
                },
            )
            sent_any = True
        except Exception:
            LOGGER.error("[wecom:%s] websocket send failed", self.account_id, exc_info=True)
            return False
```

In the existing successful image and file branches set `sent_any = True`. Keep the per-media `try/except` blocks so one broken image does not block later images. Return `sent_any` after both loops.

- [ ] **Step 5: Run the full WeCom channel suite**

Run:

```bash
uv run pytest test/unit_test/api/channels/test_wecom_channel.py -q
```

Expected: existing text/image/file ordering tests and the three new contract tests all pass.

- [ ] **Step 6: Commit the channel capability**

```bash
git add \
  api/channels/core/base.py \
  api/channels/wecom/channel.py \
  test/unit_test/api/channels/test_wecom_channel.py
git commit -m "feat: support acknowledged media-only wecom sends"
```

---

### Task 6: Text-first channel orchestration and evidence-image mapping

**Files:**

- Modify: `api/channels/bootstrap.py`
- Modify: `test/unit_test/api/channels/test_bootstrap.py`

**Interfaces:**

- Consumes: final formatted chunks and `EvidenceResolution.used_chunk_ids`.
- Produces: `_images_for_used_chunks(chunks: object, used_chunk_ids: list[str]) -> list[OutgoingImage]`.

The handler sends:

```text
Conversation save
→ text + existing source files, images=[]
→ EvidenceService.resolve_for_dialog()
→ ConversationService.update_reference_evidence()
→ media-only image message
```

- [ ] **Step 1: Write failing image-mapping tests**

```python
# append to test/unit_test/api/channels/test_bootstrap.py
def test_images_for_used_chunks_follows_used_order_and_deduplicates_image_id():
    chunks = [
        {"id": "c1", "image_id": "img-shared"},
        {"id": "c2", "image_id": "img-two"},
        {"id": "c3", "image_id": "img-shared"},
        {"id": "c4", "image_id": ""},
    ]

    assert bootstrap._images_for_used_chunks(
        chunks,
        ["c2", "missing", "c3", "c1", "c4"],
    ) == [
        OutgoingImage("img-two"),
        OutgoingImage("img-shared"),
    ]


def test_images_for_used_chunks_never_uses_citation_index():
    chunks = [{"id": "stable-id", "image_id": "right"}]

    assert bootstrap._images_for_used_chunks(chunks, ["0"]) == []
```

- [ ] **Step 2: Run the helper tests and verify the helper is missing**

Run:

```bash
uv run pytest \
  test/unit_test/api/channels/test_bootstrap.py::test_images_for_used_chunks_follows_used_order_and_deduplicates_image_id \
  test/unit_test/api/channels/test_bootstrap.py::test_images_for_used_chunks_never_uses_citation_index \
  -q
```

Expected: both fail with `AttributeError`.

- [ ] **Step 3: Implement stable-ID image mapping**

```python
def _images_for_used_chunks(
    chunks: object,
    used_chunk_ids: list[str],
) -> list[OutgoingImage]:
    valid_chunks = chunks if isinstance(chunks, list) else []
    chunks_by_id = {
        str(chunk.get("id")): chunk
        for chunk in valid_chunks
        if isinstance(chunk, dict) and chunk.get("id")
    }
    images = []
    seen_image_ids = set()
    for chunk_id in used_chunk_ids:
        chunk = chunks_by_id.get(str(chunk_id))
        if not chunk:
            continue
        image_id = str(chunk.get("image_id") or "")
        if not image_id or image_id in seen_image_ids:
            continue
        seen_image_ids.add(image_id)
        images.append(OutgoingImage(image_id=image_id))
    return images
```

- [ ] **Step 4: Write failing end-to-end handler-order tests**

Add the following shared harness to `test_bootstrap.py`. It patches the names that
`_make_chat_handler()` imports locally, so the test exercises the real handler
without a database or model provider:

```python
from types import SimpleNamespace

import pytest

from api.channels.core.base import IncomingMessage
from rag.nlp.evidence import EvidenceResolution


async def _run_handler_case(
    monkeypatch,
    *,
    chunks,
    resolution,
    text_send_result=True,
    persist_result=True,
):
    events = []
    sent_messages = []
    conversation = SimpleNamespace(
        id="conversation-1",
        message=[],
        reference=[],
    )
    conversation.to_dict = lambda: {
        "id": conversation.id,
        "message": conversation.message,
        "reference": conversation.reference,
    }
    dialog = SimpleNamespace(
        id="dialog-1",
        kb_ids=["kb-1"],
        prompt_config={
            "quote": True,
            "send_source_file": True,
            "system": "{knowledge}",
        },
    )
    channel_connection = SimpleNamespace(chat_id=dialog.id)

    class FakeChatChannelService:
        @staticmethod
        def get_by_id(account_id):
            return True, channel_connection

    class FakeDialogService:
        @staticmethod
        def get_by_id(dialog_id):
            return True, dialog

    class FakeConversationService:
        @staticmethod
        def get_or_create_for_channel(dialog_id, account_id, chat_id):
            return conversation

        @staticmethod
        def update_by_id(conversation_id, data):
            events.append(("save", conversation_id))
            return 1

        @staticmethod
        def update_reference_evidence(conversation_id, message_id, used_chunk_ids):
            events.append(
                ("persist", conversation_id, message_id, used_chunk_ids)
            )
            return persist_result

    class FakeEvidenceService:
        @classmethod
        async def resolve_for_dialog(cls, dialog, question, answer, chunks):
            events.append(("resolve", answer))
            return resolution

    async def fake_async_chat(dialog, history, stream, **kwargs):
        yield {
            "answer": "回答正文。[ID:0]",
            "reference": {"chunks": chunks, "doc_aggs": []},
            "final": True,
        }

    send_results = iter([text_send_result, True])

    class FakeChannel:
        account_id = "account-1"
        channel_id = "wecom"
        supports_reference_images = True
        supports_source_files = True
        hides_reference_markers = True

        async def send(self, outgoing):
            sent_messages.append(outgoing)
            events.append((
                "send",
                outgoing.text,
                outgoing.images,
                outgoing.files,
            ))
            return next(send_results)

    import api.db.services.chat_channel_service as chat_channel_module
    import api.db.services.conversation_service as conversation_module
    import api.db.services.dialog_service as dialog_module
    import api.db.services.evidence_service as evidence_module
    import common.misc_utils as misc_module

    monkeypatch.setattr(
        chat_channel_module,
        "ChatChannelService",
        FakeChatChannelService,
    )
    monkeypatch.setattr(
        conversation_module,
        "ConversationService",
        FakeConversationService,
    )
    monkeypatch.setattr(dialog_module, "DialogService", FakeDialogService)
    monkeypatch.setattr(dialog_module, "async_chat", fake_async_chat)
    monkeypatch.setattr(evidence_module, "EvidenceService", FakeEvidenceService)
    monkeypatch.setattr(misc_module, "get_uuid", lambda: "message-1")

    handler = bootstrap._make_chat_handler(FakeChannel())
    await handler(IncomingMessage(
        channel="wecom",
        account_id="account-1",
        chat_id="chat-1",
        chat_type="single",
        message_id="incoming-1",
        sender_id="user-1",
        text="用户问题",
    ))
    return events, sent_messages
```

Assert the exact event shape:

```python
@pytest.mark.asyncio
async def test_handler_sends_text_before_resolving_and_sending_images(monkeypatch):
    chunks = [
        {
            "id": "chunk-1",
            "content": "证据一",
            "image_id": "image-1",
            "document_id": "doc-1",
            "document_name": "guide.pdf",
            "dataset_id": "kb-1",
        },
        {
            "id": "chunk-2",
            "content": "证据二",
            "image_id": "image-2",
        },
    ]
    resolution = EvidenceResolution(
        ["chunk-1", "chunk-2"],
        [],
        [],
        "resolved",
        12.0,
    )

    events, _ = await _run_handler_case(
        monkeypatch,
        chunks=chunks,
        resolution=resolution,
    )

    assert events == [
        ("save", "conversation-1"),
        ("send", "回答正文。", [], [OutgoingFile("doc-1", "guide.pdf")]),
        ("resolve", "回答正文。[ID:0]"),
        ("persist", "conversation-1", "message-1", ["chunk-1", "chunk-2"]),
        (
            "send",
            "",
            [OutgoingImage("image-1"), OutgoingImage("image-2")],
            [],
        ),
    ]
```

Also add these independent tests:

```python
@pytest.mark.asyncio
async def test_handler_does_not_start_evidence_when_text_send_returns_false(
    monkeypatch,
):
    events, sent_messages = await _run_handler_case(
        monkeypatch,
        chunks=[{"id": "c1", "content": "证据", "image_id": "image-1"}],
        resolution=EvidenceResolution(["c1"], [], [], "resolved", 1.0),
        text_send_result=False,
    )

    assert len(sent_messages) == 1
    assert not any(event[0] == "resolve" for event in events)


@pytest.mark.asyncio
async def test_handler_does_not_start_evidence_without_image_candidates(
    monkeypatch,
):
    events, sent_messages = await _run_handler_case(
        monkeypatch,
        chunks=[{"id": "c1", "content": "纯文字证据", "image_id": ""}],
        resolution=EvidenceResolution(["c1"], [], [], "resolved", 1.0),
    )

    assert len(sent_messages) == 1
    assert not any(event[0] == "resolve" for event in events)


@pytest.mark.asyncio
async def test_handler_still_sends_images_when_evidence_persistence_fails(
    monkeypatch,
):
    events, sent_messages = await _run_handler_case(
        monkeypatch,
        chunks=[{"id": "c1", "content": "证据", "image_id": "image-1"}],
        resolution=EvidenceResolution(["c1"], [], [], "resolved", 1.0),
        persist_result=False,
    )

    assert any(event[0] == "persist" for event in events)
    assert sent_messages[-1].images == [OutgoingImage("image-1")]


@pytest.mark.asyncio
async def test_handler_sends_no_images_on_timeout_or_error_resolution(
    monkeypatch,
):
    _, sent_messages = await _run_handler_case(
        monkeypatch,
        chunks=[{"id": "c1", "content": "证据", "image_id": "image-1"}],
        resolution=EvidenceResolution([], [], [], "error", 10_000.0, "timeout"),
    )

    assert len(sent_messages) == 1
```

- [ ] **Step 5: Replace citation-selected first-package images with post-response evidence**

Inside `_make_chat_handler()`:

1. Import `EvidenceService` with the other local service imports.
2. Keep `_prepare_cited_output()` for citation-marker cleanup and source files.
3. Stop assigning `cited_images` to the first outgoing message.
4. Save the conversation before the first send exactly as today.
5. Capture the explicit send result.
6. Only resolve evidence when the channel supports reference images, quote is enabled, the send result is exactly `True`, and at least one candidate has an `image_id`.

Use this concrete orchestration:

```python
text_send_result = await ch.send(
    OutgoingMessage(
        chat_id=msg.chat_id,
        text=answer_text,
        reply_to_message_id=msg.message_id or None,
        images=[],
        files=answer_files,
    )
)

has_image_candidate = any(
    isinstance(chunk, dict) and chunk.get("image_id")
    for chunk in reference.get("chunks", [])
)
should_resolve_evidence = (
    ch.supports_reference_images
    and bool((dia.prompt_config or {}).get("quote", True))
    and text_send_result is True
    and has_image_candidate
)
if not should_resolve_evidence:
    return

resolution = await EvidenceService.resolve_for_dialog(
    dia,
    msg.text,
    raw_answer,
    reference.get("chunks", []),
)
if resolution.status == "error":
    return

saved = ConversationService.update_reference_evidence(
    conv.id,
    message_id,
    resolution.used_chunk_ids,
)
if not saved:
    LOGGER.warning(
        "[%s:%s] evidence persistence failed conversation_id=%s message_id=%s",
        ch.channel_id,
        ch.account_id,
        conv.id,
        message_id,
    )

evidence_images = _images_for_used_chunks(
    reference.get("chunks", []),
    resolution.used_chunk_ids,
)
if evidence_images:
    await ch.send(
        OutgoingMessage(
            chat_id=msg.chat_id,
            text="",
            images=evidence_images,
        )
    )
```

Keep the evidence block in its own `try/except` after the first send so an adapter bug cannot enter the completion error path and cannot send an `**ERROR**` replacement after the user already received the answer.

- [ ] **Step 6: Add structured evidence logs at the channel boundary**

Log these fields without binary image data:

```python
LOGGER.info(
    "[%s:%s] evidence delivery conversation_id=%s message_id=%s "
    "status=%s used_chunk_ids=%s image_ids=%s duration_ms=%.1f",
    ch.channel_id,
    ch.account_id,
    conv.id,
    message_id,
    resolution.status,
    resolution.used_chunk_ids,
    [image.image_id for image in evidence_images],
    resolution.duration_ms,
)
```

For every early skip, log one stable reason from:

```text
text_send_unconfirmed
no_image_candidates
quote_disabled
unsupported_channel
resolution_error
no_trusted_evidence
```

- [ ] **Step 7: Run channel orchestration tests**

Run:

```bash
uv run pytest \
  test/unit_test/api/channels/test_bootstrap.py \
  test/unit_test/api/channels/test_wecom_channel.py \
  -q
```

Expected: first-package legacy cleanup/source-file tests pass, and all new tests prove text precedes resolution and images.

- [ ] **Step 8: Commit the first consumer**

```bash
git add \
  api/channels/bootstrap.py \
  test/unit_test/api/channels/test_bootstrap.py
git commit -m "feat: send evidence images after channel text"
```

---

### Task 7: Acceptance regression, lint, and release evidence gate

**Files:**

- Modify: `test/unit_test/rag/test_evidence.py`
- Modify: `test/unit_test/api/channels/test_bootstrap.py`
- Modify: `test/unit_test/api/channels/test_wecom_channel.py`

**Interfaces:**

- Consumes: all interfaces delivered by Tasks 1–6.
- Produces: an executable regression gate covering the approved examples and unchanged legacy behavior.

- [ ] **Step 1: Add parameterized approved-query regressions**

Add parameterized cases for:

```python
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
            "考勤异常在哪里处理",
            "在考勤日历中点击异常记录即可处理。",
            ["在考勤日历中点击异常记录即可处理。"],
        ),
        (
            "有没有处理考勤异常的流程图",
            "知识库未找到流程图。处理入口位于考勤日历的异常记录。",
            ["处理入口位于考勤日历的异常记录。"],
        ),
    ],
)
def test_approved_queries_produce_only_business_fact_segments(
    question,
    answer,
    expected_segments,
):
    assert [
        segment.text
        for segment in split_evidence_segments(question, answer)
    ] == expected_segments
```

- [ ] **Step 2: Add explicit no-wrong-image regression**

Construct a deterministic candidate set where:

- retrieval order 1 is an unrelated image Chunk;
- retrieval order 2 is the correct process image Chunk;
- retrieval order 3 is a text-only supporting Chunk;
- the reranker scores them `0.22`, `0.93`, and `0.81`.

Assert:

```python
assert result.used_chunk_ids == ["correct-image", "supporting-text"]
assert _images_for_used_chunks(chunks, result.used_chunk_ids) == [
    OutgoingImage("correct-process-image")
]
```

- [ ] **Step 3: Run the complete focused feature suite**

Run:

```bash
uv run pytest \
  test/unit_test/rag/test_evidence.py \
  test/unit_test/api/db/services/test_evidence_service.py \
  test/unit_test/api/db/services/test_conversation_service_evidence.py \
  test/unit_test/api/db/services/test_conversation_service_channel_binding.py \
  test/unit_test/api/channels/test_bootstrap.py \
  test/unit_test/api/channels/test_wecom_channel.py \
  test/unit_test/api/db/services/test_dialog_service_final_answer.py \
  test/unit_test/api/db/services/test_dialog_service_use_sql_source_columns.py \
  -q
```

Expected: all tests pass.

- [ ] **Step 4: Run static checks on changed Python files**

Run:

```bash
uv run ruff check \
  rag/nlp/evidence.py \
  api/db/services/evidence_service.py \
  api/db/services/dialog_service.py \
  api/db/services/conversation_service.py \
  api/channels/core/base.py \
  api/channels/wecom/channel.py \
  api/channels/bootstrap.py \
  test/unit_test/rag/test_evidence.py \
  test/unit_test/api/db/services/test_evidence_service.py \
  test/unit_test/api/db/services/test_conversation_service_evidence.py \
  test/unit_test/api/channels/test_bootstrap.py \
  test/unit_test/api/channels/test_wecom_channel.py
```

Expected: no lint errors.

- [ ] **Step 5: Verify repository hygiene and inspect the final diff**

Run:

```bash
git diff --check
git status --short
git diff --stat
```

Expected: no whitespace errors and only the files listed in this plan are modified.

- [ ] **Step 6: Perform the labeled release gate**

Before enabling the feature in production, sample at least 50 real enterprise-WeChat answers with image-bearing retrieval candidates and label, for every answer segment:

```text
question
answer segment
candidate chunk_id
candidate image_id
directly supports segment: yes/no
image should be sent: yes/no
```

Replay them with the fixed initial thresholds:

```text
min_hybrid_score = 0.55
min_rerank_score = 0.70
min_score_margin = 0.08
```

Calculate:

```text
used_chunk_precision = correctly selected used chunks / all selected used chunks
image_recall = correctly sent labeled images / all labeled images that should be sent
wrong_image_rate = incorrectly sent images / all sent images
```

Release only when `wrong_image_rate == 0` on the labeled set and every selected
`used_chunk_id` is a real candidate ID. Record `used_chunk_precision` and
`image_recall` as the baseline for later tuning. If any wrong image is selected,
raise the relevant threshold or margin and rerun all 50+ samples; do not lower a
threshold merely to increase image recall.

- [ ] **Step 7: Commit the acceptance gate**

```bash
git add \
  test/unit_test/rag/test_evidence.py \
  test/unit_test/api/channels/test_bootstrap.py \
  test/unit_test/api/channels/test_wecom_channel.py
git commit -m "test: cover post-response evidence delivery"
```

---

## Completion Checklist

- [ ] Text/source-file first package is byte-for-byte unchanged except that `images=[]`.
- [ ] Evidence model binding, embedding, vector fetch and reranking all start after confirmed text send.
- [ ] Only answer-segment embeddings are newly computed, in one `encode(list[str])` call.
- [ ] Chunk vectors come only from `fetch_chunk_vectors()` and are never written back.
- [ ] Reranker failure, timeout, missing vectors and low confidence produce no image.
- [ ] `[ID:n]` remains available for Web display but is never used for enterprise-WeChat image selection.
- [ ] `used_chunk_ids` are stable IDs from current `reference.chunks`.
- [ ] Persistence targets `conversation_id + message_id` under a row lock.
- [ ] Multiple correct images are ordered, deduplicated and sent after text.
- [ ] One image failure does not block later images.
- [ ] Web chat, source files and non-image channel behavior pass their existing tests.
- [ ] No schema migration, reindex, OCR, vision call or supplementary retrieval is introduced.
