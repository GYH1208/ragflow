# 聊天引用与来源文件一致性修复实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让聊天回答的行内引用、reference chunks 和来源文件列表保持一致，并在引用编号越界时严格返回空来源而不是全部检索候选文档。

**Architecture:** 保留通用检索层对全部有效候选生成 `doc_aggs` 的既有语义，在聊天回答装饰阶段校验引用编号并按有效 chunk 重建最小来源文件集合。Web 端通过共享引用工具过滤历史错误数据，并在旧版和新版聊天 Markdown 渲染器中拒绝越界 `Fig.`。

**Tech Stack:** Python 3.10+、pytest、Quart/Flask 服务层、TypeScript、React 18、Jest、Ruff、ESLint。

## Global Constraints

- 全部 Git commit message 必须使用中文。
- 不修改 `rag/nlp/search.py` 的候选聚合语义。
- 不新增配置项、数据库字段或数据库迁移。
- 显式引用全部越界时必须返回空 `doc_aggs`，不得回退到候选文档。
- 无效引用不得渲染为可交互 `Fig.`。
- 日志不得包含完整问题、完整回答或 chunk 正文。
- 保持 reference JSON 的 `chunks`、`doc_aggs`、`total` 及既有可选字段结构兼容。
- 实现遵循 TDD：先写失败测试，再做最小实现。

---

## 文件结构

- Modify: `api/db/services/dialog_service.py`
  - 新增回答引用规范化和按有效引用聚合文档的纯函数。
  - 在 `decorate_answer()` 中接入严格引用收口。
  - 记录不含正文内容的结构化诊断日志。
- Modify: `test/unit_test/api/db/services/test_dialog_service_final_answer.py`
  - 覆盖纯函数边界、流式聊天最终 reference 和无引用自动插入路径。
- Modify: `web/src/utils/citation-utils.ts`
  - 提供数组/对象两种 reference 结构共用的 chunk 访问、引用有效性和文档过滤函数。
- Create: `web/src/utils/__tests__/citation-utils.test.ts`
  - 覆盖历史错误数据、混合引用、重复文档和无行内引用兼容行为。
- Modify: `web/src/components/markdown-content/index.tsx`
  - 旧聊天 Markdown 拒绝渲染越界引用。
- Modify: `web/src/components/next-markdown-content/index.tsx`
  - 新聊天 Markdown 拒绝渲染越界引用。
- Modify: `web/src/components/message-item/index.tsx`
  - 旧聊天来源文件列表使用共享过滤结果。
- Modify: `web/src/components/next-message-item/index.tsx`
  - 新聊天来源文件列表使用共享过滤结果。
- Reference only: `docs/superpowers/specs/2026-07-30-chat-reference-consistency-design.md`
  - 已批准设计和验收标准，不在实施阶段改写需求。

---

### Task 1: 新增后端引用规范化纯函数

**Files:**

- Modify: `api/db/services/dialog_service.py:517-565`
- Modify: `test/unit_test/api/db/services/test_dialog_service_final_answer.py`

**Interfaces:**

- Produces: `_normalize_answer_citations(answer: str, chunk_count: int) -> tuple[str, set[int], list[int], int]`
- Produces: `_build_cited_doc_aggs(chunks: list[dict], cited_indices: set[int]) -> list[dict]`
- Consumes: `CITATION_MARKER_PATTERN`、`normalize_arabic_digits`

- [ ] **Step 1: 写引用规范化失败测试**

在 `test_dialog_service_final_answer.py` 中新增以下测试。测试要求保留有效标记、删除越界
标记，并正确处理阿拉伯数字和波斯数字：

