import importlib.util
from pathlib import Path

import pytest


@pytest.fixture
def adapter():
    path = Path(__file__).resolve().parents[2] / "rag/llm/model_meta.py"
    spec = importlib.util.spec_from_file_location("deepseek_meta_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.DeepSeek


@pytest.mark.parametrize("provider_url, expected", [
    (None, "https://api.deepseek.com/models"),
    ("https://api.deepseek.com/", "https://api.deepseek.com/models"),
    ("https://api.deepseek.com/v1/", "https://api.deepseek.com/v1/models"),
])
def test_endpoint(adapter, provider_url, expected):
    assert adapter("key", provider_url)._get_model_list_url() == expected


def test_current_catalog_metadata(adapter):
    model = adapter("key")
    assert model._FACTORY_NAME == "DeepSeek"
    result = model._format_model_list({"data": [{
        "id": "deepseek-flash", "name": "DeepSeek-V4.1-Flash",
        "context_window": 1048576, "max_output_tokens": 393216,
        "input_modalities": ["text", "image"],
        "effort": {"supported_levels": ["low", "high", "max"]},
    }, {"id": "deepseek-v4-pro"}]})
    assert result[0]["name"] == "deepseek-flash"
    assert result[0]["max_tokens"] == 1048576
    assert result[0]["model_types"] == ["chat", "image2text"]
    assert "thinking" in result[0]["features"]
    assert result[1]["model_types"] == ["chat"]


@pytest.mark.parametrize("payload", [None, {}, {"data": None}, {"data": [None, {}, {"name": "display only"}]}])
def test_invalid_catalog_entries(adapter, payload):
    assert adapter("key")._format_model_list(payload) == []
