# WeCom PDF Reference Image Two-Level Switch Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a disabled-by-default chat-level PDF reference screenshot switch and require both that switch and the existing WeCom channel switch before sending PDF reference images.

**Architecture:** Store the chat-level value in `Dialog.prompt_config.send_pdf_reference_images` and keep the existing channel-level value in `config.credential.send_pdf_reference_images`. Pass the chat-level permission into the channel image policy at evidence-delivery time; WeCom applies the two-level AND rule only to PDF chunks, while the base channel and non-PDF behavior remain unchanged.

**Tech Stack:** Python 3.10+, Quart/Peewee backend, pytest, TypeScript, React 18, react-hook-form, Zod, Jest/Testing Library, i18next.

## Global Constraints

- Both switches default to `false`; a missing field is treated as `false`.
- Only the literal boolean `true` enables either switch; strings such as `"true"` do not.
- PDF screenshots are sent only when both the WeCom channel switch and current chat switch are `true`.
- Non-PDF reference images retain their current behavior regardless of the two PDF switches.
- Do not alter `reference.chunks`, `used_chunk_ids`, web/share citation rendering, PDF retrieval, textual citations, or source-file delivery.
- The restriction applies to WeCom WebSocket reference-image delivery; Webhook mode still does not support reference images.
- Do not add a database column, migration, dependency, knowledge-base policy, or document re-indexing step.
- Keep PDF detection compatible with `document_name` and legacy `docnm_kwd`, trimming whitespace and matching `.pdf` case-insensitively.
- Unknown source filenames retain the existing fail-closed behavior.
- Use `sendPdfReferenceImages` and `sendPdfReferenceImagesTip` for new i18n keys; English locale text uses sentence case.
- The current workspace has `web/node_modules` but no `node`, `npm`, or `npx` executable. Frontend commands and browser smoke testing must be marked environment-blocked unless a Node.js runtime is restored before execution; do not claim they passed without command output.
- Because the Husky pre-commit hook requires the missing `npx`, use `HUSKY=0` for the task commits only after the task's available explicit checks have passed.

---

## File Structure

- Modify `api/channels/core/base.py`: extend the channel image-policy interface with a dialog-level PDF permission argument while retaining default allow behavior for ordinary channels.
- Modify `api/channels/wecom/channel.py`: enforce the channel-level and dialog-level AND rule for PDF chunks and emit distinct reason logs.
- Modify `api/channels/bootstrap.py`: read the current dialog setting strictly and pass it to the channel policy before storage reads/uploads.
- Modify `api/apps/restful_apis/chat_api.py`: include the new field in both ordinary and direct-chat default prompt configurations.
- Modify `test/unit_test/api/channels/test_base.py`: lock down the public policy contract for other channels.
- Modify `test/unit_test/api/channels/test_wecom_channel.py`: cover the WeCom policy truth table, strict booleans, filenames, and reason logs.
- Modify `test/unit_test/api/channels/test_bootstrap.py`: cover end-to-end handler filtering, persistence preservation, and non-PDF behavior.
- Modify `test/testcases/restful_api/test_chats.py`: cover default persistence and create/update API round trips.
- Modify `web/src/interfaces/database/chat.ts`: expose the optional prompt-config field to frontend consumers.
- Create `web/src/pages/next-chats/chat/app-settings/prompt-config.ts`: normalize the optional persisted value to a strict form boolean.
- Modify `web/src/pages/next-chats/chat/app-settings/use-chat-setting-schema.tsx`: validate the field as an optional boolean.
- Modify `web/src/pages/next-chats/chat/app-settings/chat-settings.tsx`: default and normalize the field to `false` for old chats.
- Modify `web/src/pages/next-chats/chat/app-settings/chat-prompt-engine.tsx`: render the switch inside Advanced settings.
- Modify `web/src/pages/next-chats/hooks/use-create-chat.ts`: write `false` when creating a new chat.
- Create `web/src/pages/next-chats/chat/app-settings/chat-prompt-engine.test.tsx`: verify the field name and translated copy supplied to the switch component.
- Create `web/src/pages/next-chats/chat/app-settings/prompt-config.test.ts`: verify old, explicit, and invalid persisted values normalize safely.
- Create `web/src/pages/next-chats/chat/app-settings/use-chat-setting-schema.test.tsx`: verify boolean validation and missing-field compatibility.
- Modify `web/src/locales/en.ts` and `web/src/locales/zh.ts`: add the approved English and Simplified Chinese copy.

---

### Task 1: Extend the Channel Image Policy Contract

**Files:**
- Modify: `api/channels/core/base.py:62-64`
- Modify: `test/unit_test/api/channels/test_base.py:25-39`

**Interfaces:**
- Consumes: existing `Channel.allows_reference_image(chunk)` callers.
- Produces: `Channel.allows_reference_image(chunk: dict[str, Any], *, dialog_allows_pdf_images: bool = False) -> bool`.
- Guarantee: the base implementation returns `True` for PDF and non-PDF chunks, so channels other than WeCom are unchanged.