```python
@pytest.mark.parametrize(
    ("answer", "chunk_count", "expected_answer", "expected_valid", "expected_invalid", "expected_count"),
    [
        (
            "有效 [ID:0]，无效 [ID:42]。",
            10,
            "有效 [ID:0]，无效 。",
            {0},
            [42],
            2,
        ),
        (
            "阿拉伯数字 [ID:١]，波斯数字 [۲]。",
            3,
            "阿拉伯数字 [ID:١]，波斯数字 [۲]。",
            {1, 2},
            [],
            2,
        ),
        (
            "全部越界 [ID:42][ID:43]。",
            10,
            "全部越界 。",
            set(),
            [42, 43],
            2,
        ),
    ],
)
def test_normalize_answer_citations(
    answer,
    chunk_count,
    expected_answer,
    expected_valid,
    expected_invalid,
    expected_count,
):
    result = dialog_service._normalize_answer_citations(answer, chunk_count)

    assert result == (
        expected_answer,
        expected_valid,
        expected_invalid,
        expected_count,
    )
```

- [ ] **Step 2: 运行测试确认失败**

Run:

```bash
uv run pytest test/unit_test/api/db/services/test_dialog_service_final_answer.py \
  -k normalize_answer_citations -v
```

Expected: FAIL，提示 `dialog_service` 没有 `_normalize_answer_citations`。

- [ ] **Step 3: 实现最小引用规范化函数**

在 `CITATION_MARKER_PATTERN` 后新增：

```python
def _normalize_answer_citations(
    answer: str,
    chunk_count: int,
) -> tuple[str, set[int], list[int], int]:
    valid_indices: set[int] = set()
    invalid_indices: list[int] = []
    citation_count = 0

    def replace_marker(match: re.Match) -> str:
        nonlocal citation_count
        citation_count += 1
        digits = normalize_arabic_digits(match.group(1))
        try:
            index = int(digits)
        except (TypeError, ValueError):
            return ""
        if 0 <= index < chunk_count:
            valid_indices.add(index)
            return match.group(0)
        invalid_indices.append(index)
        return ""

    cleaned_answer = CITATION_MARKER_PATTERN.sub(replace_marker, answer or "")
    return (
        cleaned_answer,
        valid_indices,
        list(dict.fromkeys(invalid_indices)),
        citation_count,
    )
```

不得对完整回答调用 `normalize_arabic_digits()`，否则会意外修改正文中的非引用数字；只
规范化捕获到的引用数字。

- [ ] **Step 4: 运行引用规范化测试确认通过**

Run:

```bash
uv run pytest test/unit_test/api/db/services/test_dialog_service_final_answer.py \
  -k normalize_answer_citations -v
```

Expected: PASS。

- [ ] **Step 5: 写文档聚合失败测试**

新增以下测试数据和断言，锁定按 `doc_id` 去重、按引用顺序输出、同文档 chunk 计数和
同名不同 ID 行为：

```python
def test_build_cited_doc_aggs_deduplicates_by_doc_id():
    chunks = [
        {
            "chunk_id": "chunk-0",
            "doc_id": "doc-a",
            "docnm_kwd": "模板.docx",
        },
        {
            "chunk_id": "chunk-1",
            "doc_id": "doc-a",
            "docnm_kwd": "模板.docx",
        },
        {
            "chunk_id": "chunk-2",
            "doc_id": "doc-b",
            "docnm_kwd": "模板.docx",
            "url": "https://example.test/doc-b",
        },
        {
            "chunk_id": "chunk-3",
            "doc_id": "",
            "docnm_kwd": "缺少ID.docx",
        },
    ]

    assert dialog_service._build_cited_doc_aggs(
        chunks,
        {0, 1, 2, 3, 99},
    ) == [
        {
            "doc_id": "doc-a",
            "doc_name": "模板.docx",
            "count": 2,
        },
        {
            "doc_id": "doc-b",
            "doc_name": "模板.docx",
            "count": 1,
            "url": "https://example.test/doc-b",
        },
    ]
```

- [ ] **Step 6: 运行聚合测试确认失败**

Run:

```bash
uv run pytest test/unit_test/api/db/services/test_dialog_service_final_answer.py \
  -k build_cited_doc_aggs -v
```

Expected: FAIL，提示 `_build_cited_doc_aggs` 不存在。

- [ ] **Step 7: 实现最小文档聚合函数**

在引用规范化函数后新增：

