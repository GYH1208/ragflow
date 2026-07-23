# BM25 与 KNN 候选集并集实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 Elasticsearch 的 BM25 与 KNN 从“BM25 过滤 KNN”改为两个独立召回分支的候选集并集，再沿用现有 Qwen rerank。

**Architecture:** 保留单次 Elasticsearch 请求。顶层 `query` 执行 BM25，顶层 `knn` 执行向量召回；KNN 的 `filter` 只包含 `kb_id`、`doc_id`、`available_int` 等结构化条件，不包含 `query_string`。Elasticsearch 返回两个分支的并集，现有 `Dealer.retrieval()` 对并集统一 rerank、阈值过滤和 Top N 截断。

**Tech Stack:** Python 3.10+、Elasticsearch 8.11.3、elasticsearch-dsl 8.12、pytest、Qwen rerank

## Global Constraints

- 第一阶段不修改前端参数和接口协议。
- 第一阶段不要求重新解析或重新索引现有文档。
- 保留 BM25，不将系统改为纯向量检索。
- KNN 必须继续受知识库、文档和启用状态等结构化条件限制。
- 不把 dense vector 从 Elasticsearch 传回应用层。
- 当前在线聊天使用的 Python Elasticsearch 路径优先；Go 镜像实现作为独立同步任务。

---

### Task 1: 为 Elasticsearch 混合查询增加失败测试

**Files:**
- Create: `test/unit_test/rag/utils/test_es_hybrid_search.py`
- Reference: `test/unit_test/rag/utils/test_opensearch_hybrid_search.py`

**Interfaces:**
- Consumes: `ESConnection.search()`、`MatchTextExpr`、`MatchDenseExpr`、`FusionExpr`
- Produces: 对顶层 BM25、顶层 KNN、结构化 KNN filter 和权重的请求体断言

- [ ] **Step 1: 创建不连接真实 ES 的测试夹具**

```python
from unittest.mock import MagicMock

from common.doc_store.doc_store_base import (
    FusionExpr,
    MatchDenseExpr,
    MatchTextExpr,
)
from rag.utils import es_conn


def _resolve_es_connection_class():
    candidate = es_conn.ESConnection
    if isinstance(candidate, type):
        return candidate
    for cell in getattr(candidate, "__closure__", None) or ():
        if isinstance(cell.cell_contents, type):
            return cell.cell_contents
    raise RuntimeError("Could not locate ESConnection class")


def _make_connection():
    cls = _resolve_es_connection_class()
    conn = cls.__new__(cls)
    conn.es = MagicMock()
    conn.logger = MagicMock()
    conn.es.search.return_value = {
        "hits": {"total": {"value": 0}, "hits": []},
        "timed_out": False,
    }
    return conn


def _call_search(conn, match_expressions):
    conn.search(
        select_fields=["content_ltks"],
        highlight_fields=[],
        condition={"available_int": 1, "doc_id": ["doc-1"]},
        match_expressions=match_expressions,
        order_by=None,
        offset=0,
        limit=64,
        index_names=["ragflow_test"],
        knowledgebase_ids=["kb-1"],
    )
    return conn.es.search.call_args.kwargs["body"]


def _text_expr():
    return MatchTextExpr(
        fields=["question_tks^20", "content_ltks^2"],
        matching_text="换社康后今天能用吗",
        topn=10,
        extra_options={"minimum_should_match": 0.3},
    )


def _dense_expr():
    return MatchDenseExpr(
        vector_column_name="q_8_vec",
        embedding_data=[0.1] * 8,
        embedding_data_type="float",
        distance_type="cosine",
        topn=64,
        extra_options={"similarity": 0.17},
    )


def _fusion_expr():
    return FusionExpr(
        method="weighted_sum",
        topn=64,
        fusion_params={"weights": "0.05,0.95"},
    )
```

- [ ] **Step 2: 写入“顶层并集、KNN 不含 query_string”的失败测试**

```python
def test_hybrid_knn_filter_contains_only_structural_filters():
    conn = _make_connection()
    body = _call_search(conn, [_text_expr(), _dense_expr(), _fusion_expr()])
    assert "query_string" in str(body["query"])
    assert "query_string" not in str(body["knn"]["filter"])
    assert "kb_id" in str(body["knn"]["filter"])
    assert "doc_id" in str(body["knn"]["filter"])
    assert "available_int" in str(body["knn"]["filter"])
    assert body["knn"]["boost"] == 0.95
```

