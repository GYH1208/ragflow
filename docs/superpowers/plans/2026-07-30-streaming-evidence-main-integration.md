# Streaming Evidence Main Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Safely integrate trusted post-response image evidence into the latest `main` while preserving WeCom streaming, voice transcription, inbound identifier logging, and a fail-closed 0.9-second evidence budget.

**Architecture:** Keep retrieval and evidence decisions in `rag/nlp/evidence.py`, but inject an async rerank callable instead of letting the core use the shared thread pool. A new application-layer bounded executor owns synchronous reranker calls and model lifecycle. Channel orchestration confirms final text delivery first, then persists and sends at most two verified images in a separate media-only message.

**Tech Stack:** Python 3.13, asyncio, `concurrent.futures.ThreadPoolExecutor`, pytest/pytest-asyncio, Ruff, Git worktrees.

## Global Constraints

- Preserve the four `main` commits after the original fork: `dce4c9796`, `a02f04722`, `c4e881307`, and `ed0d5ec90`.
- Do not add a second knowledge-base retrieval, embedding call, runtime vision call, or image-storage schema.
- Candidate shortlists remain restricted to `reference.chunks`, with at most three candidates per evidence unit.
- At most two evidence units and two unique images may be accepted.
- Final streaming frames and ordinary text messages must never contain citation-derived, unverified images.
- Evidence starts only after an explicit successful text acknowledgement.
- Evidence returns fail-closed within 0.9 seconds.
- Slow synchronous reranker work must not use or exhaust the asyncio default executor.
- A reranker model must not close while any worker is still using it.
- This workflow merges locally into `main`; it does not push.

---

## File Structure

### Create

- `api/db/services/evidence_rerank_executor.py`
  - Owns the process-wide bounded executor and per-model lease.
  - Exposes async similarity without leaking slow calls into the default pool.
  - Delays model close until all submitted calls really finish.
- `test/unit_test/api/db/services/test_evidence_rerank_executor.py`
  - Verifies capacity, cancellation, delayed close, and exact-once cleanup.

### Modify

- `rag/nlp/evidence.py`
  - Defines the core `RerankBusyError` without introducing a `rag` → `api` dependency.
  - Replaces direct `thread_pool_exec` usage with an injected async similarity callable.
  - Distinguishes `rerank_busy` from `rerank_error`.
- `api/db/services/evidence_service.py`
  - Creates and seals the model lease.
  - Preserves the 0.9-second caller deadline.
  - Maps unexpected exceptions to stable `rerank_error`.
- `api/channels/core/base.py`
  - Preserves `supports_streaming`.
  - Makes final stream acknowledgement explicit.
- `api/channels/wecom/channel.py`
  - Preserves streaming, voice dispatch, and identifier logging.
  - Returns explicit text ACK results.
  - Supports media-only follow-up messages.
- `api/channels/bootstrap.py`
  - Merges streaming generation with post-response evidence.
  - Sends final text/source files first and verified images separately.
- `test/unit_test/rag/test_evidence.py`
- `test/unit_test/api/db/services/test_evidence_service.py`
- `test/unit_test/api/channels/test_bootstrap.py`
- `test/unit_test/api/channels/test_wecom_channel.py`
  - Add focused regression coverage for the interfaces above.

### Preserve and Re-run

- `test/unit_test/api/db/services/test_conversation_service_evidence.py`
- `test/unit_test/api/db/services/test_dialog_service_final_answer.py`
  - Preserve existing persistence, Langfuse, final-answer, and loop-isolation coverage.

---

### Task 1: Add a Bounded Reranker Executor With Deferred Model Close

**Files:**
- Modify: `rag/nlp/evidence.py`
- Create: `api/db/services/evidence_rerank_executor.py`
- Create: `test/unit_test/api/db/services/test_evidence_rerank_executor.py`

**Interfaces:**
- Produces:

