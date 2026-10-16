"""Offline checks for the one-shot, employee-bound in-memory load entry."""

import asyncio
import hashlib
import importlib
import json
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest


@pytest.fixture
def entry():
    assert Path(__file__).with_name("workbuddy_load_entry.py").exists(), "Memory load entry is not implemented"
    return importlib.import_module("test.load.workbuddy_load_entry")


def controller(entry, tmp_path, *, owner="employee-1", ttl=1, execute=None, verify=None):
    from test.load.workbuddy_load import Report

    async def verified(token):
        return SimpleNamespace(enterprise_id="123", union_id=owner)

    async def done(tokens):
        return {"stop_reason": "completed"}

    return entry.MemoryLoadEntry(
        run_id="test-run",
        expected_union_hash=hashlib.sha256(b"employee-1").hexdigest(),
        enterprise_id="123",
        verify=verify or verified,
        execute=execute or done,
        report=Report(tmp_path / "report"),
        ttl=ttl,
    )


@pytest.mark.asyncio
async def test_ticket_is_passed_only_to_one_run_then_references_are_cleared(entry, tmp_path):
    held = []
    release = asyncio.Event()

    async def execute(tokens):
        assert tokens == ["personal-private-ticket"]
        held.append(tokens)
        await release.wait()
        return {"stop_reason": "completed"}

    control = controller(entry, tmp_path, execute=execute)
    receipt = await control.start("personal-private-ticket", {"run_id": "test-run"})
    await asyncio.sleep(0)
    assert receipt == {"run_id": "test-run", "state": "accepted"}
    with pytest.raises(entry.EntryDenied):
        await control.start("personal-private-ticket", {"run_id": "test-run"})
    release.set()
    await control.task
    assert held == [[]]
    assert control.status()["state"] == "finished"
    saved = "".join(p.read_text() for p in (tmp_path / "report").iterdir())
    assert "personal-private-ticket" not in saved


@pytest.mark.asyncio
@pytest.mark.parametrize("owner,enterprise", [("other-employee", "123"), ("employee-1", "456")])
async def test_other_employee_or_enterprise_cannot_start_load(entry, tmp_path, owner, enterprise):
    async def verify(token):
        return SimpleNamespace(enterprise_id=enterprise, union_id=owner)

    control = controller(entry, tmp_path, verify=verify)
    with pytest.raises(entry.EntryDenied):
        await control.start("other-private-ticket", {"run_id": "test-run"})
    assert control.task is None
    assert control.status()["state"] == "waiting"


@pytest.mark.asyncio
async def test_wrong_confirmation_does_not_even_validate_employee(entry, tmp_path):
    async def verify(token):
        pytest.fail("A discovery or unconfirmed call must not start authentication")

    control = controller(entry, tmp_path, verify=verify)
    with pytest.raises(entry.EntryDenied):
        await control.start("private-ticket", {"run_id": "wrong-run"})
    assert control.task is None


@pytest.mark.asyncio
async def test_expiry_cancels_waiting_requests_and_clears_ticket_references(entry, tmp_path):
    held = []
    cancelled = asyncio.Event()

    async def execute(tokens):
        held.append(tokens)
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    control = controller(entry, tmp_path, ttl=0.02, execute=execute)
    await control.start("expiring-private-ticket", {"run_id": "test-run"})
    await control.task
    assert cancelled.is_set()
    assert held == [[]]
    assert control.status() == {"run_id": "test-run", "state": "expired", "stop_reason": "credential_window_expired"}


@pytest.mark.asyncio
async def test_upstream_exception_does_not_leak_ticket_or_allow_run(entry, tmp_path):
    async def verify(token):
        raise RuntimeError("upstream echoed " + token)

    control = controller(entry, tmp_path, verify=verify)
    with pytest.raises(entry.EntryDenied) as error:
        await control.start("private-ticket", {"run_id": "test-run"})
    assert "private-ticket" not in str(error.value)
    assert control.task is None


