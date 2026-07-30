import asyncio
import threading

import pytest

from api.db.services.evidence_rerank_executor import (
    BoundedRerankExecutor,
    EvidenceRerankLease,
    RerankBusyError,
)


class BlockingModel:
    def __init__(self):
        self.release = threading.Event()
        self.started = threading.Event()
        self.finished = threading.Event()
        self.closed = threading.Event()
        self.close_calls = 0

    def similarity(self, query, documents):
        self.started.set()
        self.release.wait(1)
        self.finished.set()
        return [0.9 for _document in documents], 1

    def close(self):
        self.close_calls += 1
        self.closed.set()


@pytest.mark.asyncio
async def test_executor_rejects_new_work_while_capacity_is_occupied():
    executor = BoundedRerankExecutor(max_workers=1)
    first_model = BlockingModel()
    first = EvidenceRerankLease(first_model, executor)
    task = asyncio.create_task(first.similarity("q1", ["d1"]))
    assert await asyncio.to_thread(first_model.started.wait, 0.2)

    second_model = BlockingModel()
    second = EvidenceRerankLease(second_model, executor)
    with pytest.raises(RerankBusyError):
        await second.similarity("q2", ["d2"])

    first_model.release.set()
    await task
    first.seal()
    second.seal()

    assert first_model.close_calls == 1
    assert second_model.close_calls == 1


@pytest.mark.asyncio
async def test_seal_defers_model_close_until_worker_really_finishes():
    executor = BoundedRerankExecutor(max_workers=1)
    model = BlockingModel()
    lease = EvidenceRerankLease(model, executor)
    task = asyncio.create_task(lease.similarity("q", ["d"]))
    assert await asyncio.to_thread(model.started.wait, 0.2)

    lease.seal()
    assert model.close_calls == 0

    model.release.set()
    await task
    assert await asyncio.to_thread(model.closed.wait, 0.5)

    lease.seal()
    assert model.close_calls == 1


@pytest.mark.asyncio
async def test_cancellation_keeps_lease_until_worker_really_finishes():
    executor = BoundedRerankExecutor(max_workers=1)
    model = BlockingModel()
    lease = EvidenceRerankLease(model, executor)
    task = asyncio.create_task(lease.similarity("q", ["d"]))
    assert await asyncio.to_thread(model.started.wait, 0.2)

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    lease.seal()
    assert model.close_calls == 0

    model.release.set()
    assert await asyncio.to_thread(model.finished.wait, 0.2)
    assert await asyncio.to_thread(model.closed.wait, 0.5)

    assert model.close_calls == 1
