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

"""Verify image tools using the real HTTP connector and MCP content models."""

import base64
import importlib.util
import json
from io import BytesIO
from pathlib import Path

import httpx
import pytest
from PIL import Image


@pytest.fixture()
def mcp_server():
    path = Path(__file__).resolve().parents[3] / "mcp" / "server" / "server.py"
    spec = importlib.util.spec_from_file_location("ragflow_mcp_chunk_images_unit", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _image(format="PNG"):
    buffer = BytesIO()
    Image.new("RGB", (2, 2), "white").save(buffer, format=format)
    return buffer.getvalue()


@pytest.fixture()
async def image_connector(mcp_server):
    state = {"status": 200, "body": _image(), "content_type": "image/png", "requests": [], "exception": None}

    def backend(request):
        state["requests"].append(request)
        if state["exception"]:
            raise state["exception"]
        return httpx.Response(state["status"], content=state["body"], headers={"Content-Type": state["content_type"]})

    connector = mcp_server.RAGFlowConnector(base_url="http://ragflow.test")
    connector._async_client = httpx.AsyncClient(transport=httpx.MockTransport(backend))
    yield connector, state
    await connector.close()


async def _call_image(module, connector, *, api_key="ragflow-rk-image-secret", arguments=None):
    return await module.call_tool.__wrapped__(
        "get_chunk_image",
        arguments if arguments is not None else {"dataset_id": "dataset-1", "document_id": "document-1", "chunk_id": "chunk-1"},
        connector=connector,
        api_key=api_key,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("format,mime_type", [("PNG", "image/png"), ("JPEG", "image/jpeg"), ("GIF", "image/gif"), ("WEBP", "image/webp"), ("BMP", "image/bmp")])
async def test_chunk_image_returns_binary_image_content_and_source(mcp_server, image_connector, format, mime_type):
    connector, state = image_connector
    state.update(body=_image(format), content_type=mime_type)
    content = await _call_image(mcp_server, connector)
    assert len(state["requests"]) == 1
    request = state["requests"][0]
    assert str(request.url) == "http://ragflow.test/api/v1/datasets/dataset-1/documents/document-1/chunks/chunk-1/image"
    assert request.headers["Authorization"] == "Bearer ragflow-rk-image-secret"
    assert [item.type for item in content] == ["text", "image"]
    assert json.loads(content[0].text) == {"dataset_id": "dataset-1", "document_id": "document-1", "chunk_id": "chunk-1"}
    assert content[1].mimeType == mime_type
    assert base64.b64decode(content[1].data, validate=True) == state["body"]
    assert "ragflow-rk-image-secret" not in content[0].text


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [401, 403, 404, 413, 415, 503])
async def test_chunk_image_surfaces_safe_backend_errors(mcp_server, image_connector, status):
    connector, state = image_connector
    state.update(status=status, body=json.dumps({"code": status, "message": "Chunk image unavailable."}).encode(), content_type="application/json")
    connector._set_cached_dataset_ids(connector._dataset_id_cache_key("ragflow-rk-image-secret"), ["dataset-1"])
    with pytest.raises(Exception) as error:
        await _call_image(mcp_server, connector)
    assert str(error.value) == "Chunk image unavailable."
    if status in (401, 403):
        assert connector._get_cached_dataset_ids(connector._dataset_id_cache_key("ragflow-rk-image-secret")) is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        b"<html>private response</html>",
        b'{"message":"Authorization: Bearer ragflow-rk-image-secret"}',
        b'{"message":"private-custom-secret"}',
        b'{"message":["private data"]}',
        b'{"message":"Failed to load https://alice:s3cr3t@storage.example/private.png"}',
        b'{"message":"Failed to load https://storage.example/private.png?X-Amz-Signature=s3cr3t"}',
    ],
)
async def test_chunk_image_does_not_expose_malformed_or_sensitive_error(mcp_server, image_connector, payload, caplog):
    connector, state = image_connector
    state.update(status=503, body=payload)
    with pytest.raises(Exception) as error:
        await _call_image(mcp_server, connector, api_key="private-custom-secret")
    assert str(error.value) == "Cannot retrieve this chunk image."
    assert "private-custom-secret" not in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "content_type,body", [("application/json", b'{"code":102}'), ("text/html", b"<html>error</html>"), ("image/png", b"not an image"), ("image/png", b""), ("image/svg+xml", b"<svg></svg>")]
)
async def test_chunk_image_rejects_non_image_success_response(mcp_server, image_connector, content_type, body):
    connector, state = image_connector
    state.update(content_type=content_type, body=body)
    with pytest.raises(Exception) as error:
        await _call_image(mcp_server, connector)
    assert str(error.value) == "Cannot retrieve this chunk image."


@pytest.mark.asyncio
async def test_chunk_image_enforces_actual_byte_limit(mcp_server, image_connector, monkeypatch):
    connector, state = image_connector
    monkeypatch.setattr(mcp_server, "MCP_IMAGE_MAX_BYTES", len(state["body"]), raising=False)
    assert (await _call_image(mcp_server, connector))[1].type == "image"
    state["body"] += b"x"
    with pytest.raises(Exception) as error:
        await _call_image(mcp_server, connector)
    assert str(error.value) == "Chunk image exceeds the MCP size limit."


@pytest.mark.asyncio
@pytest.mark.parametrize("field,value", [("dataset_id", "../documents"), ("document_id", "doc/other"), ("chunk_id", "chunk%2Fother"), ("chunk_id", None), ("document_id", 1), ("dataset_id", "")])
async def test_chunk_image_validates_ids_before_backend_request(mcp_server, image_connector, field, value):
    connector, state = image_connector
    arguments = {"dataset_id": "dataset-1", "document_id": "document-1", "chunk_id": "chunk-1", field: value}
    with pytest.raises(Exception) as error:
        await _call_image(mcp_server, connector, arguments=arguments)
    assert str(error.value) == "Valid dataset_id, document_id, and chunk_id are required."
    assert state["requests"] == []


@pytest.mark.asyncio
async def test_chunk_image_requires_key_without_backend_request(mcp_server, image_connector):
    connector, state = image_connector
    with pytest.raises(Exception) as error:
        await _call_image(mcp_server, connector, api_key="")
    assert str(error.value) == "RAGFlow API key is required."
    assert state["requests"] == []


@pytest.mark.asyncio
async def test_chunk_image_transport_error_is_safe(mcp_server, image_connector, caplog):
    connector, state = image_connector
    state["exception"] = httpx.ReadTimeout("private-custom-secret")
    with pytest.raises(Exception) as error:
        await _call_image(mcp_server, connector, api_key="private-custom-secret")
    assert str(error.value) == "Cannot retrieve this chunk image."
    assert "private-custom-secret" not in caplog.text