- [ ] **Step 3: 增加纯 BM25 与纯 KNN 兼容性测试**

```python
def test_text_only_does_not_add_knn():
    conn = _make_connection()
    body = _call_search(conn, [_text_expr()])
    assert "query" in body
    assert "query_string" in str(body["query"])
    assert "knn" not in body


def test_knn_only_keeps_structural_filter():
    conn = _make_connection()
    body = _call_search(conn, [_dense_expr()])
    assert "knn" in body
    assert "kb_id" in str(body["knn"]["filter"])
    assert "doc_id" in str(body["knn"]["filter"])
    assert "available_int" in str(body["knn"]["filter"])
    assert "query_string" not in str(body["knn"]["filter"])
```

- [ ] **Step 4: 运行测试并确认并集测试失败**

Run:

```bash
uv run pytest test/unit_test/rag/utils/test_es_hybrid_search.py -v
```

Expected: `test_hybrid_knn_filter_contains_only_structural_filters` 因 KNN filter 中仍包含 `query_string` 而失败。

- [ ] **Step 5: 提交测试**

```bash
git add test/unit_test/rag/utils/test_es_hybrid_search.py
git commit -m "测试：覆盖BM25与KNN候选集并集查询"
```

---

### Task 2: 将 Python Elasticsearch 查询改为候选集并集

**Files:**
- Modify: `rag/utils/es_conn.py:162-233`
- Test: `test/unit_test/rag/utils/test_es_hybrid_search.py`

**Interfaces:**
- Consumes: `condition` 生成的结构化过滤、`FusionExpr` 中的候选阶段权重
- Produces: 顶层 BM25 `query` 与顶层 KNN `knn`；两个分支由 Elasticsearch 以 OR 语义合并

- [ ] **Step 1: 在添加全文条件前复制结构化过滤**

在处理完 `condition` 后、遍历 `match_expressions` 前增加：

```python
structural_filter = Q(
    "bool",
    filter=copy.deepcopy(list(bool_query.filter)),
)
```

- [ ] **Step 2: KNN 只使用结构化过滤并显式设置向量权重**

将：

```python
s = s.knn(
    m.vector_column_name,
    m.topn,
    m.topn * 2,
    query_vector=list(m.embedding_data),
    filter=bool_query.to_dict(),
    similarity=similarity,
)
```

替换为：

```python
s = s.knn(
    m.vector_column_name,
    m.topn,
    m.topn * 2,
    query_vector=list(m.embedding_data),
    boost=vector_similarity_weight,
    filter=structural_filter,
    similarity=similarity,
)
```

顶层 `s.query(bool_query)` 保持不变。最终请求体应类似：

```json
{
  "query": {
    "bool": {
      "filter": ["kb_id/doc_id/available_int"],
      "must": [{"query_string": {"query": "用户问题"}}],
      "boost": 0.05
    }
  },
  "knn": {
    "field": "q_1024_vec",
    "query_vector": ["向量值"],
    "k": 2048,
    "num_candidates": 4096,
    "boost": 0.95,
    "filter": {
      "bool": {
        "filter": ["kb_id/doc_id/available_int"]
      }
    }
  }
}
```

- [ ] **Step 3: 运行 Elasticsearch 查询构造测试**

Run:

```bash
uv run pytest test/unit_test/rag/utils/test_es_hybrid_search.py -v
```

Expected: 全部通过。

- [ ] **Step 4: 运行检索分页与 rerank 回归测试**

Run:

```bash
uv run pytest \
  test/unit_test/rag/test_search_pagination.py \
  test/unit_test/rag/llm/test_rerank_normalization.py -v
```

Expected: 全部通过，候选窗口、分页和归一化行为不变。

- [ ] **Step 5: 提交 Python 实现**

```bash
git add rag/utils/es_conn.py test/unit_test/rag/utils/test_es_hybrid_search.py
git commit -m "修复：合并BM25与KNN候选集后统一重排"
```

---

### Task 3: 用真实知识库验证召回与最终答案

**Files:**
- No production file changes
- Evidence: RAG 日志与检索测试结果