```python
# rag/nlp/evidence.py
class RerankBusyError(RuntimeError): ...

# api/db/services/evidence_rerank_executor.py
class EvidenceRerankLease:
    def __init__(self, model, executor: BoundedRerankExecutor | None = None) -> None: ...
    async def similarity(
        self,
        query: str,
        documents: list[str],
    ) -> tuple[object, object]: ...
    def seal(self) -> None: ...

class BoundedRerankExecutor:
    def __init__(self, max_workers: int = 2) -> None: ...
    def submit(self, fn, *args) -> concurrent.futures.Future: ...
```

- The executor imports `RerankBusyError` from `rag.nlp.evidence`; core retrieval never imports the application service.
- The module-level default executor has `max_workers=2`.
- Capacity is acquired without blocking before submission.
- A future completion callback releases capacity.
- `EvidenceRerankLease.seal()` closes immediately only when no calls remain; otherwise the final completion callback closes the model.

- [ ] **Step 1: Write the failing busy-capacity test**

Add:

```python
@pytest.mark.asyncio
async def test_executor_rejects_new_work_while_capacity_is_occupied():
    release = threading.Event()
    started = threading.Event()

    class BlockingModel:
        def similarity(self, query, documents):
            started.set()
            release.wait(1)
            return [0.9], 1

        def close(self):
            pass

    executor = BoundedRerankExecutor(max_workers=1)
    first = EvidenceRerankLease(BlockingModel(), executor)
    task = asyncio.create_task(first.similarity("q1", ["d1"]))
    assert await asyncio.to_thread(started.wait, 0.2)

    second = EvidenceRerankLease(BlockingModel(), executor)
    with pytest.raises(RerankBusyError):
        await second.similarity("q2", ["d2"])

    release.set()
    await task
    first.seal()
    second.seal()
```

- [ ] **Step 2: Run the new test and verify RED**

Run:

```bash
POLARS_SKIP_CPU_CHECK=1 uv run pytest -q test/unit_test/api/db/services/test_evidence_rerank_executor.py::test_executor_rejects_new_work_while_capacity_is_occupied
```

Expected: collection fails because `evidence_rerank_executor` does not exist.

- [ ] **Step 3: Implement the executor and capacity gate**

Implementation requirements:

```python
class BoundedRerankExecutor:
    def __init__(self, max_workers=2):
        self._pool = ThreadPoolExecutor(
            max_workers=max_workers,
            thread_name_prefix="evidence-rerank",
        )
        self._capacity = threading.BoundedSemaphore(max_workers)

    def submit(self, fn, *args):
        if not self._capacity.acquire(blocking=False):
            raise RerankBusyError("evidence reranker capacity exhausted")
        try:
            future = self._pool.submit(fn, *args)
        except BaseException:
            self._capacity.release()
            raise
        future.add_done_callback(lambda _future: self._capacity.release())
        return future
```

`EvidenceRerankLease` must protect `_pending`, `_sealed`, and `_closed` with a `threading.Lock`. Register its own callback on each submitted future. The callback decrements `_pending`; if sealed and pending reaches zero, call `model.close()` exactly once outside the lock. `similarity()` awaits `asyncio.wrap_future(future)` and does not cancel or release capacity itself.

- [ ] **Step 4: Write delayed-close and cancellation tests**

Add tests proving:

```python
assert model.close_calls == 0  # after lease.seal() while worker is blocked
release.set()
await worker_finished.wait()
assert model.close_calls == 1
```

and:

```python
task.cancel()
with pytest.raises(asyncio.CancelledError):
    await task
lease.seal()
assert model.close_calls == 0
release.set()
await worker_finished.wait()
assert model.close_calls == 1
```

Use `threading.Event` for worker coordination and poll the close counter with a bounded asyncio loop; do not use an unbounded sleep.

- [ ] **Step 5: Run executor tests and commit**

Run:

```bash
POLARS_SKIP_CPU_CHECK=1 uv run pytest -q test/unit_test/api/db/services/test_evidence_rerank_executor.py
uvx ruff check api/db/services/evidence_rerank_executor.py test/unit_test/api/db/services/test_evidence_rerank_executor.py
git diff --check
```