```python
def _build_cited_doc_aggs(
    chunks: list[dict],
    cited_indices: set[int],
) -> list[dict]:
    docs_by_id: dict[str, dict] = {}

    for index in sorted(cited_indices):
        if index < 0 or index >= len(chunks):
            continue
        chunk = chunks[index]
        doc_id = str(chunk.get("doc_id") or chunk.get("document_id") or "")
        if not doc_id:
            continue
        if doc_id not in docs_by_id:
            doc = {
                "doc_id": doc_id,
                "doc_name": (
                    chunk.get("docnm_kwd")
                    or chunk.get("document_name")
                    or ""
                ),
                "count": 0,
            }
            if chunk.get("url"):
                doc["url"] = chunk["url"]
            docs_by_id[doc_id] = doc
        docs_by_id[doc_id]["count"] += 1

    return list(docs_by_id.values())
```

- [ ] **Step 8: 运行 Task 1 全部目标测试**

Run:

```bash
uv run pytest test/unit_test/api/db/services/test_dialog_service_final_answer.py \
  -k "normalize_answer_citations or build_cited_doc_aggs" -v
uv run ruff check api/db/services/dialog_service.py \
  test/unit_test/api/db/services/test_dialog_service_final_answer.py
```

Expected: pytest PASS，Ruff 无新增错误。

- [ ] **Step 9: 提交 Task 1**

```bash
git add api/db/services/dialog_service.py \
  test/unit_test/api/db/services/test_dialog_service_final_answer.py
git commit -m "修复：新增聊天引用规范化工具"
```

---

### Task 2: 在聊天回答装饰阶段严格收口 reference

**Files:**

- Modify: `api/db/services/dialog_service.py:826-868`
- Modify: `test/unit_test/api/db/services/test_dialog_service_final_answer.py`

**Interfaces:**

- Consumes: `_normalize_answer_citations(...)`
- Consumes: `_build_cited_doc_aggs(...)`
- Preserves: `decorate_answer()` 返回 `{"answer", "reference", "prompt", "created_at"}`
- Produces: `reference_mode` 日志字段，值为 `explicit`、`auto_inserted` 或 `none`

- [ ] **Step 1: 写显式引用收口失败测试**

新增一个 10 chunk、25 candidate doc 聚合的 fixture。前两个 chunk 分别属于 `doc-a` 和
`doc-b`，其余 chunk 可使用唯一 chunk ID，但原始 `doc_aggs` 必须包含 25 个文档：

```python
def _make_reference_kbinfos():
    chunks = [
        {
            "chunk_id": f"chunk-{index}",
            "doc_id": f"doc-{index % 3}",
            "docnm_kwd": f"文档-{index % 3}.docx",
            "content_ltks": f"知识块 {index}",
            "content_with_weight": f"知识块 {index}",
            "vector": [0.1, 0.2, 0.3],
        }
        for index in range(10)
    ]
    return {
        "chunks": chunks,
        "doc_aggs": [
            {
                "doc_id": f"candidate-{index}",
                "doc_name": f"候选-{index}.docx",
                "count": 1,
            }
            for index in range(25)
        ],
        "total": 70,
    }


class _ReferenceRetriever(_StubRetriever):
    def __init__(self, kbinfos, inserted_indices=None):
        self.kbinfos = deepcopy(kbinfos)
        self.inserted_indices = set(inserted_indices or [])

    async def retrieval(self, *_args, **_kwargs):
        return deepcopy(self.kbinfos)

    def insert_citations(self, answer, *_args, **_kwargs):
        if not self.inserted_indices:
            return answer, set()
        markers = "".join(
            f" [ID:{index}]"
            for index in sorted(self.inserted_indices)
        )
        return answer + markers, self.inserted_indices


def _run_reference_async_chat(
    monkeypatch,
    *,
    answer,
    kbinfos,
    inserted_indices=None,
):
    chat_mdl = _StreamingChatModel(answer)
    retriever = _ReferenceRetriever(kbinfos, inserted_indices)

    monkeypatch.setattr(
        dialog_service,
        "get_model_type_by_name",
        lambda _tenant_id, _llm_id: ["chat"],
    )
    monkeypatch.setattr(
        dialog_service,
        "get_model_config_from_provider_instance",
        lambda _tenant_id, _model_type, _llm_id: _LLM_CONFIG,
    )
    monkeypatch.setattr(
        dialog_service.TenantLangfuseService,
        "filter_by_tenant",
        lambda _tenant_id: None,
    )
    monkeypatch.setattr(
        dialog_service,
        "get_models",
        lambda _dialog, **_kwargs: (
            [_KB],
            chat_mdl,
            None,
            chat_mdl,
            None,
        ),
    )
    monkeypatch.setattr(
        dialog_service.KnowledgebaseService,
        "get_field_map",
        lambda _kb_ids: {},
    )
    monkeypatch.setattr(
        dialog_service.KnowledgebaseService,
        "get_by_ids",
        lambda _kb_ids: [_KB],
    )
    monkeypatch.setattr(
        dialog_service.settings,
        "retriever",
        retriever,
        raising=False,
    )
    monkeypatch.setattr(
        dialog_service,
        "label_question",
        lambda _question, _kbs: "",
    )
    monkeypatch.setattr(
        dialog_service,
        "kb_prompt",
        lambda _kbinfos, _max_tokens, **_kwargs: ["知识块"],
    )

    events = _collect(
        dialog_service.async_chat(
            _make_dialog(chat_mdl),
            [{"role": "user", "content": "测试引用。"}],
            stream=False,
            quote=True,
            session_id="session-reference-test",
        )
    )
    assert len(events) == 1
    return events[0]
```

