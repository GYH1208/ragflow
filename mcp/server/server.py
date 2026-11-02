#
#  Copyright 2025 The InfiniFlow Authors. All Rights Reserved.
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
import base64
import hashlib
import io
import json
import logging
import os
import random
import re
import secrets
import tempfile
import threading
import time
from collections import OrderedDict
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from functools import wraps
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlsplit

import click
import httpx
import mcp.types as types
from mcp.server.lowlevel import Server
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.responses import JSONResponse, Response
from starlette.routing import Mount, Route


class LaunchMode(StrEnum):
    SELF_HOST = "self-host"
    HOST = "host"


class Transport(StrEnum):
    SSE = "sse"
    STEAMABLE_HTTP = "streamable-http"


BASE_URL = "http://127.0.0.1:9380"
HOST = "127.0.0.1"
PORT = "9382"
HOST_API_KEY = ""
AUTH_MODE = "api-key"
MODE = ""
TRANSPORT_SSE_ENABLED = True
TRANSPORT_STREAMABLE_HTTP_ENABLED = True
JSON_RESPONSE = True
STREAMABLE_HTTP_ALLOWED_HOSTS = ["127.0.0.1:*", "localhost:*", "[::1]:*"]
STREAMABLE_HTTP_ALLOWED_ORIGINS = ["http://127.0.0.1:*", "http://localhost:*", "http://[::1]:*"]
MCP_IMAGE_MAX_BYTES = int(os.environ.get("RAGFLOW_MCP_IMAGE_MAX_BYTES", str(10 * 1024 * 1024)))
TOOL_COUNTS_FILE = None
TOOL_CALL_COUNTS = None
TOOL_NAMES = ("ragflow_retrieval", "search_documents", "get_document_chunks", "get_chunk_image", "list_knowledge_bases", "list_documents", "export_document_catalog")
logger = logging.getLogger(__name__)


CATALOG_EXPORTS = None
CATALOG_MIME_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


class CatalogExportError(ValueError):
    """An export failure safe to report without backend details or credentials."""


class CatalogDownloadLogFilter(logging.Filter):
    def filter(self, record):
        if isinstance(record.args, tuple) and len(record.args) == 5:
            client, method, path, version, status = record.args
            if isinstance(path, str) and "/mcp/catalog-download/" in path:
                record.args = (client, method, "/mcp/catalog-download/[redacted]", version, status)
        return True


def _build_catalog_workbook(dataset_id, dataset_name, documents, exported_at, cancel_event=None):
    from openpyxl import Workbook
    from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE, Cell
    from openpyxl.styles import Font, PatternFill

    def check_cancelled():
        if cancel_event is not None and cancel_event.is_set():
            raise CatalogExportError("Catalog workbook generation cancelled.")

    check_cancelled()

    def append_literal(tab, values):
        check_cancelled()
        cells = []
        for value in values:
            cell = Cell(tab, value=ILLEGAL_CHARACTERS_RE.sub("", value) if isinstance(value, str) else value)
            if isinstance(value, str):
                cell.data_type = "s"
            cells.append(cell)
        tab.append(cells)

    workbook = Workbook()
    overview = workbook.active
    overview.title = "导出说明"
    for row in [
        ["项目", "内容"],
        ["知识库", dataset_name],
        ["知识库 ID", dataset_id],
        ["文献总数", len(documents)],
        ["导出时间（UTC）", exported_at],
        ["说明", "本文件为导出期间读取的文献元数据清单，不包含文献正文；不是数据库事务快照。"],
        ["路径说明", "仅保留数据源明确提供的原始路径；空白表示未知，不根据存储位置推断目录。"],
    ]:
        append_literal(overview, row)
    sheet = workbook.create_sheet("文献目录")
    sheet.append(["序号", "文献名称", "文件类型", "解析状态", "更新时间", "原始路径", "文档 ID", "知识库 ID"])
    statuses = {"UNSTART": "未开始", "RUNNING": "解析中", "CANCEL": "已取消", "DONE": "已完成", "FAIL": "失败"}
    for index, doc in enumerate(documents, 1):
        append_literal(sheet, [index, doc["name"], doc["type"], statuses.get(doc["run"], doc["run"]), doc["update_date"], doc["path"], doc["document_id"], dataset_id])
    # Metadata strings are literal cells, including names beginning with '=' or '+'.
    for tab in (overview, sheet):
        tab.freeze_panes = "A2"
        for cell in tab[1]:
            cell.font = Font(bold=True, color="FFFFFF")
            cell.fill = PatternFill("solid", fgColor="24557A")
    overview.column_dimensions["A"].width = 24
    overview.column_dimensions["B"].width = 100
    for column, width in zip("ABCDEFGH", [10, 65, 14, 16, 24, 60, 38, 38], strict=True):
        sheet.column_dimensions[column].width = width
    sheet.auto_filter.ref = f"A1:H{len(documents) + 1}"
    output = io.BytesIO()
    check_cancelled()
    workbook.save(output)
    workbook.close()
    check_cancelled()
    return output.getvalue()


class CatalogExports:
    """Bounded per-process jobs and short-lived, file-scoped download capabilities."""

    TTL = 15 * 60
    MAX_DOCUMENTS = 50_000
    MAX_ACTIVE = 2
    MAX_JOBS = 16
    MAX_BYTES = 64 * 1024 * 1024
    MAX_FILE_BYTES = 10 * 1024 * 1024
    TIMEOUT = 120

    def __init__(self):
        self.jobs = {}

    async def __aenter__(self):
        self._cleaner = asyncio.create_task(self._cleanup_loop())
        return self

    async def __aexit__(self, *exc):
        tasks = [self._cleaner, *(job["task"] for job in self.jobs.values())]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self.jobs.clear()

    def _prune(self):
        for export_id, job in list(self.jobs.items()):
            if job["expires_at"] <= time.monotonic() and job["task"].done():
                del self.jobs[export_id]

    async def _cleanup_loop(self):
        while True:
            await asyncio.sleep(60)
            self._prune()

    @staticmethod
    async def _authorize(connector, api_key, dataset_id):
        payload = await connector._fetch_datasets_page(api_key=api_key, page=1, page_size=1, id=dataset_id)
        items = payload["data"]
        if len(items) != 1 or items[0].get("id") != dataset_id:
            raise CatalogExportError("Knowledge base is unavailable or access was denied.")
        return items[0]

    async def request(self, connector, *, api_key, dataset_id, export_id, base_url):
        self._prune()
        owner = hashlib.sha256(f"{connector.auth_mode}:{api_key}".encode()).hexdigest()
        if export_id:
            job = self.jobs.get(export_id)
            if job is None or job["owner"] != owner or job["dataset_id"] != dataset_id:
                raise CatalogExportError("Export is unavailable or expired. Start a new export without export_id.")
        dataset = await self._authorize(connector, api_key, dataset_id)
        if not export_id:
            if len(self.jobs) >= self.MAX_JOBS or sum(job["status"] == "processing" for job in self.jobs.values()) >= self.MAX_ACTIVE:
                raise CatalogExportError("Catalog export capacity is busy. Retry later.")
            export_id = secrets.token_urlsafe(24)
            job = {"owner": owner, "dataset_id": dataset_id, "status": "processing", "expires_at": time.monotonic() + self.TTL}
            self.jobs[export_id] = job
            # A worker owns its connector: the stateless MCP request may close its own connector now.
            job["task"] = asyncio.create_task(self._run(job, connector.base_url, connector.version, connector.auth_mode, api_key, dataset))
        result = {"export_id": export_id, "dataset_id": dataset_id, "status": job["status"]}
        if job["status"] == "processing":
            result["retry_after_seconds"] = 2
        elif job["status"] == "failed":
            result["message"] = job["message"]
        else:
            result.update(job["summary"])
            result["download_url"] = f"{base_url}/catalog-download/{job['download_token']}"
            result["delivery_instructions"] = (
                "Download download_url as a binary file into the WorkBuddy workspace using the returned filename. "
                "Use the client's available file presentation tool (such as present_files) with that verified local file. "
                "If file download or presentation is unavailable, provide the download link to the user. "
                "Do not treat the remote URL as a local path or reproduce the full catalog in the answer."
            )
        return [types.TextContent(type="text", text=json.dumps(result, ensure_ascii=False))]

    async def _run(self, job, backend_url, version, auth_mode, api_key, dataset):
        connector = RAGFlowConnector(backend_url, version=version, auth_mode=auth_mode)
        try:
            async with asyncio.timeout(self.TIMEOUT):
                documents = []
                seen = set()
                total = None
                page = 1
                while True:
                    content = await connector.list_documents(api_key=api_key, dataset_id=job["dataset_id"], page=page, page_size=100)
                    payload = json.loads(content[0].text)
                    current_total = payload["pagination"]["total"]
                    if total is None:
                        total = current_total
                        if total > self.MAX_DOCUMENTS:
                            raise CatalogExportError(f"Catalog exceeds the {self.MAX_DOCUMENTS:,}-document export limit; no partial file was created.")
                    if current_total != total:
                        raise CatalogExportError("The document count changed during export. Retry to generate a complete catalog.")
                    batch = payload["documents"]
                    if len(batch) != min(100, total - len(documents)):
                        raise CatalogExportError("The backend returned an incomplete document page. No partial file was created.")
                    for doc in batch:
                        if doc["document_id"] in seen:
                            raise CatalogExportError("Duplicate documents appeared during pagination. Retry the export.")
                        seen.add(doc["document_id"])
                        documents.append(doc)
                    if len(documents) == total:
                        break
                    page += 1
                exported_at = datetime.now(UTC).isoformat()
                stop_builder = threading.Event()
                builder = asyncio.create_task(asyncio.to_thread(_build_catalog_workbook, job["dataset_id"], dataset.get("name") or "知识库", documents, exported_at, stop_builder))
                try:
                    data = await asyncio.shield(builder)
                except asyncio.CancelledError:
                    # Cancelling to_thread's await cannot stop its thread. Signal row construction,
                    # then retain this job's capacity and lifetime until the builder really exits.
                    stop_builder.set()
                    cleanup = asyncio.gather(builder, return_exceptions=True)
                    while not cleanup.done():
                        try:
                            await asyncio.shield(cleanup)
                        except asyncio.CancelledError:
                            continue
                    raise
                # Recheck grants after all pages and workbook generation, before publishing bytes.
                await self._authorize(connector, api_key, job["dataset_id"])
                self._prune()
                if len(data) > self.MAX_FILE_BYTES or len(data) + sum(len(item.get("data", b"")) for item in self.jobs.values()) > self.MAX_BYTES:
                    raise CatalogExportError("Catalog file storage limit exceeded. Retry later; no partial file was published.")
                safe_name = re.sub(r"[^\w .-]", "_", dataset.get("name") or "知识库")[:80].strip(" .") or "知识库"
                expires = datetime.now(UTC) + timedelta(seconds=self.TTL)
                job.update(
                    status="ready",
                    data=data,
                    download_token=secrets.token_urlsafe(32),
                    expires_at=time.monotonic() + self.TTL,
                    summary={
                        "filename": f"{safe_name}_文献目录.xlsx",
                        "knowledge_base_name": dataset.get("name") or "知识库",
                        "document_count": total,
                        "exported_at": exported_at,
                        "expires_at": expires.isoformat(),
                        "mime_type": CATALOG_MIME_TYPE,
                        "size_bytes": len(data),
                        "sha256": hashlib.sha256(data).hexdigest(),
                        "preview": [{key: doc[key] for key in ("name", "type", "run")} for doc in documents[:5]],
                    },
                )
        except TimeoutError:
            job.update(status="failed", message="Catalog export timed out. No partial file was published; retry later.")
        except Exception as exc:  # noqa: BLE001 - isolate background failures; report no backend details
            job.update(status="failed", message=str(exc) if isinstance(exc, CatalogExportError) else "Catalog export failed. Verify access and retry; no partial file was published.")
            logger.warning("Catalog export failed (%s)", type(exc).__name__)
        finally:
            await connector.close()

    async def download(self, request):
        self._prune()
        token = request.path_params["token"]
        job = next((item for item in self.jobs.values() if item["status"] == "ready" and secrets.compare_digest(item["download_token"].encode(), token.encode())), None)
        response_headers = {"Cache-Control": "private, no-store", "Referrer-Policy": "no-referrer", "X-Content-Type-Options": "nosniff"}
        if job is None:
            return Response("Export unavailable or expired.", status_code=404, headers=response_headers)
        response_headers["Content-Disposition"] = f"attachment; filename=\"catalog.xlsx\"; filename*=UTF-8''{quote(job['summary']['filename'])}"
        return Response(job["data"], media_type=CATALOG_MIME_TYPE, headers=response_headers)


