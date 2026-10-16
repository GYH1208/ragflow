"""Bounded, opt-in load probe for the employee Streamable HTTP MCP endpoint.

No production modules are imported. The default CLI path performs no network I/O.
"""

import argparse
import asyncio
import base64
import json
import math
import os
import signal
import stat
import subprocess
import time
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit

import httpx

SERVICES = ("ragflow-server-scoped-v2.service", "ragflow-workbuddy-mcp.service", "ragflow-task-executor.service", "ragflow-data-sync.service")
TOOL_FIELDS = {
    "search_documents": {"query", "dataset_ids", "limit"},
    "ragflow_retrieval": {"question", "dataset_ids", "document_ids", "page_size", "top_k", "rerank_id", "keyword", "similarity_threshold", "vector_similarity_weight"},
    "get_document_chunks": {"dataset_id", "document_id", "page", "page_size"},
    "get_chunk_image": {"dataset_id", "document_id", "chunk_id"},
}
MAX_RESPONSE_BYTES = 8 * 1024 * 1024


@dataclass(frozen=True)
class Settings:
    url: str = "http://127.0.0.1:9396/mcp"
    users: tuple[int, ...] = (1,)
    duration: float = 120
    rps: float = 0.5
    max_requests: int = 300
    timeout: float = 20
    cooldown: float = 15

    def __post_init__(self):
        url = urlsplit(self.url)
        if url.scheme not in ("http", "https") or url.hostname not in ("127.0.0.1", "localhost", "::1") or url.username or url.password or url.query or url.fragment or url.path != "/mcp":
            raise ValueError("Use the local employee MCP URL ending in /mcp, without credentials or query parameters")
        if not self.users or self.users[0] != 1 or any(type(x) is not int or not 1 <= x <= 10 for x in self.users) or tuple(sorted(set(self.users))) != self.users:
            raise ValueError("Users must be increasing, start at 1, and be at most 10")
        for name, minimum, maximum in (("duration", 0.01, 120), ("rps", 0.01, 2), ("timeout", 0.1, 60), ("cooldown", 0, 60)):
            value = getattr(self, name)
            if not math.isfinite(value) or not minimum <= value <= maximum:
                raise ValueError(f"Invalid {name}; allowed range is {minimum}..{maximum}")
        if type(self.max_requests) is not int or not 1 <= self.max_requests <= 1000:
            raise ValueError("max_requests must be between 1 and 1000")


@dataclass(frozen=True)
class Step:
    tool: str
    arguments: dict
    min_results: int = 1


class Failure(Exception):
    def __init__(self, reason):
        self.reason = reason
        super().__init__(reason)


