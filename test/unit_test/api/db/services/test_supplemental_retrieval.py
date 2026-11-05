import asyncio
import contextvars
import threading
from time import monotonic

import pytest

from api.db.services.supplemental_retrieval import (
    BoundedRetrievalExecutor,
    retry_empty_retrieval,
)


@pytest.mark.asyncio
async def test_retry_preserves_request_context_across_worker_boundary():
    request_scope = contextvars.ContextVar("request_scope", default="missing")
    token = request_scope.set("authorized-tenant")

    async def retrieve(_query):
        return {"chunks": [{"scope": request_scope.get()}]}

    try:
        with BoundedRetrievalExecutor(max_workers=1) as executor:
            result = await retry_empty_retrieval(
                empty_result(),
                primary_query="expanded",
                fallback_query="original",
                retrieve=retrieve,
                executor=executor,
            )
        assert result["chunks"] == [{"scope": "authorized-tenant"}]
    finally:
        request_scope.reset(token)


def empty_result():
    return {"total": 0, "chunks": [], "doc_aggs": []}


@pytest.mark.asyncio
@pytest.mark.parametrize("has_chunks,same_query", [(True, False), (False, True)])
async def test_fast_path_does_not_run_supplemental_search(has_chunks, same_query):
    initial = empty_result()
    if has_chunks:
        initial["chunks"] = [{"chunk_id": "existing", "content_with_weight": "evidence"}]

    async def forbidden(_query):
        pytest.fail("ordinary answers and duplicate queries must not trigger work")

    result = await retry_empty_retrieval(
        initial,
        primary_query="original" if same_query else "expanded",
        fallback_query="original",
        retrieve=forbidden,
    )
    assert result is initial


@pytest.mark.asyncio
async def test_empty_search_retries_original_once_and_returns_evidence():
    calls = []
    recovered = {"chunks": [{"chunk_id": "recovered"}], "doc_aggs": [], "total": 1}

    async def retrieve(query):
        calls.append(query)
        return recovered

    with BoundedRetrievalExecutor(max_workers=1) as executor:
        result = await retry_empty_retrieval(
            empty_result(),
            primary_query="original,keywords",
            fallback_query="original",
            retrieve=retrieve,
            executor=executor,
        )
    assert result == recovered
    assert calls == ["original"]


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["empty", "error", "malformed"])
async def test_unsuccessful_retry_keeps_initial_evidence(outcome):
    initial = empty_result()

    async def retrieve(_query):
        if outcome == "error":
            raise RuntimeError("sensitive provider response")
        return None if outcome == "malformed" else empty_result()

    with BoundedRetrievalExecutor(max_workers=1) as executor:
        result = await retry_empty_retrieval(
            initial,
            primary_query="expanded",
            fallback_query="original",
            retrieve=retrieve,
            executor=executor,
        )
    assert result is initial


@pytest.mark.asyncio
async def test_blocking_retry_times_out_without_releasing_capacity_or_queuing():
    release = threading.Event()
    entered = threading.Event()
    initial = empty_result()
    calls = []

    async def blocking(query):
        calls.append(query)
        entered.set()
        release.wait(2)
        return {"chunks": [{"chunk_id": "late"}]}

    with BoundedRetrievalExecutor(max_workers=1) as executor:
        try:
            start = monotonic()
            result = await retry_empty_retrieval(
                initial,
                primary_query="expanded",
                fallback_query="original",
                retrieve=blocking,
                timeout_seconds=0.05,
                executor=executor,
            )
            assert entered.is_set()
            assert result is initial
            assert monotonic() - start < 0.5
            result = await retry_empty_retrieval(
                initial,
                primary_query="expanded again",
                fallback_query="another",
                retrieve=blocking,
                timeout_seconds=0.05,
                executor=executor,
            )
            assert result is initial
            assert calls == ["original"]
        finally:
            release.set()


@pytest.mark.asyncio
async def test_cancelled_request_propagates_without_freeing_running_slot():
    release = threading.Event()
    entered = threading.Event()
    calls = []

    async def blocking(query):
        calls.append(query)
        entered.set()
        release.wait(2)
        return empty_result()

    with BoundedRetrievalExecutor(max_workers=1) as executor:
        try:
            task = asyncio.create_task(
                retry_empty_retrieval(
                    empty_result(),
                    primary_query="expanded",
                    fallback_query="original",
                    retrieve=blocking,
                    executor=executor,
                )
            )
            assert await asyncio.to_thread(entered.wait, 1)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            await retry_empty_retrieval(
                empty_result(),
                primary_query="expanded",
                fallback_query="another",
                retrieve=blocking,
                executor=executor,
            )
            assert calls == ["original"]
        finally:
            release.set()


@pytest.mark.asyncio
async def test_completed_worker_releases_capacity_and_error_logs_omit_content(caplog):
    async def failing(_query):
        raise RuntimeError("sensitive provider response")

    async def successful(_query):
        return {"chunks": [{"chunk_id": "next"}]}

    with BoundedRetrievalExecutor(max_workers=1) as executor:
        await retry_empty_retrieval(
            empty_result(),
            primary_query="secret question expanded",
            fallback_query="secret question",
            retrieve=failing,
            executor=executor,
        )
        result = await retry_empty_retrieval(
            empty_result(),
            primary_query="expanded",
            fallback_query="original",
            retrieve=successful,
            executor=executor,
        )
    assert result["chunks"][0]["chunk_id"] == "next"
    assert "sensitive provider response" not in caplog.text
    assert "secret question" not in caplog.text
