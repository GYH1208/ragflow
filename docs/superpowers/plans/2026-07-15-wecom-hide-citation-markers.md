# 企业微信隐藏引用标记实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 企业微信出站文字隐藏内部引用标记，同时保持实际引用图片的筛选结果、Web 端原始回答和其他 Channel 行为不变。

**Architecture:** 在公共 Channel 能力模型中增加默认关闭的引用标记隐藏开关，仅企业微信开启。Channel 启动层使用一个单次遍历辅助函数，同时生成清理后的文字和按首次引用顺序去重的图片列表；数据库仍通过 `structure_answer` 保存清理前的原始回答。

**Tech Stack:** Python 3.13、pytest、Ruff、RAGFlow Channel 框架、企业微信 WebSocket Channel。

## Global Constraints

- 只改变企业微信出站文字，Web 端、数据库原始回答和其他 Channel 不变。
- 支持 `[ID:n]`、`[n]`、阿拉伯语数字和波斯语数字引用。
- 图片筛选必须使用清理前的原始回答，顺序和去重规则不变。
- 只清理由标记删除产生的水平空格，不压缩 Markdown 换行、列表缩进或代码块。
- 不增加模型、数据库、对象存储或网络调用。

---

### Task 1: 单次遍历准备企业微信出站文字和图片

**Files:**
- Modify: `api/channels/core/base.py:38-45`
- Modify: `api/channels/wecom/channel.py:146-156`
- Modify: `api/channels/bootstrap.py:39-76,181-204`
- Modify: `test/unit_test/api/channels/test_base.py`
- Modify: `test/unit_test/api/channels/test_bootstrap.py`
- Modify: `test/unit_test/api/channels/test_wecom_channel.py`

**Interfaces:**
- Consumes: 原始回答 `answer: str` 和格式化引用知识块 `chunks: object`。
- Produces: `_prepare_cited_output(answer: str, chunks: object) -> tuple[str, list[OutgoingImage]]`。
- Produces: `Channel.hides_reference_markers: bool = False`，企业微信覆盖为 `True`。

- [ ] **Step 1: 写能力开关和出站准备函数的失败测试**

在 `test/unit_test/api/channels/test_base.py` 增加默认能力测试：

```python
from api.channels.core.base import Channel, OutgoingImage, OutgoingMessage


def test_channel_keeps_reference_markers_by_default():
    assert Channel.hides_reference_markers is False
```

在 `test/unit_test/api/channels/test_wecom_channel.py` 增加企业微信能力测试：

```python
def test_wecom_hides_reference_markers():
    assert make_channel().hides_reference_markers is True
```

将 `test/unit_test/api/channels/test_bootstrap.py` 改为验证清理文字和图片同时产生：

```python
from api.channels.bootstrap import _prepare_cited_output
from api.channels.core.base import OutgoingImage


def test_prepares_clean_text_and_cited_images_in_first_citation_order():
    chunks = [
        {"image_id": "bucket-zero.jpg"},
        {"image_id": ""},
        {"image_id": "bucket-two.jpg"},
        {"image_id": "bucket-zero.jpg"},
    ]
    answer = "先看 [ID:2]，再看 [0]，重复 [ID:2]，无图 [ID:1]，越界 [ID:9]。"

    assert _prepare_cited_output(answer, chunks) == (
        "先看，再看，重复，无图，越界。",
        [OutgoingImage("bucket-two.jpg"), OutgoingImage("bucket-zero.jpg")],
    )


def test_prepares_arabic_and_persian_digit_citations():
    chunks = [{"image_id": "zero"}, {"image_id": "one"}, {"image_id": "two"}]

    assert _prepare_cited_output("引用 [ID:٢] [ID:۱]。", chunks) == (
        "引用。",
        [OutgoingImage("two"), OutgoingImage("one")],
    )


def test_preserves_markdown_newlines_and_indentation():
    answer = "1. 第一项 [ID:0]\n   - 子项 [ID:1]\n\n```text\n原文\n```"

    assert _prepare_cited_output(answer, [{}, {}])[0] == "1. 第一项\n   - 子项\n\n```text\n原文\n```"


def test_hides_markers_when_chunk_container_is_invalid():
    assert _prepare_cited_output("正文 [ID:0]。", None) == ("正文。", [])
    assert _prepare_cited_output("正文 [ID:0]。", {}) == ("正文。", [])
```

- [ ] **Step 2: 运行定向测试，确认因新接口和能力缺失而失败**

Run:

```bash
uv run --frozen --group test pytest -q \
  test/unit_test/api/channels/test_base.py \
  test/unit_test/api/channels/test_bootstrap.py \
  test/unit_test/api/channels/test_wecom_channel.py
```

Expected: FAIL，错误包含无法导入 `_prepare_cited_output` 或缺少 `hides_reference_markers`。

- [ ] **Step 3: 增加 Channel 能力声明并由企业微信开启**

在 `api/channels/core/base.py` 的 `Channel` 中增加：

```python
class Channel(ABC):
    """One configured bot identity on one messaging platform."""

    channel_id: ClassVar[str]
    supports_reference_images: bool = False
    hides_reference_markers: bool = False
    account_id: str
```

在 `api/channels/wecom/channel.py` 中开启能力：

```python
class WeComChannel(Channel):
    channel_id = "wecom"
    hides_reference_markers = True
```

- [ ] **Step 4: 实现单次遍历的出站准备函数**