新增集成测试：

```python
@pytest.mark.p2
def test_async_chat_prunes_candidate_docs_to_explicit_citations(monkeypatch):
    kbinfos = _make_reference_kbinfos()
    answer = "依据一 [ID:0]，依据二 [ID:1]。"

    final = _run_reference_async_chat(
        monkeypatch,
        answer=answer,
        kbinfos=kbinfos,
    )

    assert final["answer"] == answer
    assert [doc["doc_id"] for doc in final["reference"]["doc_aggs"]] == [
        "doc-0",
        "doc-1",
    ]
    assert len(final["reference"]["chunks"]) == 10
```

`_run_reference_async_chat()` 必须复用本文件已有的 async chat monkeypatch 方式：stub
`get_models`、`KnowledgebaseService`、`settings.retriever`、`label_question` 和
`kb_prompt`，收集唯一 `final=True` 事件并返回该事件。不得访问真实数据库或模型。

- [ ] **Step 2: 写全部越界和混合引用失败测试**

```python
@pytest.mark.p2
def test_async_chat_drops_all_docs_when_explicit_citations_are_out_of_range(
    monkeypatch,
    caplog,
):
    final = _run_reference_async_chat(
        monkeypatch,
        answer="无源 [ID:42]，报告 [ID:43]，有源 [ID:44][ID:45]。",
        kbinfos=_make_reference_kbinfos(),
    )

    assert "[ID:42]" not in final["answer"]
    assert "[ID:45]" not in final["answer"]
    assert final["reference"]["doc_aggs"] == []
    assert "invalid_citation_ids" in caplog.text


@pytest.mark.p2
def test_async_chat_keeps_valid_docs_and_removes_only_invalid_markers(monkeypatch):
    final = _run_reference_async_chat(
        monkeypatch,
        answer="有效 [ID:0]，无效 [ID:42]。",
        kbinfos=_make_reference_kbinfos(),
    )

    assert "[ID:0]" in final["answer"]
    assert "[ID:42]" not in final["answer"]
    assert [doc["doc_id"] for doc in final["reference"]["doc_aggs"]] == [
        "doc-0",
    ]
```

- [ ] **Step 3: 写无显式引用的自动插入回归测试**

使用 Step 1 的可配置 `_ReferenceRetriever` 新增两个测试：

