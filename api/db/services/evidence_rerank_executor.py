from __future__ import annotations

import asyncio
import threading
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any

from rag.nlp.evidence import RerankBusyError


class BoundedRerankExecutor:
    def __init__(self, max_workers: int = 2) -> None:
        self._pool = ThreadPoolExecutor(
            max_workers=max_workers,
            thread_name_prefix="evidence-rerank",
        )
        self._capacity = threading.BoundedSemaphore(max_workers)

    def submit(
        self,
        fn: Callable[..., Any],
        *args: Any,
    ) -> Future[Any]:
        if not self._capacity.acquire(blocking=False):
            raise RerankBusyError(
                "evidence reranker capacity exhausted"
            )
        try:
            future = self._pool.submit(fn, *args)
        except BaseException:
            self._capacity.release()
            raise
        future.add_done_callback(self._release_capacity)
        return future

    def _release_capacity(self, _future: Future[Any]) -> None:
        self._capacity.release()


_DEFAULT_EXECUTOR = BoundedRerankExecutor(max_workers=2)


class EvidenceRerankLease:
    def __init__(
        self,
        model: Any,
        executor: BoundedRerankExecutor | None = None,
    ) -> None:
        self._model = model
        self._executor = executor or _DEFAULT_EXECUTOR
        self._lock = threading.Lock()
        self._pending = 0
        self._sealed = False
        self._closed = False

    async def similarity(
        self,
        query: str,
        documents: list[str],
    ) -> tuple[object, object]:
        with self._lock:
            if self._sealed:
                raise RuntimeError("evidence reranker lease is sealed")
            self._pending += 1

        try:
            future = self._executor.submit(
                self._model.similarity,
                query,
                documents,
            )
        except BaseException:
            with self._lock:
                self._pending -= 1
            raise

        future.add_done_callback(self._on_similarity_done)
        return await asyncio.wrap_future(future)

    def seal(self) -> None:
        close_model = False
        with self._lock:
            self._sealed = True
            if self._pending == 0 and not self._closed:
                self._closed = True
                close_model = True
        if close_model:
            self._close_model()

    def _on_similarity_done(self, _future: Future[Any]) -> None:
        close_model = False
        with self._lock:
            self._pending -= 1
            if (
                self._sealed
                and self._pending == 0
                and not self._closed
            ):
                self._closed = True
                close_model = True
        if close_model:
            self._close_model()

    def _close_model(self) -> None:
        close = getattr(self._model, "close", None)
        if callable(close):
            close()
