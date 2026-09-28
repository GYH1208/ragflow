#
#  Copyright 2026 The InfiniFlow Authors. All Rights Reserved.
#
#  Licensed under the Apache License, Version 2.0 (the "License");
#  you may not use this file except in compliance with the License.
#  You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
#  Unless required by applicable law or agreed to in writing, software
#  distributed under the License is distributed on an "AS IS" BASIS,
#  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#  See the License for the specific language governing permissions and
#  limitations under the License.
#

import importlib.util
import json
from pathlib import Path

import httpx
import pytest


def _load_mcp_server():
    server_path = Path(__file__).resolve().parents[3] / "mcp" / "server" / "server.py"
    spec = importlib.util.spec_from_file_location("ragflow_mcp_server_retrieval_unit", server_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def mcp_server():
    return _load_mcp_server()


def _response(status_code, payload, *, api_key="ragflow-rk-retrieval-secret"):
    request = httpx.Request(
        "POST",
        "http://ragflow.test/api/v1/retrieval",
        headers={"Authorization": f"Bearer {api_key}", "X-API-Key": api_key},
        json={"question": "Where is the handbook?", "api_key": api_key},
    )
    if isinstance(payload, (bytes, str)):
        return httpx.Response(
            status_code,
            content=payload,
            headers={"Content-Type": "text/html", "X-Request-ID": "request-2"},
            request=request,
        )
    return httpx.Response(
        status_code,
        json=payload,
        headers={"Content-Type": "application/json", "X-Request-ID": "request-2"},
        request=request,
    )


def _raised_text(exc_info):
    content = exc_info.value.args[0]
    assert len(content) == 1
    return content[0].text


@pytest.mark.parametrize(
    ("status_code", "message"),
    [
        (401, "API key is invalid, disabled, or expired."),
        (403, "API key does not allow access to the requested dataset."),
        (429, "Retrieval API rate limit exceeded."),
        (503, "Retrieval quota service is unavailable."),
    ],
)
@pytest.mark.asyncio
async def test_ragflow_retrieval_surfaces_safe_backend_error_without_credentials(
    monkeypatch,
    mcp_server,
    status_code,
    message,
):
    connector = mcp_server.RAGFlowConnector(base_url=mcp_server.BASE_URL)
    api_key = "ragflow-rk-retrieval-secret"
    response = _response(
        status_code,
        {
            "code": status_code,
            "message": message,
            "data": None,
            "debug": {"authorization": f"Bearer {api_key}"},
        },
        api_key=api_key,
    )

    async def _post(_path, json=None, api_key="", **_kwargs):
        return response

    monkeypatch.setattr(connector, "_post", _post)

    with pytest.raises(Exception) as exc_info:
        await mcp_server.call_tool.__wrapped__(
            "ragflow_retrieval",
            {"question": "Where is the handbook?", "dataset_ids": ["dataset-1"]},
            connector=connector,
            api_key=api_key,
        )

    error_text = _raised_text(exc_info)
    assert error_text == message
    assert api_key not in error_text
    assert "Authorization" not in error_text


@pytest.mark.parametrize(
    "payload",
    [
        {"code": 503, "message": ["ragflow-rk-retrieval-secret"], "data": None},
        "<html>ragflow-rk-retrieval-secret</html>",
        b"\x00ragflow-rk-retrieval-secret\xff",
    ],
)
@pytest.mark.asyncio
async def test_ragflow_retrieval_uses_generic_error_for_non_string_or_non_json_response(
    monkeypatch,
    mcp_server,
    payload,
):
    connector = mcp_server.RAGFlowConnector(base_url=mcp_server.BASE_URL)
    api_key = "ragflow-rk-retrieval-secret"
    response = _response(503, payload, api_key=api_key)

    async def _post(_path, json=None, api_key="", **_kwargs):
        return response

    monkeypatch.setattr(connector, "_post", _post)

    with pytest.raises(Exception) as exc_info:
        await mcp_server.call_tool.__wrapped__(
            "ragflow_retrieval",
            {"question": "Where is the handbook?", "dataset_ids": ["dataset-1"]},
            connector=connector,
            api_key=api_key,
        )

    error_text = _raised_text(exc_info)
    assert error_text == "Cannot process this operation."
    assert api_key not in error_text


@pytest.mark.parametrize(
    ("message", "secret"),
    [
        ("Authorization: Bearer ragflow-rk-message-secret", "ragflow-rk-message-secret"),
        ("Upstream rejected Bearer ragflow-rk-message-secret", "ragflow-rk-message-secret"),
        ("Credential ragflow-rk-message-secret is invalid", "ragflow-rk-message-secret"),
        ("X-API-Key: x-header-secret", "x-header-secret"),
        ("API-Key: hyphen-header-secret", "hyphen-header-secret"),
        ("Api-Key = mixed-case-secret", "mixed-case-secret"),
        ("api_key: underscore-secret", "underscore-secret"),
        ("API key = spaced-header-secret", "spaced-header-secret"),
    ],
)
@pytest.mark.asyncio
async def test_ragflow_retrieval_rejects_credential_bearing_messages_without_logging_them(
    monkeypatch,
    mcp_server,
    caplog,
    message,
    secret,
):
    connector = mcp_server.RAGFlowConnector(base_url=mcp_server.BASE_URL)
    api_key = "ragflow-rk-retrieval-request-secret"
    response = _response(
        403,
        {"code": 403, "message": message, "data": None},
        api_key=api_key,
    )

    async def _post(_path, json=None, api_key="", **_kwargs):
        return response

    monkeypatch.setattr(connector, "_post", _post)

    with pytest.raises(Exception) as exc_info:
        await mcp_server.call_tool.__wrapped__(
            "ragflow_retrieval",
            {"question": "Where is the handbook?", "dataset_ids": ["dataset-1"]},
            connector=connector,
            api_key=api_key,
        )

    error_text = _raised_text(exc_info)
    assert error_text == "Cannot process this operation."
    assert secret not in error_text
    assert api_key not in error_text
    assert secret not in caplog.text
    assert message not in caplog.text
    assert api_key not in caplog.text


@pytest.mark.parametrize(
    ("payload", "raw_error_fragment"),
    [
        ("{not-json", "Expecting property name"),
        ([{"code": 0, "data": {}}], "list"),
        (17, "int"),
        ({"message": "missing code", "data": {}}, "missing code"),
        ({"code": 0, "message": "success", "data": []}, "data"),
    ],
)
@pytest.mark.asyncio
async def test_ragflow_retrieval_uses_generic_error_for_malformed_http_200_envelope(
    monkeypatch,
    mcp_server,
    payload,
    raw_error_fragment,
):
    connector = mcp_server.RAGFlowConnector(base_url=mcp_server.BASE_URL)
    response = _response(200, payload)

    async def _post(_path, json=None, api_key="", **_kwargs):
        return response

    monkeypatch.setattr(connector, "_post", _post)

    with pytest.raises(Exception) as exc_info:
        await mcp_server.call_tool.__wrapped__(
            "ragflow_retrieval",
            {"question": "Where is the handbook?", "dataset_ids": ["dataset-1"]},
            connector=connector,
            api_key="ragflow-rk-retrieval-request-secret",
        )

    error_text = _raised_text(exc_info)
    assert error_text == "Cannot process this operation."
    assert raw_error_fragment not in error_text


@pytest.mark.asyncio
async def test_ragflow_retrieval_surfaces_safe_nonzero_http_200_envelope(monkeypatch, mcp_server):
    connector = mcp_server.RAGFlowConnector(base_url=mcp_server.BASE_URL)
    message = "Retrieval API rate limit exceeded."
    response = _response(200, {"code": 429, "message": message, "data": None})

    async def _post(_path, json=None, api_key="", **_kwargs):
        return response

    monkeypatch.setattr(connector, "_post", _post)

    with pytest.raises(Exception) as exc_info:
        await mcp_server.call_tool.__wrapped__(
            "ragflow_retrieval",
            {"question": "Where is the handbook?", "dataset_ids": ["dataset-1"]},
            connector=connector,
            api_key="ragflow-rk-retrieval-request-secret",
        )

    assert _raised_text(exc_info) == message


@pytest.mark.asyncio
async def test_ragflow_retrieval_success_preserves_pagination_and_document_metadata(monkeypatch, mcp_server):
    connector = mcp_server.RAGFlowConnector(base_url=mcp_server.BASE_URL)
    retrieval_response = _response(
        200,
        {
            "code": 0,
            "message": "success",
            "data": {
                "chunks": [
                    {
                        "id": "chunk-1",
                        "content": "The handbook is on the intranet.",
                        "dataset_id": "dataset-1",
                        "document_id": "document-1",
                        "document_keyword": "Employee handbook",
                        "similarity": 0.91,
                        "vector_similarity": 0.88,
                        "term_similarity": 0.95,
                        "positions": [[1, 2, 3, 4, 5]],
                    }
                ],
                "doc_aggs": [{"doc_id": "document-1", "doc_name": "Employee handbook", "count": 1}],
                "page": 2,
                "page_size": 5,
                "total": 6,
            },
        },
    )

    async def _post(_path, json=None, api_key="", **_kwargs):
        return retrieval_response

    async def _metadata(_dataset_ids, *, api_key, force_refresh=False):
        return (
            {
                "document-1": {
                    "document_id": "document-1",
                    "name": "Employee handbook.pdf",
                    "location": "hr/Employee handbook.pdf",
                    "type": "pdf",
                    "size": 1024,
                    "chunk_count": 12,
                    "create_date": "2026-09-01 10:00:00",
                    "update_date": "2026-09-02 10:00:00",
                    "token_count": 4096,
                    "thumbnail": "thumbnail-id",
                    "dataset_id": "dataset-1",
                    "meta_fields": {"department": "HR"},
                }
            },
            {"dataset-1": {"name": "Policies", "description": "Company policies"}},
        )

    monkeypatch.setattr(connector, "_post", _post)
    monkeypatch.setattr(connector, "_get_document_metadata_cache", _metadata)

    content = await mcp_server.call_tool.__wrapped__(
        "ragflow_retrieval",
        {
            "question": "Where is the handbook?",
            "dataset_ids": ["dataset-1"],
            "page": 2,
            "page_size": 5,
        },
        connector=connector,
        api_key="unit-key",
    )

    result = json.loads(content[0].text)
    assert result["pagination"] == {"page": 2, "page_size": 5, "total_chunks": 6, "total_pages": 2}
    assert {
        field: result["chunks"][0][field]
        for field in (
            "id",
            "content",
            "dataset_id",
            "document_id",
            "document_keyword",
            "similarity",
            "vector_similarity",
            "term_similarity",
            "positions",
        )
    } == {
        "id": "chunk-1",
        "content": "The handbook is on the intranet.",
        "dataset_id": "dataset-1",
        "document_id": "document-1",
        "document_keyword": "Employee handbook",
        "similarity": 0.91,
        "vector_similarity": 0.88,
        "term_similarity": 0.95,
        "positions": [[1, 2, 3, 4, 5]],
    }
    assert result["chunks"][0]["dataset_name"] == "Policies"
    assert result["chunks"][0]["document_name"] == "Employee handbook"
    assert result["chunks"][0]["document_metadata"] == {
        "document_id": "document-1",
        "name": "Employee handbook.pdf",
        "location": "hr/Employee handbook.pdf",
        "type": "pdf",
        "size": 1024,
        "chunk_count": 12,
        "create_date": "2026-09-01 10:00:00",
        "update_date": "2026-09-02 10:00:00",
        "token_count": 4096,
        "thumbnail": "thumbnail-id",
        "dataset_id": "dataset-1",
        "meta_fields": {"department": "HR"},
    }