```python
@pytest.mark.p2
def test_async_chat_uses_only_auto_inserted_citation_docs(monkeypatch):
    final = _run_reference_async_chat(
        monkeypatch,
        answer="没有显式引用的回答。",
        kbinfos=_make_reference_kbinfos(),
        inserted_indices={2},
    )

    assert "[ID:2]" in final["answer"]
    assert [doc["doc_id"] for doc in final["reference"]["doc_aggs"]] == [
        "doc-2",
    ]


@pytest.mark.p2
def test_async_chat_returns_no_docs_when_auto_insertion_finds_no_evidence(
    monkeypatch,
):
    final = _run_reference_async_chat(
        monkeypatch,
        answer="没有证据匹配的回答。",
        kbinfos=_make_reference_kbinfos(),
        inserted_indices=set(),
    )

    assert final["reference"]["doc_aggs"] == []
```

- [ ] **Step 4: 运行新增集成测试确认失败**

Run:

```bash
uv run pytest test/unit_test/api/db/services/test_dialog_service_final_answer.py \
  -k "prunes_candidate_docs or explicit_citations_are_out_of_range or keeps_valid_docs or auto_inserted_citation_docs or auto_insertion_finds_no_evidence" \
  -v
```

Expected: 至少 `doc_aggs` 断言失败；当前代码会在没有有效引用时保留 25 个候选文档。

- [ ] **Step 5: 重写 `decorate_answer()` 的 reference 收口**

在 `include_references` 分支中保留现有向量补取、自动插入和坏格式修复，但按以下顺序
执行：

```python
candidate_doc_count = len(kbinfos.get("doc_aggs", []))
chunks = kbinfos.get("chunks", [])
had_explicit_citations = bool(CITATION_MARKER_PATTERN.search(answer or ""))
reference_mode = "explicit" if had_explicit_citations else "none"
idx: set[int] = set()

if embd_mdl and not had_explicit_citations:
    await _hydrate_chunk_vectors(
        retriever,
        chunks,
        tenant_ids,
        dialog.kb_ids,
    )
    answer, idx = retriever.insert_citations(
        answer,
        [ck["content_ltks"] for ck in chunks],
        [ck["vector"] for ck in chunks],
        embd_mdl,
        tkweight=1 - dialog.vector_similarity_weight,
        vtweight=dialog.vector_similarity_weight,
    )
    if idx:
        reference_mode = "auto_inserted"

answer, idx = repair_bad_citation_formats(answer, kbinfos, idx)
answer, parsed_idx, invalid_idx, explicit_count = (
    _normalize_answer_citations(answer, len(chunks))
)
idx.update(parsed_idx)
idx = {index for index in idx if 0 <= index < len(chunks)}

refs = deepcopy(kbinfos)
refs["doc_aggs"] = _build_cited_doc_aggs(chunks, idx)
```

删除以下旧兜底，不得保留等价逻辑：

```python
if not recall_docs:
    recall_docs = kbinfos["doc_aggs"]
```

保持删除 chunk vector 的既有逻辑，但作用于 `refs["chunks"]`。

- [ ] **Step 6: 增加结构化诊断日志**

在构造 `refs` 后记录：

```python
log_args = {
    "chunk_count": len(chunks),
    "candidate_doc_count": candidate_doc_count,
    "explicit_citation_count": explicit_count,
    "valid_citation_count": len(idx),
    "invalid_citation_ids": invalid_idx,
    "final_doc_count": len(refs["doc_aggs"]),
    "reference_mode": reference_mode,
}
if session_id:
    log_args["session_id"] = session_id
if trace_context.get("trace_id"):
    log_args["trace_id"] = trace_context["trace_id"]

if invalid_idx:
    logger.warning("Invalid chat citations removed: %s", log_args)
else:
    logger.info("Chat references finalized: %s", log_args)
```

`session_id` 和 `trace_context` 使用 `async_chat()` 已有局部变量；不得为日志新增数据库
查询，也不得加入用户问题、回答或 chunk 正文。

- [ ] **Step 7: 运行新增测试确认通过**

Run:

```bash
uv run pytest test/unit_test/api/db/services/test_dialog_service_final_answer.py \
  -k "prunes_candidate_docs or explicit_citations_are_out_of_range or keeps_valid_docs or auto_inserted_citation_docs or auto_insertion_finds_no_evidence" \
  -v
```

