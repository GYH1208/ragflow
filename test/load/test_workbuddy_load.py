"""Offline checks for the production-facing load driver's limits and reporting."""

import asyncio
import importlib
import json
import time
from itertools import pairwise
from pathlib import Path

import httpx
import pytest


@pytest.fixture
def driver():
    assert Path(__file__).with_name("workbuddy_load.py").is_file(), "Load driver has not been implemented"
    return importlib.import_module("test.load.workbuddy_load")


def secure_json(path, value):
    path.write_text(json.dumps(value))
    path.chmod(0o600)
    return path


def workload():
    return [{"tool": "search_documents", "arguments": {"query": "软件", "dataset_ids": ["a" * 32]}, "min_results": 1}]


@pytest.mark.parametrize("change", [{"users": (25,)}, {"users": (3, 1)}, {"rps": 3}, {"rps": float("nan")}, {"duration": 121}, {"max_requests": 1001}])
def test_settings_reject_unbounded_or_invalid_load(driver, change):
    with pytest.raises(ValueError):
        driver.Settings(**change)


def test_tokens_require_private_regular_file_and_never_echo_credentials(driver, tmp_path):
    path = secure_json(tmp_path / "tickets.json", ["private-ticket"])
    assert driver.load_tokens(path) == ["private-ticket"]
    path.chmod(0o644)
    with pytest.raises(ValueError, match="private"):
        driver.load_tokens(path)
    path.chmod(0o600)
    link = tmp_path / "link.json"
    link.symlink_to(path)
    with pytest.raises(ValueError):
        driver.load_tokens(link)
    secure_json(path, ["private-ticket", "private-ticket"])
    with pytest.raises(ValueError) as error:
        driver.load_tokens(path)
    assert "private-ticket" not in str(error.value)


@pytest.mark.parametrize("arguments", [{"query": "软件"}, {"query": "软件", "dataset_ids": ["a" * 32], "api_key": "secret"}, {"query": "软件", "dataset_ids": ["REPLACE_DATASET_ID"]}])
def test_workload_requires_explicit_scope_and_rejects_credentials_or_placeholders(driver, tmp_path, arguments):
    path = secure_json(tmp_path / "workload.json", [{"tool": "search_documents", "arguments": arguments}])
    with pytest.raises(ValueError):
        driver.load_workload(path)


@pytest.mark.parametrize(
    "result,reason",
    [
        ({"isError": True, "content": [{"type": "text", "text": "sensitive server error"}]}, "tool_error"),
        ({"content": [{"type": "text", "text": '{"documents": []}'}]}, "empty_result"),
        ({"content": [{"type": "text", "text": "not JSON"}]}, "invalid_tool_result"),
    ],
)
def test_http_200_is_not_automatically_success(driver, result, reason):
    response = httpx.Response(200, json={"jsonrpc": "2.0", "id": 7, "result": result})
    with pytest.raises(driver.Failure) as error:
        driver.validate_response(response, 7, "search_documents", 1)
    assert error.value.reason == reason
    assert "sensitive" not in str(error.value)


def test_rate_limit_is_global_across_concurrent_workers(driver):
    async def run():
        limiter = driver.RateLimiter(20)
        stop = asyncio.Event()
        times = []

        async def request():
            assert await limiter.acquire(time.monotonic() + 2, stop)
            times.append(time.monotonic())

        await asyncio.gather(*(request() for _ in range(4)))
        assert min(b - a for a, b in pairwise(times)) >= 0.045

    asyncio.run(run())


def test_rate_limit_wait_can_be_interrupted_without_sending(driver):
    async def run():
        limiter = driver.RateLimiter(0.1)
        stop = asyncio.Event()
        assert await limiter.acquire(time.monotonic() + 20, stop)
        task = asyncio.create_task(limiter.acquire(time.monotonic() + 20, stop))
        await asyncio.sleep(0.01)
        stop.set()
        assert await asyncio.wait_for(task, 0.2) is False

    asyncio.run(run())