def _catalog_base_url():
    request = getattr(app.request_context, "request", None)
    value = os.environ.get("RAGFLOW_MCP_PUBLIC_URL") or (str(request.url) if request is not None else "")
    parsed = urlsplit(value)
    if parsed.scheme not in ("http", "https") or not parsed.netloc or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise CatalogExportError("Configure RAGFLOW_MCP_PUBLIC_URL with the client-reachable MCP endpoint URL.")
    return value.rstrip("/")


class ToolCallCounts:
    """Single-process counters; all increments and snapshots run on the event loop."""

    def __init__(self, path, interval=10):
        self.path = Path(path)
        self.interval = interval
        self.counts = dict.fromkeys(TOOL_NAMES, 0)
        self._saved = None
        self._stop = asyncio.Event()
        self._flush_lock = asyncio.Lock()

    async def __aenter__(self):
        import fcntl

        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._file_lock = self.path.with_suffix(self.path.suffix + ".lock").open("a")
        try:
            fcntl.flock(self._file_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            if self.path.exists():
                saved = json.loads(self.path.read_text())
                if not isinstance(saved, dict) or any(not isinstance(k, str) or type(v) is not int or v < 0 for k, v in saved.items()):
                    raise ValueError(f"Invalid MCP tool counts file: {self.path}")
                self.counts.update(saved)
            # Validate storage before accepting requests; never silently reset bad data.
            snapshot = self.counts.copy()
            await asyncio.to_thread(self._write, snapshot)
            self._saved = snapshot
            self._task = asyncio.create_task(self._run())
        except BaseException:
            self._file_lock.close()
            raise
        return self

    def increment(self, name):
        if name in TOOL_NAMES:
            self.counts[name] += 1

    def _write(self, snapshot):
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", dir=self.path.parent, prefix=self.path.name + ".", delete=False) as output:
                temporary = Path(output.name)
                json.dump(snapshot, output, indent=2)
                output.write("\n")
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, self.path)
            directory = os.open(self.path.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    async def flush(self):
        async with self._flush_lock:
            snapshot = self.counts.copy()
            if snapshot == self._saved:
                return
            try:
                await asyncio.to_thread(self._write, snapshot)
            except OSError:
                logger.exception("Unable to save MCP tool counts to %s; counts remain in memory for retry", self.path)
            else:
                self._saved = snapshot

    async def _run(self):
        while not self._stop.is_set():
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self.interval)
            except TimeoutError:
                await self.flush()

    async def __aexit__(self, *exc):
        try:
            self._stop.set()
            await self._task
            await self.flush()
        finally:
            self._file_lock.close()


_CREDENTIAL_MESSAGE_PATTERN = re.compile(
    r"(?i)(?:\bauthorization\b|\b(?:x[\s_-]*)?api[\s_-]*key\b)\s*[:=]\s*\S+"
    r"|\bbearer\s+(?:ragflow-[A-Za-z0-9._~+/=-]+|[A-Za-z0-9._~+/=-]{16,})"
    r"|\bragflow-(?:rk-)?[A-Za-z0-9._~+/=-]{4,}"
)


def _response_json_object(response):
    if response is None:
        return None

    try:
        payload = response.json()
    except Exception:
        return None

    return payload if isinstance(payload, dict) else None


def _response_error_message(response, default):
    payload = _response_json_object(response)
    if payload is None:
        return default

    message = payload.get("message")
    if not isinstance(message, str) or not message.strip():
        return default
    if _CREDENTIAL_MESSAGE_PATTERN.search(message):
        return default
    return message


def _image_content_type(data: bytes) -> str | None:
    """Detect raster image signatures without importing backend dependencies."""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data[:2] == b"BM":
        return "image/bmp"
    return None


