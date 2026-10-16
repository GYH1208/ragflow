"""One-shot MCP test entry. Personal credentials are handed to the driver in memory.

No application authentication cache or production MCP service is modified.
"""

import argparse
import asyncio
import hashlib
import hmac
import json
import logging
import os
import re
import resource
import stat
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path

import httpx

from test.load.workbuddy_load import SERVICES, LocalSampler, Report, Settings, load_workload, run_load


class EntryDenied(Exception):
    """Contains a fixed category, never upstream text or credential material."""


def employee_ticket(headers):
    values = headers.getlist("x-oneid-access-token")
    if len(values) != 1:
        raise EntryDenied("employee_ticket_required")
    token = values[0]
    if not token or len(token) > 32768 or "," in token or any(not c.isascii() or ord(c) < 33 or c.isspace() for c in token):
        raise EntryDenied("employee_ticket_required")
    return token


class MemoryLoadEntry:
    def __init__(self, *, run_id, expected_union_hash, enterprise_id, verify, execute, report, ttl=300):
        if not re.fullmatch(r"[a-zA-Z0-9_-]{1,80}", run_id) or not re.fullmatch(r"[a-f0-9]{64}", expected_union_hash) or not 0 < ttl <= 300:
            raise ValueError("Invalid entry binding or credential window")
        self.run_id = run_id
        self.expected_union_hash = expected_union_hash
        self.enterprise_id = enterprise_id
        self.verify = verify
        self.execute = execute
        self.report = report
        self.ttl = ttl
        self.state = "waiting"
        self.reason = None
        self.task = None
        self.attempts = 0
        self.lock = asyncio.Lock()
        self.started = asyncio.Event()
        self.record()

    def status(self):
        return {"run_id": self.run_id, "state": self.state, "stop_reason": self.reason}

    def record(self):
        self.report.write("entry-status.jsonl", self.status())

    async def start(self, token, arguments):
        if arguments != {"run_id": self.run_id}:
            raise EntryDenied("explicit_run_confirmation_required")
        # Do not queue repeated captures behind an in-progress identity check.
        if self.lock.locked() or self.state != "waiting" or self.attempts >= 3:
            raise EntryDenied("entry_already_used_or_busy")
        async with self.lock:
            self.attempts += 1
            captured_at = asyncio.get_running_loop().time()
            try:
                async with asyncio.timeout(min(20, self.ttl)):
                    profile = await self.verify(token)
                digest = hashlib.sha256(profile.union_id.encode()).hexdigest()
                if profile.enterprise_id != self.enterprise_id or not hmac.compare_digest(digest, self.expected_union_hash):
                    raise EntryDenied("test_employee_mismatch")
            except Exception:  # noqa: BLE001 -- never echo upstream identity errors
                self.report.write("entry-events.jsonl", {"event": "identity_rejected"})
                raise EntryDenied("employee_verification_failed") from None
            if self.state != "waiting":
                raise EntryDenied("entry_closed_during_verification")
            self.state = "running"
            self.record()
            remaining = self.ttl - (asyncio.get_running_loop().time() - captured_at)
            self.task = asyncio.create_task(self._run([token], remaining))
            self.started.set()
            return {"run_id": self.run_id, "state": "accepted"}

    async def _run(self, tickets, remaining):
        try:
            async with asyncio.timeout(remaining):
                result = await self.execute(tickets)
            self.reason = result.get("stop_reason", "driver_error")
            self.state = "finished" if self.reason == "completed" else "stopped"
        except TimeoutError:
            self.state, self.reason = "expired", "credential_window_expired"
        except asyncio.CancelledError:
            self.state, self.reason = "stopped", "entry_shutdown"
        except Exception:  # noqa: BLE001 -- partial reports contain only fixed error categories
            self.state, self.reason = "stopped", "driver_error"
        finally:
            # Drop application references; Python strings cannot promise physical zeroization.
            tickets.clear()
            self.record()

    async def close(self):
        if self.task and not self.task.done():
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
        elif self.task is None and self.state == "waiting":
            self.state, self.reason = "closed", "entry_shutdown"
            self.record()