class HealthySampler:
    async def sample(self):
        return {"cpu_percent": 2, "available_gib": 18, "services_alive": True, "driver_rss_mib": 30, "service_processes": {}}


def test_driver_stops_on_business_error_and_report_contains_no_ticket_or_content(driver, tmp_path):
    async def run():
        calls = []

        async def server(request):
            body = json.loads(request.content)
            calls.append(body["method"])
            if body["method"] == "notifications/initialized":
                return httpx.Response(202)
            result = {"protocolVersion": "2025-06-18", "capabilities": {}, "serverInfo": {"name": "fake", "version": "1"}}
            if body["method"] == "tools/list":
                result = {"tools": [{"name": "search_documents", "inputSchema": {"type": "object"}}]}
            if body["method"] == "tools/call":
                result = {"isError": True, "content": [{"type": "text", "text": "private document private-ticket"}]}
            return httpx.Response(200, json={"jsonrpc": "2.0", "id": body["id"], "result": result})

        steps = driver.load_workload(secure_json(tmp_path / "workload.json", workload()))
        report = driver.Report(tmp_path / "report")
        settings = driver.Settings(duration=3, rps=2)
        async with httpx.AsyncClient(transport=httpx.MockTransport(server)) as client:
            summary = await driver.run_load(settings, ["private-ticket"], steps, client, HealthySampler(), report)
        assert summary["stop_reason"] == "tool_error"
        assert calls == ["initialize", "notifications/initialized", "tools/list", "tools/call"]
        assert summary["requests_started"] == 4
        assert summary["operations"]["search_documents"]["failed"] == 1
        saved = "".join(path.read_text() for path in (tmp_path / "report").iterdir())
        assert "private-ticket" not in saved
        assert "private document" not in saved
        assert (tmp_path / "report" / "summary.json").stat().st_mode & 0o777 == 0o600

    asyncio.run(run())


def test_resource_guard_stops_before_any_network_request(driver, tmp_path):
    class UnhealthySampler(HealthySampler):
        async def sample(self):
            return {**await super().sample(), "available_gib": 1}

    async def run():
        async def forbidden(request):
            pytest.fail("Resource guard must stop before sending requests")

        steps = driver.load_workload(secure_json(tmp_path / "workload.json", workload()))
        async with httpx.AsyncClient(transport=httpx.MockTransport(forbidden)) as client:
            summary = await driver.run_load(driver.Settings(), ["private-ticket"], steps, client, UnhealthySampler(), driver.Report(tmp_path / "report"))
        assert summary["stop_reason"] == "low_memory"
        assert summary["requests_started"] == 0

    asyncio.run(run())


def test_default_cli_is_offline_and_does_not_require_a_ticket(driver, tmp_path, capsys):
    path = secure_json(tmp_path / "workload.json", workload())
    assert driver.main(["--workload", str(path)]) == 0
    output = capsys.readouterr().out
    assert "dry_run" in output
    assert "network_requests" in output
    assert not (tmp_path / "report").exists()


def test_request_budget_includes_handshake_and_stops_without_extra_request(driver, tmp_path):
    async def run():
        count = 0

        async def server(request):
            nonlocal count
            count += 1
            body = json.loads(request.content)
            if body["method"] == "notifications/initialized":
                return httpx.Response(202)
            return httpx.Response(200, json={"jsonrpc": "2.0", "id": body["id"], "result": {"protocolVersion": "2025-06-18"}})

        steps = driver.load_workload(secure_json(tmp_path / "workload.json", workload()))
        async with httpx.AsyncClient(transport=httpx.MockTransport(server)) as client:
            summary = await driver.run_load(driver.Settings(duration=3, rps=2, max_requests=2), ["private-ticket"], steps, client, HealthySampler(), driver.Report(tmp_path / "report"))
        assert count == 2
        assert summary["requests_started"] == 2
        assert summary["stop_reason"] == "request_limit"

    asyncio.run(run())


