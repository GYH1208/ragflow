# 企业微信 WebSocket 引用图片回复实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**目标：** 企业微信 WebSocket 用户收到 RAG 文字回答后，继续收到正文实际引用的知识块图片。

**架构：** 公共 Channel 模型增加引用图片能力标记和存储图片引用；启动层仅为支持该能力的企业微信 WebSocket 开启引用，并从回答中筛选图片。企业微信适配器使用带 `req_id` 的 WebSocket 请求—响应关联器，按初始化、分片、完成顺序上传图片，再通过 `media_id` 主动发送。

**技术栈：** Python 3.10+、asyncio、aiohttp、pytest、pytest-asyncio、RAGFlow `STORAGE_IMPL`

## 全局约束

- 只发送正文以 `[ID:n]` 实际引用且存在 `image_id` 的图片。
- 图片按首次引用顺序发送，相同 `image_id` 只发送一次。
- 文字先发送；单张图片失败不影响后续图片。
- 仅企业微信 WebSocket 开启引用图片能力，其他 Channel 行为不变。
- 图片数据只从 RAGFlow 对象存储读取，不要求公网图片 URL。
- 测试不得访问真实企业微信或外部网络。

---

### 任务 1：扩展公共 Channel 图片消息模型

**文件：**
- 修改：`api/channels/core/base.py`
- 新建测试：`test/unit_test/api/channels/test_base.py`

**接口：**
- 产出：`OutgoingImage(image_id: str)`
- 产出：`OutgoingMessage.images: list[OutgoingImage]`
- 产出：`Channel.supports_reference_images: bool = False`

- [ ] **步骤 1：编写失败测试**

```python
from api.channels.core.base import OutgoingImage, OutgoingMessage


def test_outgoing_message_remains_text_only_by_default():
    message = OutgoingMessage(chat_id="chat-1", text="answer")
    assert message.images == []


def test_outgoing_message_accepts_storage_image_references():
    image = OutgoingImage(image_id="bucket-object.jpg")
    message = OutgoingMessage(chat_id="chat-1", text="answer", images=[image])
    assert message.images == [image]
```

- [ ] **步骤 2：运行测试并确认因新接口不存在而失败**

运行：`uv run pytest test/unit_test/api/channels/test_base.py -q`

预期：导入 `OutgoingImage` 失败。

- [ ] **步骤 3：实现最小公共模型**

在 `base.py` 中引入 `field`，增加：

```python
@dataclass(frozen=True)
class OutgoingImage:
    image_id: str


@dataclass
class OutgoingMessage:
    chat_id: str
    text: str
    reply_to_message_id: Optional[str] = None
    images: list[OutgoingImage] = field(default_factory=list)
```

并在 `Channel` 上增加默认能力：

```python
supports_reference_images: bool = False
```

- [ ] **步骤 4：运行测试确认通过**

运行：`uv run pytest test/unit_test/api/channels/test_base.py -q`

预期：2 个测试通过。

- [ ] **步骤 5：提交**

```bash
git add api/channels/core/base.py test/unit_test/api/channels/test_base.py
HUSKY=0 git commit -m "feat: add channel image references"
```

### 任务 2：从最终回答筛选实际引用图片

**文件：**
- 修改：`api/channels/bootstrap.py`
- 新建测试：`test/unit_test/api/channels/test_bootstrap.py`

**接口：**
- 消费：`OutgoingImage`
- 产出：`_extract_cited_images(answer: str, chunks: object) -> list[OutgoingImage]`
- 产出：支持图片的 Channel 使用 `quote=True`，其他 Channel 使用 `quote=False`

- [ ] **步骤 1：编写引用筛选失败测试**

