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

import asyncio
import importlib.util
import json
from pathlib import Path

import httpx
import pytest


def _load_mcp_server():
    server_path = Path(__file__).resolve().parents[3] / "mcp" / "server" / "server.py"
    spec = importlib.util.spec_from_file_location("ragflow_mcp_server_unit", server_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _FakeResponse:
    status_code = 200

    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


def _datasets(count):
    return [{"id": f"dataset-{idx}", "description": f"description-{idx}"} for idx in range(count)]


def _error_response(status_code, payload, *, api_key="ragflow-rk-dataset-secret"):
    request = httpx.Request(
        "GET",
        "http://ragflow.test/api/v1/datasets",
        headers={"Authorization": f"Bearer {api_key}", "X-API-Key": api_key},
    )
    if isinstance(payload, (bytes, str)):
        return httpx.Response(
            status_code,
            content=payload,
            headers={"Content-Type": "text/html", "X-Request-ID": "request-1"},
            request=request,
        )
    return httpx.Response(
        status_code,
        json=payload,
        headers={"Content-Type": "application/json", "X-Request-ID": "request-1"},
        request=request,
    )


def _raised_text(exc_info):
    content = exc_info.value.args[0]
    assert len(content) == 1
    return content[0].text


@pytest.fixture()
def mcp_server():
    return _load_mcp_server()


def _stub_dataset_pages(monkeypatch, connector, datasets):
    requests = []

    async def _get(path, params=None, api_key=""):
        requests.append({"path": path, "params": dict(params), "api_key": api_key})
        page = params["page"]
        page_size = params["page_size"]
        start = (page - 1) * page_size
        end = start + page_size
        return _FakeResponse({"code": 0, "data": datasets[start:end], "total": len(datasets)})

    monkeypatch.setattr(connector, "_get", _get)
    return requests


@pytest.mark.asyncio
async def test_list_datasets_default_fetches_all_with_rest_page_size_limit(monkeypatch, mcp_server):
    connector = mcp_server.RAGFlowConnector(base_url=mcp_server.BASE_URL)
    requests = _stub_dataset_pages(monkeypatch, connector, _datasets(250))

    result = await connector.list_datasets(api_key="unit-key")

    rows = [json.loads(line) for line in result.splitlines()]
    assert [row["id"] for row in rows] == [f"dataset-{idx}" for idx in range(250)]
    assert [request["params"]["page"] for request in requests] == [1, 2, 3]
    assert all(request["path"] == "/datasets" for request in requests)
    assert all(request["params"]["page_size"] == 100 for request in requests)


@pytest.mark.asyncio
async def test_resolve_dataset_ids_fetches_all_pages_and_deduplicates(monkeypatch, mcp_server):
    connector = mcp_server.RAGFlowConnector(base_url=mcp_server.BASE_URL)
    datasets = _datasets(101) + [{"id": "dataset-100", "description": "duplicate"}]
    requests = _stub_dataset_pages(monkeypatch, connector, datasets)

    result = await connector.resolve_dataset_ids(api_key="unit-key")

    assert result == [f"dataset-{idx}" for idx in range(101)]
    assert [request["params"]["page"] for request in requests] == [1, 2]
    assert all(request["params"]["page_size"] == 100 for request in requests)


@pytest.mark.asyncio
async def test_resolve_dataset_ids_stops_at_api_total_datasets(monkeypatch, mcp_server):
    connector = mcp_server.RAGFlowConnector(base_url=mcp_server.BASE_URL)
    requested_pages = []

    async def _get(_path, params=None, api_key=""):
        requested_pages.append(params["page"])
        if params["page"] != 1:
            raise AssertionError("the reported dataset total should stop pagination")
        return _FakeResponse(
            {
                "code": 0,
                "data": [{"id": "dataset-1", "description": "Policies"}],
                "total_datasets": 1,
            }
        )

    monkeypatch.setattr(connector, "_get", _get)

    result = await connector.resolve_dataset_ids(api_key="unit-key")

    assert result == ["dataset-1"]
    assert requested_pages == [1]


@pytest.mark.asyncio
async def test_resolve_dataset_ids_caches_per_api_key_until_ttl(monkeypatch, mcp_server):
    connector = mcp_server.RAGFlowConnector(base_url=mcp_server.BASE_URL)
    now = [100.0]
    fetches = []

    async def _fetch_all_datasets(*, api_key, **_kwargs):
        fetches.append(api_key)
        suffix = api_key.rsplit("-", 1)[-1]
        return [{"id": f"dataset-{suffix}", "description": "Policies"}]

    monkeypatch.setattr(mcp_server.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(connector, "_fetch_all_datasets", _fetch_all_datasets)

    assert await connector.resolve_dataset_ids(api_key="unit-key-a") == ["dataset-a"]
    assert await connector.resolve_dataset_ids(api_key="unit-key-a") == ["dataset-a"]
    assert await connector.resolve_dataset_ids(api_key="unit-key-b") == ["dataset-b"]

    now[0] += 61
    assert await connector.resolve_dataset_ids(api_key="unit-key-a") == ["dataset-a"]

    assert fetches == ["unit-key-a", "unit-key-b", "unit-key-a"]


@pytest.mark.asyncio
async def test_resolve_dataset_ids_coalesces_concurrent_cache_misses(monkeypatch, mcp_server):
    connector = mcp_server.RAGFlowConnector(base_url=mcp_server.BASE_URL)
    fetch_started = asyncio.Event()
    release_fetch = asyncio.Event()
    fetch_count = 0

    async def _fetch_all_datasets(*, api_key, **_kwargs):
        nonlocal fetch_count
        assert api_key == "unit-key"
        fetch_count += 1
        fetch_started.set()
        await release_fetch.wait()
        return [{"id": "dataset-1", "description": "Policies"}]

    monkeypatch.setattr(connector, "_fetch_all_datasets", _fetch_all_datasets)

    tasks = [asyncio.create_task(connector.resolve_dataset_ids(api_key="unit-key")) for _ in range(8)]
    await fetch_started.wait()
    await asyncio.sleep(0)
    release_fetch.set()

    results = await asyncio.gather(*tasks)

    assert results == [["dataset-1"]] * 8
    assert fetch_count == 1


@pytest.mark.asyncio
async def test_resolve_dataset_ids_does_not_cache_failed_fetch(monkeypatch, mcp_server):
    connector = mcp_server.RAGFlowConnector(base_url=mcp_server.BASE_URL)
    fetch_count = 0

    async def _fetch_all_datasets(*, api_key, **_kwargs):
        nonlocal fetch_count
        assert api_key == "unit-key"
        fetch_count += 1
        if fetch_count == 1:
            raise RuntimeError("temporary backend failure")
        return [{"id": "dataset-1", "description": "Policies"}]

    monkeypatch.setattr(connector, "_fetch_all_datasets", _fetch_all_datasets)

    with pytest.raises(RuntimeError, match="temporary backend failure"):
        await connector.resolve_dataset_ids(api_key="unit-key")

    assert await connector.resolve_dataset_ids(api_key="unit-key") == ["dataset-1"]
    assert fetch_count == 2


@pytest.mark.asyncio
async def test_forbidden_backend_response_invalidates_cached_dataset_ids(monkeypatch, mcp_server):
    connector = mcp_server.RAGFlowConnector(base_url=mcp_server.BASE_URL)
    fetch_count = 0

    async def _fetch_all_datasets(*, api_key, **_kwargs):
        nonlocal fetch_count
        assert api_key == "unit-key"
        fetch_count += 1
        return [{"id": f"dataset-{fetch_count}", "description": "Policies"}]

    class _ForbiddenClient:
        async def get(self, **_kwargs):
            response = _FakeResponse({"code": 403, "message": "Forbidden"})
            response.status_code = 403
            return response

    monkeypatch.setattr(connector, "_fetch_all_datasets", _fetch_all_datasets)

    assert await connector.resolve_dataset_ids(api_key="unit-key") == ["dataset-1"]
    connector._async_client = _ForbiddenClient()
    response = await connector._get("/forbidden", api_key="unit-key")
    assert response.status_code == 403

    assert await connector.resolve_dataset_ids(api_key="unit-key") == ["dataset-2"]
    assert fetch_count == 2


@pytest.mark.asyncio
async def test_invalidation_does_not_allow_inflight_result_to_repopulate_cache(monkeypatch, mcp_server):
    connector = mcp_server.RAGFlowConnector(base_url=mcp_server.BASE_URL)
    fetch_started = asyncio.Event()
    release_stale_fetch = asyncio.Event()
    fetch_count = 0

    async def _fetch_all_datasets(*, api_key, **_kwargs):
        nonlocal fetch_count
        assert api_key == "unit-key"
        fetch_count += 1
        if fetch_count == 1:
            fetch_started.set()
            await release_stale_fetch.wait()
            return [{"id": "stale", "description": "Old permissions"}]
        return [{"id": "fresh", "description": "Current permissions"}]

    monkeypatch.setattr(connector, "_fetch_all_datasets", _fetch_all_datasets)

    stale_resolution = asyncio.create_task(connector.resolve_dataset_ids(api_key="unit-key"))
    await fetch_started.wait()
    connector._invalidate_cached_dataset_ids("unit-key")
    release_stale_fetch.set()

    assert await stale_resolution == ["stale"]
    assert await connector.resolve_dataset_ids(api_key="unit-key") == ["fresh"]
    assert fetch_count == 2


@pytest.mark.asyncio
async def test_repeated_invalidation_keeps_all_older_inflight_results_stale(monkeypatch, mcp_server):
    connector = mcp_server.RAGFlowConnector(base_url=mcp_server.BASE_URL)
    fetch_started = [asyncio.Event(), asyncio.Event()]
    release_fetch = [asyncio.Event(), asyncio.Event()]
    fetch_count = 0

    async def _fetch_all_datasets(**_kwargs):
        nonlocal fetch_count
        fetch_index = fetch_count
        fetch_count += 1
        if fetch_index < 2:
            fetch_started[fetch_index].set()
            await release_fetch[fetch_index].wait()
            return [{"id": f"stale-{fetch_index}", "description": "Old permissions"}]
        return [{"id": "fresh", "description": "Current permissions"}]

    monkeypatch.setattr(connector, "_fetch_all_datasets", _fetch_all_datasets)

    first_resolution = asyncio.create_task(connector.resolve_dataset_ids(api_key="unit-key"))
    await fetch_started[0].wait()
    connector._invalidate_cached_dataset_ids("unit-key")
    second_resolution = asyncio.create_task(connector.resolve_dataset_ids(api_key="unit-key"))
    await fetch_started[1].wait()
    connector._invalidate_cached_dataset_ids("unit-key")

    release_fetch[1].set()
    assert await second_resolution == ["stale-1"]
    release_fetch[0].set()
    assert await first_resolution == ["stale-0"]

    assert await connector.resolve_dataset_ids(api_key="unit-key") == ["fresh"]
    assert fetch_count == 3


@pytest.mark.asyncio
async def test_close_cancels_and_awaits_inflight_dataset_resolution(monkeypatch, mcp_server):
    connector = mcp_server.RAGFlowConnector(base_url=mcp_server.BASE_URL)
    fetch_started = asyncio.Event()
    fetch_cancelled = asyncio.Event()

    async def _fetch_all_datasets(**_kwargs):
        fetch_started.set()
        try:
            await asyncio.Event().wait()
        finally:
            fetch_cancelled.set()

    monkeypatch.setattr(connector, "_fetch_all_datasets", _fetch_all_datasets)

    resolution = asyncio.create_task(connector.resolve_dataset_ids(api_key="unit-key"))
    await fetch_started.wait()
    await connector.close()

    with pytest.raises(asyncio.CancelledError):
        await resolution
    assert fetch_cancelled.is_set()
    assert connector._dataset_id_inflight == {}


@pytest.mark.asyncio
async def test_list_datasets_clamps_explicit_page_size_to_rest_limit_and_preserves_filters(monkeypatch, mcp_server):
    connector = mcp_server.RAGFlowConnector(base_url=mcp_server.BASE_URL)
    requests = _stub_dataset_pages(monkeypatch, connector, _datasets(150))

    result = await connector.list_datasets(
        api_key="unit-key",
        page=1,
        page_size=1000,
        orderby="name",
        desc=False,
        id="dataset-1",
        name="target",
    )

    assert len(result.splitlines()) == 100
    assert len(requests) == 1
    assert requests[0]["params"] == {
        "page": 1,
        "page_size": 100,
        "orderby": "name",
        "desc": False,
        "id": "dataset-1",
        "name": "target",
    }


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
async def test_list_datasets_surfaces_safe_backend_error_without_credentials(
    monkeypatch,
    mcp_server,
    status_code,
    message,
):
    connector = mcp_server.RAGFlowConnector(base_url=mcp_server.BASE_URL)
    api_key = "ragflow-rk-dataset-secret"
    response = _error_response(
        status_code,
        {
            "code": status_code,
            "message": message,
            "data": None,
            "debug": {"authorization": f"Bearer {api_key}"},
        },
        api_key=api_key,
    )

    async def _get(_path, _params=None, api_key=""):
        return response

    monkeypatch.setattr(connector, "_get", _get)

    with pytest.raises(Exception) as exc_info:
        await connector.list_datasets(api_key=api_key)

    error_text = _raised_text(exc_info)
    assert error_text == message
    assert api_key not in error_text
    assert "Authorization" not in error_text


@pytest.mark.parametrize(
    "payload",
    [
        {"code": 401, "message": {"token": "ragflow-rk-dataset-secret"}, "data": None},
        "<html>ragflow-rk-dataset-secret</html>",
        b"\x00ragflow-rk-dataset-secret\xff",
    ],
)
@pytest.mark.asyncio
async def test_list_datasets_uses_generic_error_for_non_string_or_non_json_response(
    monkeypatch,
    mcp_server,
    payload,
):
    connector = mcp_server.RAGFlowConnector(base_url=mcp_server.BASE_URL)
    api_key = "ragflow-rk-dataset-secret"
    response = _error_response(401, payload, api_key=api_key)

    async def _get(_path, _params=None, api_key=""):
        return response

    monkeypatch.setattr(connector, "_get", _get)

    with pytest.raises(Exception) as exc_info:
        await connector.list_datasets(api_key=api_key)

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
async def test_dataset_auto_resolution_rejects_credential_bearing_messages_without_logging_them(
    monkeypatch,
    mcp_server,
    caplog,
    message,
    secret,
):
    connector = mcp_server.RAGFlowConnector(base_url=mcp_server.BASE_URL)
    api_key = "ragflow-rk-dataset-request-secret"
    response = _error_response(
        401,
        {"code": 401, "message": message, "data": None},
        api_key=api_key,
    )

    async def _get(_path, _params=None, api_key=""):
        return response

    monkeypatch.setattr(connector, "_get", _get)

    with pytest.raises(Exception) as exc_info:
        await connector.resolve_dataset_ids(api_key=api_key)

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
        ([{"code": 0, "data": []}], "list"),
        (17, "int"),
        ({"message": "missing code", "data": []}, "missing code"),
        ({"code": 0, "message": "success", "data": {}}, "data"),
    ],
)
@pytest.mark.asyncio
async def test_list_datasets_uses_generic_error_for_malformed_http_200_envelope(
    monkeypatch,
    mcp_server,
    payload,
    raw_error_fragment,
):
    connector = mcp_server.RAGFlowConnector(base_url=mcp_server.BASE_URL)
    response = _error_response(200, payload)

    async def _get(_path, _params=None, api_key=""):
        return response

    monkeypatch.setattr(connector, "_get", _get)

    with pytest.raises(Exception) as exc_info:
        await connector.list_datasets(api_key="ragflow-rk-dataset-request-secret")

    error_text = _raised_text(exc_info)
    assert error_text == "Cannot process this operation."
    assert raw_error_fragment not in error_text


@pytest.mark.asyncio
async def test_list_datasets_surfaces_safe_nonzero_http_200_envelope(monkeypatch, mcp_server):
    connector = mcp_server.RAGFlowConnector(base_url=mcp_server.BASE_URL)
    message = "API key does not allow access to the requested dataset."
    response = _error_response(200, {"code": 403, "message": message, "data": None})

    async def _get(_path, _params=None, api_key=""):
        return response

    monkeypatch.setattr(connector, "_get", _get)

    with pytest.raises(Exception) as exc_info:
        await connector.list_datasets(api_key="ragflow-rk-dataset-request-secret")

    assert _raised_text(exc_info) == message