Expected: all executor tests and Ruff checks pass.

Commit:

```bash
git add api/db/services/evidence_rerank_executor.py test/unit_test/api/db/services/test_evidence_rerank_executor.py
git add rag/nlp/evidence.py
git commit -m "feat: bound evidence reranker execution"
```

---

### Task 2: Inject Async Reranking and Stabilize Evidence Errors

**Files:**
- Modify: `rag/nlp/evidence.py`
- Modify: `api/db/services/evidence_service.py`
- Modify: `test/unit_test/rag/test_evidence.py`
- Modify: `test/unit_test/api/db/services/test_evidence_service.py`

**Interfaces:**
- `resolve_evidence(..., rerank_similarity: AsyncRerankSimilarity, ...)`
- `EvidenceRerankLease.similarity` supplies the callable.
- `RerankBusyError` maps to `status="error", reason="rerank_busy"`.
- All other unexpected execution exceptions map to `status="error", reason="rerank_error"`.

- [ ] **Step 1: Replace test doubles with an async similarity callable**

In core evidence tests, replace synchronous model doubles with:

```python
async def rerank_similarity(query: str, documents: list[str]):
    return [scores_by_document[document] for document in documents], 0
```

Pass it as `rerank_similarity=rerank_similarity`.

- [ ] **Step 2: Add failing busy and stable-error tests**

Core test:

```python
async def busy_similarity(query, documents):
    raise RerankBusyError("busy")

result = await resolve_evidence(..., rerank_similarity=busy_similarity)
assert result.status == "error"
assert result.reason == "rerank_busy"
```

Service test:

```python
async def failing_resolve(**kwargs):
    raise ValueError("provider detail")

result = await EvidenceService.resolve_for_dialog(...)
assert result.status == "error"
assert result.reason == "rerank_error"
assert "ValueError" in caplog.text
assert "provider detail" not in result.reason
```

- [ ] **Step 3: Run the focused tests and verify RED**

Run:

```bash
POLARS_SKIP_CPU_CHECK=1 uv run pytest -q \
  test/unit_test/rag/test_evidence.py \
  test/unit_test/api/db/services/test_evidence_service.py
```

Expected: failures because `resolve_evidence` still expects `rerank_model` and the service exposes exception class names.

- [ ] **Step 4: Inject the async callable into the core**

In `rag/nlp/evidence.py`:

- Remove `thread_pool_exec`.
- Define:

```python
AsyncRerankSimilarity = Callable[
    [str, list[str]],
    Awaitable[tuple[object, object]],
]
```

- `_rerank_unit()` awaits `rerank_similarity(query, documents)`.
- `resolve_evidence()` accepts `rerank_similarity`.
- When a gathered result is `RerankBusyError`, record decision reason `rerank_busy`.
- Preserve all existing score shape, finite-number, cited-winner, threshold, margin, order, and de-duplication rules.

- [ ] **Step 5: Use the model lease in EvidenceService**

In `resolve_for_dialog()`:

```python
lease = EvidenceRerankLease(rerank_model)
try:
    result = await asyncio.wait_for(
        resolve_evidence(
            ...,
            rerank_similarity=lease.similarity,
        ),
        timeout=remaining_seconds,
    )
finally:
    lease.seal()
```

Remove direct `rerank_model.close()` from the existing `finally`. Map:

- `asyncio.TimeoutError` → `rerank_timeout`
- `RerankBusyError` → `rerank_busy`
- any other exception → `rerank_error`

Log `error_type=type(exc).__name__` without adding question, answer, or chunk content.

- [ ] **Step 6: Add the service-level timeout lifecycle regression**

Use a blocking synchronous model with `threading.Event`:

1. Start `resolve_for_dialog()` with `timeout_seconds=0.01`.
2. Assert it returns `rerank_timeout`.
3. Assert `close_calls == 0` before releasing the worker.
4. Release the worker.
5. Poll with a 0.5-second bound until `close_calls == 1`.