```python
from api.channels.bootstrap import _extract_cited_images
from api.channels.core.base import OutgoingImage


def test_extracts_only_cited_images_in_first_citation_order():
    chunks = [
        {"image_id": "bucket-zero.jpg"},
        {"image_id": ""},
        {"image_id": "bucket-two.jpg"},
        {"image_id": "bucket-zero.jpg"},
    ]
    answer = "先看 [ID:2]，再看 [0]，重复 [ID:2]，无图 [ID:1]，越界 [ID:9]"

    assert _extract_cited_images(answer, chunks) == [
        OutgoingImage("bucket-two.jpg"),
        OutgoingImage("bucket-zero.jpg"),
    ]


def test_extracts_citations_written_with_arabic_and_persian_digits():
    chunks = [{"image_id": "zero"}, {"image_id": "one"}, {"image_id": "two"}]
    assert _extract_cited_images("[ID:٢] [ID:۱]", chunks) == [
        OutgoingImage("two"),
        OutgoingImage("one"),
    ]


def test_returns_no_images_for_invalid_chunk_container():
    assert _extract_cited_images("[ID:0]", None) == []
    assert _extract_cited_images("[ID:0]", {}) == []
```

- [ ] **步骤 2：运行并确认失败**

运行：`uv run pytest test/unit_test/api/channels/test_bootstrap.py -q`

预期：导入 `_extract_cited_images` 失败。

- [ ] **步骤 3：实现引用筛选辅助函数**

在 `bootstrap.py` 增加正则表达式和数字规范化：

```python
_CITATION_PATTERN = re.compile(r"\[(?:ID:)?([0-9\u0660-\u0669\u06F0-\u06F9]+)\]")


def _normalize_citation_digits(value: str) -> str:
    return value.translate(str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789"))


def _extract_cited_images(answer: str, chunks: object) -> list[OutgoingImage]:
    if not isinstance(chunks, list):
        return []
    images = []
    seen = set()
    for match in _CITATION_PATTERN.finditer(answer or ""):
        index = int(_normalize_citation_digits(match.group(1)))
        if index >= len(chunks) or not isinstance(chunks[index], dict):
            continue
        image_id = str(chunks[index].get("image_id") or "")
        if not image_id or image_id in seen:
            continue
        seen.add(image_id)
        images.append(OutgoingImage(image_id=image_id))
    return images
```

- [ ] **步骤 4：把引用数据装入出站消息**

在 `_make_chat_handler` 中：

```python
answer_images = []
chat_kwargs = {"quote": bool(ch.supports_reference_images)}
...
structure_answer(conv, ans, message_id, conv.id)
reference = (ans or {}).get("reference") or {}
answer_images = _extract_cited_images(answer_text, reference.get("chunks"))
...
OutgoingMessage(..., images=answer_images)
```

确保异常回答和无图片回答仍使用空列表。

- [ ] **步骤 5：运行引用筛选测试确认通过**

运行：`uv run pytest test/unit_test/api/channels/test_bootstrap.py test/unit_test/api/channels/test_base.py -q`

预期：全部通过。

- [ ] **步骤 6：提交**

```bash
git add api/channels/bootstrap.py test/unit_test/api/channels/test_bootstrap.py
HUSKY=0 git commit -m "feat: attach cited images to channel answers"
```

### 任务 3：实现 WebSocket 请求—响应关联

**文件：**
- 修改：`api/channels/wecom/channel.py`
- 新建测试：`test/unit_test/api/channels/test_wecom_channel.py`

**接口：**
- 产出：`WeComChannel._ws_request(cmd: str, body: dict, timeout: float = 10) -> dict`
- 产出：`WeComChannel._fail_pending_requests(error: BaseException) -> None`
- 修改：`_handle_ws_payload` 优先完成匹配的 Future

- [ ] **步骤 1：编写响应关联失败测试**