class RAGFlowConnector:
    _MAX_DATASET_CACHE = 32
    _MAX_DATASET_ID_CACHE = 256
    _MAX_DOCUMENT_CACHE = 4096
    _CACHE_TTL = 300
    _DATASET_ID_CACHE_TTL = 60
    _DOCUMENT_SEARCH_CONCURRENCY = 8
    _MAX_DOCUMENT_SEARCH_DATASETS = 32
    _MAX_DOCUMENT_SEARCH_PAGES = 5
    # Keep in sync with api.utils.pagination_utils.REST_API_MAX_PAGE_SIZE.
    _REST_API_MAX_PAGE_SIZE = 100

    _dataset_metadata_cache: OrderedDict[str, tuple[dict, float | int]] = OrderedDict()  # "dataset_id" -> (metadata, expiry_ts)
    _document_metadata_cache: OrderedDict[tuple[str, str], tuple[dict, float | int]] = OrderedDict()

    def __init__(self, base_url: str, version="v1", auth_mode="api-key"):
        if auth_mode not in ("api-key", "workbuddy"):
            raise ValueError("Invalid MCP auth mode")
        self.auth_mode = auth_mode
        self.base_url = base_url
        self.version = version
        self.api_url = f"{self.base_url}/api/{self.version}"
        self._async_client = None
        self._dataset_id_cache: OrderedDict[bytes, tuple[tuple[str, ...], float]] = OrderedDict()
        self._dataset_id_inflight: dict[bytes, asyncio.Task[list[str]]] = {}
        self._dataset_id_tasks: dict[bytes, set[asyncio.Task[list[str]]]] = {}
        self._dataset_id_versions: dict[bytes, int] = {}

    async def _get_client(self):
        if self._async_client is None:
            self._async_client = httpx.AsyncClient(timeout=httpx.Timeout(60.0))
        return self._async_client

    async def close(self):
        inflight_tasks = {task for tasks in self._dataset_id_tasks.values() for task in tasks}
        self._dataset_id_inflight.clear()
        for task in inflight_tasks:
            task.cancel()
        if inflight_tasks:
            await asyncio.gather(*inflight_tasks, return_exceptions=True)
        self._dataset_id_tasks.clear()
        self._dataset_id_versions.clear()
        if self._async_client is not None:
            await self._async_client.aclose()
            self._async_client = None

    def _credential_headers(self, credential):
        return {"x-oneid-access-token": credential} if self.auth_mode == "workbuddy" else {"Authorization": f"Bearer {credential}"}

    def _require_workbuddy_response(self, response):
        if self.auth_mode != "workbuddy":
            return
        if response.status_code >= 400:
            raise Exception([types.TextContent(type="text", text=_response_error_message(response, "Knowledge base access failed."))])
        if "application/json" in response.headers.get("content-type", ""):
            payload = _response_json_object(response)
            if payload is None or payload.get("code") != 0:
                raise Exception([types.TextContent(type="text", text=_response_error_message(response, "Knowledge base access failed."))])

    async def _post(self, path, json=None, stream=False, files=None, api_key: str = ""):
        if not api_key:
            return None
        client = await self._get_client()
        res = await client.post(url=self.api_url + path, json=json, headers=self._credential_headers(api_key))
        self._require_workbuddy_response(res)
        if res.status_code in (401, 403):
            self._invalidate_cached_dataset_ids(api_key)
        return res

    async def _get(self, path, params=None, api_key: str = ""):
        if not api_key:
            return None
        client = await self._get_client()
        res = await client.get(url=self.api_url + path, params=params, headers=self._credential_headers(api_key))
        self._require_workbuddy_response(res)
        if res.status_code in (401, 403):
            self._invalidate_cached_dataset_ids(api_key)
        return res

    @staticmethod
    def _dataset_id_cache_key(api_key: str) -> bytes:
        return hashlib.sha256(api_key.encode()).digest()

    def _get_cached_dataset_ids(self, cache_key: bytes) -> list[str] | None:
        if self.auth_mode == "workbuddy":
            return None
        entry = self._dataset_id_cache.get(cache_key)
        if entry is None:
            return None

        dataset_ids, expires_at = entry
        if time.monotonic() >= expires_at:
            self._dataset_id_cache.pop(cache_key, None)
            if cache_key not in self._dataset_id_tasks:
                self._dataset_id_versions.pop(cache_key, None)
            return None

        self._dataset_id_cache.move_to_end(cache_key)
        return list(dataset_ids)

    def _set_cached_dataset_ids(self, cache_key: bytes, dataset_ids: list[str]) -> None:
        if self.auth_mode == "workbuddy":
            return
        self._dataset_id_cache[cache_key] = (tuple(dataset_ids), time.monotonic() + self._DATASET_ID_CACHE_TTL)
        self._dataset_id_cache.move_to_end(cache_key)
        if len(self._dataset_id_cache) > self._MAX_DATASET_ID_CACHE:
            evicted_key, _ = self._dataset_id_cache.popitem(last=False)
            if evicted_key not in self._dataset_id_tasks:
                self._dataset_id_versions.pop(evicted_key, None)

    def _invalidate_cached_dataset_ids(self, api_key: str) -> None:
        if not api_key:
            return

        cache_key = self._dataset_id_cache_key(api_key)
        self._dataset_id_cache.pop(cache_key, None)
        self._dataset_id_inflight.pop(cache_key, None)
        if cache_key not in self._dataset_id_tasks:
            self._dataset_id_versions.pop(cache_key, None)
        else:
            self._dataset_id_versions[cache_key] = self._dataset_id_versions.get(cache_key, 0) + 1

    def _is_cache_valid(self, ts):
        return time.time() < ts

    def _get_expiry_timestamp(self):
        offset = random.randint(-30, 30)
        return time.time() + self._CACHE_TTL + offset

    def _get_cached_dataset_metadata(self, dataset_id):
        if self.auth_mode == "workbuddy":
            return None
        entry = self._dataset_metadata_cache.get(dataset_id)
        if entry:
            data, ts = entry
            if self._is_cache_valid(ts):
                self._dataset_metadata_cache.move_to_end(dataset_id)
                return data
        return None

    def _set_cached_dataset_metadata(self, dataset_id, metadata):
        if self.auth_mode == "workbuddy":
            return
        self._dataset_metadata_cache[dataset_id] = (metadata, self._get_expiry_timestamp())
        self._dataset_metadata_cache.move_to_end(dataset_id)
        if len(self._dataset_metadata_cache) > self._MAX_DATASET_CACHE:
            self._dataset_metadata_cache.popitem(last=False)

    def _get_cached_document_metadata(self, dataset_id, document_id):
        if self.auth_mode == "workbuddy":
            return None
        cache_key = (dataset_id, document_id)
        entry = self._document_metadata_cache.get(cache_key)
        if entry:
            metadata, ts = entry
            if self._is_cache_valid(ts):
                self._document_metadata_cache.move_to_end(cache_key)
                return metadata
            self._document_metadata_cache.pop(cache_key, None)
        return None

    def _set_cached_document_metadata(self, dataset_id, document_id, metadata):
        if self.auth_mode == "workbuddy":
            return
        cache_key = (dataset_id, document_id)
        self._document_metadata_cache[cache_key] = (metadata, self._get_expiry_timestamp())
        self._document_metadata_cache.move_to_end(cache_key)
        if len(self._document_metadata_cache) > self._MAX_DOCUMENT_CACHE:
            self._document_metadata_cache.popitem(last=False)

    async def _fetch_datasets_page(
        self,
        *,
        api_key: str,
        page: int,
        page_size: int,
        orderby: str = "create_time",
        desc: bool = True,
        id: str | None = None,
        name: str | None = None,
    ):
        """Fetch one structured page of accessible datasets from the backend API."""
        params = {"page": page, "page_size": page_size, "orderby": orderby, "desc": desc}
        if id:
            params["id"] = id
        if name:
            params["name"] = name

        res = await self._get("/datasets", params, api_key=api_key)
        if not res or res.status_code != 200:
            raise Exception([types.TextContent(type="text", text=_response_error_message(res, "Cannot process this operation."))])

        res_json = _response_json_object(res)
        if res_json is None or type(res_json.get("code")) is not int:
            raise Exception([types.TextContent(type="text", text="Cannot process this operation.")])
        if res_json["code"] != 0:
            raise Exception([types.TextContent(type="text", text=_response_error_message(res, "Cannot process this operation."))])

        data = res_json.get("data")
        total = res_json.get("total", res_json.get("total_datasets"))
        if not isinstance(data, list) or (total is not None and type(total) is not int):
            raise Exception([types.TextContent(type="text", text="Cannot process this operation.")])
        if any(not isinstance(item, dict) or "id" not in item or "description" not in item for item in data):
            raise Exception([types.TextContent(type="text", text="Cannot process this operation.")])

        if total is not None:
            res_json["total"] = total
        return res_json

    async def _fetch_all_datasets(
        self,
        *,
        api_key: str,
        orderby: str = "create_time",
        desc: bool = True,
        id: str | None = None,
        name: str | None = None,
    ):
        """Fetch all accessible datasets without exceeding the REST API page-size limit."""
        datasets = []
        page = 1

        while True:
            logging.debug("fetching all /datasets page=%s page_size=%s", page, self._REST_API_MAX_PAGE_SIZE)
            res_json = await self._fetch_datasets_page(
                api_key=api_key,
                page=page,
                page_size=self._REST_API_MAX_PAGE_SIZE,
                orderby=orderby,
                desc=desc,
                id=id,
                name=name,
            )
            page_datasets = res_json.get("data", [])
            logging.debug("received %s datasets from page=%s", len(page_datasets), page)
            if not page_datasets:
                break

            datasets.extend(page_datasets)
            total = res_json.get("total")
            if total is not None and len(datasets) >= total:
                break

            page += 1

        return datasets

    async def list_datasets(self, *, api_key: str, page: int = 1, page_size: int = -1, orderby: str = "create_time", desc: bool = True, id: str | None = None, name: str | None = None):
        """Return accessible datasets as newline-delimited JSON for MCP tool descriptions."""
        if page_size == -1:
            datasets = await self._fetch_all_datasets(api_key=api_key, orderby=orderby, desc=desc, id=id, name=name)
        else:
            page_size = min(page_size, self._REST_API_MAX_PAGE_SIZE)
            res_json = await self._fetch_datasets_page(api_key=api_key, page=page, page_size=page_size, orderby=orderby, desc=desc, id=id, name=name)
            datasets = res_json["data"]

        result_list = []
        for data in datasets:
            d = {"description": data["description"], "id": data["id"]}
            result_list.append(json.dumps(d, ensure_ascii=False))
        return "\n".join(result_list)

    async def resolve_dataset_ids(self, *, api_key: str):
        """Resolve all accessible dataset IDs for MCP retrieval fallback."""
        if self.auth_mode == "workbuddy":
            datasets = await self._fetch_all_datasets(api_key=api_key)
            return list(dict.fromkeys(item["id"] for item in datasets))
        cache_key = self._dataset_id_cache_key(api_key)
        cached_dataset_ids = self._get_cached_dataset_ids(cache_key)
        if cached_dataset_ids is not None:
            return cached_dataset_ids

        task = self._dataset_id_inflight.get(cache_key)
        if task is None:
            cache_version = self._dataset_id_versions.get(cache_key, 0)
            task = asyncio.create_task(self._load_dataset_ids(api_key=api_key, cache_key=cache_key, cache_version=cache_version))
            self._dataset_id_inflight[cache_key] = task
            self._dataset_id_tasks.setdefault(cache_key, set()).add(task)

            def clear_inflight(completed_task):
                if not completed_task.cancelled():
                    completed_task.exception()
                if self._dataset_id_inflight.get(cache_key) is completed_task:
                    self._dataset_id_inflight.pop(cache_key, None)
                tasks = self._dataset_id_tasks.get(cache_key)
                if tasks is not None:
                    tasks.discard(completed_task)
                    if not tasks:
                        self._dataset_id_tasks.pop(cache_key, None)
                if cache_key not in self._dataset_id_cache and cache_key not in self._dataset_id_tasks:
                    self._dataset_id_versions.pop(cache_key, None)

            task.add_done_callback(clear_inflight)

        return list(await asyncio.shield(task))

    async def _load_dataset_ids(self, *, api_key: str, cache_key: bytes, cache_version: int) -> list[str]:
        logging.info("Resolving accessible dataset IDs for MCP retrieval")
        try:
            datasets = await self._fetch_all_datasets(api_key=api_key)
        except Exception as exc:
            logging.warning("resolve_dataset_ids failed to fetch /datasets error=%s", exc)
            raise

        dataset_ids = [data["id"] for data in datasets if data.get("id")]
        resolved = list(dict.fromkeys(dataset_ids))
        if self._dataset_id_versions.get(cache_key, 0) == cache_version:
            self._set_cached_dataset_ids(cache_key, resolved)
        logging.info("resolve_dataset_ids resolved %s accessible dataset IDs", len(resolved))
        return resolved

    @staticmethod
    def _directory_pagination(page, page_size, total):
        if type(total) is not int or total < 0:
            raise ValueError("Invalid directory total returned by backend.")
        return {
            "page": page,
            "page_size": page_size,
            "total": total,
            "total_pages": (total + page_size - 1) // page_size,
            "has_more": page * page_size < total,
        }

    async def list_knowledge_bases(self, *, api_key: str, page: int = 1, page_size: int = 20, name: str | None = None):
        """Return one authorized page without caching or enumerating other pages."""
        payload = await self._fetch_datasets_page(api_key=api_key, page=page, page_size=page_size, name=name, desc=False)
        pagination = self._directory_pagination(page, page_size, payload.get("total"))
        if any(not isinstance(item.get("id"), str) or not item["id"] for item in payload["data"]):
            raise ValueError("Invalid knowledge base entry returned by backend.")
        result = {
            "knowledge_bases": [
                {
                    "dataset_id": item["id"],
                    "name": item.get("name") or "",
                    "description": item.get("description") or "",
                    "document_count": item.get("document_count"),
                }
                for item in payload["data"]
            ],
            "pagination": pagination,
        }
        return [types.TextContent(type="text", text=json.dumps(result, ensure_ascii=False))]

    async def list_documents(self, *, api_key: str, dataset_id: str, page: int = 1, page_size: int = 20):
        """Browse documents without requiring a filename query or semantic search."""
        if not isinstance(dataset_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", dataset_id):
            raise ValueError("Invalid dataset_id.")
        response = await self._get(
            f"/datasets/{dataset_id}/documents",
            params={"page": page, "page_size": page_size, "orderby": "id", "desc": False},
            api_key=api_key,
        )
        payload = _response_json_object(response)
        if response is None or response.status_code != 200 or payload is None or type(payload.get("code")) is not int or payload["code"] != 0:
            raise ValueError(_response_error_message(response, "Cannot list documents."))
        data = payload.get("data")
        if not isinstance(data, dict) or not isinstance(data.get("docs"), list):
            raise TypeError("Invalid document list returned by backend.")
        pagination = self._directory_pagination(page, page_size, data.get("total"))
        documents = []
        for doc in data["docs"]:
            if not isinstance(doc, dict) or not isinstance(doc.get("id"), str) or not doc["id"] or doc.get("dataset_id", dataset_id) != dataset_id:
                raise ValueError("Invalid document entry returned by backend.")
            documents.append(
                {
                    "dataset_id": dataset_id,
                    "document_id": doc["id"],
                    "name": doc.get("name") or "",
                    "type": doc.get("type") or "",
                    "run": doc.get("run"),
                    "chunk_count": doc.get("chunk_count"),
                    "update_date": doc.get("update_date") or "",
                    # location is an object-storage key, not necessarily a source folder path.
                    "location": doc.get("location") or "",
                    "path": doc.get("path") or "",
                }
            )
        result = {"documents": documents, "pagination": pagination}
        return [types.TextContent(type="text", text=json.dumps(result, ensure_ascii=False))]

    async def search_documents(self, *, api_key: str, query: str, dataset_ids=None, metadata=None, limit: int = 20):
        """Find documents by filename without running semantic retrieval."""
        query = query.strip()
        if not query:
            raise Exception([types.TextContent(type="text", text="A non-empty filename query is required.")])
        if not dataset_ids:
            dataset_ids = await self.resolve_dataset_ids(api_key=api_key)
        dataset_ids = list(dict.fromkeys(dataset_ids))
        if len(dataset_ids) > self._MAX_DOCUMENT_SEARCH_DATASETS:
            raise Exception(
                [
                    types.TextContent(
                        type="text",
                        text=f"Too many datasets to search safely. Provide at most {self._MAX_DOCUMENT_SEARCH_DATASETS} dataset_ids.",
                    )
                ]
            )
        limit = max(1, min(limit, self._REST_API_MAX_PAGE_SIZE))
        filename_query = re.split(r"[/\\]", query)[-1]
        semaphore = asyncio.Semaphore(self._DOCUMENT_SEARCH_CONCURRENCY)
        query_name = filename_query.casefold()
        query_stem = query_name.rsplit(".", 1)[0]
        query_has_extension = "." in query_name
        normalized_query_path = query.replace("\\", "/").casefold()

        def document_rank(document):
            name = document["name"].casefold()
            name_stem = name.rsplit(".", 1)[0]
            location = document["location"].replace("\\", "/").casefold()
            if name == query_name or location == normalized_query_path or (not query_has_extension and name_stem == query_stem):
                return 0
            if name.startswith(query_name) or name_stem.startswith(query_stem) or location.endswith(normalized_query_path):
                return 1
            if query_name in name or normalized_query_path in location:
                return 2
            return 3

        def summarize_document(document, dataset_id):
            return {
                "dataset_id": document.get("dataset_id") or dataset_id,
                "document_id": document["id"],
                "name": document.get("name") or "",
                "location": document.get("location") or "",
                "type": document.get("type") or "",
                "chunk_count": document.get("chunk_count"),
                "update_date": document.get("update_date") or "",
                "meta_fields": document.get("meta_fields") or {},
            }

        def keep_best_documents(documents):
            documents.sort(key=lambda document: document["update_date"], reverse=True)
            documents.sort(key=document_rank)
            return documents[:limit]

        async def fetch_dataset_documents(dataset_id):
            documents = []
            total = 0
            page = 1
            scanned_documents = 0
            scan_truncated = False
            while True:
                params = {
                    "page": page,
                    "page_size": self._REST_API_MAX_PAGE_SIZE,
                    "keywords": filename_query,
                    "orderby": "update_time",
                    "desc": True,
                }
                if metadata:
                    params["metadata"] = json.dumps(metadata, ensure_ascii=False)
                async with semaphore:
                    res = await self._get(f"/datasets/{dataset_id}/documents", params=params, api_key=api_key)
                if not res or res.status_code != 200:
                    raise Exception([types.TextContent(type="text", text=_response_error_message(res, "Cannot process this operation."))])

                payload = _response_json_object(res)
                if payload is None or type(payload.get("code")) is not int:
                    raise Exception([types.TextContent(type="text", text="Cannot process this operation.")])
                if payload["code"] != 0:
                    raise Exception([types.TextContent(type="text", text=_response_error_message(res, "Cannot process this operation."))])

                data = payload.get("data")
                if not isinstance(data, dict) or type(data.get("total")) is not int or not isinstance(data.get("docs"), list):
                    raise Exception([types.TextContent(type="text", text="Cannot process this operation.")])
                page_documents = data["docs"]
                if any(not isinstance(document, dict) or not document.get("id") for document in page_documents):
                    raise Exception([types.TextContent(type="text", text="Cannot process this operation.")])

                total = data["total"]
                scanned_documents += len(page_documents)
                documents.extend(summarize_document(document, dataset_id) for document in page_documents)
                documents = keep_best_documents(documents)
                if not page_documents or scanned_documents >= total:
                    break
                if page >= self._MAX_DOCUMENT_SEARCH_PAGES:
                    scan_truncated = True
                    break
                page += 1

            return {"total": total, "documents": documents, "scan_truncated": scan_truncated}

        results = await asyncio.gather(*(fetch_dataset_documents(dataset_id) for dataset_id in dataset_ids))
        documents = []
        total_matches = 0
        scan_truncated = False
        for data in results:
            total_matches += data["total"]
            documents.extend(data["documents"])
            scan_truncated = scan_truncated or data["scan_truncated"]

        documents = keep_best_documents(documents)
        response = {
            "documents": documents,
            "total_matches": total_matches,
            "returned": len(documents),
            "query_info": {"query": query, "dataset_count": len(dataset_ids), "limit": limit, "scan_truncated": scan_truncated},
        }
        return [types.TextContent(type="text", text=json.dumps(response, ensure_ascii=False))]

    async def get_document_chunks(self, *, api_key: str, dataset_id: str, document_id: str, page: int = 1, page_size: int = 10):
        """Read a document's chunks in their backend-defined source order."""
        page_size = max(1, min(page_size, 50))
        params = {"page": page, "page_size": page_size}
        res = await self._get(f"/datasets/{dataset_id}/documents/{document_id}/chunks", params=params, api_key=api_key)
        if not res or res.status_code != 200:
            raise Exception([types.TextContent(type="text", text=_response_error_message(res, "Cannot process this operation."))])

        payload = _response_json_object(res)
        if payload is None or type(payload.get("code")) is not int:
            raise Exception([types.TextContent(type="text", text="Cannot process this operation.")])
        if payload["code"] != 0:
            raise Exception([types.TextContent(type="text", text=_response_error_message(res, "Cannot process this operation."))])

        data = payload.get("data")
        if not isinstance(data, dict) or type(data.get("total")) is not int or not isinstance(data.get("chunks"), list) or not isinstance(data.get("doc"), dict):
            raise Exception([types.TextContent(type="text", text="Cannot process this operation.")])
        if any(not isinstance(chunk, dict) for chunk in data["chunks"]):
            raise Exception([types.TextContent(type="text", text="Cannot process this operation.")])

        document = data["doc"]
        document_summary = {
            "document_id": document.get("document_id", document.get("id")),
            **{field: document[field] for field in ("name", "location", "type", "chunk_count", "token_count", "update_date", "meta_fields") if field in document},
        }
        chunk_fields = ("id", "content", "document_id", "dataset_id", "positions", "image_id", "important_keywords", "questions", "available")
        chunks = [{field: chunk[field] for field in chunk_fields if field in chunk} for chunk in data["chunks"]]
        response = {
            "document": document_summary,
            "chunks": chunks,
            "pagination": {
                "page": page,
                "page_size": page_size,
                "total_chunks": data["total"],
                "total_pages": (data["total"] + page_size - 1) // page_size,
            },
        }
        return [types.TextContent(type="text", text=json.dumps(response, ensure_ascii=False))]

    async def get_chunk_image(self, *, api_key: str, dataset_id: str, document_id: str, chunk_id: str):
        """Fetch one authorized chunk image and return native MCP image content."""
        default_error = "Cannot retrieve this chunk image."

        if not api_key:
            raise ValueError("RAGFlow API key is required.")
        if any(not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", value) for value in (dataset_id, document_id, chunk_id)):
            raise ValueError("Valid dataset_id, document_id, and chunk_id are required.")

        try:
            response = await self._get(f"/datasets/{dataset_id}/documents/{document_id}/chunks/{chunk_id}/image", api_key=api_key)
        except httpx.HTTPError:
            raise ValueError(default_error) from None
        if response is None or response.status_code != 200:
            message = _response_error_message(response, default_error)
            # Backend/proxy errors can contain storage URLs with credentials.
            if api_key in message or re.search(r"(?i)\b[a-z][a-z0-9+.-]*://", message):
                message = default_error
            raise ValueError(message)

        image_data = response.content
        if len(image_data) > MCP_IMAGE_MAX_BYTES:
            raise ValueError("Chunk image exceeds the MCP size limit.")
        content_type = response.headers.get("Content-Type", "").partition(";")[0].strip().lower()
        detected_type = _image_content_type(image_data)
        if detected_type is None or content_type != detected_type:
            raise ValueError(default_error)
        source = {"dataset_id": dataset_id, "document_id": document_id, "chunk_id": chunk_id}
        return [
            types.TextContent(type="text", text=json.dumps(source, ensure_ascii=False)),
            types.ImageContent(type="image", data=base64.b64encode(image_data).decode("ascii"), mimeType=detected_type),
        ]

    async def retrieval(
        self,
        *,
        api_key: str,
        dataset_ids,
        document_ids=None,
        question="",
        page=1,
        page_size=30,
        similarity_threshold=0.2,
        vector_similarity_weight=0.3,
        top_k=1024,
        rerank_id: str | None = None,
        keyword: bool = False,
        force_refresh: bool = False,
    ):
        if document_ids is None:
            document_ids = []

        if not dataset_ids:
            logging.info("MCP retrieval omitted dataset_ids; resolving accessible datasets")
            dataset_ids = await self.resolve_dataset_ids(api_key=api_key)
            if not dataset_ids:
                logging.info("MCP retrieval found no accessible datasets for current user")
                raise Exception([types.TextContent(type="text", text="No accessible datasets found.")])

        data_json = {
            "page": page,
            "page_size": page_size,
            "similarity_threshold": similarity_threshold,
            "vector_similarity_weight": vector_similarity_weight,
            "top_k": top_k,
            "rerank_id": rerank_id,
            "keyword": keyword,
            "question": question,
            "dataset_ids": dataset_ids,
            "document_ids": document_ids,
        }
        # Send a POST request to the backend service (using requests library as an example, actual implementation may vary)
        res = await self._post("/retrieval", json=data_json, api_key=api_key)
        if not res or res.status_code != 200:
            raise Exception([types.TextContent(type="text", text=_response_error_message(res, "Cannot process this operation."))])

        res_json = _response_json_object(res)
        if res_json is None or type(res_json.get("code")) is not int:
            raise Exception([types.TextContent(type="text", text="Cannot process this operation.")])
        if res_json["code"] == 0:
            data = res_json.get("data")
            if not isinstance(data, dict):
                raise Exception([types.TextContent(type="text", text="Cannot process this operation.")])
            chunks_data = data.get("chunks", [])
            if not isinstance(chunks_data, list) or any(not isinstance(chunk, dict) for chunk in chunks_data):
                raise Exception([types.TextContent(type="text", text="Cannot process this operation.")])
            if any(field in data and type(data[field]) is not int for field in ("page", "page_size", "total")):
                raise Exception([types.TextContent(type="text", text="Cannot process this operation.")])
            chunks = []

            # Cache metadata only for documents present in the retrieval result.
            document_cache, dataset_cache = await self._get_document_metadata_cache(
                chunks_data=chunks_data,
                api_key=api_key,
                force_refresh=force_refresh,
            )

            # Process chunks with enhanced field mapping including per-chunk metadata
            for chunk_data in chunks_data:
                enhanced_chunk = self._map_chunk_fields(chunk_data, dataset_cache, document_cache)
                chunks.append(enhanced_chunk)

            # Build structured response (no longer need response-level document_metadata)
            response = {
                "chunks": chunks,
                "pagination": {
                    "page": data.get("page", page),
                    "page_size": data.get("page_size", page_size),
                    "total_chunks": data.get("total", len(chunks)),
                    "total_pages": (data.get("total", len(chunks)) + page_size - 1) // page_size,
                },
                "query_info": {
                    "question": question,
                    "similarity_threshold": similarity_threshold,
                    "vector_weight": vector_similarity_weight,
                    "keyword_search": keyword,
                    "dataset_count": len(dataset_ids),
                },
            }

            return [types.TextContent(type="text", text=json.dumps(response, ensure_ascii=False))]

        raise Exception([types.TextContent(type="text", text=_response_error_message(res, "Cannot process this operation."))])

    async def _get_document_metadata_cache(self, *, chunks_data, api_key: str, force_refresh=False):
        """Return metadata for documents present in the retrieval result."""
        document_cache = {}
        dataset_cache = {}
        document_ids_by_dataset = {}

        for chunk in chunks_data:
            dataset_id = chunk.get("dataset_id") or chunk.get("kb_id")
            if not dataset_id:
                continue
            document_ids_by_dataset.setdefault(dataset_id, [])
            document_id = chunk.get("document_id")
            if document_id and document_id not in document_ids_by_dataset[dataset_id]:
                document_ids_by_dataset[dataset_id].append(document_id)

        try:
            for dataset_id, document_ids in document_ids_by_dataset.items():
                dataset_meta = None if force_refresh else self._get_cached_dataset_metadata(dataset_id)
                if not dataset_meta:
                    # First get dataset info for name
                    dataset_res = await self._get("/datasets", {"id": dataset_id, "page_size": 1}, api_key=api_key)
                    if dataset_res and dataset_res.status_code == 200:
                        dataset_data = dataset_res.json()
                        if dataset_data.get("code") == 0 and dataset_data.get("data"):
                            dataset_info = dataset_data["data"][0]
                            dataset_meta = {"name": dataset_info.get("name", "Unknown"), "description": dataset_info.get("description", "")}
                            self._set_cached_dataset_metadata(dataset_id, dataset_meta)
                if dataset_meta:
                    dataset_cache[dataset_id] = dataset_meta

                missing_document_ids = []
                for document_id in document_ids:
                    metadata = None if force_refresh else self._get_cached_document_metadata(dataset_id, document_id)
                    if metadata is None:
                        missing_document_ids.append(document_id)
                    else:
                        document_cache[document_id] = metadata

                for start in range(0, len(missing_document_ids), self._REST_API_MAX_PAGE_SIZE):
                    batch = missing_document_ids[start : start + self._REST_API_MAX_PAGE_SIZE]
                    params = [("page_size", len(batch)), *[("ids", document_id) for document_id in batch]]
                    docs_res = await self._get(f"/datasets/{dataset_id}/documents", params=params, api_key=api_key)
                    if not docs_res or docs_res.status_code != 200:
                        continue

                    docs_data = _response_json_object(docs_res)
                    if not docs_data or docs_data.get("code") != 0:
                        continue

                    for doc in docs_data.get("data", {}).get("docs", []):
                        document_id = doc.get("id")
                        if not document_id:
                            continue
                        metadata = {
                            "document_id": document_id,
                            "name": doc.get("name", ""),
                            "location": doc.get("location", ""),
                            "type": doc.get("type", ""),
                            "size": doc.get("size"),
                            "chunk_count": doc.get("chunk_count"),
                            "create_date": doc.get("create_date", ""),
                            "update_date": doc.get("update_date", ""),
                            "token_count": doc.get("token_count"),
                            "thumbnail": doc.get("thumbnail", ""),
                            "dataset_id": doc.get("dataset_id", dataset_id),
                            "meta_fields": doc.get("meta_fields", {}),
                        }
                        document_cache[document_id] = metadata
                        self._set_cached_document_metadata(dataset_id, document_id, metadata)

        except Exception as e:
            # Gracefully handle metadata cache failures
            if self.auth_mode == "workbuddy":
                raise
            logging.error(f"Problem building the document metadata cache: {str(e)}")
            pass

        return document_cache, dataset_cache

    def _map_chunk_fields(self, chunk_data, dataset_cache, document_cache):
        """Preserve all original API fields and add per-chunk document metadata"""
        # Start with ALL raw data from API (preserve everything like original version)
        mapped = dict(chunk_data)

        # Add dataset name enhancement
        dataset_id = chunk_data.get("dataset_id") or chunk_data.get("kb_id")
        if dataset_id and dataset_id in dataset_cache:
            mapped["dataset_name"] = dataset_cache[dataset_id]["name"]
        else:
            mapped["dataset_name"] = "Unknown"

        # Add document name convenience field
        mapped["document_name"] = chunk_data.get("document_keyword", "")

        # Add per-chunk document metadata
        document_id = chunk_data.get("document_id")
        if document_id and document_id in document_cache:
            mapped["document_metadata"] = document_cache[document_id]

        return mapped


class RAGFlowCtx:
    def __init__(self, connector: RAGFlowConnector):
        self.conn = connector


@asynccontextmanager
async def sse_lifespan(server: Server) -> AsyncIterator[dict]:
    ctx = RAGFlowCtx(RAGFlowConnector(base_url=BASE_URL, auth_mode=AUTH_MODE))

    logging.info("Legacy SSE application started with StreamableHTTP session manager!")
    try:
        yield {"ragflow_ctx": ctx}
    finally:
        await ctx.conn.close()
        logging.info("Legacy SSE application shutting down...")


app = Server("ragflow-mcp-server", lifespan=sse_lifespan)
AUTH_TOKEN_STATE_KEY = "ragflow_auth_token"


def _to_text(value: Any) -> str:
    if isinstance(value, bytes):
        return value.decode(errors="ignore")
    return str(value)


def _extract_token_from_headers(headers: Any) -> str | None:
    if not headers or not hasattr(headers, "get"):
        return None

    if AUTH_MODE == "workbuddy":
        if hasattr(headers, "getlist"):
            values = headers.getlist("x-oneid-access-token")
        else:
            values = [value for key, value in headers.items() if _to_text(key).lower() == "x-oneid-access-token"]
        if len(values) != 1:
            return None
        token = _to_text(values[0])
        return token if token and len(token) <= 32768 and "," not in token and not any(c.isspace() for c in token) else None

    auth_keys = ("authorization", "Authorization", b"authorization", b"Authorization")
    for key in auth_keys:
        auth = headers.get(key)
        if not auth:
            continue
        auth_text = _to_text(auth).strip()
        if auth_text.lower().startswith("bearer "):
            token = auth_text[7:].strip()
            if token:
                return token

    api_key_keys = ("api_key", "x-api-key", "Api-Key", "X-API-Key", b"api_key", b"x-api-key", b"Api-Key", b"X-API-Key")
    for key in api_key_keys:
        token = headers.get(key)
        if token:
            token_text = _to_text(token).strip()
            if token_text:
                return token_text

    return None


def _extract_token_from_request(request: Any) -> str | None:
    if request is None:
        return None

    state = getattr(request, "state", None)
    if state is not None:
        token = getattr(state, AUTH_TOKEN_STATE_KEY, None)
        if token:
            return token

    token = _extract_token_from_headers(getattr(request, "headers", None))
    if token and state is not None:
        setattr(state, AUTH_TOKEN_STATE_KEY, token)

    return token


def with_api_key(required: bool = True):
    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            ctx = app.request_context
            ragflow_ctx = ctx.lifespan_context.get("ragflow_ctx")
            if not ragflow_ctx:
                raise ValueError("Get RAGFlow Context failed")

            connector = ragflow_ctx.conn
            api_key = HOST_API_KEY

            if MODE == LaunchMode.HOST:
                api_key = _extract_token_from_request(getattr(ctx, "request", None)) or ""
                if required and not api_key:
                    raise ValueError("RAGFlow API key or Bearer token is required.")

            return await func(*args, connector=connector, api_key=api_key, **kwargs)

        return wrapper

    return decorator


@app.list_tools()
@with_api_key(required=True)
async def list_tools(*, connector: RAGFlowConnector, api_key: str) -> list[types.Tool]:
    return [
        types.Tool(
            name="ragflow_retrieval",
            description=(
                "Semantically retrieve relevant chunks when you do not know which document contains the answer. "
                "To browse what a knowledge base contains, use list_knowledge_bases then list_documents. "
                "To locate a known or named file, call search_documents first. After obtaining a document_id, use "
                "get_document_chunks to read the original text instead of repeatedly calling this tool. You can "
                "optionally specify dataset_ids to search only specific datasets, or omit dataset_ids entirely to "
                "search across ALL available datasets. You can also optionally specify document_ids to search within "
                "specific documents. When dataset_ids is not provided or is empty, the system will automatically "
                "search across all available datasets. If a relevant chunk has a non-empty image_id, call "
                "get_chunk_image only when the user requests the image or it helps explain the answer."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "dataset_ids": {"type": "array", "items": {"type": "string"}, "description": "Optional array of dataset IDs to search. If not provided or empty, all datasets will be searched."},
                    "document_ids": {"type": "array", "items": {"type": "string"}, "description": "Optional array of document IDs to search within."},
                    "question": {"type": "string", "description": "The question or query to search for."},
                    "page": {
                        "type": "integer",
                        "description": "Page number for pagination",
                        "default": 1,
                        "minimum": 1,
                    },
                    "page_size": {
                        "type": "integer",
                        "description": "Number of results to return per page (default: 10, max recommended: 50 to avoid token limits)",
                        "default": 10,
                        "minimum": 1,
                        "maximum": 100,
                    },
                    "similarity_threshold": {
                        "type": "number",
                        "description": "Minimum similarity threshold for results",
                        "default": 0.2,
                        "minimum": 0.0,
                        "maximum": 1.0,
                    },
                    "vector_similarity_weight": {
                        "type": "number",
                        "description": "Weight for vector similarity vs term similarity",
                        "default": 0.3,
                        "minimum": 0.0,
                        "maximum": 1.0,
                    },
                    "keyword": {
                        "type": "boolean",
                        "description": "Enable keyword-based search",
                        "default": False,
                    },
                    "top_k": {
                        "type": "integer",
                        "description": "Maximum results to consider before ranking",
                        "default": 1024,
                        "minimum": 1,
                        "maximum": 1024,
                    },
                    "rerank_id": {
                        "type": "string",
                        "description": "Optional reranking model identifier",
                    },
                    "force_refresh": {
                        "type": "boolean",
                        "description": "Set to true only if fresh dataset and document metadata is explicitly required. Otherwise, cached metadata is used (default: false).",
                        "default": False,
                    },
                },
                "required": ["question"],
            },
        ),
        types.Tool(
            name="search_documents",
            description=(
                "Find documents by filename keyword without semantic content retrieval. Use this when the user names or "
                "describes a specific file; use list_knowledge_bases and list_documents for browsing without a filename. "
                "The result returns dataset_id and document_id; then call "
                "get_document_chunks to read that document. Do not repeatedly call ragflow_retrieval after a document "
                "has been identified. Omit dataset_ids to search all datasets accessible to the current API key."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Filename keyword or partial filename to find.", "minLength": 1},
                    "dataset_ids": {"type": "array", "items": {"type": "string"}, "description": "Optional dataset IDs to search. If omitted or empty, all accessible datasets are searched."},
                    "metadata": {"type": "object", "description": "Optional exact document metadata filters. Values for the same key may be arrays."},
                    "limit": {"type": "integer", "description": "Maximum number of documents to return across all datasets.", "default": 20, "minimum": 1, "maximum": 100},
                },
                "required": ["query"],
            },
        ),
        types.Tool(
            name="get_document_chunks",
            description=(
                "Read chunks from one identified document in original document order, without semantic reranking. Use "
                "this after search_documents or ragflow_retrieval has returned dataset_id and document_id. Continue "
                "with the next page until all chunks needed for the answer have been read. If a relevant chunk has a "
                "non-empty image_id, use get_chunk_image when its image is needed for the answer."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "dataset_id": {"type": "string", "description": "Dataset ID returned with the document."},
                    "document_id": {"type": "string", "description": "Document ID to read."},
                    "page": {"type": "integer", "description": "Page number for sequential reading.", "default": 1, "minimum": 1},
                    "page_size": {"type": "integer", "description": "Chunks per page.", "default": 10, "minimum": 1, "maximum": 50},
                },
                "required": ["dataset_id", "document_id"],
            },
        ),
        types.Tool(
            name="get_chunk_image",
            description=(
                "Fetch one image associated with an authorized chunk and return native MCP image content with its source IDs. "
                "Use dataset_id, document_id, and the chunk ID (id or chunk_id) from ragflow_retrieval or get_document_chunks. "
                "Call only for a relevant chunk with a non-empty image_id when the user asks to see the image, screenshot, "
                "diagram, or chart, or when it helps explain the answer. Fetch images on demand; do not fetch every result "
                "or repeatedly fetch the same chunk within one answer. Include the image's source when presenting it. "
                "Authorization is handled by the server; never put credentials in tool arguments or display an authenticated image URL."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "dataset_id": {"type": "string", "description": "Dataset ID returned with the chunk.", "minLength": 1, "maxLength": 128, "pattern": "^[A-Za-z0-9_-]+$"},
                    "document_id": {"type": "string", "description": "Document ID returned with the chunk.", "minLength": 1, "maxLength": 128, "pattern": "^[A-Za-z0-9_-]+$"},
                    "chunk_id": {"type": "string", "description": "The chunk's id or chunk_id field, not image_id.", "minLength": 1, "maxLength": 128, "pattern": "^[A-Za-z0-9_-]+$"},
                },
                "required": ["dataset_id", "document_id", "chunk_id"],
                "additionalProperties": False,
            },
        ),
        types.Tool(
            name="list_knowledge_bases",
            description=(
                "List knowledge bases accessible to the current user, with IDs, names, descriptions and document counts. "
                "Use this first to find a named knowledge base or discover available libraries. The optional name is an "
                "exact name filter; omit it to browse. Use list_documents for a preview, or export_document_catalog "
                "with the chosen dataset_id when the user wants all documents or a complete directory."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "name": {"type": "string", "minLength": 1, "description": "Optional exact knowledge base name."},
                    "page": {"type": "integer", "default": 1, "minimum": 1},
                    "page_size": {"type": "integer", "default": 20, "minimum": 1, "maximum": 100},
                },
                "additionalProperties": False,
            },
        ),
        types.Tool(
            name="list_documents",
            description=(
                "List one page of documents in a knowledge base, without requiring a search query. Use dataset_id from "
                "list_knowledge_bases. Returns document IDs, names, types, parsing status, and pagination. Default to one "
                "preview page; fetch more only when needed. For a COMPLETE directory, use export_document_catalog "
                "instead of sending all pages into context. One page is not the full catalog. path is empty when "
                "no source path is available; location is a storage key, not a folder hierarchy. Use get_document_chunks "
                "to read a selected document, or ragflow_retrieval with dataset_ids to answer a content question."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "dataset_id": {"type": "string", "minLength": 1, "maxLength": 128, "pattern": "^[A-Za-z0-9_-]+$", "description": "Knowledge base ID from list_knowledge_bases."},
                    "page": {"type": "integer", "default": 1, "minimum": 1},
                    "page_size": {"type": "integer", "default": 20, "minimum": 1, "maximum": 100},
                },
                "required": ["dataset_id"],
                "additionalProperties": False,
            },
        ),
        types.Tool(
            name="export_document_catalog",
            description=(
                "Export a COMPLETE Excel document catalog for one authorized knowledge base. Use this when the user "
                "asks which/all papers or documents a library contains, especially for large libraries; first use "
                "list_knowledge_bases to obtain dataset_id. Call with dataset_id to start. If status is processing, "
                "wait retry_after_seconds and call again with the same dataset_id and export_id until ready or failed. "
                "Do not start a new export on each poll. Ready returns document_count, a small preview and download_url "
                "valid for 15 minutes. Download the Excel into the WorkBuddy workspace and present the verified file "
                "with the client's file tool (such as present_files), or provide the download link if unavailable. "
                "The URL grants access to this file until expiry; do not publish it outside the user's conversation. "
                "Do not put the full catalog or base64 into the answer; show total and clearly labelled preview. "
                "This exports metadata only, not document bodies or chapter tables of contents."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "dataset_id": {"type": "string", "minLength": 1, "maxLength": 128, "pattern": "^[A-Za-z0-9_-]+$", "description": "Knowledge base ID returned by list_knowledge_bases."},
                    "export_id": {
                        "type": "string",
                        "minLength": 1,
                        "maxLength": 64,
                        "pattern": "^[A-Za-z0-9_-]+$",
                        "description": "Omit to start; use the returned export_id to poll the same export.",
                    },
                },
                "required": ["dataset_id"],
                "additionalProperties": False,
            },
        ),
    ]


