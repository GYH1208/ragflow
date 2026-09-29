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
import hashlib
import json
import logging
import random
import re
import time
from collections import OrderedDict
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from enum import StrEnum
from functools import wraps
from typing import Any

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
MODE = ""
TRANSPORT_SSE_ENABLED = True
TRANSPORT_STREAMABLE_HTTP_ENABLED = True
JSON_RESPONSE = True
STREAMABLE_HTTP_ALLOWED_HOSTS = ["127.0.0.1:*", "localhost:*", "[::1]:*"]
STREAMABLE_HTTP_ALLOWED_ORIGINS = ["http://127.0.0.1:*", "http://localhost:*", "http://[::1]:*"]


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

    def __init__(self, base_url: str, version="v1"):
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

    async def _post(self, path, json=None, stream=False, files=None, api_key: str = ""):
        if not api_key:
            return None
        client = await self._get_client()
        res = await client.post(url=self.api_url + path, json=json, headers={"Authorization": f"Bearer {api_key}"})
        if res.status_code in (401, 403):
            self._invalidate_cached_dataset_ids(api_key)
        return res

    async def _get(self, path, params=None, api_key: str = ""):
        if not api_key:
            return None
        client = await self._get_client()
        res = await client.get(url=self.api_url + path, params=params, headers={"Authorization": f"Bearer {api_key}"})
        if res.status_code in (401, 403):
            self._invalidate_cached_dataset_ids(api_key)
        return res

    @staticmethod
    def _dataset_id_cache_key(api_key: str) -> bytes:
        return hashlib.sha256(api_key.encode()).digest()

    def _get_cached_dataset_ids(self, cache_key: bytes) -> list[str] | None:
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
        entry = self._dataset_metadata_cache.get(dataset_id)
        if entry:
            data, ts = entry
            if self._is_cache_valid(ts):
                self._dataset_metadata_cache.move_to_end(dataset_id)
                return data
        return None

    def _set_cached_dataset_metadata(self, dataset_id, metadata):
        self._dataset_metadata_cache[dataset_id] = (metadata, self._get_expiry_timestamp())
        self._dataset_metadata_cache.move_to_end(dataset_id)
        if len(self._dataset_metadata_cache) > self._MAX_DATASET_CACHE:
            self._dataset_metadata_cache.popitem(last=False)

    def _get_cached_document_metadata(self, dataset_id, document_id):
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
            **{
                field: document[field]
                for field in ("name", "location", "type", "chunk_count", "token_count", "update_date", "meta_fields")
                if field in document
            },
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
    ctx = RAGFlowCtx(RAGFlowConnector(base_url=BASE_URL))

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
                "To locate a known or named file, call search_documents first. After obtaining a document_id, use "
                "get_document_chunks to read the original text instead of repeatedly calling this tool. You can "
                "optionally specify dataset_ids to search only specific datasets, or omit dataset_ids entirely to "
                "search across ALL available datasets. You can also optionally specify document_ids to search within "
                "specific documents. When dataset_ids is not provided or is empty, the system will automatically "
                "search across all available datasets."
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
                "Find documents by filename keyword without semantic content retrieval. Use this first when the user "
                "names or describes a specific file. The result returns dataset_id and document_id; then call "
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
                "with the next page until all chunks needed for the answer have been read."
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
    raise ValueError(f"Tool not found: {name}")


def create_starlette_app():
    routes = []
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
                if path.startswith("/messages/") or path.startswith("/sse") or path.startswith("/mcp"):
                    headers = dict(scope["headers"])
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

    return Starlette(
        debug=False,
        routes=routes,
        middleware=middleware,
        lifespan=streamablehttp_lifespan,
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
    global STREAMABLE_HTTP_ALLOWED_HOSTS, STREAMABLE_HTTP_ALLOWED_ORIGINS
    BASE_URL = os.environ.get("RAGFLOW_MCP_BASE_URL", base_url)
    HOST = os.environ.get("RAGFLOW_MCP_HOST", host)
    PORT = os.environ.get("RAGFLOW_MCP_PORT", str(port))
    MODE = os.environ.get("RAGFLOW_MCP_LAUNCH_MODE", mode)
    HOST_API_KEY = os.environ.get("RAGFLOW_MCP_HOST_API_KEY", api_key)
    TRANSPORT_SSE_ENABLED = parse_bool_flag("RAGFLOW_MCP_TRANSPORT_SSE_ENABLED", transport_sse_enabled)
    TRANSPORT_STREAMABLE_HTTP_ENABLED = parse_bool_flag("RAGFLOW_MCP_TRANSPORT_STREAMABLE_ENABLED", transport_streamable_http_enabled)
    JSON_RESPONSE = parse_bool_flag("RAGFLOW_MCP_JSON_RESPONSE", json_response)
    STREAMABLE_HTTP_ALLOWED_HOSTS = [value.strip() for value in os.environ.get("RAGFLOW_MCP_ALLOWED_HOSTS", ",".join(STREAMABLE_HTTP_ALLOWED_HOSTS)).split(",") if value.strip()]
    STREAMABLE_HTTP_ALLOWED_ORIGINS = [value.strip() for value in os.environ.get("RAGFLOW_MCP_ALLOWED_ORIGINS", ",".join(STREAMABLE_HTTP_ALLOWED_ORIGINS)).split(",") if value.strip()]

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