在 `api/channels/bootstrap.py` 中用 `_prepare_cited_output` 替换 `_extract_cited_images`。函数使用 `_CITATION_PATTERN.finditer` 的匹配区间组装清理文字，在同一循环中规范化编号、校验知识块、保持图片首次引用顺序并去重。标记后的水平空格仅在会与标记前空格重复或紧邻标点、换行、文本结尾时跳过；不得对完整结果再做全局空白压缩。

增加边界字符常量和以下完整实现：

```python
_HORIZONTAL_WHITESPACE = " \t"
_CITATION_TRAILING_PUNCTUATION = frozenset(",.;:!?，。；：！？、)]}）】")


def _last_output_char(parts: list[str]) -> str:
    for part in reversed(parts):
        if part:
            return part[-1]
    return ""


def _trim_output_horizontal_suffix(parts: list[str]) -> None:
    while parts:
        trimmed = parts[-1].rstrip(_HORIZONTAL_WHITESPACE)
        if trimmed:
            parts[-1] = trimmed
            return
        parts.pop()


def _prepare_cited_output(answer: str, chunks: object) -> tuple[str, list[OutgoingImage]]:
    text = answer or ""
    valid_chunks = chunks if isinstance(chunks, list) else []
    parts: list[str] = []
    images: list[OutgoingImage] = []
    seen: set[str] = set()
    cursor = 0

    for match in _CITATION_PATTERN.finditer(text):
        parts.append(text[cursor : match.start()])
        cursor = match.end()

        index = int(match.group(1).translate(_CITATION_DIGIT_TRANSLATION))
        if index < len(valid_chunks) and isinstance(valid_chunks[index], dict):
            image_id = str(valid_chunks[index].get("image_id") or "")
            if image_id and image_id not in seen:
                seen.add(image_id)
                images.append(OutgoingImage(image_id=image_id))

        space_end = cursor
        while space_end < len(text) and text[space_end] in _HORIZONTAL_WHITESPACE:
            space_end += 1

        next_char = text[space_end : space_end + 1]
        last_char = _last_output_char(parts)
        if not next_char or next_char in "\r\n" or next_char in _CITATION_TRAILING_PUNCTUATION:
            _trim_output_horizontal_suffix(parts)
            cursor = space_end
        elif last_char in _HORIZONTAL_WHITESPACE or not last_char or last_char in "\r\n":
            cursor = space_end

    parts.append(text[cursor:])
    return "".join(parts), images
```

- [ ] **Step 5: 在聊天处理器中保留原始回答并只清理企业微信出站文字**

在 `api/channels/bootstrap.py` 的 `async_chat` 循环中分别保留原始回答和出站回答：

```python
raw_answer = (ans or {}).get("answer", "") or ""
reference = (ans or {}).get("reference") or {}
prepared_text, cited_images = _prepare_cited_output(raw_answer, reference.get("chunks"))
answer_text = prepared_text if ch.hides_reference_markers else raw_answer
answer_images = cited_images if ch.supports_reference_images else []
```

`structure_answer` 和 `ConversationService.update_by_id` 的调用顺序保持不变，以确保数据库继续保存 `ans` 中的原始回答。

- [ ] **Step 6: 运行定向测试，确认新增行为通过**

Run:

```bash
uv run --frozen --group test pytest -q \
  test/unit_test/api/channels/test_base.py \
  test/unit_test/api/channels/test_bootstrap.py \
  test/unit_test/api/channels/test_wecom_channel.py
```

Expected: 所有测试 PASS，无失败和错误。

- [ ] **Step 7: 运行格式与语法检查**

Run:

```bash
uvx ruff check \
  api/channels/core/base.py \
  api/channels/bootstrap.py \
  api/channels/wecom/channel.py \
  test/unit_test/api/channels/test_base.py \
  test/unit_test/api/channels/test_bootstrap.py \
  test/unit_test/api/channels/test_wecom_channel.py
uvx ruff format --check \
  api/channels/core/base.py \
  api/channels/bootstrap.py \
  api/channels/wecom/channel.py \
  test/unit_test/api/channels/test_base.py \
  test/unit_test/api/channels/test_bootstrap.py \
  test/unit_test/api/channels/test_wecom_channel.py
.venv/bin/python -m py_compile \
  api/channels/core/base.py \
  api/channels/bootstrap.py \
  api/channels/wecom/channel.py
```

Expected: 三条命令均退出码为 0。

- [ ] **Step 8: 提交实现**

```bash
git add \
  api/channels/core/base.py \
  api/channels/bootstrap.py \
  api/channels/wecom/channel.py \
  test/unit_test/api/channels/test_base.py \
  test/unit_test/api/channels/test_bootstrap.py \
  test/unit_test/api/channels/test_wecom_channel.py
git commit -m "feat: hide citation markers in WeCom replies"
```

- [ ] **Step 9: 重启后端并验证运行版本和企业微信连接**

Run:

```bash
systemctl --user restart ragflow-server-manual.service
curl --fail --silent http://127.0.0.1:9380/api/v1/system/version
journalctl --user -u ragflow-server-manual.service --since "2 minutes ago" --no-pager \
  | rg "RAGFlow version|server is ready|websocket connected|websocket subscribed"
```

Expected: 版本接口返回实现提交号，日志包含服务器就绪、企业微信 WebSocket 已连接和已订阅。