- [ ] **Step 1: Write the failing base-policy contract test**

Replace the existing base-policy test with an assertion that the new keyword argument is accepted without changing the default channel decision:

```python
def test_channel_allows_reference_images_regardless_of_dialog_pdf_setting():
    channel = RecordingChannel()
    chunk = {
        "image_id": "image-1",
        "document_name": "policy.pdf",
    }

    assert channel.allows_reference_image(
        chunk,
        dialog_allows_pdf_images=False,
    ) is True
    assert channel.allows_reference_image(
        chunk,
        dialog_allows_pdf_images=True,
    ) is True
```

- [ ] **Step 2: Run the focused test and verify it fails**

Run:

```bash
uv run pytest test/unit_test/api/channels/test_base.py::test_channel_allows_reference_images_regardless_of_dialog_pdf_setting -v
```

Expected: FAIL with `TypeError: Channel.allows_reference_image() got an unexpected keyword argument 'dialog_allows_pdf_images'`.

- [ ] **Step 3: Implement the backward-compatible base signature**

Change the base method to:

```python
def allows_reference_image(
    self,
    chunk: dict[str, Any],
    *,
    dialog_allows_pdf_images: bool = False,
) -> bool:
    """Return whether this channel may deliver an image from a reference chunk."""
    return True
```

Do not add PDF logic to the base class. The unused values are part of the polymorphic contract.

- [ ] **Step 4: Run the base channel test file**

Run:

```bash
uv run pytest test/unit_test/api/channels/test_base.py -v
```

Expected: all tests PASS.

- [ ] **Step 5: Commit the interface change**

```bash
git add api/channels/core/base.py test/unit_test/api/channels/test_base.py
HUSKY=0 git commit -m "refactor: pass dialog PDF permission to channel policy"
```

---

### Task 2: Enforce the Two-Level Rule in the WeCom Policy

**Files:**
- Modify: `api/channels/wecom/channel.py:172-184`
- Modify: `test/unit_test/api/channels/test_wecom_channel.py:34-121`

**Interfaces:**
- Consumes: `Channel.allows_reference_image(..., dialog_allows_pdf_images=False)` from Task 1 and existing `WeComAccount.send_pdf_reference_images: bool`.
- Produces: `WeComChannel.allows_reference_image(chunk, *, dialog_allows_pdf_images=False) -> bool` with the PDF truth table.
- Logs: `reason=channel_pdf_images_disabled` and `reason=dialog_pdf_images_disabled` when a known PDF is rejected by the corresponding level.

- [ ] **Step 1: Replace the one-level WeCom tests with a parameterized failing truth-table test**

Add this test and remove the superseded `test_wecom_allows_pdf_reference_images_when_enabled`, whose one-level expectation is no longer valid:

```python
@pytest.mark.parametrize(
    ("channel_enabled", "dialog_enabled", "expected"),
    [
        (False, False, False),
        (True, False, False),
        (False, True, False),
        (True, True, True),
    ],
)
def test_wecom_pdf_reference_images_require_both_switches(
    channel_enabled,
    dialog_enabled,
    expected,
):
    channel = make_channel(send_pdf_reference_images=channel_enabled)

    assert channel.allows_reference_image(
        {
            "image_id": "pdf-image",
            "document_name": " policy.PdF ",
        },
        dialog_allows_pdf_images=dialog_enabled,
    ) is expected
```

Keep or adapt the legacy filename test so `docnm_kwd="policy.pdf"` follows the same two-level rule. Update the non-PDF test to assert it remains allowed with both dialog values:

```python
@pytest.mark.parametrize("dialog_enabled", [False, True])
def test_wecom_allows_non_pdf_reference_images_regardless_of_switches(
    dialog_enabled,
):
    assert make_channel().allows_reference_image(
        {
            "image_id": "answer-image",
            "document_name": "faq.docx",
        },
        dialog_allows_pdf_images=dialog_enabled,
    ) is True
```

- [ ] **Step 2: Add failing diagnostic-log tests**

Add explicit checks for which level rejected a known PDF:

```python
def test_wecom_logs_channel_level_pdf_rejection(caplog):
    channel = make_channel(send_pdf_reference_images=False)

    with caplog.at_level(logging.INFO, logger="api.channels.wecom.channel"):
        allowed = channel.allows_reference_image(
            {"image_id": "pdf-image", "document_name": "policy.pdf"},
            dialog_allows_pdf_images=True,
        )

    assert allowed is False
    assert "reason=channel_pdf_images_disabled" in caplog.text


def test_wecom_logs_dialog_level_pdf_rejection(caplog):
    channel = make_channel(send_pdf_reference_images=True)

    with caplog.at_level(logging.INFO, logger="api.channels.wecom.channel"):
        allowed = channel.allows_reference_image(
            {"image_id": "pdf-image", "document_name": "policy.pdf"},
            dialog_allows_pdf_images=False,
        )

    assert allowed is False
    assert "reason=dialog_pdf_images_disabled" in caplog.text
```