@app.call_tool()
@with_api_key(required=True)
async def call_tool(
    name: str,
    arguments: dict,
    *,
    connector: RAGFlowConnector,
    api_key: str,
) -> list[types.TextContent | types.ImageContent | types.EmbeddedResource]:
    if TOOL_CALL_COUNTS is not None:
        TOOL_CALL_COUNTS.increment(name)
    if name == "export_document_catalog":
        if CATALOG_EXPORTS is None:
            raise CatalogExportError("Catalog export service is unavailable.")
        return await CATALOG_EXPORTS.request(
            connector,
            api_key=api_key,
            dataset_id=arguments.get("dataset_id", ""),
            export_id=arguments.get("export_id"),
            base_url=_catalog_base_url(),
        )
    if name == "list_knowledge_bases":
        return await connector.list_knowledge_bases(
            api_key=api_key,
            page=arguments.get("page", 1),
            page_size=arguments.get("page_size", 20),
            name=arguments.get("name"),
        )
    if name == "list_documents":
        return await connector.list_documents(
            api_key=api_key,
            dataset_id=arguments.get("dataset_id", ""),
            page=arguments.get("page", 1),
            page_size=arguments.get("page_size", 20),
        )
    if name == "ragflow_retrieval":
        document_ids = arguments.get("document_ids", [])
        dataset_ids = arguments.get("dataset_ids", [])
        question = arguments.get("question", "")
        page = arguments.get("page", 1)
        page_size = arguments.get("page_size", 10)
        similarity_threshold = arguments.get("similarity_threshold", 0.2)
        vector_similarity_weight = arguments.get("vector_similarity_weight", 0.3)
        keyword = arguments.get("keyword", False)
        top_k = arguments.get("top_k", 1024)
        rerank_id = arguments.get("rerank_id")
        force_refresh = arguments.get("force_refresh", False)

        return await connector.retrieval(
            api_key=api_key,
            dataset_ids=dataset_ids,
            document_ids=document_ids,
            question=question,
            page=page,
            page_size=page_size,
            similarity_threshold=similarity_threshold,
            vector_similarity_weight=vector_similarity_weight,
            keyword=keyword,
            top_k=top_k,
            rerank_id=rerank_id,
            force_refresh=force_refresh,
        )
    if name == "search_documents":
        return await connector.search_documents(
            api_key=api_key,
            query=arguments.get("query", ""),
            dataset_ids=arguments.get("dataset_ids", []),
            metadata=arguments.get("metadata"),
            limit=arguments.get("limit", 20),
        )
    if name == "get_document_chunks":
        return await connector.get_document_chunks(
            api_key=api_key,
            dataset_id=arguments.get("dataset_id", ""),
            document_id=arguments.get("document_id", ""),
            page=arguments.get("page", 1),
            page_size=arguments.get("page_size", 10),
        )
    if name == "get_chunk_image":
        return await connector.get_chunk_image(
            api_key=api_key,
            dataset_id=arguments.get("dataset_id", ""),
            document_id=arguments.get("document_id", ""),
            chunk_id=arguments.get("chunk_id", ""),
        )
    raise ValueError(f"Tool not found: {name}")


