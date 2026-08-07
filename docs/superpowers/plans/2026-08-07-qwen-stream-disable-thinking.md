# Qwen Stream Disable-Thinking Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ensure LiteLLM streaming requests for Qwen3 models send `extra_body={"enable_thinking": false}`.

**Architecture:** Reuse `_apply_model_family_policies()` at the external LiteLLM streaming boundary. Merge only the policy-produced request arguments with the already-cleaned generation configuration so internal stream controls such as `with_reasoning` do not leak to the provider.

**Tech Stack:** Python 3.13, pytest, pytest-asyncio, LiteLLM, Ruff

## Global Constraints

- Change only the LiteLLM streaming request behavior and its focused regression test.
- Preserve behavior for non-Qwen models and all non-streaming requests.
- Do not change assistant configuration, retrieval, reranking, evidence selection, or UI behavior.
- Keep existing retry, timeout, exception, and stream parsing behavior unchanged.

---

## File Structure

- Create `test/unit_test/rag/test_chat_model_stream_policy.py`: regression test at the LiteLLM request boundary.
- Modify `rag/llm/chat_model.py`: apply existing model-family request policies before constructing a streaming completion.

The new test intentionally lives above `test/unit_test/rag/llm/` because that directory's existing local `conftest.py` currently stubs `rag.llm` without symbols required by `chat_model.py`, causing collection failure unrelated to this fix.

### Task 1: Forward Qwen Disable-Thinking Policy in Streaming Requests

**Files:**
- Create: `test/unit_test/rag/test_chat_model_stream_policy.py`
- Modify: `rag/llm/chat_model.py:120-122`
- Modify: `rag/llm/chat_model.py:1548-1559`

**Interfaces:**
- Consumes: `_apply_model_family_policies(model_name, backend="litellm", provider=provider, request_kwargs={}) -> tuple[dict, dict]`
- Produces: `LiteLLMBase.async_chat_streamly()` requests whose `extra_body` is `{"enable_thinking": false}` when `self.model_name` contains `qwen3`.

- [ ] **Step 1: Write the failing request-boundary test**

Create `test/unit_test/rag/test_chat_model_stream_policy.py` with:

```python
from types import SimpleNamespace

import pytest

from rag.llm import SupportedLiteLLMProvider
from rag.llm.chat_model import LiteLLMBase


class _ConcreteLiteLLM(LiteLLMBase):
    pass


def _make_qwen_model():
    model = _ConcreteLiteLLM.__new__(_ConcreteLiteLLM)
    model.model_name = "dashscope/qwen3.5-flash"
    model.provider = SupportedLiteLLMProvider.Tongyi_Qianwen
    model.api_key = "test-key"
    model.base_url = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    model.max_retries = 0
    model.timeout = 1
    return model


@pytest.mark.asyncio
async def test_qwen_stream_disables_thinking_in_provider_request(monkeypatch):
    captured_request = {}

    async def empty_stream():
        if False:
            yield SimpleNamespace()

    async def fake_acompletion(**kwargs):
        captured_request.update(kwargs)
        return empty_stream()

    monkeypatch.setattr("rag.llm.chat_model.litellm.acompletion", fake_acompletion)

    chunks = [
        chunk
        async for chunk in _make_qwen_model().async_chat_streamly(
            None,
            [{"role": "user", "content": "hello"}],
            {"temperature": 0.2},
        )
    ]

    assert chunks == [0]
    assert captured_request["extra_body"] == {"enable_thinking": False}
```

The production mutation this test catches is removal or omission of the Qwen3 model-family policy at the streaming provider boundary. The external LiteLLM call is mocked because it is a network dependency; request construction and stream handling remain real.

- [ ] **Step 2: Run the test and verify RED**

Run:

```bash
uv run pytest test/unit_test/rag/test_chat_model_stream_policy.py -q
```

Expected: one failure at `captured_request["extra_body"]` because the current streaming request does not contain that key.

- [ ] **Step 3: Apply the minimal production change**

In `_apply_model_family_policies()`, update the Qwen3 comment to cover chat requests generally:

```python
# Qwen3 family disables thinking by extra_body on chat requests.
```

In `LiteLLMBase.async_chat_streamly()`, immediately after `_clean_conf()` apply the existing policy with an empty request-argument mapping, then merge the resulting policy arguments into request construction:

```python
gen_conf = self._clean_conf(gen_conf)
_, policy_kwargs = _apply_model_family_policies(
    self.model_name,
    backend="litellm",
    provider=self.provider,
    request_kwargs={},
)
reasoning_start = False
total_tokens = 0

completion_args = self._construct_completion_args(
    history=history,
    stream=True,
    tools=False,
    **{**gen_conf, **policy_kwargs},
)
```

Keep the existing `stop = kwargs.get("stop")` handling unchanged. Do not forward `kwargs` wholesale because `with_reasoning` is an internal stream control and must not reach LiteLLM.

- [ ] **Step 4: Run the focused test and verify GREEN**

Run:

```bash
uv run pytest test/unit_test/rag/test_chat_model_stream_policy.py -q
```

Expected: `1 passed` with exit code 0.

- [ ] **Step 5: Run relevant regression tests**

Run:

```bash
uv run pytest test/unit_test/rag/test_chat_model_stream_policy.py test/unit_test/api/db/services/test_dialog_service_final_answer.py -q
```

Expected: all collected tests pass with exit code 0.

Also record, but do not repair as part of this task, the pre-existing collection failure from:

```bash
uv run pytest test/unit_test/rag/llm/test_clean_conf_whitelist.py -q
```

Expected existing blocker: `ImportError` because the local `rag/llm/conftest.py` stub omits `FACTORY_DEFAULT_BASE_URL`.

- [ ] **Step 6: Run formatting and lint checks**

Run:

```bash
uv run ruff check rag/llm/chat_model.py test/unit_test/rag/test_chat_model_stream_policy.py
uv run ruff format --check rag/llm/chat_model.py test/unit_test/rag/test_chat_model_stream_policy.py
git diff --check
```

Expected: every command exits 0 with no lint, formatting, or whitespace errors.

- [ ] **Step 7: Review scope and commit**

Run:

```bash
git diff -- rag/llm/chat_model.py test/unit_test/rag/test_chat_model_stream_policy.py
git status --short
git add rag/llm/chat_model.py test/unit_test/rag/test_chat_model_stream_policy.py
PATH=/home/qaadmin/.local/node-v22.12.0-linux-x64/bin:$PATH git commit -m "fix: disable qwen thinking for streamed chat"
```

Expected: the diff contains only the streaming policy forwarding, the comment correction, and the focused regression test. Existing unrelated changes in `rag/nlp/search.py` and `test/unit_test/rag/test_search_pagination.py` remain unstaged.