def test_successful_stage_reports_hand_checked_tool_latency_samples(driver, tmp_path):
    async def run():
        async def server(request):
            body = json.loads(request.content)
            if body["method"] == "notifications/initialized":
                return httpx.Response(202)
            result = {"protocolVersion": "2025-06-18"}
            if body["method"] == "tools/list":
                result = {"tools": [{"name": "search_documents"}]}
            elif body["method"] == "tools/call":
                result = {"content": [{"type": "text", "text": '{"documents": [{"document_id": "doc-1"}]}'}]}
            return httpx.Response(200, json={"jsonrpc": "2.0", "id": body["id"], "result": result})

        steps = driver.load_workload(secure_json(tmp_path / "workload.json", workload()))
        async with httpx.AsyncClient(transport=httpx.MockTransport(server)) as client:
            summary = await driver.run_load(driver.Settings(duration=1.8, rps=2), ["ticket"], steps, client, HealthySampler(), driver.Report(tmp_path / "report"))
        assert summary["stop_reason"] == "completed"
        assert summary["stages_completed"] == [1]
        assert summary["stages"][0]["workers_with_tool_samples"] == 1
        assert summary["operations"]["search_documents"]["total"] == 1
        assert summary["operations"]["search_documents"]["failed"] == 0

    asyncio.run(run())


def test_incomplete_workload_is_not_reported_as_a_successful_stage(driver, tmp_path):
    async def run():
        async def server(request):
            body = json.loads(request.content)
            if body["method"] == "notifications/initialized":
                return httpx.Response(202)
            result = {"protocolVersion": "2025-06-18"}
            if body["method"] == "tools/list":
                result = {"tools": [{"name": "search_documents"}, {"name": "ragflow_retrieval"}]}
            elif body["method"] == "tools/call":
                result = {"content": [{"type": "text", "text": '{"documents": [{"document_id": "doc-1"}]}'}]}
            return httpx.Response(200, json={"jsonrpc": "2.0", "id": body["id"], "result": result})

        raw = workload() + [{"tool": "ragflow_retrieval", "arguments": {"question": "软件", "dataset_ids": ["a" * 32]}}]
        steps = driver.load_workload(secure_json(tmp_path / "workload.json", raw))
        async with httpx.AsyncClient(transport=httpx.MockTransport(server)) as client:
            summary = await driver.run_load(driver.Settings(duration=1.8, rps=2), ["ticket"], steps, client, HealthySampler(), driver.Report(tmp_path / "report"))
        assert summary["stop_reason"] == "incomplete_stage"
        assert summary["stages_completed"] == []

    asyncio.run(run())


@pytest.mark.parametrize(
    "result,expected",
    [
        ({"services_alive": False}, "service_exited"),
        ({"cpu_percent": 90}, "high_cpu"),
    ],
)
def test_resource_guards_stop_after_service_exit_or_three_high_cpu_samples(driver, tmp_path, result, expected):
    class Sampler(HealthySampler):
        async def sample(self):
            return {**await super().sample(), **result}

    async def run():
        runner = driver.Runner(driver.Settings(), ["ticket"], [], None, Sampler(), driver.Report(tmp_path / "report"))
        await runner.check_resources()
        if expected == "high_cpu":
            assert not runner.stop.is_set()
            await runner.check_resources()
            assert not runner.stop.is_set()
            await runner.check_resources()
        assert runner.reason == expected
        assert runner.stop.is_set()

    asyncio.run(run())


