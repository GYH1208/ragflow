# 低延迟引用图片选择实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将现有回答后图片解析器改造成“硬规则筛选 + 最多两组并行小规模 rerank + 严格拒绝”的低延迟策略，使一个独立回答点最多发送一张图、一次回答最多发送两张图。

**Architecture:** 文字回复继续先发送；纯函数核心负责清理可见回答、解析引用、构建只来自 `reference.chunks` 的候选短名单并独立判定每个回答点。服务层只创建 reranker、执行 900ms 硬超时并输出结构化日志；渠道层根据稳定 chunk ID 映射图片、去重并限制最多两张，不修改文档解析、拼图或企业微信媒体协议。

**Tech Stack:** Python 3.10+、asyncio、NumPy、pytest/pytest-asyncio、RAGFlow `LLMBundle` reranker、现有企业微信 Channel 抽象。

## Global Constraints

- 准确率优先：可以漏发图片，不能发送不相关图片。
- 候选只能来自当前回答已有的 `reference.chunks`；不得发起第二次知识库检索。
- 不调用新的 embedding，不加载 chunk 向量，不调用运行时视觉模型。
- 不修改文档解析器、chunk 边界、拼图逻辑、图片存储结构或企业微信媒体协议。
- 不引入 `image_sets`、`image_assets` 或 chunk 级图片 ID 列表。
- 拼接图继续作为单个不可拆分的 `image_id` 原样发送。
- 每个证据单元最多接受一张图片；最多处理两个证据单元；一次回答最多发送两张图片。
- 每个证据单元最多比较三个候选 chunk。
- 初始 `min_rerank_score = 0.75`，`min_score_margin = 0.10`，`timeout_seconds = 0.9`。
- 文字必须先发送；图片选择失败、超时或结果不确定时不得改变文字回复。
- info 日志不得记录完整问题、完整回答、文档正文或图片数据。

---

## 文件结构

### 修改

- `rag/nlp/evidence.py`
  - 定义证据 chunk、证据单元、单元决策和最终解析结果。
  - 清除 `<think>` 内容并保留引用索引。
  - 构建每个证据单元的候选短名单。
  - 并发执行最多两个 rerank 调用。
  - 应用引用、绝对分数、分差、去重和最多两张规则。
- `api/db/services/dialog_service.py`
  - 提供只创建 reranker 的 `get_rerank_model()`，避免证据解析阶段实例化 embedding。
  - 让现有 `get_retrieval_models()` 复用该入口。
- `api/db/services/evidence_service.py`
  - 将 `reference.chunks` 的稳定 ID、原始排名和三个检索分数字段转换为核心类型。
  - 只装配 reranker，不再加载向量或注入词法评分器。
  - 执行 900ms 硬超时、关闭模型并记录结构化决策日志。
- `api/channels/bootstrap.py`
  - 保持文字先发。
  - 将解析结果映射为最多两张、去重且按回答点顺序排列的 `OutgoingImage`。
- `api/channels/wecom/channel.py`
  - 不改变上传和发送协议，只为图片加载失败及发送失败补充稳定原因码。

### 测试

- `test/unit_test/rag/test_evidence.py`
  - 覆盖可见回答解析、引用保留、候选构建、严格判定、双回答点和真实事故回放。
- `test/unit_test/api/db/services/test_evidence_service.py`
  - 覆盖 reranker-only 装配、chunk 字段映射、900ms 超时、模型关闭和安全日志。
- `test/unit_test/api/channels/test_bootstrap.py`
  - 覆盖文字先发、最多两图、去重、顺序、持久化失败和解析失败。
- `test/unit_test/api/channels/test_wecom_channel.py`
  - 覆盖 `image_load_error` 和 `image_send_error` 日志原因码。
- `test/unit_test/api/db/services/test_dialog_service_final_answer.py`
  - 覆盖 `get_rerank_model()` 被正常检索模型装配复用且不会改变原有回答流程。

---

### Task 1: 定义证据单元并正确解析可见回答

**Files:**
- Modify: `rag/nlp/evidence.py:15-105`
- Test: `test/unit_test/rag/test_evidence.py:1-110`

**Interfaces:**
- Consumes: LLM 原始回答字符串，其中引用格式为 `[N]` 或 `[ID:N]`，数字可为 ASCII、阿拉伯-印度或波斯数字。
- Produces:
  - `EvidenceParseError(reason: str)`
  - `EvidenceUnit(index: int, text: str, citation_indexes: tuple[int, ...])`
  - `split_evidence_units(answer: str) -> list[EvidenceUnit]`

- [ ] **Step 1: 将旧分段测试改成带引用的证据单元失败测试**

```python
import pytest

from rag.nlp.evidence import EvidenceParseError, split_evidence_units


def test_split_evidence_units_removes_think_and_preserves_citations():
    answer = (
        "<think>内部分析引用 [ID:9]</think>"
        "1. 查看审批进度。[ID:0]\n"
        "2. 设置审批代理人。[1]"
    )

    units = split_evidence_units(answer)

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
    with pytest.raises(EvidenceParseError, match="malformed_think_markup"):
        split_evidence_units(answer)
```

- [ ] **Step 2: 运行测试并确认旧实现失败**

Run:

```bash
uv run pytest \
  test/unit_test/rag/test_evidence.py::test_split_evidence_units_removes_think_and_preserves_citations \
  test/unit_test/rag/test_evidence.py::test_split_evidence_units_rejects_malformed_think_markup \
  -v
```

Expected: FAIL，原因是 `EvidenceParseError` 或 `split_evidence_units` 尚未定义。

- [ ] **Step 3: 实现 think 清理、引用提取和证据单元数据类型**