This test must fail if the service closes the model in its timeout `finally`.

- [ ] **Step 7: Run evidence tests and commit**

Run:

```bash
POLARS_SKIP_CPU_CHECK=1 uv run pytest -q \
  test/unit_test/rag/test_evidence.py \
  test/unit_test/api/db/services/test_evidence_rerank_executor.py \
  test/unit_test/api/db/services/test_evidence_service.py
uvx ruff check \
  rag/nlp/evidence.py \
  api/db/services/evidence_rerank_executor.py \
  api/db/services/evidence_service.py \
  test/unit_test/rag/test_evidence.py \
  test/unit_test/api/db/services/test_evidence_rerank_executor.py \
  test/unit_test/api/db/services/test_evidence_service.py
git diff --check
```

Expected: all focused tests and Ruff checks pass.

Commit:

```bash
git add \
  rag/nlp/evidence.py \
  api/db/services/evidence_service.py \
  test/unit_test/rag/test_evidence.py \
  test/unit_test/api/db/services/test_evidence_service.py
git commit -m "fix: contain timed out evidence rerankers"
```

---

### Task 3: Merge Main Streaming With Text-First Evidence Delivery

**Files:**
- Modify: `api/channels/core/base.py`
- Modify: `api/channels/wecom/channel.py`
- Modify: `api/channels/bootstrap.py`
- Modify: `test/unit_test/api/channels/test_bootstrap.py`
- Modify: `test/unit_test/api/channels/test_wecom_channel.py`

**Interfaces:**
- Consumes: `main` at `ed0d5ec9034d4b0001b90968160d5e689892a036`
- `Channel.send(message) -> bool | None`
- `Channel.send_stream(message, stream_id, finish) -> bool | None`
- Successful WeCom final stream ACK returns `True`.
- The handler stores `raw_answer/reference` in both streaming and non-streaming paths.
- One post-send evidence block consumes an explicit `text_send_result`.

- [ ] **Step 1: Confirm both worktrees are clean and refs have not moved**

Run:

```bash
git -C /home/qaadmin/ragflow status --short --branch
git status --short --branch
git rev-parse main
git rev-parse controlled/post-response-evidence
```

Expected:

- Main worktree: `## main...origin/main`
- Feature worktree: `## controlled/post-response-evidence`
- `main`: `ed0d5ec9034d4b0001b90968160d5e689892a036`

- [ ] **Step 2: Merge `main` into the feature branch**

Run:

```bash
git merge main
```

Expected: conflicts in the three channel files and their channel tests; do not accept either side wholesale.

- [ ] **Step 3: Resolve code and tests as a semantic union**

Use the `main` implementations as the structural baseline for:

- `supports_streaming`
- `send_stream`
- streaming placeholder/delta/final handling
- `_send_websocket_attachments`
- voice transcript dispatch
- inbound message identifier logging

Preserve from the evidence branch:

- `Channel.send() -> bool | None`
- WebSocket text acknowledgement
- media-only `OutgoingMessage`
- `_images_for_used_chunks`
- `EvidenceService`
- stable-ID evidence persistence

The combined bootstrap tests must retain all four main streaming tests and all seven evidence-handler tests. The combined WeCom tests must retain streaming, voice, inbound logging, upload, media-only, image failure, and source-file coverage.

After resolving conflict markers, run:

```bash
rg -n "^(<<<<<<<|=======|>>>>>>>)" api/channels test/unit_test/api/channels
uv run python -m py_compile api/channels/bootstrap.py api/channels/core/base.py api/channels/wecom/channel.py
git diff --check
```

Expected: no conflict markers, compilation succeeds, and `git diff --check` is silent.

- [ ] **Step 4: Add failing WeCom acknowledgement tests**

Add assertions:

```python
assert await channel.send_stream(
    OutgoingMessage(
        chat_id="chat-1",
        text="final",
        reply_to_message_id="callback-1",
        images=[],
    ),
    "stream-1",
    True,
) is True
```

and:

```python
assert await channel.send(
    OutgoingMessage(
        chat_id="chat-1",
        text="",
        images=[OutgoingImage("image-1")],
    )
) is True
```

The first test must also assert that `_send_websocket_attachments()` receives no unverified images.

- [ ] **Step 5: Add failing streaming orchestration tests**

Extend the recording streaming channel so every operation appends to one `events` list.

Add:

```python
async def test_streaming_finalizes_text_before_resolving_and_sends_verified_images_separately(...):
    ...
    assert events == [
        "stream:placeholder",
        "stream:delta",
        "stream:final",
        "evidence:resolve",
        "send:images",
    ]
    assert final_stream_message.images == []
    assert media_message.text == ""
    assert [image.image_id for image in media_message.images] == [
        "image-a",
        "image-b",
    ]
```

Add:

```python
async def test_streaming_final_ack_failure_skips_evidence(...):
    ...
    assert "evidence:resolve" not in events
    assert no media-only message was sent
```

Retain the existing approval/proxy incident regression.

- [ ] **Step 6: Run channel tests and verify RED**

Run:

```bash
POLARS_SKIP_CPU_CHECK=1 uv run pytest -q \
  test/unit_test/api/channels/test_bootstrap.py \
  test/unit_test/api/channels/test_wecom_channel.py
```

Expected: new tests fail because the merged main path still puts citation images in the final stream and does not return a final-stream acknowledgement.

- [ ] **Step 7: Implement explicit WeCom acknowledgements**

In `api/channels/core/base.py`:

```python
async def send_stream(... ) -> bool | None:
    if finish:
        return await self.send(message)
    return None
```

In `WeComChannel`:

- `send()` returns the result of `_send_websocket_message()` for WebSocket.
- `_send_websocket_message()` accepts text-only or attachment-only messages and returns whether any requested operation succeeded.
- `send_stream()` returns `True` after `_ws_request()` confirms the frame ACK.
- On `finish=True`, source files may be sent best-effort after the ACK; their result does not revoke the text ACK.
- `_send_websocket_attachments()` retains per-media failure isolation.
- Preserve voice message text extraction and inbound identifier logging exactly.

- [ ] **Step 8: Implement one shared post-send evidence block**

In `_make_chat_handler()` initialize:

```python
raw_answer = ""
reference = {}
text_send_result: bool | None = None
completion_succeeded = False
```

Streaming final frame:

```python
prepared_text, _, cited_files = _prepare_cited_output(...)
text_send_result = await ch.send_stream(
    OutgoingMessage(
        chat_id=msg.chat_id,
        text=prepared_text,
        reply_to_message_id=msg.message_id,
        images=[],
        files=cited_files,
    ),
    stream_id,
    True,
)
```

Non-streaming send:

```python
text_send_result = await ch.send(
    OutgoingMessage(
        chat_id=msg.chat_id,
        text=answer_text,
        reply_to_message_id=msg.message_id or None,
        images=[],
        files=answer_files,
    )
)
```

After either path, run the existing evidence block only when:

- completion succeeded;
- channel supports reference images;
- quote is enabled;
- `text_send_result is True`;
- at least one `reference.chunks` entry has `image_id`.

Persist `used_chunk_ids` and send:

```python
await ch.send(
    OutgoingMessage(
        chat_id=msg.chat_id,
        text="",
        images=evidence_images[:2],
    )
)
```

- [ ] **Step 9: Run channel and conversation regressions**

Run:

```bash
POLARS_SKIP_CPU_CHECK=1 uv run pytest -q \
  test/unit_test/api/channels/test_bootstrap.py \
  test/unit_test/api/channels/test_wecom_channel.py \
  test/unit_test/api/db/services/test_conversation_service_evidence.py \
  test/unit_test/api/db/services/test_dialog_service_final_answer.py
git diff --check
```

