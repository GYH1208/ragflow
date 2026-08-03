# 渠道知识库历史隔离与精确 FAQ 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 消除知识库回答中的历史助手事实污染，并让精确匹配的结构化 FAQ 直接返回当前知识块中的答案。

**Architecture:** 渠道入口只传最近两条用户消息；带知识库的聊天服务用这些消息完成可选的问题改写，但最终检索和生成只使用一条独立问题。召回 chunk 中存在精确匹配的 `问题/回答` 对时走确定性回答，否则沿用现有 RAG 生成、引用和图片证据链路。

**Tech Stack:** Python 3.13、Quart/Flask 服务层、pytest、Ruff、MySQL 5.7、Docker Compose。

## Global Constraints

- 不实施操作路径自动转换为流程图。
- 不修改 `api/db/services/evidence_service.py`、`api/db/services/evidence_rerank_executor.py`、`rag/nlp/evidence.py`、`api/channels/wecom/channel.py` 或前端文件。
- 生产代码只修改 `api/channels/bootstrap.py` 和 `api/db/services/dialog_service.py`。
- 不改变流式发送、可信图片证据、引用规范化、精确文件编号和无知识块兜底行为。
- 无知识库的自由聊天继续保留完整多轮上下文。
- 不修改既有测试断言来迁就新行为。
- 每项生产改动之前必须先运行对应失败测试，并确认因缺少该行为而失败。
- 修改前保护基线为六个测试文件共 107 项通过；修改后必须通过全部既有测试和新增测试。
- 所有测试命令使用 `POLARS_SKIP_CPU_CHECK=1`，仅绕过本机 Polars CPU 探测。
- 会话数据清理在代码验证之后单独执行，且只允许影响 conversation `e905459e77e2923e2bba84c48e7668d4` 一行。
- Git 提交信息使用中文；由于当前环境缺少 `npx`，提交前先运行目标测试与 `git diff --check`，然后使用 `--no-verify` 跳过无法启动的 Husky 钩子。

---

## 文件结构

- Modify: `api/channels/bootstrap.py`
  - 新增渠道用户历史选择纯函数，并在渠道 handler 中替换完整历史构造。
- Modify: `test/unit_test/api/channels/test_bootstrap.py`
  - 覆盖长历史裁剪、助手消息隔离和单消息行为。
- Modify: `api/db/services/dialog_service.py`
  - 新增最近用户消息、FAQ 规范化/解析/匹配纯函数。
  - 统一问题改写、检索和生成问题。
  - 在模型调用前接入确定性 FAQ 回答。
- Modify: `test/unit_test/api/db/services/test_dialog_service_final_answer.py`
  - 覆盖未改写与改写生成隔离、FAQ 解析、模型旁路和普通 RAG 回退。
- Reference only: `docs/superpowers/specs/2026-08-03-channel-grounded-faq-history-design.md`
  - 已批准的范围、兼容性约束和验收标准。

---

### Task 1: 渠道入口只选择最近两条用户消息

**Files:**

- Modify: `api/channels/bootstrap.py:196-260`
- Modify: `test/unit_test/api/channels/test_bootstrap.py`

**Interfaces:**

- Produces: `_select_channel_history(messages: list[dict], max_user_messages: int = 2) -> list[dict]`
- Consumes: `conv.message`

- [ ] **Step 1: 写历史选择失败测试**

在 `test_bootstrap.py` 增加：

```python
def test_select_channel_history_keeps_only_two_recent_user_messages():
    messages = [
        {"role": "system", "content": "system"},
        {"role": "user", "content": "旧问题"},
        {"role": "assistant", "content": "错误联系人：IT-陶正浩"},
        {"role": "user", "content": "上一问"},
        {"role": "assistant", "content": "错误联系人：IT-刘尧"},
        {"role": "user", "content": "当前问题", "id": "current"},
    ]

    assert bootstrap._select_channel_history(messages) == [
        {"role": "user", "content": "上一问"},
        {"role": "user", "content": "当前问题", "id": "current"},
    ]


def test_select_channel_history_ignores_empty_and_invalid_messages():
    messages = [
        {"role": "user", "content": ""},
        {"role": "assistant", "content": "旧回答"},
        {"role": "user", "content": "当前问题"},
        {"role": "tool", "content": "工具结果"},
    ]

    assert bootstrap._select_channel_history(messages) == [
        {"role": "user", "content": "当前问题"},
    ]
```