def create_app(control, *, allowed_hosts, allowed_peers):
    from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
    from mcp.server.transport_security import TransportSecuritySettings
    from starlette.applications import Starlette
    from starlette.middleware import Middleware
    from starlette.responses import JSONResponse
    from starlette.routing import Route

    from mcp import types
    from mcp.server import Server

    server = Server("workbuddy-controlled-load-entry")

    @server.list_tools()
    async def list_tools():
        control.report.write("entry-events.jsonl", {"event": "tools_listed", "timestamp": datetime.now(UTC).isoformat()})
        return [
            types.Tool(
                name="start_controlled_load_test",
                description="Start the operator-approved, one-shot employee MCP load test. Only call when the user explicitly requests this test. This starts real read requests against the authorized knowledge base.",
                inputSchema={"type": "object", "properties": {"run_id": {"type": "string", "enum": [control.run_id]}}, "required": ["run_id"], "additionalProperties": False},
            )
        ]

    @server.call_tool()
    async def call_tool(name, arguments):
        try:
            if name != "start_controlled_load_test":
                raise EntryDenied("unknown_tool")
            control.report.write("entry-events.jsonl", {"event": "start_tool_called", "timestamp": datetime.now(UTC).isoformat()})
            token = employee_ticket(server.request_context.request.headers)
            result = await control.start(token, arguments)
            return types.CallToolResult(content=[types.TextContent(type="text", text=json.dumps(result))], isError=False)
        except EntryDenied as error:
            return types.CallToolResult(content=[types.TextContent(type="text", text=str(error))], isError=True)

    manager = StreamableHTTPSessionManager(
        server,
        json_response=True,
        stateless=True,
        max_request_body_size=16384,
        security_settings=TransportSecuritySettings(enable_dns_rebinding_protection=True, allowed_hosts=allowed_hosts, allowed_origins=[]),
    )

    class MCPEntry:
        async def __call__(self, scope, receive, send):
            peer = scope.get("client", (None,))[0]
            if peer not in allowed_peers:
                await JSONResponse({"error": "peer_not_allowed"}, status_code=403)(scope, receive, send)
                return
            await manager.handle_request(scope, receive, send)

    class ConnectionDiagnostics:
        def __init__(self, app):
            self.app = app

        async def __call__(self, scope, receive, send):
            if scope["type"] != "http":
                await self.app(scope, receive, send)
                return
            method = scope.get("method")
            metadata = {
                "method": method if method in {"GET", "POST", "DELETE", "OPTIONS", "HEAD"} else "OTHER",
                "path": "/mcp" if scope.get("path") == "/mcp" else "other",
                "peer_ip": scope.get("client", (None,))[0],
            }
            control.report.write("entry-http.jsonl", {"event": "request_received", "timestamp": datetime.now(UTC).isoformat(), **metadata})

            async def safe_send(message):
                if message["type"] == "http.response.start":
                    control.report.write("entry-http.jsonl", {"event": "response_started", "timestamp": datetime.now(UTC).isoformat(), **metadata, "status": message["status"]})
                await send(message)

            await self.app(scope, receive, safe_send)

    @asynccontextmanager
    async def lifespan(app):
        async with manager.run():
            try:
                yield
            finally:
                await control.close()

    return Starlette(routes=[Route("/mcp", MCPEntry(), methods=["POST", "DELETE"])], middleware=[Middleware(ConnectionDiagnostics)], lifespan=lifespan)