```python
_CITATION_PATTERN = re.compile(
    r"\[(?:ID:)?([0-9\u0660-\u0669\u06F0-\u06F9]+)\]"
)
_THINK_TOKEN_PATTERN = re.compile(r"</?think>", re.IGNORECASE)


class EvidenceParseError(ValueError):
    pass


@dataclass(frozen=True)
class EvidenceUnit:
    index: int
    text: str
    citation_indexes: tuple[int, ...]


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
            visible.append(text[cursor:match.start()])
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
    raw_pieces = _SENTENCE_BOUNDARY.split(visible)
    units: list[EvidenceUnit] = []
    seen: set[tuple[str, tuple[int, ...]]] = set()
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
        if not citations or len(piece) < 5:
            continue

        key = (piece, citations)
        if key in seen:
            continue
        seen.add(key)
        units.append(
            EvidenceUnit(
                index=len(units),
                text=piece,
                citation_indexes=citations,
            )
        )
    return units
```

- [ ] **Step 4: 增加边界测试并运行解析器测试**

```python
def test_split_evidence_units_ignores_citation_only_and_duplicate_units():
    answer = (
        "[ID:0]\n"
        "查看审批进度。[ID:0]\n"
        "查看审批进度。[ID:0]\n"
        "知识库未找到截图。[ID:1]"
    )

    units = split_evidence_units(answer)

    assert [(unit.text, unit.citation_indexes) for unit in units] == [
        ("查看审批进度。", (0,)),
    ]


def test_split_evidence_units_reads_non_ascii_digits():
    units = split_evidence_units("查看审批进度。[ID:٢]\n设置代理人。[ID:۱]")

    assert [unit.citation_indexes for unit in units] == [(2,), (1,)]
```

Run:

```bash
uv run pytest test/unit_test/rag/test_evidence.py -k "split_evidence_units" -v
```

Expected: PASS。

- [ ] **Step 5: 提交证据单元解析**

```bash
git add rag/nlp/evidence.py test/unit_test/rag/test_evidence.py
git commit -m "refactor: parse cited visible evidence units"
```

---

### Task 2: 用原始 Top-N 构建严格候选短名单

**Files:**
- Modify: `rag/nlp/evidence.py:20-165`
- Test: `test/unit_test/rag/test_evidence.py`

**Interfaces:**
- Consumes:
  - `EvidenceUnit`
  - 按原始 `reference.chunks` 顺序排列的 `list[EvidenceChunk]`
  - `retrieval_similarity_threshold: float`
- Produces:
  - `EvidenceChunk(chunk_id, content, image_id, similarity, vector_similarity, term_similarity, retrieval_rank)`
  - `build_unit_shortlist(unit, chunks, retrieval_similarity_threshold, shortlist_size) -> list[EvidenceChunk]`

- [ ] **Step 1: 写出候选来源、引用优先和竞争候选测试**

```python
from rag.nlp.evidence import EvidenceChunk, EvidenceUnit, build_unit_shortlist


def _chunk(
    chunk_id,
    image_id,
    rank,
    similarity=0.8,
    vector_similarity=0.8,
    term_similarity=0.8,
):
    return EvidenceChunk(
        chunk_id=chunk_id,
        content=f"{chunk_id} 内容",
        image_id=image_id,
        similarity=similarity,
        vector_similarity=vector_similarity,
        term_similarity=term_similarity,
        retrieval_rank=rank,
    )


def test_shortlist_keeps_cited_image_then_adds_top_ranked_competitors():
    chunks = [
        _chunk("c0", "img0", 0),
        _chunk("c1", "img1", 1),
        _chunk("c2", "img2", 2),
        _chunk("c3", "img3", 3),
    ]
    unit = EvidenceUnit(0, "回答点", (2,))

    shortlist = build_unit_shortlist(unit, chunks, 0.2, 3)

    assert [chunk.chunk_id for chunk in shortlist] == ["c2", "c0", "c1"]


def test_shortlist_rejects_chunks_outside_hard_gates():
    chunks = [
        _chunk("no-image", "", 0),
        _chunk("low", "img-low", 1, similarity=0.1),
        _chunk("nan", "img-nan", 2, vector_similarity=float("nan")),
        _chunk("valid", "img-valid", 3),
    ]
    unit = EvidenceUnit(0, "回答点", (0, 1, 2, 3))

    shortlist = build_unit_shortlist(unit, chunks, 0.2, 3)

    assert [chunk.chunk_id for chunk in shortlist] == ["valid"]
```

- [ ] **Step 2: 运行候选测试并确认失败**

Run:

```bash
uv run pytest test/unit_test/rag/test_evidence.py -k "shortlist" -v
```

Expected: FAIL，原因是新 `EvidenceChunk` 字段或 `build_unit_shortlist()` 尚未实现。

- [ ] **Step 3: 替换旧向量字段并实现确定性硬门控**

```python
@dataclass(frozen=True)
class EvidenceChunk:
    chunk_id: str
    content: str
    image_id: str | None
    similarity: float
    vector_similarity: float
    term_similarity: float
    retrieval_rank: int


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
        if _passes_hard_gates(chunk, retrieval_similarity_threshold)
    ]
    cited = [
        chunks[index]
        for index in unit.citation_indexes
        if 0 <= index < len(chunks)
        and chunks[index] in eligible
    ]
    ordered: list[EvidenceChunk] = []
    for chunk in cited + sorted(eligible, key=lambda item: item.retrieval_rank):
        if chunk not in ordered:
            ordered.append(chunk)
        if len(ordered) == shortlist_size:
            break
    return ordered
```

- [ ] **Step 4: 增加“不做二次检索”和引用越界测试**

```python
def test_shortlist_ignores_out_of_range_citations_without_substitution():
    chunks = [_chunk("competitor", "img", 0)]
    unit = EvidenceUnit(0, "回答点", (9,))

    shortlist = build_unit_shortlist(unit, chunks, 0.2, 3)

    assert [chunk.chunk_id for chunk in shortlist] == ["competitor"]
    assert unit.citation_indexes == (9,)
```

该测试明确竞争候选可以进入比较，但后续决策必须因为胜出 chunk 未被引用而拒绝，不能把竞争候选静默发送。