- [ ] **Step 2: 运行测试并确认 RED**

Run:

```bash
POLARS_SKIP_CPU_CHECK=1 uv run pytest -q \
  test/unit_test/api/channels/test_bootstrap.py \
  -k select_channel_history
```

Expected: FAIL，`bootstrap` 尚无 `_select_channel_history`。

- [ ] **Step 3: 实现最小纯函数并接入 handler**

在 `_make_chat_handler()` 之前增加：

```python
def _select_channel_history(
    messages: list[dict],
    max_user_messages: int = 2,
) -> list[dict]:
    selected = []
    for message in reversed(messages or []):
        if message.get("role") != "user":
            continue
        content = message.get("content")
        if not isinstance(content, str) or not content.strip():
            continue
        selected.append(message)
        if len(selected) >= max_user_messages:
            break
    return list(reversed(selected))
```

将 handler 中构造全部 `history` 的循环替换为：

```python
history = _select_channel_history(conv.message)
```

- [ ] **Step 4: 运行 Task 1 测试并确认 GREEN**

```bash
POLARS_SKIP_CPU_CHECK=1 uv run pytest -q \
  test/unit_test/api/channels/test_bootstrap.py
uv run ruff check api/channels/bootstrap.py \
  test/unit_test/api/channels/test_bootstrap.py
```

Expected: bootstrap 全部测试通过，Ruff 无错误。

- [ ] **Step 5: 提交 Task 1**

```bash
git diff --check
git add api/channels/bootstrap.py test/unit_test/api/channels/test_bootstrap.py
git commit --no-verify -m "修复：隔离渠道历史助手消息"
```

---

### Task 2: 统一知识库问题改写、检索和最终生成上下文

**Files:**

- Modify: `api/db/services/dialog_service.py:740-820,960-980`
- Modify: `test/unit_test/api/db/services/test_dialog_service_final_answer.py`

**Interfaces:**

- Produces: `_recent_user_messages(messages: list[dict], limit: int = 2) -> list[dict]`
- Consumes: `full_question(tenant_id, llm_id, messages)`
- Produces: 最终生成消息固定为 `[{'role': 'user', 'content': generation_question}]`

- [ ] **Step 1: 写未启用改写时的失败测试**

新增测试，沿用 `_run_reference_async_chat()`：

```python
def test_kb_generation_without_refinement_excludes_all_history(monkeypatch):
    messages = [
        {"role": "user", "content": "云文档找谁？"},
        {"role": "assistant", "content": "错误联系人：IT-陶正浩"},
        {"role": "user", "content": "EHR、培训、绩效系统找谁？"},
    ]
    _, chat_mdl, retriever = _run_reference_async_chat(
        monkeypatch,
        answer="钟志斌或者陈国萌 [ID:0]",
        kbinfos=_make_reference_kbinfos(),
        messages=messages,
        refine_multiturn=False,
    )

    assert retriever.retrieval_calls[0][0][0] == "EHR、培训、绩效系统找谁？"
    assert chat_mdl.chat_calls[-1][1] == [
        {"role": "user", "content": "EHR、培训、绩效系统找谁？"},
    ]
```

- [ ] **Step 2: 运行并确认 RED**

```bash
POLARS_SKIP_CPU_CHECK=1 uv run pytest -q \
  test/unit_test/api/db/services/test_dialog_service_final_answer.py \
  -k kb_generation_without_refinement_excludes_all_history
```

Expected: FAIL，当前生成消息仍包含旧用户和助手消息。

- [ ] **Step 3: 写多轮改写输入隔离失败测试**

扩展 `_run_reference_async_chat()` 中的 fake `full_question()`，把收到的 messages 保存到
`chat_mdl.refinement_messages`：

```python
    chat_mdl.refinement_messages = []
    if refined_question is not None:
        async def fake_full_question(_tenant_id, _llm_id, refinement_messages):
            chat_mdl.refinement_messages = deepcopy(refinement_messages)
            return refined_question

        monkeypatch.setattr(dialog_service, "full_question", fake_full_question)
```

新增测试：