Retain the existing `missing_document_name` warning test and builder tests, including rejection of the string `"true"` at channel level.

- [ ] **Step 3: Run the focused policy tests and verify they fail**

Run:

```bash
uv run pytest test/unit_test/api/channels/test_wecom_channel.py -k "reference_image or pdf_rejection or builder" -v
```

Expected: the new truth table or log tests FAIL because the WeCom override does not accept or enforce dialog permission.

- [ ] **Step 4: Implement strict two-level filtering for known PDFs**

Replace the WeCom override with logic equivalent to:

```python
def allows_reference_image(
    self,
    chunk: dict[str, Any],
    *,
    dialog_allows_pdf_images: bool = False,
) -> bool:
    filename = str(
        chunk.get("document_name") or chunk.get("docnm_kwd") or ""
    ).strip()
    if not filename:
        LOGGER.warning(
            "[wecom:%s] reference image skipped "
            "reason=missing_document_name image_id=%s",
            self.account_id,
            chunk.get("image_id") or chunk.get("img_id") or "",
        )
        return False

    if not filename.casefold().endswith(".pdf"):
        return True

    if self.account.send_pdf_reference_images is not True:
        LOGGER.info(
            "[wecom:%s] reference image skipped "
            "reason=channel_pdf_images_disabled image_id=%s",
            self.account_id,
            chunk.get("image_id") or chunk.get("img_id") or "",
        )
        return False

    if dialog_allows_pdf_images is not True:
        LOGGER.info(
            "[wecom:%s] reference image skipped "
            "reason=dialog_pdf_images_disabled image_id=%s",
            self.account_id,
            chunk.get("image_id") or chunk.get("img_id") or "",
        )
        return False

    return True
```

Checking the filename before either flag ensures non-PDF images are never blocked by PDF-specific settings.

- [ ] **Step 5: Run the complete WeCom channel tests**

Run:

```bash
uv run pytest test/unit_test/api/channels/test_wecom_channel.py -v
```

Expected: all tests PASS.

- [ ] **Step 6: Commit the WeCom policy**

```bash
git add api/channels/wecom/channel.py test/unit_test/api/channels/test_wecom_channel.py
HUSKY=0 git commit -m "feat: require both PDF image switches in WeCom"
```

---

### Task 3: Pass the Strict Chat Permission Through Evidence Delivery

**Files:**
- Modify: `api/channels/bootstrap.py:436-511`
- Modify: `test/unit_test/api/channels/test_bootstrap.py:11-27,361-469`

**Interfaces:**
- Consumes: `WeComChannel.allows_reference_image(chunk, *, dialog_allows_pdf_images=False)` from Task 2 and `dia.prompt_config`.
- Produces: a policy callable passed to `_images_for_used_chunks` that supplies `dialog_allows_pdf_images=(prompt_config.get("send_pdf_reference_images") is True)`.
- Guarantee: image filtering occurs after evidence persistence but before any `OutgoingMessage(images=...)`, object storage read, or WeCom upload.

- [ ] **Step 1: Make the handler test helper configurable for both levels**

Extend `_run_handler_case` with two keyword arguments:

```python
async def _run_handler_case(
    monkeypatch,
    *,
    chunks,
    resolution,
    question="用户问题",
    answer="回答正文。[ID:0]",
    text_send_result=True,
    persist_result=True,
    channel_pdf_images_enabled=True,
    dialog_pdf_images_enabled=True,
):
```

Put the dialog value into the fake prompt config exactly as supplied:

```python
"send_pdf_reference_images": dialog_pdf_images_enabled,
```

Capture policy calls and make the fake channel emulate strict channel behavior without duplicating the production filename parser:

```python
policy_calls = []

def allows_reference_image(
    self,
    chunk,
    *,
    dialog_allows_pdf_images=False,
):
    policy_calls.append((chunk["id"], dialog_allows_pdf_images))
    filename = str(chunk.get("document_name") or "")
    if filename.lower().endswith(".pdf"):
        return channel_pdf_images_enabled and dialog_allows_pdf_images
    return True
```

Return `policy_calls` with `events` and `sent_messages`:

```python
return events, sent_messages, policy_calls
```

Update the seven existing callers at the current lines 535, 589, 612, 625, 639, 652, and 700 to unpack and ignore a third result. For example, change `events, sent_messages = await _run_handler_case(...)` to `events, sent_messages, _ = await _run_handler_case(...)`, and change `_, sent_messages = ...` to `_, sent_messages, _ = ...`.

- [ ] **Step 2: Add failing handler-level truth-table and preservation tests**

Add a parameterized test using one PDF and one DOCX image:

```python
@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("channel_enabled", "dialog_enabled", "expected_images"),
    [
        (False, False, [OutgoingImage("docx-image")]),
        (True, False, [OutgoingImage("docx-image")]),
        (False, True, [OutgoingImage("docx-image")]),
        (
            True,
            True,
            [OutgoingImage("pdf-image"), OutgoingImage("docx-image")],
        ),
    ],
)
async def test_handler_applies_pdf_image_two_level_switches(
    monkeypatch,
    channel_enabled,
    dialog_enabled,
    expected_images,
):
    chunks = [
        {
            "id": "pdf",
            "content": "PDF evidence",
            "image_id": "pdf-image",
            "document_name": "policy.pdf",
        },
        {
            "id": "docx",
            "content": "DOCX evidence",
            "image_id": "docx-image",
            "document_name": "faq.docx",
        },
    ]
    resolution = EvidenceResolution(
        ["pdf", "docx"],
        [],
        [],
        "resolved",
        1.0,
    )

    events, sent_messages, policy_calls = await _run_handler_case(
        monkeypatch,
        chunks=chunks,
        resolution=resolution,
        channel_pdf_images_enabled=channel_enabled,
        dialog_pdf_images_enabled=dialog_enabled,
    )

    assert sent_messages[-1].images == expected_images
    assert policy_calls == [
        ("pdf", dialog_enabled is True),
        ("docx", dialog_enabled is True),
    ]
    assert (
        "persist",
        "conversation-1",
        "message-1",
        ["pdf", "docx"],
    ) in events
    assert chunks[0]["image_id"] == "pdf-image"
```

Use `dialog_pdf_images_enabled="true"` in an additional test and assert the captured permission is `False`, proving strict boolean handling:

```python
@pytest.mark.asyncio
async def test_handler_does_not_enable_pdf_images_for_string_dialog_value(
    monkeypatch,
):
    chunks = [{
        "id": "pdf",
        "content": "PDF evidence",
        "image_id": "pdf-image",
        "document_name": "policy.pdf",
    }]

    _, sent_messages, policy_calls = await _run_handler_case(
        monkeypatch,
        chunks=chunks,
        resolution=EvidenceResolution(
            ["pdf"], [], [], "resolved", 1.0
        ),
        dialog_pdf_images_enabled="true",
    )

    assert len(sent_messages) == 1
    assert policy_calls == [("pdf", False)]
```

- [ ] **Step 3: Run the focused handler tests and verify they fail**

Run:

```bash
uv run pytest test/unit_test/api/channels/test_bootstrap.py -k "two_level_switches or string_dialog_value" -v
```

Expected: FAIL because the bootstrap policy call does not provide the chat permission.

- [ ] **Step 4: Read and pass the strict dialog-level value**

Near the existing `quote_enabled` calculation, add:

```python
dialog_allows_pdf_images = (
    (dia.prompt_config or {}).get("send_pdf_reference_images") is True
)
```

Replace the direct bound-method callback with:

```python
evidence_images = _images_for_used_chunks(
    valid_chunks,
    resolution.used_chunk_ids,
    image_allowed=lambda chunk: ch.allows_reference_image(
        chunk,
        dialog_allows_pdf_images=dialog_allows_pdf_images,
    ),
)
```

Update every test fake `allows_reference_image` signature in this file to accept the keyword-only argument with default `False`, even where the test ignores it. Do not filter or mutate `valid_chunks` before `update_reference_evidence`.

- [ ] **Step 5: Run bootstrap and channel regression tests**

Run:

```bash
uv run pytest \
  test/unit_test/api/channels/test_base.py \
  test/unit_test/api/channels/test_wecom_channel.py \
  test/unit_test/api/channels/test_bootstrap.py \
  -v
```

Expected: all tests PASS, including existing send-order, source-file, and error-isolation tests.

- [ ] **Step 6: Commit the message-pipeline integration**

```bash
git add api/channels/bootstrap.py test/unit_test/api/channels/test_bootstrap.py
HUSKY=0 git commit -m "feat: apply chat PDF image setting to channel evidence"
```

---

### Task 4: Persist Safe Backend Defaults Through the Chat API

**Files:**
- Modify: `api/apps/restful_apis/chat_api.py:87-114`
- Modify: `test/testcases/restful_api/test_chats.py:1546-1640,1819-1930`

**Interfaces:**
- Consumes: existing nested `prompt_config` merge behavior in chat create/update endpoints.
- Produces: `prompt_config.send_pdf_reference_images: false` in default chat responses and preserves explicit boolean values through create/update.
- Guarantee: no database migration; values remain inside `Dialog.prompt_config` JSON.

- [ ] **Step 1: Add failing create-contract cases**

In `test_chat_create_prompt_contract`, extend the default expected map with:

```python
("prompt_config", "send_pdf_reference_images"): False,
```

Add explicit cases alongside `quote true/false`:

```python
(
    "send PDF reference images true",
    {"prompt_config": {"send_pdf_reference_images": True}},
    {("prompt_config", "send_pdf_reference_images"): True},
),
(
    "send PDF reference images false",
    {"prompt_config": {"send_pdf_reference_images": False}},
    {("prompt_config", "send_pdf_reference_images"): False},
),
```

