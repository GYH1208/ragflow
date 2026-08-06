# WeCom PDF Reference Image Filter Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a per-WeCom-WebSocket switch that defaults off and prevents PDF reference screenshots from being sent to WeCom while preserving non-PDF answer images and the complete RAGFlow web reference experience.

**Architecture:** Keep `reference.chunks` unchanged and introduce a channel-level `allows_reference_image(chunk)` policy. The base channel permits images, WeCom applies its `send_pdf_reference_images` setting using the source filename, and the bootstrap layer invokes the policy only while converting trusted chunks into outbound images.

**Tech Stack:** Python 3.10+, pytest/pytest-asyncio, React 18, TypeScript, Jest, React Hook Form dynamic fields.

## Global Constraints

- The switch lives in the WeCom WebSocket bot configuration, not assistant chat settings.
- The saved key is `config.credential.send_pdf_reference_images`.
- Missing/legacy configuration means `false`.
- When disabled, filenames ending in `.pdf` case-insensitively are rejected for WeCom image delivery.
- Missing source filenames are rejected for WeCom image delivery and logged.
- Non-PDF reference images continue to be delivered.
- `reference`, `image_id`, web chat, share pages, evidence resolution, source-file delivery, and other channels remain unchanged.
- No database migration, document reparse, index rewrite, or new dependency.

---

## File Map

- `api/channels/core/base.py`: Defines the default channel image-authorization contract.
- `api/channels/wecom/channel.py`: Stores the WeCom switch, parses legacy/new configuration, and implements PDF filename filtering.
- `api/channels/bootstrap.py`: Applies the active channel policy while building `OutgoingImage` values from trusted Chunk IDs.
- `test/unit_test/api/channels/test_base.py`: Protects the default allow behavior for non-WeCom channels.
- `test/unit_test/api/channels/test_wecom_channel.py`: Protects WeCom PDF, non-PDF, missing-filename, enabled-switch, and builder-default behavior.
- `test/unit_test/api/channels/test_bootstrap.py`: Protects mixed PDF/non-PDF outbound image selection without mutating references.
- `web/src/pages/user-setting/chat-channel/constant/index.tsx`: Adds the WebSocket-only switch field and false default.
- `web/src/pages/user-setting/chat-channel/constant/index.test.tsx`: Protects field type, key, default, and conditional visibility.

---

### Task 1: Channel Policy Contract and WeCom Configuration

**Files:**
- Modify: `api/channels/core/base.py:25-75`
- Modify: `api/channels/wecom/channel.py:23-42,146-165,815-855`
- Modify: `test/unit_test/api/channels/test_base.py:1-55`
- Modify: `test/unit_test/api/channels/test_wecom_channel.py:1-35`

**Interfaces:**
- Produces: `Channel.allows_reference_image(chunk: dict[str, Any]) -> bool`, defaulting to `True`.
- Produces: `WeComAccount.send_pdf_reference_images: bool = False`.
- Produces: `WeComChannel.allows_reference_image(chunk: dict[str, Any]) -> bool`.
- Consumes: `chunk["document_name"]` with fallback to `chunk["docnm_kwd"]`.

- [ ] **Step 1: Write the failing default-policy test**

Add to `test/unit_test/api/channels/test_base.py`:

```python
def test_channel_allows_reference_images_by_default():
    channel = RecordingChannel()

    assert channel.allows_reference_image({
        "image_id": "image-1",
        "document_name": "policy.pdf",
    }) is True
```

- [ ] **Step 2: Run the default-policy test and verify RED**

Run:

```bash
uv run pytest test/unit_test/api/channels/test_base.py::test_channel_allows_reference_images_by_default -q
```

Expected: FAIL with `AttributeError: 'RecordingChannel' object has no attribute 'allows_reference_image'`.

- [ ] **Step 3: Add the minimal base-channel contract**

Add to `Channel` in `api/channels/core/base.py` before `send_stream`:

```python
    def allows_reference_image(self, chunk: dict[str, Any]) -> bool:
        """Return whether this channel may deliver an image from a reference chunk."""
        return True
```

- [ ] **Step 4: Run the default-policy test and verify GREEN**