Run:

```bash
uv run pytest test/unit_test/rag/test_evidence.py -k "shortlist" -v
```

Expected: PASS。

- [ ] **Step 5: 提交候选构建**

```bash
git add rag/nlp/evidence.py test/unit_test/rag/test_evidence.py
git commit -m "refactor: build strict image candidate shortlists"
```

---

### Task 3: 实现并行 Rerank 和每回答点严格拒绝

**Files:**
- Modify: `rag/nlp/evidence.py:35-327`
- Test: `test/unit_test/rag/test_evidence.py`

**Interfaces:**
- Consumes:
  - `resolve_evidence(question, answer, chunks, rerank_model, retrieval_similarity_threshold, config)`
  - reranker 接口 `similarity(query: str, documents: list[str]) -> tuple[array-like, int]`
- Produces:
  - `EvidenceDecision(unit_index, cited_chunk_ids, candidate_chunk_ids, selected_chunk_id, rerank_scores, margin, reason)`
  - `EvidenceMatch(segment_index, chunk_id, retrieval_score, rerank_score, rerank_margin)`
  - 向后兼容主要字段的 `EvidenceResolution`

测试文件在本任务开始时增加 `import dataclasses`；保留现有 `numpy as np` 和 `pytest` 导入。

- [ ] **Step 1: 定义新配置和结果类型的失败测试**

```python
from dataclasses import fields

from rag.nlp.evidence import (
    EvidenceConfig,
    EvidenceDecision,
    EvidenceMatch,
    EvidenceResolution,
)


def test_evidence_config_uses_precision_first_defaults():
    config = EvidenceConfig()

    assert config.max_evidence_units == 2
    assert config.shortlist_size == 3
    assert config.max_images == 2
    assert config.min_rerank_score == 0.75
    assert config.min_score_margin == 0.10
    assert config.timeout_seconds == 0.9


def test_resolution_keeps_existing_positional_fields_and_adds_decisions():
    result = EvidenceResolution([], [], [], "no_match", 1.0, "reason")

    assert result.decisions == []
    assert fields(EvidenceDecision)
    assert fields(EvidenceMatch)
```

- [ ] **Step 2: 运行类型和默认值测试并确认失败**

Run:

```bash
uv run pytest \
  test/unit_test/rag/test_evidence.py::test_evidence_config_uses_precision_first_defaults \
  test/unit_test/rag/test_evidence.py::test_resolution_keeps_existing_positional_fields_and_adds_decisions \
  -v
```

Expected: FAIL，旧配置仍包含 embedding/hybrid 参数且超时为 10 秒。

- [ ] **Step 3: 定义新结果类型并删除 embedding/向量接口**

```python
from dataclasses import dataclass, field


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
```

删除以下不再使用的接口和实现：

- `ChunkVectorLoader`
- `LexicalScorer`
- `_usable_vector`
- `_tokenize`
- `_lexical_scores`
- `_cosine_scores`
- `embedding_model.encode`
- chunk 向量加载
- hybrid shortlist 计算

- [ ] **Step 4: 写“原问题 + 回答点”、错误引用和分差测试**

```python
class FakeReranker:
    def __init__(self, scores):
        self.scores = scores
        self.calls = []

    def similarity(self, query, documents):
        self.calls.append((query, list(documents)))
        values = [self.scores[(query, document)] for document in documents]
        return np.asarray(values, dtype=float), 0


@pytest.mark.asyncio
async def test_resolver_rejects_wrong_citation_when_uncited_competitor_wins():
    question = "怎么查看报销申请的进度？"
    answer = "进入审批页面查看当前进度。[ID:1]"
    chunks = [
        _chunk("approval", "approval-image", 0),
        _chunk("proxy", "proxy-image", 1),
    ]
    chunks[0] = dataclasses.replace(
        chunks[0],
        content="进入报销审批页面，查看审批进度和当前节点",
    )
    chunks[1] = dataclasses.replace(
        chunks[1],
        content="设置我的代理人，代理报销或审批",
    )
    query = (
        "用户原始问题：\n怎么查看报销申请的进度？\n\n"
        "回答中的相关回答点：\n进入审批页面查看当前进度。"
    )
    reranker = FakeReranker(
        {
            (query, chunks[1].content): 0.62,
            (query, chunks[0].content): 0.80,
        }
    )

    result = await resolve_evidence(
        question,
        answer,
        chunks,
        reranker,
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
        dataclasses.replace(_chunk("a", "img-a", 0), content="打开审批记录"),
        dataclasses.replace(_chunk("b", "img-b", 1), content="打开审批流程"),
    ]
    query = (
        "用户原始问题：\n怎么处理审批？\n\n"
        "回答中的相关回答点：\n打开审批记录处理。"
    )
    reranker = FakeReranker(
        {
            (query, chunks[0].content): 0.86,
            (query, chunks[1].content): 0.80,
        }
    )

    result = await resolve_evidence(
        question,
        answer,
        chunks,
        reranker,
        0.2,
    )

    assert result.used_chunk_ids == []
    assert result.decisions[0].reason == "below_score_margin"
```

- [ ] **Step 5: 实现单元查询、并发 rerank 和严格接受**

