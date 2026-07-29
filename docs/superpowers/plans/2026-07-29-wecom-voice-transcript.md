# WeCom Voice Transcript Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Route WeCom AI Bot voice transcripts through the existing text conversation pipeline.

**Architecture:** Extend only the WebSocket callback parser in `WeComChannel`. Select content from `text.content` for text callbacks and `voice.content` for voice callbacks, then reuse `_handle_text_message` so validation, message construction, dispatch, conversation handling, and replies remain unchanged.

**Tech Stack:** Python 3.10+, asyncio, pytest, pytest-asyncio

## Global Constraints

- Use WeCom's existing `voice.content`; do not download audio or invoke RAGFlow ASR.
- Preserve sender, chat, request ID, chat type, and raw callback metadata.
- Keep unsupported message types ignored.
- Preserve all existing uncommitted workspace changes.

---

### Task 1: Route Voice Transcripts

**Files:**
- Modify: `test/unit_test/api/channels/test_wecom_channel.py`
- Modify: `api/channels/wecom/channel.py:567-596`

**Interfaces:**
- Consumes: `WeComChannel._handle_ws_message(headers: Any, body: Any, raw: Any) -> None`
- Produces: a normal `IncomingMessage` dispatched through `WeComChannel._handle_text_message`

- [ ] **Step 1: Write the failing regression test**

```python
@pytest.mark.asyncio
async def test_websocket_voice_message_dispatches_wecom_transcript():
    channel = make_channel()
    received = []

    async def handler(message):
        received.append(message)

    channel.set_message_handler(handler)
    raw = {"cmd": "aibot_msg_callback"}

    await channel._handle_ws_message(
        {"req_id": "voice-request-1"},
        {
            "msgtype": "voice",
            "from": {"userid": "user-1"},
            "chatid": "chat-1",
            "chattype": "group",
            "voice": {"content": "我的年假有多少天"},
        },
        raw,
    )

    assert len(received) == 1
    assert received[0].text == "我的年假有多少天"
    assert received[0].sender_id == "user-1"
    assert received[0].chat_id == "chat-1"
    assert received[0].chat_type == "group"
    assert received[0].message_id == "voice-request-1"
    assert received[0].raw is raw
```

This test catches either restoring the text-only guard or reading the transcript
from the wrong callback field.

- [ ] **Step 2: Run the test and verify RED**

Run:

```bash
uv run pytest -q test/unit_test/api/channels/test_wecom_channel.py::test_websocket_voice_message_dispatches_wecom_transcript
```

Expected: `FAIL` because `received` remains empty while `_handle_ws_message`
returns early for `msgtype: "voice"`.

- [ ] **Step 3: Implement the minimal parser change**

```python
msgtype = str(body.get("msgtype") or "")
if msgtype == "text":
    content = str((body.get("text") or {}).get("content") or "")
elif msgtype == "voice":
    content = str((body.get("voice") or {}).get("content") or "")
else:
    return
```

Remove the later text-only `content` assignment. Do not change
`_handle_text_message` or the downstream message model.

- [ ] **Step 4: Run the focused test and verify GREEN**

Run:

```bash
uv run pytest -q test/unit_test/api/channels/test_wecom_channel.py::test_websocket_voice_message_dispatches_wecom_transcript
```

Expected: `1 passed`.

- [ ] **Step 5: Run the WeCom regression suite**

Run:

```bash
uv run pytest -q test/unit_test/api/channels/test_wecom_channel.py
uv run ruff check api/channels/wecom/channel.py test/unit_test/api/channels/test_wecom_channel.py
```

Expected: all tests pass and Ruff reports no errors.

- [ ] **Step 6: Commit the implementation if repository hooks are available**

```bash
git add api/channels/wecom/channel.py test/unit_test/api/channels/test_wecom_channel.py docs/superpowers/specs/2026-07-29-wecom-voice-transcript-design.md docs/superpowers/plans/2026-07-29-wecom-voice-transcript.md
git commit -m "fix: handle WeCom voice transcripts"
```

If the existing Husky hook cannot start because `npx` is unavailable, do not
bypass it; leave the verified changes uncommitted and report the environment
blocker.