**Interfaces:**
- Consumes: 修改后的在线检索链路
- Produces: 三道失败题的候选排名、rerank 排名、最终上下文和答案证据

- [ ] **Step 1: 记录改动前基线**

对下列问题分别记录 BM25、KNN、混合候选和最终 Top 10：

```text
KB055：我今天在深圳把绑定社康换了一家，新的今天就能用统筹吗？
KB059：我刚从非深户转成深户，这个月在EHR改完信息，医保什么时候才真正按一档用？
KB085：费控单子现在卡在谁那里，怎么查？
```

- [ ] **Step 2: 启动修改后的后端并复测**

Run:

```bash
source .venv/bin/activate
export PYTHONPATH="$(pwd)"
bash docker/launch_backend_service.sh
```

Expected correct chunks:

```text
KB055: acd26d4d9c7f31f3
KB059: 549784eec653dc9a
KB085: eab6502820610ded
```

- [ ] **Step 3: 验收候选与答案**

每题必须同时满足：

```text
正确 Chunk 进入候选并集；
正确 Chunk 进入 rerank Top 10；
最终传给 LLM 的上下文包含关键答案；
最终回答没有把“操作流程”和“生效时间”混为一谈。
```

- [ ] **Step 4: 扩展回归集**

至少补充：

```text
10道口语化改写题；
10道精确系统路径题；
10道包含金额、日期或档次的精确事实题；
5道知识库确实没有答案的问题。
```

验收目标：

```text
35题正确答案切片 Recall@64 >= 95%；
KB055、KB059、KB085 的 Hit@10 = 100%；
无答案题不得因低质量向量候选而编造答案；
P95 检索耗时相对基线增长不超过 30%。
```

- [ ] **Step 5: 完成验收提交**

如只产生测试记录，不新增文件则无需提交；如增加固定回归测试文件：

```bash
git add test/
git commit -m "测试：补充企业知识库口语化检索回归用例"
```

---

### Task 4: 同步 Go Elasticsearch 镜像实现

**Files:**
- Modify: `internal/engine/elasticsearch/chunk.go:773-869`
- Test: matching Go Elasticsearch query-builder test file

**Interfaces:**
- Consumes: `buildBoolQueryFromCondition()` 生成的结构化过滤
- Produces: 与 Python 路径相同的 BM25/KNN 并集语义

- [ ] **Step 1: 写入 Go 失败测试**

测试断言：

```go
if strings.Contains(string(knnFilterJSON), "query_string") {
    t.Fatal("KNN filter must not contain BM25 query_string")
}
if !strings.Contains(string(knnFilterJSON), "kb_id") {
    t.Fatal("KNN filter must preserve structural filters")
}
```

- [ ] **Step 2: 在添加 textQuery 前复制结构化 boolQuery**

```go
structuralFilter := buildBoolQueryFromCondition(req.Filter, req.KbIDs, isSkillIndex)
```

随后将 KNN 请求中的：

```go
"filter": boolQuery,
```

替换为：

```go
"filter": structuralFilter,
"boost":  vectorSimilarityWeight,
```

- [ ] **Step 3: 运行 Go 定向测试**

Run:

```bash
go test ./internal/engine/elasticsearch/... -run Hybrid -count=1
```

Expected: 全部通过。

- [ ] **Step 4: 提交 Go 同步实现**

```bash
git add internal/engine/elasticsearch
git commit -m "修复：同步Go检索的BM25与KNN并集逻辑"
```

---

## 工期估算

| 范围 | 预计时间 |
|---|---:|
| Python 查询构造失败测试 | 0.5～1小时 |
| Python 最小实现 | 0.5～1小时 |
| 单元测试、分页和 rerank 回归 | 1～1.5小时 |
| KB055、KB059、KB085 真实复测 | 1～1.5小时 |
| 30～35题扩展回归和性能对比 | 1.5～2小时 |
| 发布、重启和观察日志 | 0.5～1小时 |
| **Python 在线链路合计** | **5～7小时，约1个工作日** |
| Go 镜像实现与测试 | 2～4小时 |
| **Python + Go 全部完成** | **1～1.5个工作日** |

如果升级为“两次独立查询、固定 BM25/KNN 配额、去重、RRF 融合、完整分路日志”，预计需要 **2～3个工作日**，不建议作为第一步。