```python
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
):
    scores, _ = await thread_pool_exec(
        rerank_model.similarity,
        _rerank_query(question, unit),
        [chunk.content for chunk in shortlist],
    )
    values = np.asarray(scores, dtype=float)
    if values.ndim != 1 or len(values) != len(shortlist):
        raise ValueError("reranker result shape does not match shortlist")
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
            pre_decisions.append(EvidenceDecision(
                unit_index=unit.index,
                cited_chunk_ids=[],
                candidate_chunk_ids=[],
                selected_chunk_id=None,
                rerank_scores=[],
                margin=None,
                reason="citation_not_found",
            ))
            continue
        shortlist = build_unit_shortlist(
            unit,
            chunks,
            retrieval_similarity_threshold,
            config.shortlist_size,
        )
        cited_chunk_ids = [
            chunks[index].chunk_id
            for index in unit.citation_indexes
            if 0 <= index < len(chunks)
            and chunks[index] in shortlist
        ]
        if not cited_chunk_ids:
            pre_unmatched.append(unit.index)
            pre_decisions.append(EvidenceDecision(
                unit_index=unit.index,
                cited_chunk_ids=[
                    chunk.chunk_id for chunk in resolved_citations
                ],
                candidate_chunk_ids=[
                    chunk.chunk_id for chunk in shortlist
                ],
                selected_chunk_id=None,
                rerank_scores=[],
                margin=None,
                reason="no_image_candidates",
            ))
            continue
        work.append((unit, shortlist, cited_chunk_ids))
        if len(work) == config.max_evidence_units:
            break

    if not work:
        reason = (
            pre_decisions[0].reason
            if pre_decisions
            else "no_visible_evidence_units"
        )
        return EvidenceResolution(
            used_chunk_ids=[],
            matches=[],
            unmatched_segments=(
                pre_unmatched
                if pre_unmatched
                else [unit.index for unit in units]
            ),
            status="no_match",
            duration_ms=(timer() - started_at) * 1000,
            reason=reason,
            decisions=pre_decisions,
        )

    rerank_calls = [
        _rerank_unit(question, unit, shortlist, rerank_model)
        for unit, shortlist, _ in work
    ]
    rerank_results = await asyncio.gather(
        *rerank_calls,
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
            decisions.append(EvidenceDecision(
                unit_index=unit.index,
                cited_chunk_ids=cited_chunk_ids,
                candidate_chunk_ids=[
                    chunk.chunk_id for chunk in shortlist
                ],
                selected_chunk_id=None,
                rerank_scores=[],
                margin=None,
                reason="rerank_error",
            ))
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
        elif margin is not None and margin < config.min_score_margin:
            reason = "below_score_margin"
        elif winner.image_id in seen_image_ids:
            reason = "duplicate_image"

        selected_chunk_id = (
            winner.chunk_id if reason == "accepted" else None
        )
        decisions.append(EvidenceDecision(
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
        ))
        if reason != "accepted":
            unmatched_segments.append(unit.index)
            continue

        seen_image_ids.add(str(winner.image_id))
        used_chunk_ids.append(winner.chunk_id)
        matches.append(EvidenceMatch(
            segment_index=unit.index,
            chunk_id=winner.chunk_id,
            retrieval_score=winner.similarity,
            rerank_score=float(winner_score),
            rerank_margin=margin,
        ))
        if len(used_chunk_ids) == config.max_images:
            break

    status: Literal["resolved", "no_match", "error"]
    if used_chunk_ids:
        status = "resolved"
        reason = None
    elif saw_rerank_error:
        status = "error"
        reason = "rerank_error"
    else:
        status = "no_match"
        reason = decisions[0].reason

    return EvidenceResolution(
        used_chunk_ids=used_chunk_ids,
        matches=matches,
        unmatched_segments=unmatched_segments,
        status=status,
        duration_ms=(timer() - started_at) * 1000,
        reason=reason,
        decisions=sorted(
            pre_decisions + decisions,
            key=lambda decision: decision.unit_index,
        ),
    )
```

- [ ] **Step 6: 写双回答点、单回答点最多一图和去重测试**

```python
@pytest.mark.asyncio
async def test_two_distinct_units_can_select_two_images_in_answer_order():
    question = "怎么查审批进度，怎么设置代理人？"
    answer = "查看审批记录。[ID:0]\n设置我的代理人。[ID:1]"
    chunks = [
        dataclasses.replace(_chunk("approval", "img-approval", 0), content="查看审批进度"),
        dataclasses.replace(_chunk("proxy", "img-proxy", 1), content="设置我的代理人"),
    ]
    first_query = (
        "用户原始问题：\n怎么查审批进度，怎么设置代理人？\n\n"
        "回答中的相关回答点：\n查看审批记录。"
    )
    second_query = (
        "用户原始问题：\n怎么查审批进度，怎么设置代理人？\n\n"
        "回答中的相关回答点：\n设置我的代理人。"
    )
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
        dataclasses.replace(_chunk("a", "img-a", 0), content="查看审批记录"),
        dataclasses.replace(_chunk("b", "img-b", 1), content="查看审批当前节点"),
    ]
    query = (
        "用户原始问题：\n怎么查审批进度？\n\n"
        "回答中的相关回答点：\n查看审批记录和当前节点。"
    )
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
        _chunk("approval", "img-approval", 0),
        content="查看审批记录",
    )
    query = (
        "用户原始问题：\n怎么查审批进度？\n\n"
        "回答中的相关回答点：\n查看审批记录。"
    )
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
            _chunk("progress", "shared-image", 0),
            content="查看审批进度",
        ),
        dataclasses.replace(
            _chunk("node", "shared-image", 1),
            content="查看审批节点",
        ),
    ]
    first_query = (
        "用户原始问题：\n怎么查进度和节点？\n\n"
        "回答中的相关回答点：\n查看审批进度。"
    )
    second_query = (
        "用户原始问题：\n怎么查进度和节点？\n\n"
        "回答中的相关回答点：\n查看审批节点。"
    )
    reranker = FakeReranker({
        (first_query, chunks[0].content): 0.95,
        (first_query, chunks[1].content): 0.40,
        (second_query, chunks[0].content): 0.30,
        (second_query, chunks[1].content): 0.94,
    })

    result = await resolve_evidence(question, answer, chunks, reranker, 0.2)

    assert result.used_chunk_ids == ["progress"]
    assert [decision.reason for decision in result.decisions] == [
        "accepted",
        "duplicate_image",
    ]


@pytest.mark.asyncio
async def test_non_finite_rerank_output_is_fail_closed():
    class NonFiniteReranker:
        def similarity(self, query, documents):
            return np.asarray([float("nan")]), 0

    result = await resolve_evidence(
        "怎么查审批？",
        "查看审批。[ID:0]",
        [_chunk("approval", "img", 0)],
        NonFiniteReranker(),
        0.2,
    )

    assert result.status == "error"
    assert result.used_chunk_ids == []
    assert result.decisions[0].reason == "rerank_error"
```