```python
def test_multiturn_refinement_uses_only_two_recent_user_messages(monkeypatch):
    messages = [
        {"role": "user", "content": "更早的问题"},
        {"role": "assistant", "content": "更早的回答"},
        {"role": "user", "content": "上一问"},
        {"role": "assistant", "content": "错误历史答案"},
        {"role": "user", "content": "再确认一下"},
    ]
    _, chat_mdl, _ = _run_reference_async_chat(
        monkeypatch,
        answer="当前答案 [ID:0]",
        kbinfos=_make_reference_kbinfos(),
        messages=messages,
        refine_multiturn=True,
        refined_question="改写后的独立问题",
    )

    assert chat_mdl.refinement_messages == [
        {"role": "user", "content": "上一问"},
        {"role": "user", "content": "再确认一下"},
    ]
    assert chat_mdl.chat_calls[-1][1] == [
        {"role": "user", "content": "改写后的独立问题"},
    ]
```

- [ ] **Step 4: 运行并确认 RED**

```bash
POLARS_SKIP_CPU_CHECK=1 uv run pytest -q \
  test/unit_test/api/db/services/test_dialog_service_final_answer.py \
  -k "kb_generation_without_refinement or refinement_uses_only_recent_users"
```

Expected: 至少一个测试因助手历史仍进入改写或生成上下文而失败。

- [ ] **Step 5: 实现最小上下文隔离**

在 `async_chat()` 之前增加：

```python
def _recent_user_messages(messages: list[dict], limit: int = 2) -> list[dict]:
    users = [
        {"role": "user", "content": message["content"]}
        for message in messages
        if message.get("role") == "user"
        and isinstance(message.get("content"), str)
        and message["content"].strip()
    ]
    return users[-limit:]
```

在带知识库分支中使用：

```python
recent_user_messages = _recent_user_messages(messages)
questions = [message["content"] for message in recent_user_messages]
```

改写时传入 `recent_user_messages`：

```python
questions = [
    await full_question(dialog.tenant_id, dialog.llm_id, recent_user_messages)
]
```

生成阶段无条件追加一条独立问题：

```python
msg.append({"role": "user", "content": generation_question})
```

删除带知识库路径中将原始 `messages` 全量扩展到 `msg` 的分支。无知识库路径在函数顶部已
返回 `async_chat_solo()`，不得改动。

- [ ] **Step 6: 运行 Task 2 测试并确认 GREEN**

```bash
POLARS_SKIP_CPU_CHECK=1 uv run pytest -q \
  test/unit_test/api/db/services/test_dialog_service_final_answer.py \
  -k "history or multiturn or refinement or kb_generation_without_refinement"
uv run ruff check api/db/services/dialog_service.py \
  test/unit_test/api/db/services/test_dialog_service_final_answer.py
```

- [ ] **Step 7: 提交 Task 2**

```bash
git diff --check
git add api/db/services/dialog_service.py \
  test/unit_test/api/db/services/test_dialog_service_final_answer.py
git commit --no-verify -m "修复：统一知识库本轮生成上下文"
```

---

### Task 3: 对结构化 FAQ 精确问题返回当前知识块答案

**Files:**

- Modify: `api/db/services/dialog_service.py:520-700,1080-1160`
- Modify: `test/unit_test/api/db/services/test_dialog_service_final_answer.py`

**Interfaces:**

- Produces: `_normalize_faq_question(value: str) -> str`
- Produces: `_parse_faq_pairs(content: str) -> list[tuple[str, str]]`
- Produces: `_find_exact_faq_answer(question: str, chunks: list[dict]) -> tuple[str, int] | None`
- Consumes: `kbinfos['chunks']`、现有 `decorate_answer()`

- [ ] **Step 1: 写 FAQ 解析和规范化失败测试**