def load_tokens(path):
    """Read tickets without accepting symlinks or printing decoder/OS details."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, encoding="utf-8") as file:
            info = os.fstat(file.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077 or info.st_size > 1024 * 1024:
                raise ValueError
            tokens = json.load(file)
        if not isinstance(tokens, list) or not 1 <= len(tokens) <= 400:
            raise ValueError
        if any(not isinstance(x, str) or not x or len(x) > 32768 or "," in x or any(c.isspace() or not c.isascii() or ord(c) < 33 for c in x) for x in tokens):
            raise ValueError
        if len(set(tokens)) != len(tokens):
            raise ValueError
        return tokens
    except (OSError, ValueError, UnicodeError):
        raise ValueError("Tickets must be a private owner-only regular JSON file containing distinct valid tickets") from None


def _identifier(value):
    return isinstance(value, str) and 1 <= len(value) <= 128 and value.isascii() and all(c.isalnum() or c in "_-" for c in value) and "REPLACE" not in value.upper()


def load_workload(path):
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(raw, list) or not 1 <= len(raw) <= 20:
            raise ValueError
        steps = []
        for item in raw:
            if not isinstance(item, dict) or set(item) - {"tool", "arguments", "min_results"}:
                raise ValueError
            tool, args = item["tool"], item["arguments"]
            minimum = item.get("min_results", 1)
            if tool not in TOOL_FIELDS or not isinstance(args, dict) or set(args) - TOOL_FIELDS[tool] or type(minimum) is not int or not 0 <= minimum <= 20:
                raise ValueError
            if tool in ("search_documents", "ragflow_retrieval"):
                ids = args.get("dataset_ids")
                if not isinstance(ids, list) or not 1 <= len(ids) <= 2 or not all(_identifier(x) for x in ids):
                    raise ValueError
                query = args.get("query" if tool == "search_documents" else "question")
                if not isinstance(query, str) or not query.strip() or len(query) > 2000:
                    raise ValueError
            elif not all(_identifier(args.get(key)) for key in ("dataset_id", "document_id")):
                raise ValueError
            if tool == "get_chunk_image" and not _identifier(args.get("chunk_id")):
                raise ValueError
            if "document_ids" in args and (not isinstance(args["document_ids"], list) or len(args["document_ids"]) > 10 or not all(_identifier(x) for x in args["document_ids"])):
                raise ValueError
            max_page_size = 100 if tool == "ragflow_retrieval" else 10
            for field, low, high in (("limit", 1, 20), ("page_size", 1, max_page_size), ("top_k", 1, 1024), ("page", 1, 1000)):
                if field in args and (type(args[field]) is not int or not low <= args[field] <= high):
                    raise ValueError
            for field in ("similarity_threshold", "vector_similarity_weight"):
                if field in args and (type(args[field]) not in (int, float) or not math.isfinite(args[field]) or not 0 <= args[field] <= 1):
                    raise ValueError
            if "keyword" in args and type(args["keyword"]) is not bool:
                raise ValueError
            if "rerank_id" in args and (not isinstance(args["rerank_id"], str) or len(args["rerank_id"]) > 256):
                raise ValueError
            steps.append(Step(tool, args, minimum))
        return steps
    except (OSError, ValueError, KeyError, TypeError):
        raise ValueError("Invalid workload: use supported read tools, explicit dataset IDs, valid arguments and no placeholders or credentials") from None


def validate_response(response, request_id, tool=None, min_results=0):
    if response.status_code != 200:
        reason = {401: "authentication_failed", 403: "access_denied", 429: "rate_limited"}.get(response.status_code, "http_error")
        raise Failure(reason)
    try:
        data = response.json()
        if not isinstance(data, dict) or data.get("jsonrpc") != "2.0" or data.get("id") != request_id:
            raise Failure("invalid_rpc_response")
        if "error" in data:
            raise Failure("rpc_error")
        result = data.get("result")
        if not isinstance(result, dict):
            raise Failure("invalid_rpc_response")
        if tool is None:
            return result
        if result.get("isError", False) is not False:
            raise Failure("tool_error")
        content = result.get("content")
        if not isinstance(content, list) or not content or any(not isinstance(item, dict) for item in content):
            raise Failure("invalid_tool_result")
        if tool == "get_chunk_image":
            images = [item for item in content if item.get("type") == "image"]
            if not images or any(
                not isinstance(item.get("mimeType"), str) or not item["mimeType"].startswith("image/") or not base64.b64decode(item.get("data", ""), validate=True) for item in images
            ):
                raise Failure("invalid_tool_result")
            count = len(images)
        else:
            texts = [item["text"] for item in content if item.get("type") == "text"]
            if len(texts) != 1:
                raise Failure("invalid_tool_result")
            payload = json.loads(texts[0])
            field = "documents" if tool == "search_documents" else "chunks"
            if not isinstance(payload, dict) or payload.get("code", 0) != 0 or not isinstance(payload.get(field), list) or any(not isinstance(row, dict) for row in payload[field]):
                raise Failure("invalid_tool_result")
            count = len(payload[field])
        if count < min_results:
            raise Failure("empty_result")
        return count
    except (ValueError, TypeError, KeyError):
        raise Failure("invalid_tool_result" if tool else "invalid_rpc_response") from None


class RateLimiter:
    """One shared request start clock: no per-worker multiplication or catch-up bursts."""

    def __init__(self, rps):
        self.interval = 1 / rps
        self.next_start = 0
        self.lock = asyncio.Lock()

    async def acquire(self, deadline, stop):
        async with self.lock:
            slot = max(time.monotonic(), self.next_start)
            if stop.is_set() or slot >= deadline:
                return False
            delay = slot - time.monotonic()
            if delay > 0:
                try:
                    await asyncio.wait_for(stop.wait(), delay)
                    return False
                except TimeoutError:
                    pass
            if stop.is_set() or time.monotonic() >= deadline:
                return False
            self.next_start = time.monotonic() + self.interval
            return True


class Report:
    def __init__(self, path):
        self.path = Path(path)
        self.path.mkdir(parents=True, mode=0o700, exist_ok=False)
        self.events = []

    def write(self, name, value):
        with os.fdopen(os.open(self.path / name, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW, 0o600), "a", encoding="utf-8") as file:
            file.write(json.dumps(value, ensure_ascii=False, allow_nan=False) + "\n")

    def request(self, event):
        self.events.append(event)
        self.write("requests.jsonl", event)


class LocalSampler:
    """Watch the original service processes so a restart cannot pass unnoticed."""

    def __init__(self, services):
        import psutil

        self.psutil = psutil
        self.processes = {}
        for service in services:
            result = subprocess.run(["systemctl", "--user", "show", service, "--property=MainPID,ActiveState"], capture_output=True, text=True, timeout=3, check=True)
            values = dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)
            if values.get("ActiveState") != "active" or int(values.get("MainPID", 0)) <= 0:
                raise ValueError("A watched service is not active")
            process = psutil.Process(int(values["MainPID"]))
            self.processes[service] = process
        self.driver = psutil.Process()
        self.previous_time = time.monotonic()
        self.previous_cpu = psutil.cpu_times()
        self.process_cpu = {name: sum(process.cpu_times()[:2]) for name, process in {**self.processes, "driver": self.driver}.items()}

    async def sample(self):
        psutil = self.psutil
        now, cpu = time.monotonic(), psutil.cpu_times()
        # guest counters are already included in user/nice on Linux.
        total = sum(cpu) - getattr(cpu, "guest", 0) - getattr(cpu, "guest_nice", 0)
        previous_total = sum(self.previous_cpu) - getattr(self.previous_cpu, "guest", 0) - getattr(self.previous_cpu, "guest_nice", 0)
        idle = cpu.idle + getattr(cpu, "iowait", 0) - self.previous_cpu.idle - getattr(self.previous_cpu, "iowait", 0)
        cpu_percent = max(0, min(100, 100 * (1 - idle / (total - previous_total)))) if total > previous_total else 0
        processes, alive = {}, True
        for name, process in {**self.processes, "driver": self.driver}.items():
            try:
                if not process.is_running() or process.status() == psutil.STATUS_ZOMBIE:
                    raise psutil.NoSuchProcess(process.pid)
                seconds = sum(process.cpu_times()[:2])
                processes[name] = {
                    "pid": process.pid,
                    "cpu_percent": round(100 * (seconds - self.process_cpu[name]) / max(now - self.previous_time, 0.001), 2),
                    "rss_mib": round(process.memory_info().rss / 1024**2, 2),
                }
                self.process_cpu[name] = seconds
            except psutil.Error:
                alive = False
        self.previous_time, self.previous_cpu = now, cpu
        return {
            "cpu_percent": round(cpu_percent, 2),
            "available_gib": round(psutil.virtual_memory().available / 1024**3, 2),
            "services_alive": alive,
            "driver_rss_mib": processes.get("driver", {}).get("rss_mib"),
            "service_processes": processes,
        }


class Runner:
    def __init__(self, settings, tokens, steps, client, sampler, report, *, manage_signals=True):
        self.settings, self.tokens, self.steps = settings, tokens, steps
        self.client, self.sampler, self.report = client, sampler, report
        self.stop = asyncio.Event()
        self.reason = None
        self.limiter = RateLimiter(settings.rps)
        self.sent = 0
        self.stage = 0
        self.high_cpu = 0
        self.slow_calls = 0
        self.manage_signals = manage_signals

    def halt(self, reason):
        if self.reason is None:
            self.reason = reason
            self.stop.set()

    async def check_resources(self):
        try:
            sample = await self.sampler.sample()
            self.report.write("resources.jsonl", {"timestamp": datetime.now(UTC).isoformat(), "stage_users": self.stage, **sample})
            self.high_cpu = self.high_cpu + 1 if sample["cpu_percent"] >= 85 else 0
            if not sample["services_alive"]:
                self.halt("service_exited")
            elif sample["available_gib"] < 4:
                self.halt("low_memory")
            elif self.high_cpu >= 3:
                self.halt("high_cpu")
        except Exception:  # noqa: BLE001 -- monitoring must fail closed without printing external data
            self.halt("monitor_failed")

    async def monitor(self):
        while not self.stop.is_set():
            try:
                await asyncio.wait_for(self.stop.wait(), 2)
            except TimeoutError:
                await self.check_resources()

    async def request(self, method, token, deadline, *, step=None, session_headers=None, worker_index=0, step_index=None):
        if not await self.limiter.acquire(deadline, self.stop):
            return None
        if self.sent >= self.settings.max_requests:
            self.halt("request_limit")
            return None
        self.sent += 1
        request_id = self.sent
        body = {"jsonrpc": "2.0", "method": method}
        if method != "notifications/initialized":
            body["id"] = request_id
        if method == "initialize":
            body["params"] = {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "ragflow-bounded-load", "version": "1"}}
        elif step:
            body["params"] = {"name": step.tool, "arguments": step.arguments}
        headers = {"accept": "application/json, text/event-stream", "content-type": "application/json", "x-oneid-access-token": token, **(session_headers or {})}
        operation = step.tool if step else method
        started = time.monotonic()
        event = {
            "id": request_id,
            "timestamp": datetime.now(UTC).isoformat(),
            "stage_users": self.stage,
            "worker_index": worker_index,
            "step_index": step_index,
            "operation": operation,
            "status": None,
            "success": False,
            "error": None,
        }
        try:
            async with asyncio.timeout(self.settings.timeout):
                async with self.client.stream("POST", self.settings.url, headers=headers, json=body) as response:
                    event["status"] = response.status_code
                    expected_statuses = (202, 204) if method == "notifications/initialized" else (200,)
                    if response.status_code not in expected_statuses:
                        if response.status_code == 200:
                            raise Failure("invalid_notification_response")
                        # Reject rate limits/auth failures on headers; do not wait for a slow error body.
                        validate_response(response, request_id)
                    content = bytearray()
                    async for chunk in response.aiter_bytes():
                        content.extend(chunk)
                        if len(content) > MAX_RESPONSE_BYTES:
                            raise Failure("response_too_large")
                    response = httpx.Response(response.status_code, headers=response.headers, content=bytes(content))
            if method == "notifications/initialized":
                if response.status_code not in (202, 204):
                    raise Failure("invalid_notification_response")
                result = {}
            else:
                result = validate_response(response, request_id, step.tool if step else None, step.min_results if step else 0)
            if method == "initialize":
                if result.get("protocolVersion") != "2025-06-18":
                    raise Failure("unsupported_protocol")
                if session_headers is not None:
                    session_headers["MCP-Protocol-Version"] = result["protocolVersion"]
                    if response.headers.get("mcp-session-id"):
                        session_headers["Mcp-Session-Id"] = response.headers["mcp-session-id"]
            if method == "tools/list" and (
                not isinstance(result.get("tools"), list) or not {step.tool for step in self.steps} <= {item.get("name") for item in result["tools"] if isinstance(item, dict)}
            ):
                raise Failure("tools_missing")
            event["success"] = True
            if step:
                event["result_count"] = result
            return result
        except (TimeoutError, httpx.TimeoutException):
            event["error"] = "timeout"
            self.halt("timeout")
        except Failure as error:
            event["error"] = error.reason
            self.halt(error.reason)
        except httpx.HTTPError:
            event["error"] = "transport_error"
            self.halt("transport_error")
        except asyncio.CancelledError:
            event["error"] = "cancelled"
            raise
        finally:
            event["elapsed_ms"] = round((time.monotonic() - started) * 1000, 2)
            self.report.request(event)
            if step and event["success"]:
                self.slow_calls = self.slow_calls + 1 if event["elapsed_ms"] >= 15000 else 0
                if self.slow_calls >= 3:
                    self.halt("slow_tools")

    async def worker(self, index, deadline):
        token = self.tokens[index % len(self.tokens)]
        headers = {}
        for method in ("initialize", "notifications/initialized", "tools/list"):
            if await self.request(method, token, deadline, session_headers=headers, worker_index=index) is None:
                return
        cursor = index
        while not self.stop.is_set() and time.monotonic() < deadline:
            step = self.steps[cursor % len(self.steps)]
            if await self.request("tools/call", token, deadline, step=step, session_headers=headers, worker_index=index, step_index=cursor % len(self.steps)) is None:
                return
            cursor += 1

    async def run(self):
        started = time.monotonic()
        completed = []
        stages = []
        loop = asyncio.get_running_loop()
        if self.manage_signals:
            for sig in (signal.SIGINT, signal.SIGTERM):
                loop.add_signal_handler(sig, self.halt, "interrupted")
        monitor = None
        try:
            await self.check_resources()
            if not self.stop.is_set():
                monitor = asyncio.create_task(self.monitor())
            for users in self.settings.users:
                if self.stop.is_set():
                    break
                self.stage = users
                stage_start = time.monotonic()
                before = len(self.report.events)
                tasks = [asyncio.create_task(self.worker(index, stage_start + self.settings.duration)) for index in range(users)]
                group = asyncio.gather(*tasks)
                stopping = asyncio.create_task(self.stop.wait())
                try:
                    await asyncio.wait((group, stopping), return_when=asyncio.FIRST_COMPLETED)
                    if self.stop.is_set():
                        for task in tasks:
                            task.cancel()
                    await group
                except asyncio.CancelledError:
                    if not self.stop.is_set():
                        raise
                finally:
                    for task in tasks:
                        if not task.done():
                            task.cancel()
                    await asyncio.gather(*tasks, return_exceptions=True)
                    await asyncio.gather(group, return_exceptions=True)
                    stopping.cancel()
                    await asyncio.gather(stopping, return_exceptions=True)
                events = self.report.events[before:]
                samples = [event for event in events if event["operation"] in TOOL_FIELDS and event["success"]]
                sampled_workers = len({event["worker_index"] for event in samples})
                sampled_steps = len({event["step_index"] for event in samples})
                stages.append(
                    {
                        "users": users,
                        "elapsed_seconds": round(time.monotonic() - stage_start, 3),
                        "workers_with_tool_samples": sampled_workers,
                        "workload_steps_sampled": sampled_steps,
                        "operations": summarize(events),
                    }
                )
                if not self.stop.is_set():
                    if not samples:
                        self.halt("no_tool_samples")
                    elif sampled_workers != users or sampled_steps != len(self.steps):
                        self.halt("incomplete_stage")
                if not self.stop.is_set():
                    completed.append(users)
                if users != self.settings.users[-1] and not self.stop.is_set():
                    try:
                        await asyncio.wait_for(self.stop.wait(), self.settings.cooldown)
                    except TimeoutError:
                        pass
        except Exception:  # noqa: BLE001 -- stop all load and retain a sanitized partial report on unexpected failures
            self.halt("driver_error")
        finally:
            self.stop.set()
            if monitor:
                await monitor
            if self.manage_signals:
                for sig in (signal.SIGINT, signal.SIGTERM):
                    loop.remove_signal_handler(sig)
        elapsed = time.monotonic() - started
        summary = {
            "stop_reason": self.reason or "completed",
            "settings": asdict(self.settings),
            "stage_duration_semantics": "request_start_window; inflight requests drain for at most timeout seconds",
            "credential_count": len(self.tokens),
            "identity_isolation_verified": False,
            "requests_started": self.sent,
            "elapsed_seconds": round(elapsed, 3),
            "actual_requests_per_second": round(self.sent / max(elapsed, 0.001), 3),
            "stages_completed": completed,
            "operations": summarize(self.report.events),
            "stages": stages,
        }
        self.report.write("summary.json", summary)
        return summary


def summarize(events):
    groups = defaultdict(list)
    for event in events:
        groups[event["operation"]].append(event)
    output = {}
    for name, rows in groups.items():
        latencies = sorted(row["elapsed_ms"] for row in rows if row["success"])
        output[name] = {
            "total": len(rows),
            "failed": sum(not row["success"] for row in rows),
            "success_latency_samples": len(latencies),
            **{label: latencies[max(0, math.ceil(len(latencies) * percentile) - 1)] if latencies else None for label, percentile in (("p50_ms", 0.5), ("p95_ms", 0.95), ("p99_ms", 0.99))},
        }
    return output


async def run_load(settings, tokens, steps, client, sampler, report, *, manage_signals=True):
    return await Runner(settings, tokens, steps, client, sampler, report, manage_signals=manage_signals).run()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workload", type=Path, required=True)
    parser.add_argument("--tokens-file", type=Path)
    parser.add_argument("--execute", action="store_true", help="Explicitly enable real HTTP requests")
    parser.add_argument("--url", default=Settings.url)
    parser.add_argument("--users", default="1", help="Increasing stages, e.g. 1,3,5,10; maximum 10")
    parser.add_argument("--duration", type=float, default=120, help="Request-start window per stage including initialization, max 120s; inflight requests drain up to --timeout")
    parser.add_argument("--rps", type=float, default=0.5, help="Global MCP request starts per second, maximum 2")
    parser.add_argument("--max-requests", type=int, default=300, help="Global request budget, including initialization")
    parser.add_argument("--timeout", type=float, default=20)
    parser.add_argument("--report-dir", type=Path)
    parser.add_argument("--service", action="append", help="User systemd service to watch; repeat to override deployed defaults")
    args = parser.parse_args(argv)
    try:
        settings = Settings(url=args.url, users=tuple(int(x) for x in args.users.split(",")), duration=args.duration, rps=args.rps, max_requests=args.max_requests, timeout=args.timeout)
        steps = load_workload(args.workload)
        tokens = load_tokens(args.tokens_file) if args.tokens_file else []
        if not args.execute:
            print(json.dumps({"mode": "dry_run", "network_requests": 0, "settings": asdict(settings), "tools": [step.tool for step in steps], "credential_count": len(tokens)}, ensure_ascii=False))
            return 0
        if not tokens:
            raise ValueError("--execute requires --tokens-file; never put tickets in CLI arguments")
        sampler = LocalSampler(args.service or SERVICES)
        path = args.report_dir or Path("logs/workbuddy-load") / datetime.now(UTC).strftime("%Y%m%dT%H%M%S.%fZ")
        report = Report(path)

        async def execute():
            async with httpx.AsyncClient(
                timeout=settings.timeout, follow_redirects=False, trust_env=False, limits=httpx.Limits(max_connections=max(settings.users), max_keepalive_connections=max(settings.users))
            ) as client:
                return await run_load(settings, tokens, steps, client, sampler, report)

        summary = asyncio.run(execute())
        print(json.dumps({"stop_reason": summary["stop_reason"], "requests_started": summary["requests_started"], "report_dir": str(path)}, ensure_ascii=False))
        return 0 if summary["stop_reason"] == "completed" else 2
    except (ValueError, OSError, subprocess.SubprocessError, ImportError):
        # Neither credentials nor arbitrary upstream/decoder messages reach the terminal.
        print("Configuration/preflight failed. Check workload, private tickets file, new report directory, dependencies and watched services.")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