Run:

```bash
uv run pytest test/unit_test/rag/test_evidence.py -v
```

Expected: PASS。

- [ ] **Step 7: 提交严格决策核心**

```bash
git add rag/nlp/evidence.py test/unit_test/rag/test_evidence.py
git commit -m "feat: verify cited images with bounded reranking"
```

---

### Task 4: 装配 Reranker-only 服务和 900ms 硬超时

**Files:**
- Modify: `api/db/services/dialog_service.py:340-358`
- Modify: `api/db/services/evidence_service.py:1-158`
- Test: `test/unit_test/api/db/services/test_evidence_service.py`
- Test: `test/unit_test/api/db/services/test_dialog_service_final_answer.py`

**Interfaces:**
- Consumes:
  - `get_rerank_model(dialog, trace_context=None, langfuse_session_id=None)`
  - `EvidenceService.resolve_for_dialog(dialog, question, answer, chunks, config=None)`
- Produces:
  - 一个独立、可关闭的 reranker `LLMBundle | None`
  - 带原始检索分数和原始排名的 `EvidenceChunk`
  - 900ms 内完成的 `EvidenceResolution`

- [ ] **Step 1: 写“证据服务不创建或调用 embedding”的失败测试**

```python
@pytest.mark.asyncio
async def test_service_uses_only_reranker_and_preserves_reference_order(monkeypatch):
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
        {
            "id": "c1",
            "content": "第一条",
            "image_id": "img-1",
            "similarity": 0.80,
            "vector_similarity": 0.70,
            "term_similarity": 0.90,
        },
        {
            "id": "c2",
            "content": "第二条",
            "image_id": "img-2",
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
    reranker.close.assert_called_once()
```

- [ ] **Step 2: 运行服务测试并确认旧接口失败**

Run:

```bash
uv run pytest \
  test/unit_test/api/db/services/test_evidence_service.py::test_service_uses_only_reranker_and_preserves_reference_order \
  -v
```

Expected: FAIL，旧服务仍调用 `get_retrieval_models()` 并要求 embedding 和向量加载器。

- [ ] **Step 3: 在对话服务中提取独立 reranker 工厂**

```python
def get_rerank_model(
    dialog,
    trace_context=None,
    langfuse_session_id=None,
):
    if not dialog.rerank_id:
        return None
    rerank_model_config = get_model_config_from_provider_instance(
        dialog.tenant_id,
        LLMType.RERANK,
        dialog.rerank_id,
    )
    return LLMBundle(
        dialog.tenant_id,
        rerank_model_config,
        trace_context=trace_context,
        langfuse_session_id=langfuse_session_id,
    )
```

将 `get_retrieval_models()` 中原有 reranker 构造替换为：

```python
rerank_mdl = get_rerank_model(
    dialog,
    trace_context=trace_context,
    langfuse_session_id=langfuse_session_id,
)
```

在 `test_dialog_service_final_answer.py` 增加精确的工厂测试：

```python
from types import SimpleNamespace

from api.db.services import dialog_service


def test_get_rerank_model_returns_none_without_rerank_id():
    dialog = SimpleNamespace(
        tenant_id="tenant-1",
        rerank_id="",
    )

    assert dialog_service.get_rerank_model(dialog) is None


def test_get_rerank_model_uses_dialog_tenant_and_config(monkeypatch):
    config = {"llm_name": "reranker"}
    created = {}
    bundle = object()

    def fake_config(tenant_id, llm_type, rerank_id):
        created["config_args"] = (tenant_id, llm_type, rerank_id)
        return config

    def fake_bundle(tenant_id, model_config, **kwargs):
        created["bundle_args"] = (tenant_id, model_config, kwargs)
        return bundle

    monkeypatch.setattr(
        dialog_service,
        "get_model_config_from_provider_instance",
        fake_config,
    )
    monkeypatch.setattr(dialog_service, "LLMBundle", fake_bundle)
    dialog = SimpleNamespace(
        tenant_id="tenant-1",
        rerank_id="reranker-1",
    )

    result = dialog_service.get_rerank_model(
        dialog,
        trace_context={"trace_id": "trace-1"},
        langfuse_session_id="session-1",
    )

    assert result is bundle
    assert created["config_args"] == (
        "tenant-1",
        LLMType.RERANK,
        "reranker-1",
    )
    assert created["bundle_args"] == (
        "tenant-1",
        config,
        {
            "trace_context": {"trace_id": "trace-1"},
            "langfuse_session_id": "session-1",
        },
    )
```

该测试文件需要从 `common.constants import LLMType` 导入 `LLMType`；如果文件已有相同导入，则复用现有导入，不重复添加。

- [ ] **Step 4: 将 EvidenceService 改为 reranker-only**

```python
from api.db.services.dialog_service import get_rerank_model


def _score(value: object) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float("nan")


evidence_chunks = [
    EvidenceChunk(
        chunk_id=str(chunk.get("id") or ""),
        content=str(chunk.get("content") or ""),
        image_id=(str(chunk.get("image_id") or "") or None),
        similarity=_score(chunk.get("similarity")),
        vector_similarity=_score(chunk.get("vector_similarity")),
        term_similarity=_score(chunk.get("term_similarity")),
        retrieval_rank=index,
    )
    for index, chunk in enumerate(chunks)
    if isinstance(chunk, dict)
]
```

服务层调用必须精确为：