def create_starlette_app():
    if AUTH_MODE == "workbuddy" and (TRANSPORT_SSE_ENABLED or not TRANSPORT_STREAMABLE_HTTP_ENABLED):
        raise ValueError("WorkBuddy requires Streamable HTTP only; disable SSE")
    if AUTH_MODE == "workbuddy" and MODE != LaunchMode.HOST:
        raise ValueError("WorkBuddy authentication requires host mode")
    exports = CatalogExports()
    routes = [Route("/mcp/catalog-download/{token}", endpoint=exports.download, methods=["GET"])]
    access_logger = logging.getLogger("uvicorn.access")
    if not any(isinstance(item, CatalogDownloadLogFilter) for item in access_logger.filters):
        access_logger.addFilter(CatalogDownloadLogFilter())
    middleware = None
    if MODE == LaunchMode.HOST:
        from starlette.types import ASGIApp, Receive, Scope, Send

        class AuthMiddleware:
            def __init__(self, app: ASGIApp):
                self.app = app

            async def __call__(self, scope: Scope, receive: Receive, send: Send):
                if scope["type"] != "http":
                    await self.app(scope, receive, send)
                    return

                path = scope["path"]
                if path.startswith("/mcp/catalog-download/") and scope["method"] in ("GET", "HEAD"):
                    await self.app(scope, receive, send)
                    return

                if path.startswith("/messages/") or path.startswith("/sse") or path.startswith("/mcp"):
                    from starlette.datastructures import Headers

                    headers = Headers(raw=scope["headers"]) if AUTH_MODE == "workbuddy" else dict(scope["headers"])
                    token = _extract_token_from_headers(headers)

                    if not token:
                        response = JSONResponse({"error": "Missing or invalid authorization header"}, status_code=401)
                        await response(scope, receive, send)
                        return
                    scope.setdefault("state", {})[AUTH_TOKEN_STATE_KEY] = token

                await self.app(scope, receive, send)

        middleware = [Middleware(AuthMiddleware)]

    # Add SSE routes if enabled
    if TRANSPORT_SSE_ENABLED:
        from mcp.server.sse import SseServerTransport

        sse = SseServerTransport("/messages/")

        async def handle_sse(request):
            async with sse.connect_sse(request.scope, request.receive, request._send) as streams:
                await app.run(streams[0], streams[1], app.create_initialization_options(experimental_capabilities={"headers": dict(request.headers)}))
            return Response()

        routes.extend(
            [
                Route("/sse", endpoint=handle_sse, methods=["GET"]),
                Mount("/messages/", app=sse.handle_post_message),
            ]
        )

    # Add streamable HTTP route if enabled
    streamablehttp_lifespan = None
    if TRANSPORT_STREAMABLE_HTTP_ENABLED:
        from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
        from mcp.server.transport_security import TransportSecuritySettings
        from starlette.types import Receive, Scope, Send

        session_manager = StreamableHTTPSessionManager(
            app=app,
            event_store=None,
            json_response=JSON_RESPONSE,
            stateless=True,
            security_settings=TransportSecuritySettings(
                enable_dns_rebinding_protection=True,
                allowed_hosts=STREAMABLE_HTTP_ALLOWED_HOSTS,
                allowed_origins=STREAMABLE_HTTP_ALLOWED_ORIGINS,
            ),
        )

        class StreamableHTTPEntry:
            async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
                await session_manager.handle_request(scope, receive, send)

        streamable_http_entry = StreamableHTTPEntry()

        @asynccontextmanager
        async def streamablehttp_lifespan(app: Starlette) -> AsyncIterator[None]:
            async with session_manager.run():
                logging.info("StreamableHTTP application started with StreamableHTTP session manager!")
                try:
                    yield
                finally:
                    logging.info("StreamableHTTP application shutting down...")

        routes.extend(
            [
                Route("/mcp", endpoint=streamable_http_entry, methods=["GET", "POST", "DELETE"]),
                Mount("/mcp", app=streamable_http_entry),
            ]
        )

    @asynccontextmanager
    async def application_lifespan(starlette_app):
        global TOOL_CALL_COUNTS, CATALOG_EXPORTS
        try:
            async with AsyncExitStack() as stack:
                CATALOG_EXPORTS = await stack.enter_async_context(exports)
                if TOOL_COUNTS_FILE is not None:
                    TOOL_CALL_COUNTS = await stack.enter_async_context(ToolCallCounts(TOOL_COUNTS_FILE))
                if streamablehttp_lifespan is not None:
                    await stack.enter_async_context(streamablehttp_lifespan(starlette_app))
                yield
        finally:
            TOOL_CALL_COUNTS = None
            CATALOG_EXPORTS = None

    return Starlette(
        debug=False,
        routes=routes,
        middleware=middleware,
        lifespan=application_lifespan,
    )