In `test_chat_update_prompt_contract`, add the same explicit true/false cases so update and subsequent GET preserve the setting.

- [ ] **Step 2: Run the create contract and verify the default case fails**

Run:

```bash
uv run pytest test/testcases/restful_api/test_chats.py::test_chat_create_prompt_contract -v
```

Expected: FAIL because the default prompt config does not contain `send_pdf_reference_images`.

- [ ] **Step 3: Add the backend defaults**

Add this entry to both `_DEFAULT_PROMPT_CONFIG` and `_DEFAULT_DIRECT_CHAT_PROMPT_CONFIG`:

```python
"send_pdf_reference_images": False,
```

Place it next to `send_source_file` so outbound-channel settings remain grouped. Do not add validation that coerces strings to booleans; bootstrap already enables only the literal boolean `True`.

- [ ] **Step 4: Run create and update chat contracts**

Run:

```bash
uv run pytest \
  test/testcases/restful_api/test_chats.py::test_chat_create_prompt_contract \
  test/testcases/restful_api/test_chats.py::test_chat_update_prompt_contract \
  -v
```

Expected: both tests PASS.

- [ ] **Step 5: Commit backend prompt defaults**

```bash
git add api/apps/restful_apis/chat_api.py test/testcases/restful_api/test_chats.py
HUSKY=0 git commit -m "feat: persist chat PDF image preference"
```

---

### Task 5: Add Frontend Types, Defaults, Schema, and Approved Copy

**Files:**
- Modify: `web/src/interfaces/database/chat.ts:11-30`
- Create: `web/src/pages/next-chats/chat/app-settings/prompt-config.ts`
- Create: `web/src/pages/next-chats/chat/app-settings/prompt-config.test.ts`
- Modify: `web/src/pages/next-chats/chat/app-settings/use-chat-setting-schema.tsx:18-24`
- Modify: `web/src/pages/next-chats/chat/app-settings/chat-settings.tsx:44-64,119-140`
- Modify: `web/src/pages/next-chats/hooks/use-create-chat.ts:25-38`
- Modify: `web/src/locales/en.ts:1084-1091`
- Modify: `web/src/locales/zh.ts:983-990`
- Create: `web/src/pages/next-chats/chat/app-settings/use-chat-setting-schema.test.tsx`

**Interfaces:**
- Produces: `PromptConfig.send_pdf_reference_images?: boolean`.
- Produces: `normalizePromptConfigPdfImageSetting<T>(promptConfig: T)`, preserving all prompt-config keys and setting `send_pdf_reference_images` to a strict boolean.
- Produces: Zod field `send_pdf_reference_images: z.boolean().optional()`.
- Produces: default/normalized form value `prompt_config.send_pdf_reference_images === false`.
- Produces i18n keys `chat.sendPdfReferenceImages` and `chat.sendPdfReferenceImagesTip`.

- [ ] **Step 1: Add failing normalization tests for old and invalid persisted values**

Create `prompt-config.test.ts`:

```typescript
import { normalizePromptConfigPdfImageSetting } from './prompt-config';

describe('normalizePromptConfigPdfImageSetting', () => {
  test.each([
    [{ system: 'Prompt' }, false],
    [{ system: 'Prompt', send_pdf_reference_images: false }, false],
    [{ system: 'Prompt', send_pdf_reference_images: 'true' }, false],
    [{ system: 'Prompt', send_pdf_reference_images: true }, true],
  ])('normalizes %p to %p', (promptConfig, expected) => {
    expect(
      normalizePromptConfigPdfImageSetting(promptConfig)
        .send_pdf_reference_images,
    ).toBe(expected);
    expect(
      normalizePromptConfigPdfImageSetting(promptConfig).system,
    ).toBe('Prompt');
  });
});
```

- [ ] **Step 2: Run the normalization test and verify it fails**

Run:

```bash
cd web && npx jest src/pages/next-chats/chat/app-settings/prompt-config.test.ts --runInBand
```

Expected: FAIL because `prompt-config.ts` and its exported function do not exist.

- [ ] **Step 3: Implement the strict normalization helper**

Create `prompt-config.ts`:

```typescript
export function normalizePromptConfigPdfImageSetting<
  T extends Record<string, unknown>,
>(promptConfig: T) {
  return {
    ...promptConfig,
    send_pdf_reference_images:
      promptConfig.send_pdf_reference_images === true,
  };
}
```

- [ ] **Step 4: Add a failing schema test for boolean-only values and old-chat compatibility**

Create `use-chat-setting-schema.test.tsx` with the hook dependencies mocked and a complete valid payload factory:

```tsx
import { renderHook } from '@testing-library/react';

import { useChatSettingSchema } from './use-chat-setting-schema';

jest.mock('@/hooks/common-hooks', () => ({
  useTranslate: () => ({ t: (key: string) => key }),
}));

const validValues = {
  name: 'Assistant',
  icon: '',
  description: '',
  dataset_ids: [],
  prompt_config: {
    quote: true,
    keyword: false,
    tts: false,
    system: 'Use {knowledge}',
    refine_multiturn: true,
    use_kg: false,
  },
  llm_setting: {},
  top_n: 8,
  similarity_threshold: 0.2,
  vector_similarity_weight: 0.2,
  top_k: 1024,
  meta_data_filter: { method: 'disabled', manual: [] },
};

describe('chat setting PDF reference image schema', () => {
  it('accepts a missing field for old chats and boolean values', () => {
    const { result } = renderHook(() => useChatSettingSchema());

    expect(result.current.safeParse(validValues).success).toBe(true);
    expect(
      result.current.safeParse({
        ...validValues,
        prompt_config: {
          ...validValues.prompt_config,
          send_pdf_reference_images: true,
        },
      }).success,
    ).toBe(true);
  });

  it('rejects string values', () => {
    const { result } = renderHook(() => useChatSettingSchema());

    expect(
      result.current.safeParse({
        ...validValues,
        prompt_config: {
          ...validValues.prompt_config,
          send_pdf_reference_images: 'true',
        },
      }).success,
    ).toBe(false);
  });
});
```

If imported component schemas require heavy browser dependencies, mock those modules with their real exported schema shapes instead of weakening the tested `prompt_config` assertions.

- [ ] **Step 5: Run the schema test and verify the string case fails**

Run:

```bash
cd web && npx jest src/pages/next-chats/chat/app-settings/use-chat-setting-schema.test.tsx --runInBand
```

Expected: FAIL because Zod strips the unknown new key and accepts the containing object, so the string case incorrectly succeeds.

- [ ] **Step 6: Add the type and Zod schema field**

In `PromptConfig`, add:

```typescript
send_pdf_reference_images?: boolean;
```

In `promptConfigSchema`, add:

```typescript
send_pdf_reference_images: z.boolean().optional(),
```

- [ ] **Step 7: Add safe creation and edit-form defaults**

Add this entry beside `send_source_file` in both `ChatSettings.defaultValues.prompt_config` and `useCreateChatDialog.InitialData.prompt_config`:

```typescript
send_pdf_reference_images: false,
```

Import the helper in `chat-settings.tsx`:

```typescript
import { normalizePromptConfigPdfImageSetting } from './prompt-config';
```

When resetting `ChatSettings` from fetched data, normalize old records explicitly:

```typescript
const nextData = {
  ...data,
  prompt_config: normalizePromptConfigPdfImageSetting({
    ...(data.prompt_config || {}),
    reference_metadata: normalizedReferenceMetadata,
  }),
  ...llmSettingEnabledValues,
};
```

This preserves explicit `true` and converts missing or non-boolean legacy values to `false`.

- [ ] **Step 8: Add the approved English and Simplified Chinese copy**

Add beside `sendSourceFile` in `en.ts`:

```typescript
sendPdfReferenceImages: 'Send PDF reference screenshots to chat channels',
sendPdfReferenceImagesTip:
  'PDF reference screenshots are sent only when this chat assistant and the corresponding WeCom channel both enable this setting.',
```

Add beside `sendSourceFile` in `zh.ts`:

```typescript
sendPdfReferenceImages: '发送 PDF 引用截图到聊天渠道',
sendPdfReferenceImagesTip:
  '仅当当前聊天助手和对应企业微信渠道均开启此功能时，才会发送 PDF 引用截图。',
```

Do not add translations to unrelated locale files in this feature; i18next falls back to English where project locale coverage is incomplete.

- [ ] **Step 9: Run normalization/schema tests and TypeScript checking**

Run:

```bash
cd web && npx jest \
  src/pages/next-chats/chat/app-settings/prompt-config.test.ts \
  src/pages/next-chats/chat/app-settings/use-chat-setting-schema.test.tsx \
  --runInBand
cd web && npm run type-check
```

Expected: schema tests PASS and TypeScript reports no errors.

- [ ] **Step 10: Commit frontend data-contract changes**

```bash
git add \
  web/src/interfaces/database/chat.ts \
  web/src/pages/next-chats/chat/app-settings/prompt-config.ts \
  web/src/pages/next-chats/chat/app-settings/prompt-config.test.ts \
  web/src/pages/next-chats/chat/app-settings/use-chat-setting-schema.tsx \
  web/src/pages/next-chats/chat/app-settings/use-chat-setting-schema.test.tsx \
  web/src/pages/next-chats/chat/app-settings/chat-settings.tsx \
  web/src/pages/next-chats/hooks/use-create-chat.ts \
  web/src/locales/en.ts \
  web/src/locales/zh.ts
HUSKY=0 git commit -m "feat: add chat PDF image preference model"
```

---

### Task 6: Render the Chat Advanced-Settings Switch

**Files:**
- Modify: `web/src/pages/next-chats/chat/app-settings/chat-prompt-engine.tsx:115-130`
- Create: `web/src/pages/next-chats/chat/app-settings/chat-prompt-engine.test.tsx`