```python
def test_parse_faq_pairs_removes_csv_noise():
    content = (
        "问题：云文档的相关问题可以找谁咨询呢？；回答：余李; Unnamed: 2: nan ——Data\n"
        "问题：EHR系统、培训系统、绩效系统的相关问题可以找谁咨询呢？；"
        "回答：钟志斌或者陈国萌; Unnamed: 2: nan ——Data"
    )

    assert dialog_service._parse_faq_pairs(content) == [
        ("云文档的相关问题可以找谁咨询呢？", "余李"),
        (
            "EHR系统、培训系统、绩效系统的相关问题可以找谁咨询呢？",
            "钟志斌或者陈国萌",
        ),
    ]


def test_normalize_faq_question_is_strict_except_formatting():
    assert dialog_service._normalize_faq_question(" 云文档的相关问题可以找谁咨询呢? ") \
        == dialog_service._normalize_faq_question("云文档的相关问题可以找谁咨询呢？")
    assert dialog_service._normalize_faq_question("云文档找谁？") \
        != dialog_service._normalize_faq_question("云文档的相关问题可以找谁咨询呢？")
```

- [ ] **Step 2: 运行并确认 RED**

```bash
POLARS_SKIP_CPU_CHECK=1 uv run pytest -q \
  test/unit_test/api/db/services/test_dialog_service_final_answer.py \
  -k "parse_faq_pairs or normalize_faq_question"
```

Expected: FAIL，三个 FAQ 辅助函数尚不存在。

- [ ] **Step 3: 实现严格解析纯函数**

增加 `import unicodedata`，并实现：

```python
FAQ_PAIR_PATTERN = re.compile(
    r"问题\s*[:：]\s*(.*?)\s*[;；]\s*回答\s*[:：]\s*(.*)"
)


def _normalize_faq_question(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value or "").casefold()
    normalized = re.sub(r"\s+", "", normalized)
    return normalized.rstrip("?？")


def _parse_faq_pairs(content: str) -> list[tuple[str, str]]:
    pairs = []
    for line in (content or "").splitlines():
        match = FAQ_PAIR_PATTERN.search(line)
        if not match:
            continue
        question = match.group(1).strip()
        answer = re.split(
            r"\s*[;；]\s*Unnamed\s*:",
            match.group(2),
            maxsplit=1,
            flags=re.IGNORECASE,
        )[0].strip()
        answer = re.split(r"\s*[—-]{2,}\s*Data\b", answer, maxsplit=1)[0].strip()
        if question and answer:
            pairs.append((question, answer))
    return pairs


def _find_exact_faq_answer(
    question: str,
    chunks: list[dict],
) -> tuple[str, int] | None:
    normalized_question = _normalize_faq_question(question)
    for index, chunk in enumerate(chunks or []):
        content = chunk.get("content_with_weight") or chunk.get("content") or ""
        for faq_question, faq_answer in _parse_faq_pairs(content):
            if _normalize_faq_question(faq_question) == normalized_question:
                return faq_answer, index
    return None
```

- [ ] **Step 4: 运行纯函数测试并确认 GREEN**

```bash
POLARS_SKIP_CPU_CHECK=1 uv run pytest -q \
  test/unit_test/api/db/services/test_dialog_service_final_answer.py \
  -k "parse_faq_pairs or normalize_faq_question or find_exact_faq"
```

- [ ] **Step 5: 写精确命中旁路模型的失败测试**

新增共享 fixture 和两条测试：

```python
def _make_faq_kbinfos():
    return {
        "chunks": [{
            "chunk_id": "faq-chunk",
            "doc_id": "faq-doc",
            "docnm_kwd": "IT常问问题-工作表1.csv",
            "content_ltks": "云文档 联系人 EHR 培训 绩效",
            "content_with_weight": (
                "问题：云文档的相关问题可以找谁咨询呢？；回答：余李; "
                "Unnamed: 2: nan ——Data\n"
                "问题：EHR系统、培训系统、绩效系统的相关问题可以找谁咨询呢？；"
                "回答：钟志斌或者陈国萌; Unnamed: 2: nan ——Data"
            ),
            "vector": [0.1, 0.2, 0.3],
        }],
        "doc_aggs": [{
            "doc_id": "faq-doc",
            "doc_name": "IT常问问题-工作表1.csv",
            "count": 1,
        }],
        "total": 1,
    }


def test_exact_faq_answer_bypasses_model(monkeypatch):
    final, chat_mdl, _ = _run_reference_async_chat(
        monkeypatch,
        answer="错误联系人：IT-陶正浩",
        kbinfos=_make_faq_kbinfos(),
        messages=[{
            "role": "user",
            "content": "云文档的相关问题可以找谁咨询呢？",
        }],
    )

    assert final["answer"].startswith("余李")
    assert final["reference"]["doc_aggs"][0]["doc_id"] == "faq-doc"
    assert chat_mdl.chat_calls == []


def test_approximate_faq_question_uses_rag_generation(monkeypatch):
    _, chat_mdl, _ = _run_reference_async_chat(
        monkeypatch,
        answer="生成回答 [ID:0]",
        kbinfos=_make_faq_kbinfos(),
        messages=[{"role": "user", "content": "云文档找谁？"}],
    )

    assert len(chat_mdl.chat_calls) == 1
```