```python
import asyncio
import json
from unittest.mock import AsyncMock

import pytest

from api.channels.wecom.channel import WeComAccount, WeComChannel


def make_channel():
    channel = WeComChannel(WeComAccount(account_id="acc", connection_type="websocket", bot_id="bot", secret="secret"))
    channel._ws = AsyncMock()
    channel._ws.closed = False
    channel._ws_send_lock = asyncio.Lock()
    return channel


@pytest.mark.asyncio
async def test_ws_request_resolves_matching_response():
    channel = make_channel()
    task = asyncio.create_task(channel._ws_request("aibot_send_msg", {"chatid": "chat"}))
    await asyncio.sleep(0)
    sent = channel._ws.send_json.await_args.args[0]
    await channel._handle_ws_payload(json.dumps({"cmd": "aibot_send_msg", "headers": sent["headers"], "errcode": 0, "body": {"ok": True}}))
    assert await task == {"cmd": "aibot_send_msg", "headers": sent["headers"], "errcode": 0, "body": {"ok": True}}


@pytest.mark.asyncio
async def test_ws_request_raises_for_protocol_error():
    channel = make_channel()
    task = asyncio.create_task(channel._ws_request("aibot_send_msg", {}))
    await asyncio.sleep(0)
    sent = channel._ws.send_json.await_args.args[0]
    await channel._handle_ws_payload(json.dumps({"headers": sent["headers"], "errcode": 40001, "errmsg": "bad request"}))
    with pytest.raises(RuntimeError, match="40001"):
        await task


@pytest.mark.asyncio
async def test_disconnect_fails_and_clears_pending_requests():
    channel = make_channel()
    task = asyncio.create_task(channel._ws_request("aibot_send_msg", {}))
    await asyncio.sleep(0)
    channel._fail_pending_requests(ConnectionError("disconnected"))
    with pytest.raises(ConnectionError, match="disconnected"):
        await task
    assert channel._ws_pending == {}
```

- [ ] **步骤 2：运行并确认失败**

运行：`uv run pytest test/unit_test/api/channels/test_wecom_channel.py -q`

预期：缺少 `_ws_request` 或 `_ws_pending`。

- [ ] **步骤 3：实现关联器和清理逻辑**

在构造函数初始化：

```python
self._ws_pending: dict[str, asyncio.Future] = {}
```

实现 `_ws_request`：先登记 Future，再在锁内发送 `{cmd, headers, body}`，使用 `asyncio.wait_for` 等待；`finally` 删除映射。实现 `_fail_pending_requests`，为所有未完成 Future 设置异常并清空映射。

在 `_handle_ws_payload` 解析 JSON 后，先验证顶层对象、响应头和 `errcode`，再读取 `headers.req_id`；命中待处理 Future 时设置结果或异常并返回。未命中时把消息回调和事件处理调度到受跟踪的后台任务，使接收循环可以继续读取出站消息的 ACK。

在 `_run_websocket` 的 `finally` 以及 `stop` 中调用 `_fail_pending_requests(ConnectionError(...))`。

- [ ] **步骤 4：运行响应关联测试确认通过**

运行：`uv run pytest test/unit_test/api/channels/test_wecom_channel.py -q`

预期：3 个测试通过。

- [ ] **步骤 5：提交**

```bash
git add api/channels/wecom/channel.py test/unit_test/api/channels/test_wecom_channel.py
HUSKY=0 git commit -m "feat: correlate WeCom WebSocket responses"
```

### 任务 4：实现 WebSocket 图片分片上传

**文件：**
- 修改：`api/channels/wecom/channel.py`
- 修改测试：`test/unit_test/api/channels/test_wecom_channel.py`

**接口：**
- 产出：`WECOM_MEDIA_CHUNK_SIZE`
- 产出：`_upload_websocket_image(image: bytes, filename: str) -> str`
- 产出：`_load_stored_image(image_id: str) -> bytes | None`
- 产出：`_send_websocket_image(chat_id: str, media_id: str) -> None`

- [ ] **步骤 1：编写分片上传失败测试**

```python
import base64
import hashlib


@pytest.mark.asyncio
async def test_upload_image_uses_init_chunks_and_finish(monkeypatch):
    channel = make_channel()
    requests = []

    async def request(cmd, body, timeout=10):
        requests.append((cmd, body))
        if cmd == "aibot_upload_media_init":
            return {"body": {"upload_id": "upload-1"}}
        if cmd == "aibot_upload_media_finish":
            return {"body": {"media_id": "media-1"}}
        return {"body": {}}

    monkeypatch.setattr(channel, "_ws_request", request)
    monkeypatch.setattr("api.channels.wecom.channel.WECOM_MEDIA_CHUNK_SIZE", 3)

    assert await channel._upload_websocket_image(b"abcdefg", "reference.jpg") == "media-1"
    assert requests[0] == ("aibot_upload_media_init", {
        "type": "image",
        "filename": "reference.jpg",
        "total_size": 7,
        "total_chunks": 3,
        "md5": hashlib.md5(b"abcdefg").hexdigest(),
    })
    assert [request[1]["chunk_index"] for request in requests[1:4]] == [0, 1, 2]
    assert base64.b64decode(requests[1][1]["base64_data"]) == b"abc"
    assert requests[-1] == ("aibot_upload_media_finish", {"upload_id": "upload-1"})
```