def test_monitor_stop_cancels_and_records_inflight_request(driver, tmp_path):
    class Sampler(HealthySampler):
        samples = 0

        async def sample(self):
            self.samples += 1
            return {**await super().sample(), "services_alive": self.samples < 2}

    async def run():
        async def server(request):
            await asyncio.sleep(30)
            pytest.fail("Request should have been cancelled by the monitor")

        steps = driver.load_workload(secure_json(tmp_path / "workload.json", workload()))
        report = driver.Report(tmp_path / "report")
        async with httpx.AsyncClient(transport=httpx.MockTransport(server)) as client:
            summary = await asyncio.wait_for(driver.run_load(driver.Settings(duration=10), ["ticket"], steps, client, Sampler(), report), 4)
        assert summary["stop_reason"] == "service_exited"
        assert summary["requests_started"] == 1
        assert len(report.events) == 1
        assert report.events[0]["error"] == "cancelled"

    asyncio.run(run())


def test_response_identity_mismatch_and_http_rate_limit_are_failures(driver):
    with pytest.raises(driver.Failure, match="invalid_rpc_response"):
        driver.validate_response(httpx.Response(200, json={"jsonrpc": "2.0", "id": 99, "result": {}}), 7)
    with pytest.raises(driver.Failure, match="rate_limited"):
        driver.validate_response(httpx.Response(429, text="private diagnostic"), 7)


def test_percentiles_are_success_only_and_match_known_values(driver):
    values = [{"operation": "search_documents", "elapsed_ms": x, "success": True} for x in (10, 20, 30, 40, 50)]
    values += [{"operation": "search_documents", "elapsed_ms": 999, "success": False}]
    summary = driver.summarize(values)["search_documents"]
    assert summary == {"total": 6, "failed": 1, "success_latency_samples": 5, "p50_ms": 30, "p95_ms": 50, "p99_ms": 50}


def test_probe_initializes_and_calls_actual_mcp_app_without_external_io(driver, tmp_path, monkeypatch):
    from test.unit_test.mcp.test_server_streamable_http import _client_for, _load_mcp_server

    server = _load_mcp_server()
    server.AUTH_MODE = "workbuddy"
    server.MODE = server.LaunchMode.HOST
    server.TRANSPORT_SSE_ENABLED = False
    server.TRANSPORT_STREAMABLE_HTTP_ENABLED = True
    server.JSON_RESPONSE = True

    async def search_documents(connector, *, api_key, **arguments):
        assert api_key == "offline-ticket"
        assert arguments["dataset_ids"] == ["a" * 32]
        return [server.types.TextContent(type="text", text='{"documents": [{"document_id": "doc-1"}]}')]

    monkeypatch.setattr(server.RAGFlowConnector, "search_documents", search_documents)

    async def run():
        steps = driver.load_workload(secure_json(tmp_path / "workload.json", workload()))
        async with _client_for(server) as client:
            summary = await driver.run_load(driver.Settings(duration=1.8, rps=2), ["offline-ticket"], steps, client, HealthySampler(), driver.Report(tmp_path / "report"))
        assert summary["stop_reason"] == "completed"
        assert summary["operations"]["search_documents"]["total"] == 1
        assert summary["operations"]["search_documents"]["failed"] == 0

    asyncio.run(run())


@pytest.mark.parametrize("status,reason", [(401, "authentication_failed"), (403, "access_denied"), (429, "rate_limited"), (503, "http_error")])
def test_http_error_headers_stop_load_without_waiting_for_response_body(driver, tmp_path, status, reason):
    class StalledBody(httpx.AsyncByteStream):
        consumed = False

        async def __aiter__(self):
            self.consumed = True
            await asyncio.sleep(30)
            yield b"private upstream diagnostic"

    async def run():
        body = StalledBody()

        async def server(request):
            return httpx.Response(status, stream=body)

        steps = driver.load_workload(secure_json(tmp_path / "workload.json", workload()))
        async with httpx.AsyncClient(transport=httpx.MockTransport(server)) as client:
            summary = await asyncio.wait_for(driver.run_load(driver.Settings(), ["ticket"], steps, client, HealthySampler(), driver.Report(tmp_path / "report")), 0.3)
        assert summary["stop_reason"] == reason
        assert summary["requests_started"] == 1
        assert body.consumed is False

    asyncio.run(run())