@pytest.mark.asyncio
async def test_real_mcp_discovery_does_not_start_load_and_tool_requires_current_ticket(entry, tmp_path):
    control = controller(entry, tmp_path)
    app = entry.create_app(control, allowed_hosts=["entry.test"], allowed_peers=["127.0.0.1"])
    async with app.router.lifespan_context(app), httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://entry.test") as client:
        headers = {"accept": "application/json, text/event-stream", "content-type": "application/json"}
        response = await client.post(
            "/mcp",
            headers=headers,
            json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "offline-test", "version": "1"}}},
        )
        assert response.status_code == 200
        response = await client.post("/mcp", headers=headers, json={"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        assert response.json()["result"]["tools"][0]["name"] == "start_controlled_load_test"
        assert control.task is None
        call = {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "start_controlled_load_test", "arguments": {"run_id": "test-run"}}}
        response = await client.post("/mcp", headers=headers, json=call)
        assert response.json()["result"]["isError"] is True
        assert control.task is None
        response = await client.post("/mcp", headers={**headers, "x-oneid-access-token": "personal-private-ticket"}, json=call)
        assert response.json()["result"]["isError"] is False
        assert "personal-private-ticket" not in response.text
        await control.task


@pytest.mark.asyncio
async def test_unlisted_network_peer_is_rejected_before_mcp(entry, tmp_path):
    control = controller(entry, tmp_path)
    app = entry.create_app(control, allowed_hosts=["entry.test"], allowed_peers=["10.0.0.1"])
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://entry.test") as client:
        response = await client.post("/mcp", json={})
    assert response.status_code == 403
    assert control.task is None


@pytest.mark.asyncio
async def test_json_entry_does_not_open_long_lived_sse_connections(entry, tmp_path):
    control = controller(entry, tmp_path)
    app = entry.create_app(control, allowed_hosts=["entry.test"], allowed_peers=["127.0.0.1"])
    async with app.router.lifespan_context(app), httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://entry.test") as client:
        response = await asyncio.wait_for(client.get("/mcp", headers={"accept": "text/event-stream"}), 0.1)
    assert response.status_code == 405


@pytest.mark.asyncio
async def test_connection_diagnostics_record_no_headers_query_values_or_body(entry, tmp_path):
    control = controller(entry, tmp_path)
    app = entry.create_app(control, allowed_hosts=["entry.test"], allowed_peers=["127.0.0.1"])
    async with app.router.lifespan_context(app), httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://entry.test") as client:
        response = await client.post(
            "/mcp?diagnostic=private-query",
            headers={"accept": "application/json, text/event-stream", "content-type": "application/json", "x-oneid-access-token": "private-ticket"},
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
        )
    assert response.status_code == 200
    records = [json.loads(x) for x in (tmp_path / "report/entry-http.jsonl").read_text().splitlines()]
    assert records[0]["event"] == "request_received"
    assert records[0]["path"] == "/mcp"
    assert records[-1]["status"] == 200
    saved = "".join(p.read_text() for p in (tmp_path / "report").iterdir())
    assert "private-ticket" not in saved
    assert "private-query" not in saved


@pytest.mark.asyncio
async def test_concurrent_starts_cannot_launch_two_runs(entry, tmp_path):
    verifying = asyncio.Event()
    release = asyncio.Event()

    async def verify(token):
        verifying.set()
        await release.wait()
        return SimpleNamespace(enterprise_id="123", union_id="employee-1")

    control = controller(entry, tmp_path, verify=verify)
    first = asyncio.create_task(control.start("ticket", {"run_id": "test-run"}))
    await verifying.wait()
    with pytest.raises(entry.EntryDenied):
        await control.start("ticket", {"run_id": "test-run"})
    release.set()
    await first
    await control.task
    assert control.status()["state"] == "finished"


@pytest.mark.asyncio
async def test_disarming_during_verification_cannot_launch_late_load(entry, tmp_path):
    verifying = asyncio.Event()
    release = asyncio.Event()

    async def verify(token):
        verifying.set()
        await release.wait()
        return SimpleNamespace(enterprise_id="123", union_id="employee-1")

    control = controller(entry, tmp_path, verify=verify)
    pending = asyncio.create_task(control.start("ticket", {"run_id": "test-run"}))
    await verifying.wait()
    control.state, control.reason = "closed", "arming_window_expired"
    release.set()
    with pytest.raises(entry.EntryDenied):
        await pending
    assert control.task is None
    assert control.status()["state"] == "closed"


@pytest.mark.asyncio
async def test_entry_shutdown_cancels_run_and_drops_ticket_references(entry, tmp_path):
    held = []
    running = asyncio.Event()

    async def execute(tokens):
        held.append(tokens)
        running.set()
        await asyncio.Event().wait()

    control = controller(entry, tmp_path, execute=execute)
    await control.start("private-ticket", {"run_id": "test-run"})
    await running.wait()
    await control.close()
    assert held == [[]]
    assert control.status()["stop_reason"] == "entry_shutdown"


@pytest.mark.parametrize("change", [{"users": [1, 10]}, {"duration": 61}, {"rps": 2}, {"max_requests": 241}, {"allowed_peers": ["10.0.0.123"]}])
def test_cli_cannot_expand_approved_load_or_network_envelope(entry, tmp_path, monkeypatch, change):
    workload_path = tmp_path / "workload.json"
    workload_path.write_text(json.dumps([{"tool": "ragflow_retrieval", "arguments": {"question": "测试", "dataset_ids": ["a" * 32]}}]))
    config = {"workload": str(workload_path), "host": "127.0.0.1", "port": 9397, "allowed_peers": ["127.0.0.1"], **change}
    monkeypatch.setattr(entry, "private_config", lambda path: config)
    assert entry.main(["--config", "ignored"]) == 1


@pytest.mark.asyncio
async def test_embedded_driver_preserves_server_signal_handlers(tmp_path, monkeypatch):
    from test.load.workbuddy_load import Report, Settings, Step, run_load

    class HealthySampler:
        async def sample(self):
            return {"cpu_percent": 1, "available_gib": 18, "services_alive": True}

    def forbidden(*args):
        pytest.fail("Embedded driver must leave server signal handlers alone")

    loop = asyncio.get_running_loop()
    monkeypatch.setattr(loop, "add_signal_handler", forbidden)
    monkeypatch.setattr(loop, "remove_signal_handler", forbidden)
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(401))) as client:
        result = await run_load(Settings(duration=0.01), ["ticket"], [Step("ragflow_retrieval", {})], client, HealthySampler(), Report(tmp_path / "report"), manage_signals=False)
    assert result["stop_reason"] == "authentication_failed"


