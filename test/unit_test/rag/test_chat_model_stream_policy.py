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


def _make_deepseek_model():
    model = _ConcreteLiteLLM.__new__(_ConcreteLiteLLM)
    model.model_name = "deepseek/deepseek-v4-flash"
    model.provider = SupportedLiteLLMProvider.DeepSeek
    model.api_key = "test-key"
    model.base_url = "https://api.deepseek.com/v1"
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


@pytest.mark.asyncio
async def test_deepseek_v4_stream_maps_disabled_thinking_to_extra_body(
    monkeypatch,
):
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
        async for chunk in _make_deepseek_model().async_chat_streamly(
            None,
            [{"role": "user", "content": "hello"}],
            {
                "temperature": 0.2,
                "_disable_thinking": True,
                "extra_body": {"custom": "value"},
            },
        )
    ]

    assert chunks == [0]
    assert captured_request["extra_body"] == {
        "custom": "value",
        "thinking": {"type": "disabled"},
    }
    assert "_disable_thinking" not in captured_request