```python
@pytest.mark.asyncio
async def test_upload_image_requires_media_id(monkeypatch):
    channel = make_channel()

    async def request(cmd, body, timeout=10):
        if cmd == "aibot_upload_media_init":
            return {"body": {"upload_id": "upload-1"}}
        return {"body": {}}

    monkeypatch.setattr(channel, "_ws_request", request)
    with pytest.raises(RuntimeError, match="media_id"):
        await channel._upload_websocket_image(b"image", "reference.jpg")


@pytest.mark.asyncio
async def test_send_websocket_image_uses_media_id(monkeypatch):
    channel = make_channel()
    request = AsyncMock(return_value={"body": {}})
    monkeypatch.setattr(channel, "_ws_request", request)

    await channel._send_websocket_image("chat-1", "media-1")

    request.assert_awaited_once_with(
        "aibot_send_msg",
        {"chatid": "chat-1", "msgtype": "image", "image": {"media_id": "media-1"}},
    )
```

- [ ] **步骤 2：运行并确认失败**

运行：`uv run pytest test/unit_test/api/channels/test_wecom_channel.py -q`

预期：缺少 `_upload_websocket_image`。

- [ ] **步骤 3：实现上传协议**

使用 `base64.b64encode`、`hashlib.md5` 和向上取整的分片数量。每片最大 512 KiB，`chunk_index` 从 0 开始，最多允许 100 片。严格检查 init 返回的 `upload_id` 和 finish 返回的 `media_id`，缺失时抛出含命令名称的 `RuntimeError`。通过 `_ws_request` 发送每个协议帧。

`_load_stored_image` 使用 `parse_storage_composite_id` 解析 ID，并通过 `settings.STORAGE_IMPL.get(bucket=..., fnm=...)` 读取数据；无效或空数据返回 `None`。

- [ ] **步骤 4：运行上传测试确认通过**

运行：`uv run pytest test/unit_test/api/channels/test_wecom_channel.py -q`

预期：新增上传测试全部通过。

- [ ] **步骤 5：提交**

```bash
git add api/channels/wecom/channel.py test/unit_test/api/channels/test_wecom_channel.py
HUSKY=0 git commit -m "feat: upload images through WeCom WebSocket"
```

### 任务 5：串联文字和引用图片发送

**文件：**
- 修改：`api/channels/wecom/channel.py`
- 修改测试：`test/unit_test/api/channels/test_wecom_channel.py`

**接口：**
- 消费：`OutgoingMessage.images`
- 修改：`_send_websocket_message` 先发 Markdown，再逐图上传并发送

- [ ] **步骤 1：编写发送顺序和失败隔离测试**

```python
from api.channels.core.base import OutgoingImage, OutgoingMessage


@pytest.mark.asyncio
async def test_websocket_sends_text_then_images_in_order(monkeypatch):
    channel = make_channel()
    events = []
    monkeypatch.setattr(channel, "_ws_request", AsyncMock(side_effect=lambda cmd, body, timeout=10: events.append((cmd, body)) or {"body": {}}))
    monkeypatch.setattr(channel, "_load_stored_image", lambda image_id: image_id.encode())
    monkeypatch.setattr(channel, "_upload_websocket_image", AsyncMock(side_effect=["media-a", "media-b"]))
    monkeypatch.setattr(channel, "_send_websocket_image", AsyncMock(side_effect=lambda chat_id, media_id: events.append(("image", media_id))))

    await channel.send(OutgoingMessage(
        chat_id="chat-1",
        text="answer",
        images=[OutgoingImage("image-a"), OutgoingImage("image-b")],
    ))

    assert events[0][1]["msgtype"] == "markdown"
    assert events[1:] == [("image", "media-a"), ("image", "media-b")]
```