Expected: PASS。

- [ ] **Step 8: 运行完整对话服务回归**

Run:

```bash
uv run pytest test/unit_test/api/db/services/test_dialog_service_final_answer.py -v
uv run pytest test/unit_test/api/db/services/test_conversation_service_evidence.py -v
uv run ruff check api/db/services/dialog_service.py \
  test/unit_test/api/db/services/test_dialog_service_final_answer.py
```

Expected: 全部 PASS，Ruff 无新增错误。

- [ ] **Step 9: 提交 Task 2**

```bash
git add api/db/services/dialog_service.py \
  test/unit_test/api/db/services/test_dialog_service_final_answer.py
git commit -m "修复：按有效引用收缩聊天来源文件"
```

---

### Task 3: 增加前端历史数据过滤与越界渲染保护

**Files:**

- Modify: `web/src/utils/citation-utils.ts`
- Create: `web/src/utils/__tests__/citation-utils.test.ts`
- Modify: `web/src/components/markdown-content/index.tsx:234-251`
- Modify: `web/src/components/next-markdown-content/index.tsx`
- Modify: `web/src/components/message-item/index.tsx:66-68`
- Modify: `web/src/components/next-message-item/index.tsx:92-103`

**Interfaces:**

- Produces: `getReferenceChunks(reference) -> IReferenceChunk[]`
- Produces: `hasReferenceChunk(reference, index: number) -> boolean`
- Produces: `getRenderableReferenceDocuments(content, reference) -> Docagg[]`
- Consumes: `IReference | IReferenceObject`

- [ ] **Step 1: 写共享工具失败测试**

创建 `web/src/utils/__tests__/citation-utils.test.ts`：

```typescript
import {
  getRenderableReferenceDocuments,
  hasReferenceChunk,
} from '../citation-utils';
import { IReference, IReferenceChunk } from '@/interfaces/database/chat';

const makeChunk = (
  id: string,
  documentId: string,
): IReferenceChunk => ({
  id,
  content: null,
  document_id: documentId,
  document_name: `${documentId}.docx`,
  dataset_id: 'dataset-1',
  image_id: '',
  similarity: 0.9,
  vector_similarity: 0.9,
  term_similarity: 0.9,
  positions: [],
});

const reference: IReference = {
  chunks: [
    makeChunk('chunk-0', 'doc-a'),
    makeChunk('chunk-1', 'doc-a'),
    makeChunk('chunk-2', 'doc-b'),
  ],
  doc_aggs: [
    { doc_id: 'doc-a', doc_name: 'A.docx', count: 2 },
    { doc_id: 'doc-b', doc_name: 'B.docx', count: 1 },
    { doc_id: 'candidate-only', doc_name: '无关.docx', count: 8 },
  ],
  total: 70,
};

describe('chat reference consistency', () => {
  it('keeps only documents referenced by valid markers', () => {
    expect(
      getRenderableReferenceDocuments('依据 [ID:0][ID:2]。', reference),
    ).toEqual([
      { doc_id: 'doc-a', doc_name: 'A.docx', count: 2 },
      { doc_id: 'doc-b', doc_name: 'B.docx', count: 1 },
    ]);
  });

  it('returns no documents when every marker is out of range', () => {
    expect(
      getRenderableReferenceDocuments('错误 [ID:42][ID:43]。', reference),
    ).toEqual([]);
  });

  it('keeps valid documents when markers are mixed', () => {
    expect(
      getRenderableReferenceDocuments('有效 [ID:1]，无效 [ID:42]。', reference),
    ).toEqual([
      { doc_id: 'doc-a', doc_name: 'A.docx', count: 2 },
    ]);
  });

  it('preserves backend doc_aggs when the message has no markers', () => {
    expect(
      getRenderableReferenceDocuments('没有行内引用的历史消息。', reference),
    ).toEqual(reference.doc_aggs);
  });

  it('recognizes array and record chunk containers', () => {
    expect(hasReferenceChunk(reference, 2)).toBe(true);
    expect(hasReferenceChunk(reference, 42)).toBe(false);
    expect(
      hasReferenceChunk(
        {
          chunks: { 0: reference.chunks[0] },
          doc_aggs: { 0: reference.doc_aggs[0] },
        },
        0,
      ),
    ).toBe(true);
  });
});
```

