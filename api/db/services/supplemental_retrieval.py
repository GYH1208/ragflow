"""Optional recovery for empty searches, isolated from the serving event loop."""

from __future__ import annotations

import asyncio
import contextvars
import logging
import threading
from collections.abc import Awaitable, Callable
from concurrent.futures import ThreadPoolExecutor
from time import monotonic

LOGGER = logging.getLogger(__name__)


class RetrievalBusyError(RuntimeError):
    pass


class BoundedRetrievalExecutor(ThreadPoolExecutor):
    """Never queue more work than workers, including requests that timed out."""

    def __init__(self, max_workers: int = 2):
        super().__init__(max_workers=max_workers, thread_name_prefix="qa-retrieval")
        self._capacity = threading.BoundedSemaphore(max_workers)

    def submit(self, fn, /, *args, **kwargs):
        if not self._capacity.acquire(blocking=False):
            raise RetrievalBusyError("supplemental retrieval capacity exhausted")
        try:
            future = super().submit(fn, *args, **kwargs)
        except BaseException:
            self._capacity.release()
            raise
        # A caller timeout cannot interrupt blocking provider I/O. Retain the
        # slot until the concurrent future really finishes, not until await ends.
        future.add_done_callback(lambda _future: self._capacity.release())
        return future


_DEFAULT_EXECUTOR = BoundedRetrievalExecutor()


def _run_retrieval(retrieve: Callable[[str], Awaitable[dict]], question: str):
    # The existing retriever uses sync embedding/reranking/database clients.
    # Its coroutine needs its own loop because reranking can block that loop.
    return asyncio.run(retrieve(question))


async def retry_empty_retrieval(
    initial: dict,
    *,
    primary_query: str,
    fallback_query: str,
    retrieve: Callable[[str], Awaitable[dict]],
    timeout_seconds: float = 2,
    executor: BoundedRetrievalExecutor | None = None,
) -> dict:
    """Retry a different existing query once; do not generate a new query.

    ``retrieve`` must use loop-independent clients and preserve the caller's
    tenant, knowledge-base, document, and metadata restrictions. Only the
    supplemental operation is subject to this wait budget.
    """
    if initial.get("chunks") or not fallback_query.strip() or " ".join(primary_query.split()) == " ".join(fallback_query.split()):
        return initial

    started_at = monotonic()
    outcome = "empty"
    try:
        future = (executor or _DEFAULT_EXECUTOR).submit(
            contextvars.copy_context().run,
            _run_retrieval,
            retrieve,
            fallback_query,
        )
        result = await asyncio.wait_for(
            asyncio.wrap_future(future),
            timeout=timeout_seconds,
        )
        if isinstance(result, dict) and isinstance(result.get("chunks"), list) and result["chunks"]:
            outcome = "recovered"
            return result
    except TimeoutError:
        outcome = "timeout"
    except RetrievalBusyError:
        outcome = "busy"
    except asyncio.CancelledError:
        outcome = "cancelled"
        raise
    except Exception as exc:  # noqa: BLE001 - optional recovery must not break chat
        outcome = "error"
        LOGGER.warning("Supplemental retrieval failed error_type=%s", type(exc).__name__)
    finally:
        LOGGER.info(
            "Supplemental retrieval outcome=%s duration_ms=%.1f",
            outcome,
            (monotonic() - started_at) * 1000,
        )
    return initial