Expected: all tests pass; no resource warnings or conflict markers.

- [ ] **Step 10: Commit the completed semantic merge**

Because Step 2 began a merge, stage all conflict resolutions and integration changes:

```bash
git add \
  api/channels/bootstrap.py \
  api/channels/core/base.py \
  api/channels/wecom/channel.py \
  test/unit_test/api/channels/test_bootstrap.py \
  test/unit_test/api/channels/test_wecom_channel.py
git commit -m "merge: integrate streaming with trusted evidence delivery"
```

Expected: merge concludes and the worktree is clean.

---

### Task 4: Verify the Feature Branch and Merge It Into Local Main

**Files:**
- Verify: all files changed from `main..controlled/post-response-evidence`
- Merge: `controlled/post-response-evidence` into local `main`

**Interfaces:**
- Consumes: completed feature branch with green target tests.
- Produces: local `main` containing streaming, voice, logging, evidence selection, bounded reranker execution, and all regressions.

- [ ] **Step 1: Run the complete target suite on the feature branch**

Run:

```bash
POLARS_SKIP_CPU_CHECK=1 uv run pytest -q \
  test/unit_test/rag/test_evidence.py \
  test/unit_test/api/db/services/test_evidence_rerank_executor.py \
  test/unit_test/api/db/services/test_evidence_service.py \
  test/unit_test/api/db/services/test_conversation_service_evidence.py \
  test/unit_test/api/db/services/test_dialog_service_final_answer.py \
  test/unit_test/api/channels/test_bootstrap.py \
  test/unit_test/api/channels/test_wecom_channel.py
```

Expected: all collected tests pass with exit code 0.

- [ ] **Step 2: Run focused Ruff and scope checks**

Run:

```bash
uvx ruff check \
  rag/nlp/evidence.py \
  api/db/services/evidence_rerank_executor.py \
  api/db/services/evidence_service.py \
  test/unit_test/rag/test_evidence.py \
  test/unit_test/api/db/services/test_evidence_rerank_executor.py \
  test/unit_test/api/db/services/test_evidence_service.py
git diff --check main...HEAD
rg -n "embedding_model|chunk_vector_loader|fetch_chunk_vectors|image_sets|image_assets" \
  rag/nlp/evidence.py \
  api/db/services/evidence_service.py
```

Expected:

- Ruff: `All checks passed!`
- `git diff --check`: silent
- Forbidden-symbol search: no output

Legacy channel and dialog files are not claimed to be globally Ruff-clean; the existing repository reports unrelated historical findings in those files.

- [ ] **Step 3: Request final code review**

Review:

```bash
git diff --stat main..HEAD
git diff main..HEAD
```

The reviewer must confirm:

- no unverified image is included in a final stream;
- final text acknowledgement gates evidence;
- slow reranker work is bounded and models close after real completion;
- voice and inbound identifier behavior remain;
- no Critical or Important findings remain.

- [ ] **Step 4: Merge into local main**

From `/home/qaadmin/ragflow`:

```bash
git checkout main
git merge controlled/post-response-evidence
```

Expected: merge succeeds without unresolved conflicts.

- [ ] **Step 5: Re-run the complete target suite on merged main**

Run the exact pytest command from Step 1 with working directory `/home/qaadmin/ragflow`.

Expected: all collected tests pass with exit code 0.

- [ ] **Step 6: Verify final repository state**

Run:

```bash
git status --short --branch
git log --oneline --decorate -5
git branch --contains 9aea866ca
```

Expected:

- `main` is ahead of `origin/main`;
- the merge commit is at `HEAD`;
- `main` contains the feature history;
- there are no uncommitted source changes.

- [ ] **Step 7: Clean up the owned feature worktree and branch**

Only after the merged-main suite is green:

```bash
git worktree remove /home/qaadmin/ragflow/.worktrees/controlled-post-response-evidence
git worktree prune
git branch -d controlled/post-response-evidence
```

Do not push.