- [ ] **Step 6: 运行并确认 RED**

```bash
POLARS_SKIP_CPU_CHECK=1 uv run pytest -q \
  test/unit_test/api/db/services/test_dialog_service_final_answer.py \
  -k "exact_faq_answer_bypasses_model or approximate_faq_uses_rag"
```

Expected: 精确问题仍调用模型，测试失败；近似问题保持现有路径。

- [ ] **Step 7: 在模型调用前接入确定性回答**

在 `decorate_answer()` 定义完成、Langfuse generation 启动之前增加：

```python
    exact_faq = _find_exact_faq_answer(
        generation_question,
        kbinfos.get("chunks", []),
    )
    if exact_faq is not None:
        exact_answer, chunk_index = exact_faq
        answer = exact_answer
        if include_references:
            answer = f"{answer} [ID:{chunk_index}]"
        result = await decorate_answer(answer)
        result["audio_binary"] = tts(tts_mdl, answer)
        result["final"] = True
        logger.info(
            "Exact FAQ answer selected: chunk_index=%d chunk_count=%d",
            chunk_index,
            len(kbinfos.get("chunks", [])),
        )
        yield result
        return
```

不得移动或重写现有 `decorate_answer()`、流式输出、证据图片和引用收口逻辑。

- [ ] **Step 8: 运行 Task 3 测试并确认 GREEN**

```bash
POLARS_SKIP_CPU_CHECK=1 uv run pytest -q \
  test/unit_test/api/db/services/test_dialog_service_final_answer.py
uv run ruff check api/db/services/dialog_service.py \
  test/unit_test/api/db/services/test_dialog_service_final_answer.py
```

- [ ] **Step 9: 提交 Task 3**

```bash
git diff --check
git add api/db/services/dialog_service.py \
  test/unit_test/api/db/services/test_dialog_service_final_answer.py
git commit --no-verify -m "修复：精确返回结构化FAQ答案"
```

---

### Task 4: 兼容性回归、会话备份与清理

**Files:**

- Verify only: Task 1-3 的四个代码/测试文件
- Database target: `conversation.id=e905459e77e2923e2bba84c48e7668d4`
- Backup artifact example: `/home/qaadmin/ragflow-backups/conversation-e905459e77e2923e2bba84c48e7668d4-20260803T090000Z.sql`，实际时间由 `date -u` 生成，位于仓库外

**Interfaces:**

- Consumes: Docker container `docker-mysql-1`、database `rag_flow`
- Produces: 可恢复的单行 SQL 备份和空消息/引用的目标 conversation

- [ ] **Step 1: 重跑 107 项保护基线与新增测试**

```bash
POLARS_SKIP_CPU_CHECK=1 uv run pytest -q \
  test/unit_test/api/channels/test_bootstrap.py \
  test/unit_test/api/channels/test_wecom_channel.py \
  test/unit_test/api/db/services/test_dialog_service_final_answer.py \
  test/unit_test/api/db/services/test_evidence_service.py \
  test/unit_test/api/db/services/test_evidence_rerank_executor.py \
  test/unit_test/rag/test_evidence.py
uv run ruff check \
  api/channels/bootstrap.py \
  api/db/services/dialog_service.py \
  test/unit_test/api/channels/test_bootstrap.py \
  test/unit_test/api/db/services/test_dialog_service_final_answer.py
git diff --check
```

Expected: 既有 107 项和新增测试全部通过；Ruff 与 diff check 成功。

- [ ] **Step 2: 审计最终改动范围**

```bash
git diff --name-only a9cd4ba32..HEAD
git log --oneline a9cd4ba32..HEAD
```

Expected: 除设计/计划文档外，生产代码只出现 `api/channels/bootstrap.py` 和
`api/db/services/dialog_service.py`，测试只出现两个对应测试文件。