def private_config(path):
    with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK), encoding="utf-8") as file:
        info = os.fstat(file.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077 or info.st_size > 65536:
            raise ValueError("Configuration must be private")
        return json.load(file)


async def serve(config, settings, steps):
    import uvicorn

    from api.auth.workbuddy.config import WorkBuddyConfig
    from api.auth.workbuddy.oneid_client import OneIDClient

    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    # Prevent dependency debug output from echoing provider responses or request headers.
    logging.getLogger().setLevel(logging.CRITICAL)
    report = Report(config["report_dir"])
    sampler = LocalSampler(SERVICES)
    sample = await sampler.sample()
    if not sample["services_alive"] or sample["available_gib"] < 4:
        raise ValueError("Resource preflight failed")
    report.write("preflight.jsonl", sample)

    async def execute(tickets):
        load_report = Report(Path(config["report_dir"]) / "load")
        async with httpx.AsyncClient(
            timeout=settings.timeout, follow_redirects=False, trust_env=False, limits=httpx.Limits(max_connections=max(settings.users), max_keepalive_connections=max(settings.users))
        ) as client:
            return await run_load(settings, tickets, steps, client, sampler, load_report, manage_signals=False)

    oneid_config = WorkBuddyConfig.from_env()
    if not oneid_config.enabled or oneid_config.enterprise_id != config["enterprise_id"]:
        raise ValueError("Employee enterprise configuration mismatch")
    async with OneIDClient(oneid_config) as oneid:
        control = MemoryLoadEntry(
            run_id=config["run_id"], expected_union_hash=config["expected_union_hash"], enterprise_id=config["enterprise_id"], verify=oneid.verify_employee, execute=execute, report=report
        )
        host, port = config["host"], config["port"]
        app = create_app(control, allowed_hosts=[f"{host}:{port}", f"127.0.0.1:{port}", f"localhost:{port}"], allowed_peers=config["allowed_peers"])
        server = uvicorn.Server(uvicorn.Config(app, host=host, port=port, access_log=False, log_level="critical", log_config=None, proxy_headers=False, timeout_graceful_shutdown=2))

        async def stop_when_done():
            try:
                await asyncio.wait_for(control.started.wait(), config.get("arm_seconds", 3600))
                await control.task
                await asyncio.sleep(2)
            except TimeoutError:
                control.state, control.reason = "closed", "arming_window_expired"
                control.record()
            server.should_exit = True

        stopper = asyncio.create_task(stop_when_done())
        try:
            await server.serve()
        finally:
            stopper.cancel()
            await asyncio.gather(stopper, return_exceptions=True)
            await control.close()
        return control.status()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--serve", action="store_true", help="Explicitly open the temporary MCP listener")
    args = parser.parse_args(argv)
    try:
        config = private_config(args.config)
        profile = config.get("profile", "baseline")
        if profile == "distributed_peak":
            fixed = {"users": [1, 5, 10], "duration": 45, "rps": 2, "max_requests": 300}
            if any(key in config and config[key] != value for key, value in fixed.items()):
                raise ValueError("Distributed peak plan cannot be overridden")
            settings = Settings(users=(1, 5, 10), duration=45, rps=2, max_requests=300)
        elif profile == "baseline":
            settings = Settings(users=tuple(config.get("users", [1, 3, 5])), duration=config.get("duration", 60), rps=config.get("rps", 1), max_requests=config.get("max_requests", 240))
            if max(settings.users) > 5 or settings.duration > 60 or settings.rps > 1 or settings.max_requests > 240:
                raise ValueError("Plan exceeds the approved entry load envelope")
        else:
            raise ValueError("Unknown entry load profile")
        peers = config.get("allowed_peers")
        if not isinstance(peers, list) or not peers or not set(peers) <= {"127.0.0.1", "10.10.78.93"}:
            raise ValueError("Unapproved entry peer")
        if settings.duration * len(settings.users) + settings.cooldown * (len(settings.users) - 1) + settings.timeout * len(settings.users) > 290:
            raise ValueError("Plan exceeds the credential window")
        steps = load_workload(config["workload"])
        if any(s.tool != "ragflow_retrieval" for s in steps):
            raise ValueError("Temporary entry only supports semantic retrieval")
        if config["host"] not in ("127.0.0.1", "192.168.1.183") or config["port"] != 9397 or not 1 <= config.get("arm_seconds", 3600) <= 3600:
            raise ValueError("Invalid temporary listener")
        if not args.serve:
            print(json.dumps({"mode": "dry_run", "network_requests": 0, "users": settings.users, "tools": [s.tool for s in steps]}))
            return 0
        outcome = asyncio.run(serve(config, settings, steps))
        return 0 if outcome["state"] == "finished" and outcome["stop_reason"] == "completed" else 2
    except Exception:  # noqa: BLE001 -- never expose provider/runtime exception text
        print("Entry configuration or runtime failed; no credential details are emitted.")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