@click.command()
@click.option("--base-url", type=str, default="http://127.0.0.1:9380", help="API base URL for RAGFlow backend")
@click.option("--host", type=str, default="127.0.0.1", help="Host to bind the RAGFlow MCP server")
@click.option("--port", type=int, default=9382, help="Port to bind the RAGFlow MCP server")
@click.option(
    "--mode",
    type=click.Choice(["self-host", "host"]),
    default="self-host",
    help=("Launch mode:\n  self-host: run MCP for a single tenant (requires --api-key)\n  host: multi-tenant mode, users must provide Authorization headers"),
)
@click.option("--api-key", type=str, default="", help="API key to use when in self-host mode")
@click.option(
    "--transport-sse-enabled/--no-transport-sse-enabled",
    default=True,
    help="Enable or disable legacy SSE transport mode (default: enabled)",
)
@click.option(
    "--transport-streamable-http-enabled/--no-transport-streamable-http-enabled",
    default=True,
    help="Enable or disable streamable-http transport mode (default: enabled)",
)
@click.option(
    "--json-response/--no-json-response",
    default=True,
    help="Enable or disable JSON response mode for streamable-http (default: enabled)",
)
def main(base_url, host, port, mode, api_key, transport_sse_enabled, transport_streamable_http_enabled, json_response):
    import os

    import uvicorn
    from dotenv import load_dotenv

    load_dotenv()

    def parse_bool_flag(key: str, default: bool) -> bool:
        val = os.environ.get(key, str(default))
        return str(val).strip().lower() in ("1", "true", "yes", "on")

    global BASE_URL, HOST, PORT, MODE, HOST_API_KEY, TRANSPORT_SSE_ENABLED, TRANSPORT_STREAMABLE_HTTP_ENABLED, JSON_RESPONSE
    global STREAMABLE_HTTP_ALLOWED_HOSTS, STREAMABLE_HTTP_ALLOWED_ORIGINS, AUTH_MODE, TOOL_COUNTS_FILE
    AUTH_MODE = os.environ.get("RAGFLOW_MCP_AUTH_MODE", "api-key")
    if AUTH_MODE not in ("api-key", "workbuddy"):
        raise click.UsageError("Invalid MCP authentication mode")
    BASE_URL = os.environ.get("RAGFLOW_MCP_BASE_URL", base_url)
    HOST = os.environ.get("RAGFLOW_MCP_HOST", host)
    PORT = os.environ.get("RAGFLOW_MCP_PORT", str(port))
    TOOL_COUNTS_FILE = Path(os.environ.get("RAGFLOW_MCP_TOOL_COUNTS_FILE", str(Path(__file__).resolve().parents[2] / "logs" / f"mcp-tool-counts-{PORT}.json")))
    MODE = os.environ.get("RAGFLOW_MCP_LAUNCH_MODE", mode)
    HOST_API_KEY = os.environ.get("RAGFLOW_MCP_HOST_API_KEY", api_key)
    TRANSPORT_SSE_ENABLED = parse_bool_flag("RAGFLOW_MCP_TRANSPORT_SSE_ENABLED", transport_sse_enabled)
    TRANSPORT_STREAMABLE_HTTP_ENABLED = parse_bool_flag("RAGFLOW_MCP_TRANSPORT_STREAMABLE_ENABLED", transport_streamable_http_enabled)
    JSON_RESPONSE = parse_bool_flag("RAGFLOW_MCP_JSON_RESPONSE", json_response)
    STREAMABLE_HTTP_ALLOWED_HOSTS = [value.strip() for value in os.environ.get("RAGFLOW_MCP_ALLOWED_HOSTS", ",".join(STREAMABLE_HTTP_ALLOWED_HOSTS)).split(",") if value.strip()]
    STREAMABLE_HTTP_ALLOWED_ORIGINS = [value.strip() for value in os.environ.get("RAGFLOW_MCP_ALLOWED_ORIGINS", ",".join(STREAMABLE_HTTP_ALLOWED_ORIGINS)).split(",") if value.strip()]

    if AUTH_MODE == "workbuddy" and MODE != LaunchMode.HOST:
        raise click.UsageError("WorkBuddy authentication requires --mode=host")
    if MODE == LaunchMode.SELF_HOST and not HOST_API_KEY:
        raise click.UsageError("--api-key is required when --mode is 'self-host'")

    if not TRANSPORT_STREAMABLE_HTTP_ENABLED and JSON_RESPONSE:
        JSON_RESPONSE = False

    print(
        r"""
__  __  ____ ____       ____  _____ ______     _______ ____
|  \/  |/ ___|  _ \     / ___|| ____|  _ \ \   / / ____|  _ \
| |\/| | |   | |_) |    \___ \|  _| | |_) \ \ / /|  _| | |_) |
| |  | | |___|  __/      ___) | |___|  _ < \ V / | |___|  _ <
|_|  |_|\____|_|        |____/|_____|_| \_\ \_/  |_____|_| \_\
        """,
        flush=True,
    )
    print(f"MCP launch mode: {MODE}", flush=True)
    print(f"MCP host: {HOST}", flush=True)
    print(f"MCP port: {PORT}", flush=True)
    print(f"MCP base_url: {BASE_URL}", flush=True)

    if not any([TRANSPORT_SSE_ENABLED, TRANSPORT_STREAMABLE_HTTP_ENABLED]):
        print("At least one transport should be enabled, enable streamable-http automatically", flush=True)
        TRANSPORT_STREAMABLE_HTTP_ENABLED = True

    if TRANSPORT_SSE_ENABLED:
        print("SSE transport enabled: yes", flush=True)
        print("SSE endpoint available at /sse", flush=True)
    else:
        print("SSE transport enabled: no", flush=True)

    if TRANSPORT_STREAMABLE_HTTP_ENABLED:
        print("Streamable HTTP transport enabled: yes", flush=True)
        print("Streamable HTTP endpoint available at /mcp", flush=True)
        if JSON_RESPONSE:
            print("Streamable HTTP mode: JSON response enabled", flush=True)
        else:
            print("Streamable HTTP mode: SSE over HTTP enabled", flush=True)
    else:
        print("Streamable HTTP transport enabled: no", flush=True)
        if JSON_RESPONSE:
            print("Warning: --json-response ignored because streamable transport is disabled.", flush=True)

    uvicorn.run(
        create_starlette_app(),
        host=HOST,
        port=int(PORT),
    )