```python
deadline = started_at + config.timeout_seconds
rerank_model = get_rerank_model(dialog)
if rerank_model is None:
    return _error_resolution(started_at, "model_unavailable")

remaining_seconds = deadline - timer()
if remaining_seconds <= 0:
    return _error_resolution(started_at, "rerank_timeout")

result = await asyncio.wait_for(
    resolve_evidence(
        question=question,
        answer=answer,
        chunks=evidence_chunks,
        rerank_model=rerank_model,
        retrieval_similarity_threshold=float(dialog.similarity_threshold),
        config=config,
    ),
    timeout=remaining_seconds,
)

score_by_id = {
    chunk.chunk_id: {
        "similarity": round(chunk.similarity, 4),
        "vector_similarity": round(chunk.vector_similarity, 4),
        "term_similarity": round(chunk.term_similarity, 4),
        "retrieval_rank": chunk.retrieval_rank,
    }
    for chunk in evidence_chunks
}
LOGGER.info(
    "evidence resolved dialog_id=%s status=%s candidates=%d "
    "used_chunk_ids=%s "
    "decisions=%s duration_ms=%.1f",
    getattr(dialog, "id", ""),
    result.status,
    len(evidence_chunks),
    result.used_chunk_ids,
    [
        {
            "unit_index": decision.unit_index,
            "cited_chunk_ids": decision.cited_chunk_ids,
            "candidate_chunk_ids": decision.candidate_chunk_ids,
            "original_scores": {
                chunk_id: score_by_id.get(chunk_id)
                for chunk_id in decision.candidate_chunk_ids
            },
            "selected_chunk_id": decision.selected_chunk_id,
            "rerank_scores": [
                (chunk_id, round(score, 4))
                for chunk_id, score in decision.rerank_scores
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
```

`finally` 只关闭 `rerank_model`。删除 embedding、知识库 tenant ID、chunk 向量加载和词法评分器代码。

超时分支必须使用稳定原因码：

```python
except asyncio.TimeoutError:  # noqa: UP041
    LOGGER.warning(
        "evidence resolution timed out reason=rerank_timeout "
        "timeout_seconds=%.3f",
        config.timeout_seconds,
    )
    return _error_resolution(started_at, "rerank_timeout")
```

- [ ] **Step 5: 更新超时、模型缺失和结构化日志测试**

```python
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
        [{
            "id": "c1",
            "content": "证据",
            "image_id": "img",
            "similarity": 0.8,
            "vector_similarity": 0.8,
            "term_similarity": 0.8,
        }],
        EvidenceConfig(timeout_seconds=0.01),
    )

    assert result.status == "error"
    assert result.reason == "rerank_timeout"
    assert result.used_chunk_ids == []
    reranker.close.assert_called_once()
```

增加结构化日志测试：

```python
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
        SimpleNamespace(similarity_threshold=0.2, rerank_id="r1"),
        "不得写入日志的完整问题",
        "不得写入日志的完整回答。[ID:0]",
        [{
            "id": "c1",
            "content": "证据正文",
            "image_id": "img",
            "similarity": 0.8,
            "vector_similarity": 0.8,
            "term_similarity": 0.8,
        }],
    )

    assert "candidate_chunk_ids" in caplog.text
    assert "accepted" in caplog.text
    assert "12.5" in caplog.text
    assert "不得写入日志的完整问题" not in caplog.text
    assert "不得写入日志的完整回答" not in caplog.text
    assert "证据正文" not in caplog.text
```

测试文件需要增加 `import logging`，并从 `rag.nlp.evidence` 导入 `EvidenceDecision`。

Run:

```bash
uv run pytest \
  test/unit_test/api/db/services/test_evidence_service.py \
  test/unit_test/api/db/services/test_dialog_service_final_answer.py \
  -v
```

Expected: PASS。

- [ ] **Step 6: 提交服务层改造**

```bash
git add \
  api/db/services/dialog_service.py \
  api/db/services/evidence_service.py \
  test/unit_test/api/db/services/test_evidence_service.py \
  test/unit_test/api/db/services/test_dialog_service_final_answer.py
git commit -m "refactor: resolve image evidence with reranker only"
```

---

### Task 5: 在渠道边界强制最多两张并保持发送顺序

**Files:**
- Modify: `api/channels/bootstrap.py:126-147`
- Modify: `api/channels/bootstrap.py:288-405`
- Modify: `api/channels/wecom/channel.py:697-725`
- Test: `test/unit_test/api/channels/test_bootstrap.py:68-410`
- Test: `test/unit_test/api/channels/test_wecom_channel.py:345-420`

**Interfaces:**
- Consumes:
  - `reference.chunks`
  - 按证据单元顺序排列的 `EvidenceResolution.used_chunk_ids`
- Produces:
  - `_images_for_used_chunks(chunks, used_chunk_ids, max_images=2) -> list[OutgoingImage]`
  - 一次独立图片消息，其中图片数量为 0、1 或 2。

- [ ] **Step 1: 写渠道层最多两张和去重测试**

```python
def test_images_for_used_chunks_caps_at_two_after_deduplication():
    chunks = [
        {"id": "c1", "image_id": "img-shared"},
        {"id": "c2", "image_id": "img-two"},
        {"id": "c3", "image_id": "img-three"},
        {"id": "c4", "image_id": "img-shared"},
    ]

    images = bootstrap._images_for_used_chunks(
        chunks,
        ["c4", "c2", "c3", "c1"],
    )

    assert images == [
        OutgoingImage("img-shared"),
        OutgoingImage("img-two"),
    ]
```

- [ ] **Step 2: 运行测试并确认旧实现会返回三张**

Run:

```bash
uv run pytest \
  test/unit_test/api/channels/test_bootstrap.py::test_images_for_used_chunks_caps_at_two_after_deduplication \
  -v
```

Expected: FAIL，旧 helper 在去重后仍返回 `img-three`。

- [ ] **Step 3: 在稳定 ID 映射边界增加防御性上限**