- [ ] **Step 2: 运行共享工具测试确认失败**

Run:

```bash
cd web
npm run test -- --runInBand src/utils/__tests__/citation-utils.test.ts
```

Expected: FAIL，提示导出的函数不存在。

- [ ] **Step 3: 实现 reference 共享工具**

在 `citation-utils.ts` 增加类型导入和以下函数：

```typescript
import {
  Docagg,
  IReference,
  IReferenceChunk,
  IReferenceObject,
} from '@/interfaces/database/chat';

type ReferenceLike = IReference | IReferenceObject | undefined;

export const getReferenceChunks = (
  reference: ReferenceLike,
): IReferenceChunk[] => {
  const chunks = reference?.chunks ?? [];
  return Array.isArray(chunks) ? chunks : Object.values(chunks);
};

const getReferenceDocuments = (
  reference: ReferenceLike,
): Docagg[] => {
  const docs = reference?.doc_aggs ?? [];
  return Array.isArray(docs) ? docs : Object.values(docs);
};

export const hasReferenceChunk = (
  reference: ReferenceLike,
  index: number,
) => {
  return (
    Number.isInteger(index) &&
    index >= 0 &&
    Boolean(getReferenceChunks(reference)[index])
  );
};

export const getRenderableReferenceDocuments = (
  content: string,
  reference: ReferenceLike,
): Docagg[] => {
  const chunks = getReferenceChunks(reference);
  const docs = getReferenceDocuments(reference);
  const markerReg = new RegExp(citationMarkerReg.source, 'g');
  const matches = Array.from(
    normalizeCitationDigits(content ?? '').matchAll(markerReg),
  );

  if (matches.length === 0) return docs;

  const citedDocIds = new Set(
    matches
      .map((match) => Number(match[1]))
      .filter(
        (index) =>
          Number.isInteger(index) &&
          index >= 0 &&
          index < chunks.length,
      )
      .map((index) => chunks[index]?.document_id)
      .filter((documentId): documentId is string => Boolean(documentId)),
  );

  if (citedDocIds.size === 0) return [];
  return docs.filter((doc) => citedDocIds.has(doc.doc_id));
};
```

不得复用全局正则实例直接执行 `exec()` 或 `matchAll()`；必须创建新的 `RegExp`，避免
`lastIndex` 在多条消息之间泄漏。

- [ ] **Step 4: 运行共享工具测试确认通过**

Run:

```bash
cd web
npm run test -- --runInBand src/utils/__tests__/citation-utils.test.ts
```

Expected: PASS。

- [ ] **Step 5: 接入旧版和新版来源文件列表**

在 `web/src/components/message-item/index.tsx`：

```typescript
import { getRenderableReferenceDocuments } from '@/utils/citation-utils';
```

将现有 `reference?.doc_aggs ?? []` 替换为：

```typescript
const referenceDocumentList = useMemo(
  () => getRenderableReferenceDocuments(messageContent, reference),
  [messageContent, reference],
);
```

确保 `messageContent = item.content` 在该 `useMemo` 之前声明。

在 `web/src/components/next-message-item/index.tsx` 做同样处理：

```typescript
const referenceDocuments = useMemo(
  () => getRenderableReferenceDocuments(messageContent, reference),
  [messageContent, reference],
);
```

删除仅用于 `Object.values(reference.doc_aggs)` 的旧代码。

- [ ] **Step 6: 接入两个 Markdown 越界保护**

在旧版和新版 Markdown 组件中导入 `hasReferenceChunk`。在
`reactStringReplace()` 回调取得 `chunkIndex` 后立即增加：

```typescript
if (!hasReferenceChunk(reference, chunkIndex)) {
  return null;
}
```