def test_duplicate_employee_header_is_rejected(entry):
    from starlette.datastructures import Headers

    with pytest.raises(entry.EntryDenied):
        entry.employee_ticket(Headers(raw=[(b"x-oneid-access-token", b"one"), (b"x-oneid-access-token", b"two")]))


@pytest.mark.asyncio
async def test_real_http_server_exits_when_no_employee_starts_the_run(entry, tmp_path, monkeypatch):
    from api.auth.workbuddy.config import WorkBuddyConfig
    from test.load.workbuddy_load import Settings, Step

    class NoNetworkIdentityClient:
        def __init__(self, config):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def verify_employee(self, token):
            pytest.fail("Arming an entry must not contact OneID")

    class HealthySampler:
        def __init__(self, services):
            pass

        async def sample(self):
            return {"cpu_percent": 1, "available_gib": 18, "services_alive": True}

    monkeypatch.setattr(WorkBuddyConfig, "from_env", lambda: SimpleNamespace(enabled=True, enterprise_id="123"))
    monkeypatch.setattr("api.auth.workbuddy.oneid_client.OneIDClient", NoNetworkIdentityClient)
    monkeypatch.setattr(entry, "LocalSampler", HealthySampler)
    # Port zero binds a fresh kernel-selected loopback port, never a business service.
    config = {
        "report_dir": str(tmp_path / "report"),
        "run_id": "test-run",
        "expected_union_hash": hashlib.sha256(b"employee-1").hexdigest(),
        "enterprise_id": "123",
        "host": "127.0.0.1",
        "port": 0,
        "allowed_peers": ["127.0.0.1"],
        "arm_seconds": 0.05,
    }
    outcome = await asyncio.wait_for(entry.serve(config, Settings(), [Step("ragflow_retrieval", {})]), 3)
    assert outcome == {"run_id": "test-run", "state": "closed", "stop_reason": "arming_window_expired"}


@pytest.mark.asyncio
@pytest.mark.parametrize("state,reason,exit_code", [("finished", "completed", 0), ("expired", "credential_window_expired", 2), ("closed", "arming_window_expired", 2)])
async def test_cli_distinguishes_success_from_expiry_or_never_started(entry, tmp_path, monkeypatch, state, reason, exit_code):
    workload_path = tmp_path / "workload.json"
    workload_path.write_text(json.dumps([{"tool": "ragflow_retrieval", "arguments": {"question": "测试", "dataset_ids": ["a" * 32]}}]))
    config = {"workload": str(workload_path), "host": "127.0.0.1", "port": 9397, "allowed_peers": ["127.0.0.1"]}

    async def serve(config, settings, steps):
        return {"state": state, "stop_reason": reason}

    monkeypatch.setattr(entry, "private_config", lambda path: config)
    monkeypatch.setattr(entry, "serve", serve)
    # The CLI owns its event loop; do not replace pytest's loop-policy state.
    assert await asyncio.to_thread(entry.main, ["--config", "ignored", "--serve"]) == exit_code


@pytest.mark.asyncio
async def test_distributed_peak_profile_has_a_fixed_bounded_execution_plan(entry, tmp_path, monkeypatch):
    workload_path = tmp_path / "workload.json"
    workload_path.write_text(json.dumps([{"tool": "ragflow_retrieval", "arguments": {"question": "测试", "dataset_ids": ["a" * 32]}}]))
    config = {"profile": "distributed_peak", "workload": str(workload_path), "host": "127.0.0.1", "port": 9397, "allowed_peers": ["127.0.0.1"]}

    async def serve(config, settings, steps):
        assert settings.users == (1, 5, 10)
        assert settings.duration == 45
        assert settings.rps == 2
        assert settings.max_requests == 300
        return {"state": "finished", "stop_reason": "completed"}

    monkeypatch.setattr(entry, "private_config", lambda path: config)
    monkeypatch.setattr(entry, "serve", serve)
    assert await asyncio.to_thread(entry.main, ["--config", "ignored", "--serve"]) == 0
    config["rps"] = 3
    assert entry.main(["--config", "ignored"]) == 1


@pytest.mark.parametrize("size", [30, 100])
def test_retrieval_workload_can_cover_real_large_response_sizes(tmp_path, size):
    from test.load.workbuddy_load import load_workload

    path = tmp_path / "workload.json"
    path.write_text(json.dumps([{"tool": "ragflow_retrieval", "arguments": {"question": "测试阶段", "dataset_ids": ["a" * 32], "page_size": size}}]))
    assert load_workload(path)[0].arguments["page_size"] == size