Run:

```bash
uv run pytest test/unit_test/api/channels/test_base.py::test_channel_allows_reference_images_by_default -q
```

Expected: PASS.

- [ ] **Step 5: Write failing WeCom policy and configuration tests**

Change the import and factory at the top of `test/unit_test/api/channels/test_wecom_channel.py`:

```python
from api.channels.wecom.channel import WeComAccount, WeComChannel, _build


def make_channel(*, send_pdf_reference_images: bool = False):
    channel = WeComChannel(
        WeComAccount(
            account_id="acc",
            connection_type="websocket",
            bot_id="bot",
            secret="secret",
            send_pdf_reference_images=send_pdf_reference_images,
        )
    )
    channel._ws = AsyncMock()
    channel._ws.closed = False
    channel._ws_send_lock = asyncio.Lock()
    return channel
```

Add these tests next to `test_wecom_hides_reference_markers`:

```python
@pytest.mark.parametrize("filename", ["policy.pdf", "POLICY.PDF", " policy.PdF "])
def test_wecom_rejects_pdf_reference_images_by_default(filename):
    assert make_channel().allows_reference_image({
        "image_id": "pdf-image",
        "document_name": filename,
    }) is False


def test_wecom_allows_non_pdf_reference_images_by_default():
    assert make_channel().allows_reference_image({
        "image_id": "answer-image",
        "document_name": "faq.docx",
    }) is True


def test_wecom_rejects_reference_images_without_source_filename(caplog):
    channel = make_channel()

    with caplog.at_level(logging.WARNING, logger="api.channels.wecom.channel"):
        allowed = channel.allows_reference_image({"image_id": "unknown-image"})

    assert allowed is False
    assert "reason=missing_document_name" in caplog.text


def test_wecom_allows_pdf_reference_images_when_enabled():
    assert make_channel(send_pdf_reference_images=True).allows_reference_image({
        "image_id": "pdf-image",
        "document_name": "policy.pdf",
    }) is True


def test_wecom_builder_defaults_pdf_reference_images_to_disabled():
    channel = _build("acc", {
        "connection_type": "websocket",
        "bot_id": "bot",
        "secret": "secret",
    })

    assert channel.account.send_pdf_reference_images is False


def test_wecom_builder_reads_enabled_pdf_reference_image_setting():
    channel = _build("acc", {
        "connection_type": "websocket",
        "bot_id": "bot",
        "secret": "secret",
        "send_pdf_reference_images": True,
    })

    assert channel.account.send_pdf_reference_images is True
```

- [ ] **Step 6: Run the WeCom tests and verify RED**

Run:

```bash
uv run pytest \
  test/unit_test/api/channels/test_wecom_channel.py::test_wecom_rejects_pdf_reference_images_by_default \
  test/unit_test/api/channels/test_wecom_channel.py::test_wecom_allows_non_pdf_reference_images_by_default \
  test/unit_test/api/channels/test_wecom_channel.py::test_wecom_rejects_reference_images_without_source_filename \
  test/unit_test/api/channels/test_wecom_channel.py::test_wecom_allows_pdf_reference_images_when_enabled \
  test/unit_test/api/channels/test_wecom_channel.py::test_wecom_builder_defaults_pdf_reference_images_to_disabled \
  test/unit_test/api/channels/test_wecom_channel.py::test_wecom_builder_reads_enabled_pdf_reference_image_setting -q
```

Expected: collection or setup FAIL because `WeComAccount` lacks `send_pdf_reference_images` and `WeComChannel` still inherits the default allow policy.

- [ ] **Step 7: Implement the WeCom setting and policy**

Add the field to `WeComAccount` in `api/channels/wecom/channel.py`:

```python
    send_pdf_reference_images: bool = False
```

Add this method to `WeComChannel` after `__init__`:

```python
    def allows_reference_image(self, chunk: dict[str, Any]) -> bool:
        if self.account.send_pdf_reference_images:
            return True

        filename = str(
            chunk.get("document_name")
            or chunk.get("docnm_kwd")
            or ""
        ).strip()
        if not filename:
            LOGGER.warning(
                "[wecom:%s] reference image skipped "
                "reason=missing_document_name image_id=%s",
                self.account_id,
                chunk.get("image_id") or chunk.get("img_id") or "",
            )
            return False
        return not filename.casefold().endswith(".pdf")
```