- [ ] **Step 3: 只读核对目标会话**

在 MySQL 容器中用环境中已有的 root 密码执行只读查询，返回 id、dialog_id、name、消息和
引用 JSON 长度：

```bash
docker exec docker-mysql-1 sh -lc \
  'mysql -uroot -p"$MYSQL_ROOT_PASSWORD" -N -B rag_flow -e \
  "SELECT id,dialog_id,name,JSON_LENGTH(message),JSON_LENGTH(reference) \
   FROM conversation \
   WHERE id='\''e905459e77e2923e2bba84c48e7668d4'\'';"'
```

只有 id 等于目标值且查询结果恰好一行时继续。

- [ ] **Step 4: 生成单行数据库备份**

使用容器内 `mysqldump --where` 和 `--result-file` 生成只包含目标 conversation 的 SQL，
再复制到仓库外目录：

```bash
backup_stamp=$(date -u +%Y%m%dT%H%M%SZ)
backup_dir=/home/qaadmin/ragflow-backups
backup_file="$backup_dir/conversation-e905459e77e2923e2bba84c48e7668d4-$backup_stamp.sql"
container_backup=/tmp/conversation-e905459e77e2923e2bba84c48e7668d4.sql
mkdir -p "$backup_dir"
docker exec docker-mysql-1 sh -lc \
  'mysqldump -uroot -p"$MYSQL_ROOT_PASSWORD" --single-transaction \
  --skip-lock-tables --no-create-info --complete-insert \
  --where="id='\''e905459e77e2923e2bba84c48e7668d4'\''" \
  --result-file=/tmp/conversation-e905459e77e2923e2bba84c48e7668d4.sql \
  rag_flow conversation'
docker cp "docker-mysql-1:$container_backup" "$backup_file"
test -s "$backup_file"
rg -q 'e905459e77e2923e2bba84c48e7668d4' "$backup_file"
docker exec docker-mysql-1 rm -f "$container_backup"
```

任一步失败都不得执行会话更新。

- [ ] **Step 5: 清空并核对目标会话**

先记录目标 dialog 的行数，再在事务中执行带完整 ID 条件的单行更新：

```bash
docker exec docker-mysql-1 sh -lc \
  'mysql -uroot -p"$MYSQL_ROOT_PASSWORD" -N -B rag_flow -e \
  "SELECT dialog_id,COUNT(*) FROM conversation \
   WHERE dialog_id=(SELECT dialog_id FROM conversation \
   WHERE id='\''e905459e77e2923e2bba84c48e7668d4'\'') GROUP BY dialog_id; \
   START TRANSACTION; \
   UPDATE conversation SET message=JSON_ARRAY(), reference=JSON_ARRAY() \
   WHERE id='\''e905459e77e2923e2bba84c48e7668d4'\''; \
   SELECT ROW_COUNT() AS affected_rows; \
   COMMIT; \
   SELECT id,dialog_id,JSON_LENGTH(message),JSON_LENGTH(reference) \
   FROM conversation \
   WHERE id='\''e905459e77e2923e2bba84c48e7668d4'\'';"'
```

输出必须显示 `affected_rows=1` 且两个 JSON 长度均为 0。随后重新执行本步骤开头的
`SELECT dialog_id,COUNT(*)` 查询，确认相同 dialog 的 conversation 行数未变化。

- [ ] **Step 6: 重启后端以加载代码并做实际复测**

当前后端由用户级 systemd transient unit `ragflow-server-manual.service` 管理。只重启该
服务，不重启 MySQL、ES、Redis、MinIO 或 task executor：

```bash
systemctl --user restart ragflow-server-manual.service
systemctl --user is-active ragflow-server-manual.service
curl -fsS http://127.0.0.1:9380/api/v1/system/version
```

确认状态为 `active` 且版本接口成功后，依次发送三条验收问题，检查回答为“余李”、
“钟志斌或者陈国萌”和知识库中的 QMS 回答，并检查日志中生成消息不含旧助手联系人。

- [ ] **Step 7: 记录结果与恢复方式**

最终报告包含提交列表、测试数量、备份绝对路径、被清理 conversation ID、实际复测结果和
恢复命令所需信息。若实际复测失败，不删除备份、不清理其他会话，并回到对应失败阶段。