```python
def _images_for_used_chunks(
    chunks: object,
    used_chunk_ids: list[str],
    max_images: int = 2,
) -> list[OutgoingImage]:
    valid_chunks = chunks if isinstance(chunks, list) else []
    chunks_by_id = {
        str(chunk.get("id")): chunk
        for chunk in valid_chunks
        if isinstance(chunk, dict) and chunk.get("id")
    }
    images: list[OutgoingImage] = []
    seen_image_ids: set[str] = set()
    for chunk_id in used_chunk_ids:
        chunk = chunks_by_id.get(str(chunk_id))
        if not chunk:
            continue
        image_id = str(chunk.get("image_id") or "")
        if not image_id or image_id in seen_image_ids:
            continue
        seen_image_ids.add(image_id)
        images.append(OutgoingImage(image_id=image_id))
        if len(images) == max_images:
            break
    return images
```

- [ ] **Step 4: 更新渠道集成测试，确认文字先发和两图顺序**

```python
@pytest.mark.asyncio
async def test_handler_sends_two_verified_images_after_text_in_unit_order(
    monkeypatch,
):
    chunks = [
        {"id": "approval", "content": "审批进度", "image_id": "img-approval"},
        {"id": "proxy", "content": "代理设置", "image_id": "img-proxy"},
    ]
    resolution = EvidenceResolution(
        ["approval", "proxy"],
        [],
        [],
        "resolved",
        20.0,
    )

    events, sent_messages = await _run_handler_case(
        monkeypatch,
        question="怎么查进度和设置代理人？",
        answer="查审批进度。[ID:0]\n设置代理人。[ID:1]",
        chunks=chunks,
        resolution=resolution,
    )

    assert sent_messages[0].text == "查审批进度。\n设置代理人。"
    assert sent_messages[0].images == []
    assert sent_messages[1].text == ""
    assert sent_messages[1].images == [
        OutgoingImage("img-approval"),
        OutgoingImage("img-proxy"),
    ]
    assert [event[0] for event in events].index("resolve") > [
        event[0] for event in events
    ].index("send")
```

同时保留并更新以下现有行为测试：

- 文字发送返回 `False` 时不解析图片；
- 没有带图片 chunk 时不解析；
- 解析状态为 `error` 时不发送图片；
- 证据持久化失败时仍发送已验证图片；
- 引用编号不能被当作稳定 chunk ID。

Run:

```bash
uv run pytest test/unit_test/api/channels/test_bootstrap.py -v
```

Expected: PASS。

- [ ] **Step 5: 为企业微信图片失败日志增加稳定原因码**

图片不存在时将日志改为：

```python
LOGGER.error(
    "[wecom:%s] image skipped reason=image_load_error image_id=%s",
    self.account_id,
    image.image_id,
)
```

图片上传或发送抛出异常时将日志改为：

```python
LOGGER.error(
    "[wecom:%s] image send failed reason=image_send_error image_id=%s",
    self.account_id,
    image.image_id,
    exc_info=True,
)
```

不改变 `_load_stored_image()`、`_upload_websocket_image()`、`_send_websocket_image()` 的参数、返回值或调用顺序。

在现有测试中加入日志断言：

```python
@pytest.mark.asyncio
async def test_missing_stored_image_logs_stable_reason(
    monkeypatch,
    caplog,
):
    channel = make_channel()
    monkeypatch.setattr(channel, "_ws_request", AsyncMock(return_value={"body": {}}))
    monkeypatch.setattr(channel, "_load_stored_image", lambda image_id: None)
    caplog.set_level("ERROR")

    await channel.send(OutgoingMessage(
        chat_id="chat-1",
        text="answer",
        images=[OutgoingImage("missing")],
    ))

    assert "reason=image_load_error" in caplog.text
    assert "image_id=missing" in caplog.text


@pytest.mark.asyncio
async def test_image_send_failure_logs_stable_reason(
    monkeypatch,
    caplog,
):
    channel = make_channel()
    monkeypatch.setattr(channel, "_ws_request", AsyncMock(return_value={"body": {}}))
    monkeypatch.setattr(channel, "_load_stored_image", lambda image_id: b"image")
    monkeypatch.setattr(
        channel,
        "_upload_websocket_image",
        AsyncMock(side_effect=RuntimeError("upload failed")),
    )
    caplog.set_level("ERROR")

    await channel.send(OutgoingMessage(
        chat_id="chat-1",
        text="answer",
        images=[OutgoingImage("broken")],
    ))

    assert "reason=image_send_error" in caplog.text
    assert "image_id=broken" in caplog.text
```

Run:

```bash
uv run pytest \
  test/unit_test/api/channels/test_bootstrap.py \
  test/unit_test/api/channels/test_wecom_channel.py \
  -v
```

Expected: PASS。

- [ ] **Step 6: 提交渠道边界**

```bash
git add \
  api/channels/bootstrap.py \
  api/channels/wecom/channel.py \
  test/unit_test/api/channels/test_bootstrap.py \
  test/unit_test/api/channels/test_wecom_channel.py
git commit -m "feat: cap trusted reference images at two"
```

---

### Task 6: 加入真实事故回放和完整回归验证

**Files:**
- Modify: `test/unit_test/rag/test_evidence.py`
- Modify: `test/unit_test/api/channels/test_bootstrap.py`
- Verify: `rag/nlp/evidence.py`
- Verify: `api/db/services/evidence_service.py`
- Verify: `api/db/services/dialog_service.py`
- Verify: `api/channels/bootstrap.py`
- Verify: `api/channels/wecom/channel.py`

**Interfaces:**
- Consumes: Tasks 1–5 的最终接口。
- Produces: 可重复的真实误发回归用例、双回答点正例以及完整 lint/test 证据。

- [ ] **Step 1: 写“审批进度误发我的代理人”的确定性回归测试**