Pass the setting in `_build`:

```python
            send_pdf_reference_images=(
                cfg.get("send_pdf_reference_images") is True
            ),
```

Use an exact boolean check so a malformed string such as `"false"` cannot enable PDF delivery.

- [ ] **Step 8: Run the Task 1 test files**

Run:

```bash
uv run pytest \
  test/unit_test/api/channels/test_base.py \
  test/unit_test/api/channels/test_wecom_channel.py -q
```

Expected: all tests PASS.

- [ ] **Step 9: Commit Task 1**

```bash
git add \
  api/channels/core/base.py \
  api/channels/wecom/channel.py \
  test/unit_test/api/channels/test_base.py \
  test/unit_test/api/channels/test_wecom_channel.py
git commit -m "功能：增加企业微信引用图片发送策略"
```

---

### Task 2: Apply the Policy to Trusted Image Delivery

**Files:**
- Modify: `api/channels/bootstrap.py:126-153,500-525`
- Modify: `test/unit_test/api/channels/test_bootstrap.py:1-45,284-325,730-820`

**Interfaces:**
- Consumes: `Channel.allows_reference_image(chunk: dict[str, Any]) -> bool` from Task 1.
- Produces: `_images_for_used_chunks(chunks, used_chunk_ids, max_images=2, image_allowed=None) -> list[OutgoingImage]`.
- Preserves: original `reference.chunks` and `image_id` values.

- [ ] **Step 1: Write a failing helper-level mixed-source test**

Add to `test/unit_test/api/channels/test_bootstrap.py` after the existing `_images_for_used_chunks` tests:

```python
def test_images_for_used_chunks_applies_policy_before_image_limit():
    chunks = [
        {
            "id": "pdf",
            "image_id": "pdf-image",
            "document_name": "policy.pdf",
        },
        {
            "id": "faq-1",
            "image_id": "faq-image-1",
            "document_name": "faq.docx",
        },
        {
            "id": "faq-2",
            "image_id": "faq-image-2",
            "document_name": "answers.xlsx",
        },
    ]

    images = bootstrap._images_for_used_chunks(
        chunks,
        ["pdf", "faq-1", "faq-2"],
        image_allowed=lambda chunk: not chunk["document_name"].lower().endswith(".pdf"),
    )

    assert images == [
        OutgoingImage("faq-image-1"),
        OutgoingImage("faq-image-2"),
    ]
    assert chunks[0]["image_id"] == "pdf-image"
```

- [ ] **Step 2: Run the helper test and verify RED**

Run:

```bash
uv run pytest test/unit_test/api/channels/test_bootstrap.py::test_images_for_used_chunks_applies_policy_before_image_limit -q
```

Expected: FAIL with `TypeError` because `_images_for_used_chunks` does not accept `image_allowed`.

- [ ] **Step 3: Add the optional policy callback to the helper**

Import `Callable` in `api/channels/bootstrap.py`:

```python
from collections.abc import Callable
```

Change the helper signature and filter before deduplication/limit accounting:

```python
def _images_for_used_chunks(
    chunks: object,
    used_chunk_ids: list[str],
    max_images: int = 2,
    image_allowed: Callable[[dict], bool] | None = None,
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
        if image_allowed is not None and not image_allowed(chunk):
            continue
        seen_image_ids.add(image_id)
        images.append(OutgoingImage(image_id=image_id))
        if len(images) == max_images:
            break
    return images
```

- [ ] **Step 4: Run all helper tests and verify GREEN**

Run:

```bash
uv run pytest test/unit_test/api/channels/test_bootstrap.py -k images_for_used_chunks -q
```

Expected: all selected tests PASS, including existing ordering and deduplication tests.

- [ ] **Step 5: Write a failing handler integration test**

Add a default policy method to the existing `RecordingStreamingChannel` test double:

```python
    def allows_reference_image(self, chunk):
        return True
```

