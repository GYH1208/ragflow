# Lightweight QA trial validation

Branch: `feat/hr-qa-lightweight-agent`, based on `493e87269`.
The main checkout and production configuration were not changed. Keep the
trial disabled and the branch unmerged until a representative comparison.

## Review and repairs

An independent read-only review found no critical issue and one important
issue: settings validation discarded the new flag before the existing PUT.
Three failing schema cases reproduced it; the schema now preserves an optional
boolean. There is no new visible UI control.

Two timestamp findings also violated the conservative input-handling contract:
an oversized integer could raise, and a backwards clock could cause false
expiry on the following turn. Both were reproduced with failing tests and
repaired in the same review pass. Missing/invalid timestamps still preserve
context; backwards clocks no longer lower the comparison timestamp.

## Checks

- Focused refinement, chat pipeline, worker, and channel tests: **168 passed**,
  process exit 0 (11.85 seconds).
- Adjacent SQL, evidence, and message timestamp tests: **20 passed**, process
  exit 0 (16.54 seconds).
- Frontend settings schema tests: **5 passed**, process exit 0 (1.257 seconds).
- Changed frontend files: ESLint and Prettier checks passed.
- New Python files: full Ruff passed. Existing touched Python files retain
  baseline Ruff diagnostics: dialog service 80, refinement 6, dialog tests 3.
  No new diagnostics; fatal syntax/name checks and diff whitespace checks pass.

Python commands use the existing virtualenv with `PYTHONPATH=.` and
`-W ignore::RuntimeWarning` because Polars emits a CPU-feature warning at import
and this repository treats warnings as errors. No packages were changed.

## Limitations recorded rather than hidden

- Combined execution reports 188 assertions passing but exits 1 during pytest
  cleanup on unclosed event-loop/socket warnings. The same combined baseline
  run on unmodified `main` reports 149 passing and exits 1 with the same warnings.
  The focused and adjacent groups above pass separately. This existing test
  lifecycle issue was not folded into the feature change.
- Full test collection exits before execution because
  `test/testcases/configs.py` requires `ZHIPU_AI_API_KEY`. Full integration
  fixtures also configure providers and delete datasets/chats, so they were not
  executed against the services on this host.
- No live model quality or production latency benchmark was run. Passing tests
  establish routing, call counts, scope, timeout, persistence, and regression
  behavior—not improved factual accuracy or a guaranteed end-to-end response time.

See `docs/guides/lightweight-qa-trial.md` for activation, rollback, and the
historical-question comparison checklist. The two-second budget is only for
waiting on supplemental retrieval, including child-chunk assembly.