只有有效 chunk 才创建 `HoverCard` 和 `Fig. {chunkIndex + 1}`。不得为越界 marker 创建
空悬浮卡片。

- [ ] **Step 7: 运行前端目标测试、类型检查和 lint**

Run:

```bash
cd web
npm run test -- --runInBand \
  src/utils/__tests__/citation-utils.test.ts \
  src/pages/next-chats/utils.test.ts
npm run type-check
npm run lint
```

Expected: Jest PASS、TypeScript 无错误、ESLint 无新增错误。

- [ ] **Step 8: 提交 Task 3**

```bash
git add web/src/utils/citation-utils.ts \
  web/src/utils/__tests__/citation-utils.test.ts \
  web/src/components/markdown-content/index.tsx \
  web/src/components/next-markdown-content/index.tsx \
  web/src/components/message-item/index.tsx \
  web/src/components/next-message-item/index.tsx
git commit -m "修复：前端过滤无效聊天引用"
```

---

### Task 4: 完整回归与问题场景验收

**Files:**

- Verify only: `api/db/services/dialog_service.py`
- Verify only: `test/unit_test/api/db/services/test_dialog_service_final_answer.py`
- Verify only: `web/src/utils/citation-utils.ts`
- Verify only: 四个聊天渲染组件

**Interfaces:**

- Consumes: Task 1 至 Task 3 的全部接口。
- Produces: 可交付的验证记录；本任务不新增功能接口。

- [ ] **Step 1: 运行后端目标回归**

Run:

```bash
uv run pytest test/unit_test/api/db/services/test_dialog_service_final_answer.py -v
uv run pytest test/unit_test/api/db/services/test_conversation_service_evidence.py -v
uv run pytest test/unit_test/api/channels/test_bootstrap.py -v
```

Expected: 全部 PASS。

- [ ] **Step 2: 运行前端目标回归**

Run:

```bash
cd web
npm run test -- --runInBand \
  src/utils/__tests__/citation-utils.test.ts \
  src/pages/next-chats/utils.test.ts \
  src/utils/__tests__/chat.test.ts
npm run type-check
```

Expected: 全部 PASS，TypeScript 无错误。

- [ ] **Step 3: 运行静态检查**

Run:

```bash
uv run ruff check api/db/services/dialog_service.py \
  test/unit_test/api/db/services/test_dialog_service_final_answer.py
git diff --check
cd web
npm run lint
```

Expected: Ruff、`git diff --check` 和 ESLint 均通过。

- [ ] **Step 4: 手工复放 2026-07-30 问题场景**

在测试环境使用同一知识库提问：

```text
关键工序验证，方案和报告分别用哪两个模板？
```

分别验证：

- 当前 reference 只有 10 个 chunks 时不出现 `Fig. 43` 至 `Fig. 46`。
- 模型再次输出 `[ID:42]` 至 `[ID:45]` 时，最终 `doc_aggs` 为空。
- 页面不再展示 25 个候选文件。
- 有效 `[ID:0]`、`[ID:1]` 场景只展示对应文档。
- 重开历史 conversation 时，前端不展示越界 `Fig.` 和无关来源文件。
- 企业微信启用来源文件发送时，全部引用越界不会发送候选文件。

- [ ] **Step 5: 检查诊断日志**

确认日志包含以下字段且不包含完整问答或 chunk 正文：

```text
chunk_count
candidate_doc_count
explicit_citation_count
valid_citation_count
invalid_citation_ids
final_doc_count
reference_mode
```

对复现场景预期：

```text
chunk_count=10
candidate_doc_count=25
valid_citation_count=0
invalid_citation_ids=[42, 43, 44, 45]
final_doc_count=0
reference_mode=explicit
```

- [ ] **Step 6: 确认提交与工作区状态**

Run:

```bash
git log -3 --format='%h %s'
git status --short
```

Expected:

- 最近三条实现提交信息均为中文。
- 工作区为空。
- 不创建额外提交；若静态检查自动格式化了文件，将格式化内容归入对应任务提交并重新
  运行本任务全部验证。