Add this integration test near the existing trusted-image streaming tests:

```python
@pytest.mark.asyncio
async def test_handler_keeps_pdf_reference_but_filters_it_from_channel_images(monkeypatch):
    class PdfFilteringChannel(RecordingStreamingChannel):
        def allows_reference_image(self, chunk):
            filename = str(chunk.get("document_name") or "").lower()
            return bool(filename) and not filename.endswith(".pdf")

    channel = PdfFilteringChannel()
    conversation = FakeConversation()
    dialog = SimpleNamespace(
        id="dialog-1",
        kb_ids=["kb-1"],
        prompt_config={"quote": True},
    )

    async def fake_async_chat(dia, history, stream, **kwargs):
        yield {
            "answer": "回答正文",
            "reference": {
                "chunks": [
                    {
                        "id": "pdf-chunk",
                        "content": "制度内容",
                        "image_id": "pdf-image",
                        "document_name": "policy.pdf",
                    },
                    {
                        "id": "faq-chunk",
                        "content": "问答配图",
                        "image_id": "faq-image",
                        "document_name": "faq.docx",
                    },
                ]
            },
            "final": True,
        }

    install_handler_service_stubs(
        monkeypatch,
        conversation=conversation,
        dialog=dialog,
        async_chat=fake_async_chat,
        persisted=[],
    )

    handler = bootstrap._make_chat_handler(channel)
    await handler(IncomingMessage(
        channel="wecom",
        account_id="account-1",
        chat_id="chat-1",
        chat_type="p2p",
        message_id="callback-1",
        sender_id="user-1",
        text="问题",
    ))

    assert conversation.reference[-1]["chunks"][0]["image_id"] == "pdf-image"
    assert channel.messages[0].images == [OutgoingImage("faq-image")]
```

- [ ] **Step 6: Run the integration test and verify RED**

Run:

```bash
uv run pytest test/unit_test/api/channels/test_bootstrap.py::test_handler_keeps_pdf_reference_but_filters_it_from_channel_images -q
```

Expected: FAIL because `channel.messages[0].images` still includes `pdf-image`.

- [ ] **Step 7: Wire the active channel policy into image selection**

Change the evidence-image call in `_make_chat_handler`:

```python
            evidence_images = _images_for_used_chunks(
                valid_chunks,
                resolution.used_chunk_ids,
                image_allowed=ch.allows_reference_image,
            )
```

Do not alter `valid_chunks`, `resolution.used_chunk_ids`, `reference`, or the `has_image_candidate` gate.

- [ ] **Step 8: Run the complete bootstrap test file**

Run:

```bash
uv run pytest test/unit_test/api/channels/test_bootstrap.py -q
```

Expected: all tests PASS.

- [ ] **Step 9: Commit Task 2**

```bash
git add api/channels/bootstrap.py test/unit_test/api/channels/test_bootstrap.py
git commit -m "功能：过滤企业微信 PDF 引用截图"
```

---

### Task 3: Add the WebSocket-Only WeCom Switch

**Files:**
- Modify: `web/src/pages/user-setting/chat-channel/constant/index.tsx:613-680`
- Create: `web/src/pages/user-setting/chat-channel/constant/index.test.tsx`

**Interfaces:**
- Produces form value: `config.credential.send_pdf_reference_images: boolean`.
- Consumes: `FormFieldType.Switch` and the existing `shouldRender(values)` convention.
- Visibility rule: only `connection_type === "websocket"`.

- [ ] **Step 1: Write the failing form-field configuration test**

Create `web/src/pages/user-setting/chat-channel/constant/index.test.tsx`:

```typescript
import { FormFieldType } from '@/components/dynamic-form';
import {
  ChatChannelKey,
  getChatChannelFields,
} from './index';

describe('WeCom channel fields', () => {
  const field = getChatChannelFields(ChatChannelKey.WECOM).find(
    (item) =>
      item.name === 'config.credential.send_pdf_reference_images',
  );

  test('defines a disabled-by-default PDF reference image switch', () => {
    expect(field).toBeDefined();
    expect(field?.type).toBe(FormFieldType.Switch);
    expect(field?.defaultValue).toBe(false);
  });

  test('shows the switch only for WebSocket connections', () => {
    expect(
      field?.shouldRender?.({
        config: { credential: { connection_type: 'websocket' } },
      }),
    ).toBe(true);
    expect(
      field?.shouldRender?.({
        config: { credential: { connection_type: 'webhook' } },
      }),
    ).toBe(false);
  });
});
```