**Interfaces:**
- Consumes: `prompt_config.send_pdf_reference_images` and i18n keys from Task 5.
- Produces: one `SwitchFormField` within `ChatPromptEngine` bound to the exact prompt-config path.

- [ ] **Step 1: Add a failing component wiring test**

Create a focused test that mocks complex child fields and captures `SwitchFormField` props:

```tsx
import { render } from '@testing-library/react';
import React from 'react';
import { FormProvider, useForm } from 'react-hook-form';

import { ChatPromptEngine } from './chat-prompt-engine';

const renderedSwitches: Array<{
  name: string;
  label: unknown;
  tooltip: unknown;
}> = [];

jest.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}));
jest.mock('@/components/switch-fom-field', () => ({
  SwitchFormField: (props: {
    name: string;
    label: unknown;
    tooltip: unknown;
  }) => {
    renderedSwitches.push(props);
    return null;
  },
}));
jest.mock('@/components/collapse', () => ({
  Collapse: ({ children }: React.PropsWithChildren) => <>{children}</>,
}));
jest.mock('@/components/cross-language-form-field', () => ({
  CrossLanguageFormField: () => null,
}));
jest.mock('@/components/metadata-filter', () => ({
  MetadataFilter: () => null,
}));
jest.mock('@/components/rerank', () => ({
  RerankFormFields: () => null,
}));
jest.mock('@/components/similarity-slider', () => ({
  SimilaritySliderFormField: () => null,
}));
jest.mock('@/components/tavily-form-field', () => ({
  TavilyFormField: () => null,
}));
jest.mock('@/components/toc-enhance-form-field', () => ({
  TOCEnhanceFormField: () => null,
}));
jest.mock('@/components/top-n-item', () => ({
  TopNFormField: () => null,
}));
jest.mock('@/components/use-knowledge-graph-item', () => ({
  UseKnowledgeGraphFormField: () => null,
}));
jest.mock('@/hooks/use-knowledge-request', () => ({
  useFetchKnowledgeMetadataKeys: () => ({ data: [], loading: false }),
}));
jest.mock('./dynamic-variable', () => ({
  DynamicVariableForm: () => null,
}));
```

Render through this `FormProvider` harness, then assert:

```tsx
function Harness() {
  const form = useForm({
    defaultValues: {
      dataset_ids: [],
      prompt_config: {
        empty_response: '',
        system: '',
        reference_metadata: { include: false },
      },
    },
  });
  return (
    <FormProvider {...form}>
      <ChatPromptEngine />
    </FormProvider>
  );
}

describe('ChatPromptEngine PDF reference image switch', () => {
  beforeEach(() => {
    renderedSwitches.length = 0;
  });

  it('binds the approved copy to the prompt-config field', () => {
    render(<Harness />);

    expect(renderedSwitches).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          name: 'prompt_config.send_pdf_reference_images',
          label: 'chat.sendPdfReferenceImages',
          tooltip: 'chat.sendPdfReferenceImagesTip',
        }),
      ]),
    );
  });
});
```

- [ ] **Step 2: Run the component test and verify it fails**

Run:

```bash
cd web && npx jest src/pages/next-chats/chat/app-settings/chat-prompt-engine.test.tsx --runInBand
```

Expected: FAIL because the switch field is absent.

- [ ] **Step 3: Add the switch immediately after the existing source-file switch**

In `ChatPromptEngine`, add:

```tsx
<SwitchFormField
  name={prefixName(prefix, 'prompt_config.send_pdf_reference_images')}
  label={t('chat.sendPdfReferenceImages')}
  tooltip={t('chat.sendPdfReferenceImagesTip')}
></SwitchFormField>
```

Keeping it beside `send_source_file` groups the two outbound-channel controls inside the existing Advanced settings collapse.

- [ ] **Step 4: Run the focused frontend tests, type check, and lint changed files**

Run:

```bash
cd web && npx jest \
  src/pages/next-chats/chat/app-settings/chat-prompt-engine.test.tsx \
  src/pages/next-chats/chat/app-settings/prompt-config.test.ts \
  src/pages/next-chats/chat/app-settings/use-chat-setting-schema.test.tsx \
  src/pages/user-setting/chat-channel/constant/index.test.tsx \
  --runInBand
cd web && npm run type-check
cd web && npx eslint \
  src/interfaces/database/chat.ts \
  src/pages/next-chats/chat/app-settings/chat-prompt-engine.tsx \
  src/pages/next-chats/chat/app-settings/chat-prompt-engine.test.tsx \
  src/pages/next-chats/chat/app-settings/prompt-config.ts \
  src/pages/next-chats/chat/app-settings/prompt-config.test.ts \
  src/pages/next-chats/chat/app-settings/use-chat-setting-schema.tsx \
  src/pages/next-chats/chat/app-settings/use-chat-setting-schema.test.tsx \
  src/pages/next-chats/chat/app-settings/chat-settings.tsx \
  src/pages/next-chats/hooks/use-create-chat.ts \
  src/locales/en.ts \
  src/locales/zh.ts
```

