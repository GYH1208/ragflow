# WeCom WebSocket Streaming Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stream RAG answers into one WeCom WebSocket reply bubble while preserving final citations, images, source files, and stored conversation state.

**Architecture:** Add an opt-in streaming method to the shared Channel contract. WeCom implements that method with `aibot_respond_msg`, the inbound callback request ID, and one stable stream ID; the bootstrap handler consumes RAG deltas, hides thinking, sends cumulative visible text, and finishes the stream with final reference attachments.

**Tech Stack:** Python 3.13, asyncio, aiohttp WebSocket, pytest/pytest-asyncio.

## Global Constraints

- Only WeCom WebSocket mode enables streaming.
- WeCom webhook mode and all other channels retain one-shot sending.
- Each WeCom update contains cumulative content and reuses both the inbound request ID and answer stream ID.
- Thinking content is never sent to the external channel.
- Final cited images and source files retain their existing order and failure isolation.
- Streaming implementation changes remain uncommitted at the user's request.

---

### Task 1: Shared Streaming Capability and WeCom Protocol

**Files:**
- Modify: `api/channels/core/base.py`
- Modify: `api/channels/wecom/channel.py`
- Test: `test/unit_test/api/channels/test_base.py`
- Test: `test/unit_test/api/channels/test_wecom_channel.py`

**Interfaces:**
- Produces: `Channel.supports_streaming: bool`
- Produces: `Channel.send_stream(message: OutgoingMessage, stream_id: str, finish: bool) -> None`
- Produces: `WeComChannel._ws_request(cmd: str, body: dict, *, request_id: str | None = None) -> dict`

- [ ] **Step 1: Write failing shared-contract and WeCom frame tests**

```python
@pytest.mark.asyncio
async def test_websocket_stream_reply_reuses_callback_request_and_stream_ids(monkeypatch):
    channel = make_channel()
    request = AsyncMock(return_value={"body": {}})
    monkeypatch.setattr(channel, "_ws_request", request)

    await channel.send_stream(
        OutgoingMessage(
            chat_id="chat-1",
            text="完整正文",
            reply_to_message_id="callback-1",
        ),
        stream_id="stream-1",
        finish=False,
    )

    request.assert_awaited_once_with(
        "aibot_respond_msg",
        {
            "msgtype": "stream",
            "stream": {
                "id": "stream-1",
                "content": "完整正文",
                "finish": False,
            },
        },
        request_id="callback-1",
    )
```

- [ ] **Step 2: Run the focused tests and verify missing streaming API failures**

Run:

```bash
uv run pytest \
  test/unit_test/api/channels/test_base.py \
  test/unit_test/api/channels/test_wecom_channel.py -q
```

Expected: FAIL because `supports_streaming`, `send_stream`, and explicit request ID support do not exist.

- [ ] **Step 3: Implement the minimal shared and WeCom streaming API**

```python
class Channel(ABC):
    supports_streaming: bool = False

    async def send_stream(
        self,
        message: OutgoingMessage,
        stream_id: str,
        finish: bool,
    ) -> None:
        if finish:
            await self.send(message)
```

`WeComChannel.send_stream` validates WebSocket reply context, sends `aibot_respond_msg`, and sends existing attachments only after a successful final frame. `_ws_request` uses `request_id` when supplied and otherwise keeps its generated request ID behavior.

- [ ] **Step 4: Run the focused tests and verify green**

Run the Task 1 command. Expected: all selected tests PASS.

### Task 2: RAG Delta Consumption in Channel Bootstrap

**Files:**
- Modify: `api/channels/bootstrap.py`
- Test: `test/unit_test/api/channels/test_bootstrap.py`

**Interfaces:**
- Consumes: `Channel.supports_streaming`
- Consumes: `Channel.send_stream(message, stream_id, finish)`
- Produces: `_run_streaming_completion(...)` that returns the final prepared `OutgoingMessage`

- [ ] **Step 1: Write a failing handler-level streaming test**

The test uses a real handler and controlled service boundaries. Its fake `async_chat` yields:

```python
{"answer": "", "reference": {}, "final": False, "start_to_think": True}
{"answer": "internal reasoning", "reference": {}, "final": False}
{"answer": "", "reference": {}, "final": False, "end_to_think": True}
{"answer": "第一段", "reference": {}, "final": False}
{"answer": "第二段 [ID:0]", "reference": {}, "final": False}
{
    "answer": "",
    "reference": {"chunks": [{"image_id": "bucket-image.jpg"}]},
    "final": True,
}
```

Assert that:

- `async_chat` receives `stream=True`.
- The first outbound content is the processing placeholder.
- Later outbound content is cumulative: `第一段`, then cleaned `第一段第二段`.
- No outbound message contains `internal reasoning`.
- The last update has `finish=True` and includes the cited image.
- Conversation persistence runs once after completion.

- [ ] **Step 2: Run the new test and verify non-streaming behavior failure**

Run:

```bash
uv run pytest \
  test/unit_test/api/channels/test_bootstrap.py::test_streaming_channel_sends_cumulative_visible_answer -q
```

Expected: FAIL because the current handler calls `async_chat(..., False)` and only invokes `send`.

- [ ] **Step 3: Implement streaming and retain the existing non-stream branch**

The streaming branch:

```python
stream_id = get_uuid()
await ch.send_stream(
    OutgoingMessage(
        chat_id=msg.chat_id,
        text="正在查询知识库，请稍候…",
        reply_to_message_id=msg.message_id,
    ),
    stream_id,
    False,
)

visible_answer = ""
thinking = False
async for ans in async_chat(dia, history, True, **chat_kwargs):
    structure_answer(conv, ans, message_id, conv.id)
    if ans.get("start_to_think"):
        thinking = True
        continue
    if ans.get("end_to_think"):
        thinking = False
        continue
    if not ans.get("final"):
        if not thinking:
            visible_answer += ans.get("answer") or ""
            await ch.send_stream(partial_message, stream_id, False)
        continue
    await ch.send_stream(final_message, stream_id, True)
```

The final message derives text and attachments with `_prepare_cited_output`. The non-streaming branch retains its existing one-shot `async_chat(..., False)` behavior.

- [ ] **Step 4: Run bootstrap and channel tests**

Run:

```bash
uv run pytest \
  test/unit_test/api/channels/test_bootstrap.py \
  test/unit_test/api/channels/test_base.py \
  test/unit_test/api/channels/test_wecom_channel.py -q
```

Expected: all selected tests PASS.

### Task 3: Failure Completion and Regression Verification

**Files:**
- Modify: `api/channels/bootstrap.py`
- Test: `test/unit_test/api/channels/test_bootstrap.py`

**Interfaces:**
- Consumes: the streaming handler from Task 2
- Produces: a completed error stream or ordinary-send fallback after streaming transport failure

- [ ] **Step 1: Write failure-path tests**

Cover two observable outcomes:

1. A completion exception after the stream starts results in a final `finish=True` error update on the same stream.
2. Failure to send the initial stream placeholder falls back to the existing one-shot generation and `send` path.

- [ ] **Step 2: Run each failure test and verify red**

Run:

```bash
uv run pytest test/unit_test/api/channels/test_bootstrap.py -k "streaming and (error or fallback)" -q
```

Expected: FAIL because the current implementation does not close or fall back from a failed stream.

- [ ] **Step 3: Implement minimal failure handling**

Track whether the first stream frame succeeded. If generation fails after start, call `send_stream` once with the Markdown error and `finish=True`. If the initial frame fails, run the existing non-streaming completion path and send one ordinary `OutgoingMessage`.

- [ ] **Step 4: Run formatting and regression checks**

Run:

```bash
uv run ruff check \
  api/channels/core/base.py \
  api/channels/bootstrap.py \
  api/channels/wecom/channel.py \
  test/unit_test/api/channels/test_base.py \
  test/unit_test/api/channels/test_bootstrap.py \
  test/unit_test/api/channels/test_wecom_channel.py

uv run pytest test/unit_test/api/channels -q
```

Expected: Ruff exits 0 and all channel tests PASS.

- [ ] **Step 5: Inspect the final uncommitted change set**

Run:

```bash
git diff --check
git status --short
git diff --stat
```

Expected: only the plan plus streaming implementation/tests are uncommitted; no implementation commit is created.