```python
@pytest.mark.asyncio
async def test_approval_progress_never_sends_wrong_proxy_citation():
    question = "怎么查看报销申请的进度？"
    answer = "打开企业微信审批页面查看当前审批进度。[ID:1]"
    chunks = [
        EvidenceChunk(
            chunk_id="eab6502820610ded",
            content="进入报销审批页面，查看审批进度、当前审批人和处理节点",
            image_id="approval-progress-image",
            similarity=0.82,
            vector_similarity=0.79,
            term_similarity=0.86,
            retrieval_rank=0,
        ),
        EvidenceChunk(
            chunk_id="dbb4bfdaf5619fa5",
            content="打开企业微信工作台，设置我的代理人，代理报销或审批",
            image_id="my-proxy-image",
            similarity=0.76,
            vector_similarity=0.74,
            term_similarity=0.80,
            retrieval_rank=1,
        ),
    ]
    query = (
        "用户原始问题：\n怎么查看报销申请的进度？\n\n"
        "回答中的相关回答点：\n打开企业微信审批页面查看当前审批进度。"
    )
    reranker = FakeReranker(
        {
            (query, chunks[1].content): 0.6262,
            (query, chunks[0].content): 0.7986,
        }
    )

    result = await resolve_evidence(question, answer, chunks, reranker, 0.2)

    assert result.status == "no_match"
    assert result.used_chunk_ids == []
    assert result.decisions[0].reason == "cited_candidate_not_top1"
```

该测试复现“正确 chunk 为原始第 0 条，但回答尾部错误引用第 1 条”的真实故障。正确的未引用竞争候选只能阻止错误图片，不能被自动替换发送。

- [ ] **Step 2: 写第二回答点失败但第一回答点可发送的测试**

```python
@pytest.mark.asyncio
async def test_failed_second_unit_does_not_suppress_confirmed_first_unit():
    question = "怎么查审批进度，怎么设置代理人？"
    answer = "查看审批记录。[ID:0]\n设置代理人。[ID:1]"
    chunks = [
        dataclasses.replace(_chunk("approval", "img-approval", 0), content="查看审批进度"),
        dataclasses.replace(_chunk("proxy", "img-proxy", 1), content="设置审批代理"),
        dataclasses.replace(_chunk("other", "img-other", 2), content="审批代理说明"),
    ]
    first_query = (
        "用户原始问题：\n怎么查审批进度，怎么设置代理人？\n\n"
        "回答中的相关回答点：\n查看审批记录。"
    )
    second_query = (
        "用户原始问题：\n怎么查审批进度，怎么设置代理人？\n\n"
        "回答中的相关回答点：\n设置代理人。"
    )
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

    result = await resolve_evidence(question, answer, chunks, reranker, 0.2)

    assert result.used_chunk_ids == ["approval"]
    assert [decision.reason for decision in result.decisions] == [
        "accepted",
        "below_score_margin",
    ]
```

- [ ] **Step 3: 运行三组目标测试**

Run:

```bash
uv run pytest \
  test/unit_test/rag/test_evidence.py \
  test/unit_test/api/db/services/test_evidence_service.py \
  test/unit_test/api/channels/test_bootstrap.py \
  -q
```

Expected: PASS，且没有 warning 被升级为测试错误。

- [ ] **Step 4: 运行相关对话、持久化和企业微信回归测试**

Run:

```bash
uv run pytest \
  test/unit_test/api/db/services/test_conversation_service_evidence.py \
  test/unit_test/api/db/services/test_dialog_service_final_answer.py \
  test/unit_test/api/channels/test_wecom_channel.py \
  -q
```

Expected: PASS。

- [ ] **Step 5: 运行 Ruff**

Run:

```bash
uv run ruff check \
  rag/nlp/evidence.py \
  api/db/services/evidence_service.py \
  api/db/services/dialog_service.py \
  api/channels/bootstrap.py \
  api/channels/wecom/channel.py \
  test/unit_test/rag/test_evidence.py \
  test/unit_test/api/db/services/test_evidence_service.py \
  test/unit_test/api/db/services/test_dialog_service_final_answer.py \
  test/unit_test/api/channels/test_bootstrap.py \
  test/unit_test/api/channels/test_wecom_channel.py
```

Expected: `All checks passed!`

- [ ] **Step 6: 检查改动范围和禁止项**

Run:

```bash
git diff --check
git status --short
git diff --stat
rg -n "embedding_model|chunk_vector_loader|fetch_chunk_vectors|image_sets|image_assets" \
  rag/nlp/evidence.py \
  api/db/services/evidence_service.py
```

Expected:

- `git diff --check` 无输出；
- 只有计划列出的实现和测试文件发生变化；
- 最后一条 `rg` 无输出；
- 文档解析、图片拼接和企业微信媒体协议没有实现改动；企业微信渠道文件只允许日志文案变化。

- [ ] **Step 7: 提交事故回归测试和最终验证调整**

```bash
git add \
  test/unit_test/rag/test_evidence.py \
  test/unit_test/api/channels/test_bootstrap.py
git commit -m "test: prevent unrelated reference image delivery"
```

如果 Step 3–6 未产生新的测试文件修改，则不创建空提交；保留前面任务的提交作为最终可审查提交序列。

---

## 完成定义

实现只有在以下条件全部满足时才算完成：

- 文字回复在图片解析前发送；
- 图片候选只来自 `reference.chunks`；
- 证据解析阶段没有 embedding 调用或 chunk 向量加载；
- 每个证据单元最多三个候选、最多一个胜出图片；
- 最多两个不同证据单元参与并发 rerank；
- 一次回答最多发送两张去重图片；
- 错误引用不能被未引用竞争候选替换发送；
- `0.75` 绝对分数、`0.10` 分差和 `0.9s` 超时均为可配置默认值；
- 真实“审批进度/我的代理人”事故回归通过；
- 三组目标测试、相关集成测试和 Ruff 全部通过；
- 没有修改图片解析、拼图、存储和企业微信媒体协议。
