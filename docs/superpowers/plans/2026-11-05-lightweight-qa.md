# Lightweight QA Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans inline; one whole-branch review at the end.

**Goal:** Improve context handling and recover empty expanded-query searches without adding work to ordinary answers.

**Architecture:** Opt-in per-dialog flag wraps existing refinement and generation. A bounded supplemental retrieval reuses the original question only when the expanded query returns no chunks.

**Tech Stack:** Existing Python 3.13, asyncio, concurrent.futures, pytest, Ruff.

**Spec:** `docs/superpowers/specs/2026-11-05-lightweight-qa-design.md`

## Global Constraints

- No new dependency, schema migration, live configuration edit, or deployment.
- `prompt_config.lightweight_qa` defaults off; only boolean true enables it.
- No extra model calls on ordinary questions; at most one supplemental retrieval.
- Fallback: 2-second wait budget; 2 workers per process; no queued work.
- Preserve access scope, threshold, and existing external response shape.

## Review Focus

- String "false" must not enable the experiment.
- Expired pronouns must not be sent directly to retrieval.
- Timeout must not block the serving loop or release a still-running worker slot.
- Supplemental retrieval must preserve explicit document and metadata scope.
- Empty/failed fallback must preserve the normal no-evidence response and citations.

## Task 1: Context and intent without extra model calls

**Files:** `rag/prompts/multiturn.py`, `rag/prompts/multiturn_refinement_prompt.md`, new `rag/prompts/lightweight_qa.md`, `test/unit_test/rag/prompts/test_multiturn_refinement.py`.

**Interfaces:** Extend `refine_multiturn_question(..., *, lightweight=False)`; keep the default contract. `select_active_messages(messages, idle_seconds=1800)` returns the latest contiguous conversation without mutating input.

- [x] Add failing tests for expired/fresh/missing/invalid/ISO/millisecond timestamps, old gaps within history, backwards clocks, and expired references.
- [x] Run focused pytest and confirm failures concern missing behavior.
- [x] Implement history selection and optional prompt guidance; write final-answer guidance.
- [x] Run refinement tests and commit.

## Task 2: Bounded retry and pipeline integration

**Files:** new `api/db/services/supplemental_retrieval.py`, `api/db/services/dialog_service.py`, new `test/unit_test/api/db/services/test_supplemental_retrieval.py`, existing `test_dialog_service_final_answer.py`.

**Interfaces:** `retry_empty_retrieval(initial, *, primary_query, fallback_query, retrieve, timeout_seconds=2, executor=None)` returns original or successful result. `retrieve` is an async callable run in an isolated worker loop. Executor owns its threads; tests use scoped executors.

- [x] Add failing tests for fast-path/identical-query skip, successful single retry, empty/error/timeout, cancellation, capacity under blocking work, and scope-preserving pipeline calls.
- [x] Implement a no-queue two-worker executor and retry helper; keep capacity until actual worker completion.
- [x] Integrate opt-in context/generation guidance and retry before TOC/children; skip TOC only after an attempted retry.
- [x] Verify streaming/nonstreaming, exact FAQ, disabled flag, no-knowledge, scoped documents, and existing channel regressions.
- [x] Write activation/rollback and evaluation instructions. Commit implementation and tests.

## Verification

Use `/home/qaadmin/ragflow/.venv/bin/python -m pytest` from the worktree with
`PYTHONPATH=.`. The environment emits a Polars CPU-feature RuntimeWarning on
import; use `-W ignore::RuntimeWarning` for these tests, without modifying
installed packages. Baseline: 129 relevant tests passed in 13.95 seconds.
Run affected service/prompt/channel tests and Ruff. Full integration fixtures
delete datasets/chats and configure providers, so use full collection only in
this environment and record the limitation; do not mutate production services.

## Verification results

- Focused refinement/pipeline/worker/channel regression tests: 168 passed.
- Adjacent SQL/evidence/message-timestamp regression tests: 20 passed.
- Settings schema preservation: 5 frontend tests passed, ESLint/Prettier clean.
- New Python files pass full Ruff; touched existing files add no diagnostics
  relative to the base revision. Fatal syntax/name checks and diff whitespace pass.
- Full collection stops on missing `ZHIPU_AI_API_KEY` in test configuration;
  no full-suite pass is claimed.
- No live model quality or latency benchmark has been performed. Rollout remains
  off, on the isolated branch, pending comparison with historical cases.

## Review follow-up

Preserve `lightweight_qa` as an optional boolean in the frontend settings schema
and shared type so a normal settings save does not silently disable it. Repaired
two timestamp edge cases after red regressions. See
`docs/superpowers/reviews/2026-11-05-lightweight-qa.md` for final results and the
combined-run cleanup warning reproduced on unmodified main.