Expected: all three Jest files PASS, type-check PASS, and ESLint reports no errors.

- [ ] **Step 5: Commit the advanced-settings UI**

```bash
git add \
  web/src/pages/next-chats/chat/app-settings/chat-prompt-engine.tsx \
  web/src/pages/next-chats/chat/app-settings/chat-prompt-engine.test.tsx
HUSKY=0 git commit -m "feat: expose PDF image switch in chat settings"
```

---

### Task 7: Full Verification and Manual Acceptance Check

**Files:**
- Verify only: all files changed in Tasks 1-6.

**Interfaces:**
- Consumes: completed backend two-level policy, chat defaults, frontend data contract, and advanced-settings UI.
- Produces: verified implementation with no additional feature scope.

- [ ] **Step 1: Run backend formatting and focused lint**

Run:

```bash
uv run ruff format --check \
  api/channels/core/base.py \
  api/channels/wecom/channel.py \
  api/channels/bootstrap.py \
  api/apps/restful_apis/chat_api.py \
  test/unit_test/api/channels/test_base.py \
  test/unit_test/api/channels/test_wecom_channel.py \
  test/unit_test/api/channels/test_bootstrap.py \
  test/testcases/restful_api/test_chats.py
uv run ruff check \
  api/channels/core/base.py \
  api/channels/wecom/channel.py \
  api/channels/bootstrap.py \
  api/apps/restful_apis/chat_api.py \
  test/unit_test/api/channels/test_base.py \
  test/unit_test/api/channels/test_wecom_channel.py \
  test/unit_test/api/channels/test_bootstrap.py \
  test/testcases/restful_api/test_chats.py
```

Expected: both commands PASS. If formatting is required, run `uv run ruff format` on exactly these files and rerun both checks.

- [ ] **Step 2: Run the complete affected backend suites**

Run:

```bash
uv run pytest \
  test/unit_test/api/channels/test_base.py \
  test/unit_test/api/channels/test_wecom_channel.py \
  test/unit_test/api/channels/test_bootstrap.py \
  test/testcases/restful_api/test_chats.py::test_chat_create_prompt_contract \
  test/testcases/restful_api/test_chats.py::test_chat_update_prompt_contract \
  -v
```

Expected: all tests PASS.

- [ ] **Step 3: Run the complete focused frontend verification**

Run:

```bash
cd web && npx jest \
  src/pages/next-chats/chat/app-settings/chat-prompt-engine.test.tsx \
  src/pages/next-chats/chat/app-settings/prompt-config.test.ts \
  src/pages/next-chats/chat/app-settings/use-chat-setting-schema.test.tsx \
  src/pages/user-setting/chat-channel/constant/index.test.tsx \
  --runInBand
cd web && npm run type-check
```

Expected: all Jest tests and the type check PASS.

- [ ] **Step 4: Inspect the final diff against the acceptance matrix**

Run:

```bash
git diff --check HEAD~6..HEAD
git diff --stat HEAD~6..HEAD
git status --short
```

Then verify from the diff:

- both switches must be literal `true` before a PDF image is allowed;
- non-PDF images bypass the PDF-specific switch checks;
- `update_reference_evidence` still runs before image filtering;
- source-file sending does not read either PDF screenshot switch;
- no migration, dependency, parser, retrieval, or web citation-rendering file changed;
- the working tree contains no unexpected files.

If the branch contains a different number of commits, replace `HEAD~6` with the design-document parent commit `a43f4771c` in both diff commands.

- [ ] **Step 5: Perform a browser smoke check when the frontend runtime is available**

Start the frontend with the normal project command:

```bash
cd web && npm run dev
```

Open one existing chat assistant and verify:

1. “高级设置” contains “发送 PDF 引用截图到聊天渠道”.
2. An old chat with no saved field displays the switch as off.
3. Turn it on, save, reopen settings, and confirm it remains on.
4. Turn it off, save, reopen settings, and confirm it remains off.
5. The existing WeCom channel-level “Send PDF reference screenshots” switch remains present under User settings → Chat channels → WeCom WebSocket configuration.

Stop the dev server after the check. If Node.js is unavailable in the execution environment, record this step as environment-blocked and rely on the Jest/type-check evidence rather than claiming a browser check passed.

- [ ] **Step 6: Create a verification-only commit only if checks changed files**

If Ruff or another required formatter modified tracked files, commit only those verified formatting changes:

```bash
git add api/channels/core/base.py api/channels/wecom/channel.py api/channels/bootstrap.py api/apps/restful_apis/chat_api.py test/unit_test/api/channels/test_base.py test/unit_test/api/channels/test_wecom_channel.py test/unit_test/api/channels/test_bootstrap.py test/testcases/restful_api/test_chats.py
HUSKY=0 git commit -m "style: format PDF image switch changes"
```

If verification changed no files, do not create an empty commit.