```python
@pytest.mark.asyncio
async def test_image_failure_does_not_block_later_images(monkeypatch):
    channel = make_channel()
    request = AsyncMock(return_value={"body": {}})
    monkeypatch.setattr(channel, "_ws_request", request)
    monkeypatch.setattr(channel, "_load_stored_image", lambda image_id: image_id.encode())
    monkeypatch.setattr(channel, "_upload_websocket_image", AsyncMock(side_effect=[RuntimeError("failed"), "media-b"]))
    send_image = AsyncMock()
    monkeypatch.setattr(channel, "_send_websocket_image", send_image)

    await channel.send(OutgoingMessage(
        chat_id="chat-1",
        text="answer",
        images=[OutgoingImage("image-a"), OutgoingImage("image-b")],
    ))

    send_image.assert_awaited_once_with("chat-1", "media-b")


@pytest.mark.asyncio
async def test_missing_stored_image_is_skipped(monkeypatch):
    channel = make_channel()
    request = AsyncMock(return_value={"body": {}})
    monkeypatch.setattr(channel, "_ws_request", request)
    monkeypatch.setattr(channel, "_load_stored_image", lambda image_id: None)
    upload = AsyncMock()
    monkeypatch.setattr(channel, "_upload_websocket_image", upload)

    await channel.send(OutgoingMessage(chat_id="chat-1", text="answer", images=[OutgoingImage("missing")]))

    upload.assert_not_awaited()


@pytest.mark.asyncio
async def test_text_only_message_sends_only_markdown(monkeypatch):
    channel = make_channel()
    request = AsyncMock(return_value={"body": {}})
    monkeypatch.setattr(channel, "_ws_request", request)

    await channel.send(OutgoingMessage(chat_id="chat-1", text="answer"))

    request.assert_awaited_once_with(
        "aibot_send_msg",
        {"chatid": "chat-1", "msgtype": "markdown", "markdown": {"content": "answer"}},
    )
```

- [ ] **步骤 2：运行并确认失败**

运行：`uv run pytest test/unit_test/api/channels/test_wecom_channel.py -q`

预期：现有实现不处理 `message.images`，顺序断言失败。

- [ ] **步骤 3：实现编排**

企业微信 WebSocket 实例在构造时设置：

```python
self.supports_reference_images = self.connection_type == "websocket"
```

把 Markdown 发送改为 `_ws_request("aibot_send_msg", body)`。成功后依次处理 `message.images`：读取对象存储、上传、发送。每张图片使用独立的 `try/except`，记录带 `image_id` 的错误日志并继续循环。

- [ ] **步骤 4：运行所有 Channel 单元测试**

运行：`uv run pytest test/unit_test/api/channels -q`

预期：全部通过，无警告或未处理 Future。

- [ ] **步骤 5：运行 Ruff 和差异检查**

运行：

```bash
uv run ruff check api/channels/core/base.py api/channels/bootstrap.py api/channels/wecom/channel.py test/unit_test/api/channels
uv run ruff format --check api/channels/core/base.py api/channels/bootstrap.py api/channels/wecom/channel.py test/unit_test/api/channels
git diff --check
```

预期：三个命令均以状态码 0 结束。

- [ ] **步骤 6：提交**

```bash
git add api/channels/wecom/channel.py test/unit_test/api/channels/test_wecom_channel.py
HUSKY=0 git commit -m "feat: send cited images to WeCom chats"
```

### 任务 6：最终回归验证

**文件：** 无新增修改；若验证发现缺陷，先增加能够复现问题的失败测试，再修复。

- [ ] **步骤 1：运行目标测试集**

运行：`uv run pytest test/unit_test/api/channels -q`

预期：全部通过。

- [ ] **步骤 2：运行相关现有测试**

运行：

```bash
uv run pytest test/unit_test/rag/utils/test_base64_image.py test/unit_test/api/db/services/test_dialog_service_final_answer.py -q
```

预期：全部通过。

- [ ] **步骤 3：检查最终工作区和提交历史**

运行：

```bash
git status --short
git log -7 --oneline
```

预期：工作区干净，实施任务均有对应提交。