- [ ] **Step 2: Run the Jest file and verify RED**

Run:

```bash
cd web && /home/qaadmin/.local/node-v22.12.0-linux-x64/bin/node \
  node_modules/jest/bin/jest.js \
  --runInBand \
  src/pages/user-setting/chat-channel/constant/index.test.tsx
```

Expected: FAIL because the field lookup returns `undefined`.

- [ ] **Step 3: Add the WeCom switch field**

Add this object to `ChatChannelFormFields[ChatChannelKey.WECOM]` immediately after the Bot ID field in `web/src/pages/user-setting/chat-channel/constant/index.tsx`:

```typescript
      {
        label: 'Send PDF reference screenshots',
        name: 'config.credential.send_pdf_reference_images',
        type: FormFieldType.Switch,
        required: false,
        defaultValue: false,
        tooltip:
          'When disabled, PDF page screenshots are not sent to WeCom. Non-PDF reference images and RAGFlow web references are unaffected.',
        shouldRender: (values: any) =>
          values?.config?.credential?.connection_type === 'websocket',
      },
```

The dynamic form already generates `false` defaults for switches and deep-merges saved records, so no second default assignment is required.

- [ ] **Step 4: Run the Jest file and verify GREEN**

Run:

```bash
cd web && /home/qaadmin/.local/node-v22.12.0-linux-x64/bin/node \
  node_modules/jest/bin/jest.js \
  --runInBand \
  src/pages/user-setting/chat-channel/constant/index.test.tsx
```

Expected: 2 tests PASS.

- [ ] **Step 5: Run frontend type checking**

Run:

```bash
cd web && /home/qaadmin/.local/node-v22.12.0-linux-x64/bin/node \
  node_modules/typescript/bin/tsc --noEmit
```

Expected: exit code 0 with no TypeScript errors.

- [ ] **Step 6: Commit Task 3**

```bash
git add \
  web/src/pages/user-setting/chat-channel/constant/index.tsx \
  web/src/pages/user-setting/chat-channel/constant/index.test.tsx
git commit -m "功能：增加企业微信 PDF 截图开关"
```

---

### Task 4: Full Regression Verification

**Files:**
- Verify only; no planned source changes.

**Interfaces:**
- Verifies the Task 1 policy contract, Task 2 delivery integration, and Task 3 UI field together.

- [ ] **Step 1: Run targeted backend tests**

```bash
uv run pytest \
  test/unit_test/api/channels/test_base.py \
  test/unit_test/api/channels/test_wecom_channel.py \
  test/unit_test/api/channels/test_bootstrap.py -q
```

Expected: all tests PASS with zero failures.

- [ ] **Step 2: Run targeted frontend tests**

```bash
cd web && /home/qaadmin/.local/node-v22.12.0-linux-x64/bin/node \
  node_modules/jest/bin/jest.js \
  --runInBand \
  src/pages/user-setting/chat-channel/constant/index.test.tsx
```

Expected: 2 tests PASS.

- [ ] **Step 3: Run lint/type checks for modified code**

```bash
uv run ruff check \
  api/channels/core/base.py \
  api/channels/wecom/channel.py \
  api/channels/bootstrap.py \
  test/unit_test/api/channels/test_base.py \
  test/unit_test/api/channels/test_wecom_channel.py \
  test/unit_test/api/channels/test_bootstrap.py

cd web && /home/qaadmin/.local/node-v22.12.0-linux-x64/bin/node \
  node_modules/typescript/bin/tsc --noEmit
```

Expected: both commands exit 0.

- [ ] **Step 4: Inspect final scope**

```bash
git status --short
git diff --check
git diff --stat HEAD~3..HEAD
```

Expected: no uncommitted implementation changes, no whitespace errors, and changes limited to the files listed in this plan.
