# Low-latency knowledge-base QA trial

The user approved implementing the conservative first revision on an isolated
branch, with easy rollback. Their primary constraint is response speed.

## Scope and approach

Keep the existing chat and enterprise-channel entrypoints. Add an opt-in
`prompt_config.lightweight_qa` boolean (only JSON `true` enables it). No database
migration, new dependency, production configuration change, or deployment.
Missing/false retains the original path. The trial is backend-only and is
enabled through the existing chat update API; a UI control is deferred.

Alternatives discussed: prompt/configuration changes only; an incremental
bounded agent in the current chat pipeline (selected); full Agent workflow
migration. This first slice implements deterministic orchestration and improved
instructions, not an unrestricted tool-calling agent or a semantic verifier.

## Behavior

- Normal requests incur no extra chat-model calls or retrievals. Existing
  keyword expansion, reranking, and generation remain the fast path.
- In trial mode, multi-turn refinement discards context across a gap greater
  than 30 minutes between user messages with reliable timestamps. Missing,
  invalid, or backwards timestamps do not invent a boundary. A dangling
  reference after expiry asks for clarification instead of retrieving it.
  This duration is an implementation choice, not an agreed business rule.
- Add topic-switch guidance to the existing refinement prompt only in trial
  mode. Add concise intent/condition guidance to the existing final generation
  prompt: distinguish办理 from费用, clarify ambiguous requests, and never turn
  meal-allowance eligibility into overtime eligibility. No extra reviewer call.
- Only an empty primary retrieval triggers a possible fallback. If the primary
  query differs from the unexpanded standalone question, retry that question
  once, preserving tenant, KB, attachment/metadata scope, threshold, and reranker.
  Otherwise skip duplicate work. Do not claim that nonempty results prove
  semantic adequacy, and do not lower the evidence threshold.
- The fallback uses a separate bounded worker pool (2 workers per process,
  no queue). It waits at most 2 seconds; busy, failure, or timeout retains the
  original result. Blocking providers may continue after timeout, retaining
  their slot until completion; never accumulate a retry queue. No extra TOC
  model pass after fallback. Cancellation of the caller propagates.
- Preserve streaming, exact FAQ, citation, SQL, document scope, and no-evidence
  behavior. Deep research does not acquire another retry layer.
- Log only fallback outcome, elapsed time, and counts; no questions or content
  in new logs. The 2-second budget covers supplemental retrieval, not the whole
  answer or existing provider latency.

## Validation and rollout

Test disabled behavior, unchanged fast-path call counts, expiry and follow-ups,
one retry with identical access scope, retry failures/timeouts/cancellation,
bounded capacity with blocking operations, and streaming citations. Use the
historical question patterns as sanitized manual evaluation cases. Unit tests
verify routing and budgets, not the model's factual accuracy.

Before any production rollout, compare ordinary and fallback cohorts separately
for first visible text, total duration, answer relevance, unsupported conclusions,
and useful clarification. No production latency or accuracy improvement is
claimed without this comparison. Disable by setting `lightweight_qa` false; keep
the feature branch unmerged for straightforward code rollback.