if __name__ == "__main__":
    """
    Launch examples:

    1. Self-host mode with both SSE and Streamable HTTP (in JSON response mode) enabled (default):
        uv run mcp/server/server.py --host=127.0.0.1 --port=9382 \
            --base-url=http://127.0.0.1:9380 \
            --mode=self-host --api-key=ragflow-xxxxx

    2. Host mode (multi-tenant, clients must provide Authorization headers):
        uv run mcp/server/server.py --host=127.0.0.1 --port=9382 \
            --base-url=http://127.0.0.1:9380 \
            --mode=host

    3. Disable legacy SSE (only streamable HTTP will be active):
        uv run mcp/server/server.py --no-transport-sse-enabled \
            --mode=self-host --api-key=ragflow-xxxxx

    4. Disable streamable HTTP (only legacy SSE will be active):
        uv run mcp/server/server.py --no-transport-streamable-http-enabled \
            --mode=self-host --api-key=ragflow-xxxxx

    5. Use streamable HTTP with SSE-style events (disable JSON response):
        uv run mcp/server/server.py --transport-streamable-http-enabled --no-json-response \
            --mode=self-host --api-key=ragflow-xxxxx

    6. Disable both transports (for testing):
        uv run mcp/server/server.py --no-transport-sse-enabled --no-transport-streamable-http-enabled \
            --mode=self-host --api-key=ragflow-xxxxx
    """
    main()
