# Low-Latency Reference Image Selection Design

## Status

Approved in conversation on 2026-07-29.

## Problem

The existing post-response evidence resolver can send an unrelated reference
image even when its configured score thresholds are met. The observed failure
was a question about checking approval progress that caused the "My Agent"
image to be sent.

The failure has two causes:

1. The resolver scores answer fragments against chunks without consistently
   preserving the original user question as the primary intent.
2. Passing an absolute threshold is treated as sufficient evidence, so several
   semantically adjacent chunks can qualify at the same time.

The desired product behavior is precision-first:

- It is acceptable to omit a useful image.
- It is not acceptable to send an unrelated image.
- Text delivery must not wait for image selection.
- Image selection should normally finish within one second.
- A response may send at most two images, but the second image must support a
  different answer point.

## Scope

This change is limited to post-response image selection for channels that
support reference images, initially WeCom.

In scope:

- visible-answer parsing;
- evidence-unit extraction;
- selection from the chunks already returned by retrieval;
- hard candidate gates;
- bounded semantic reranking;
- strict acceptance and rejection;
- at most two image sends;
- observability and regression tests.

Out of scope:

- changing document parsers;
- changing chunk boundaries;
- introducing `image_sets`, `image_assets`, or a list of image IDs per chunk;
- splitting or inspecting a composite image at runtime;
- runtime vision-model calls;
- a second knowledge-base retrieval;
- changing the existing image upload or WeCom media-send protocol.

If several source images were already joined into one stored image, the joined
image remains one indivisible `image_id` and is sent unchanged.

## Retrieval Terminology

RAGFlow uses two retrieval limits:

- `top_k` is the larger internal search window.
- `top_n` is the final, thresholded and ranked chunk list returned to the
  answer/reference flow.

The image selector receives only `reference.chunks`, which is the final Top-N
result after the normal retrieval pipeline. It never searches the whole
knowledge base and never sees the full internal Top-K window.

For this design:

- **retrieval pool** means the image-bearing chunks in `reference.chunks`;
- **evidence unit** means one visible answer paragraph, list item, or sentence
  containing at least one valid citation;
- **unit shortlist** means at most three image-bearing chunks from the retrieval
  pool that are compared for one evidence unit.

## Chosen Approach

Use three layers:

1. fast hard gates;
2. one bounded semantic-rerank round;
3. strict fail-closed acceptance.

This approach adds semantic verification without adding an embedding call or a
vision-model call. It also preserves the existing parser, stored images, and
channel sender.

### Alternatives Considered

#### Local rules only

This has the lowest latency, but keyword overlap and existing retrieval scores
cannot reliably separate semantically adjacent chunks. It is kept as the first
gate, not used as the final decision.

#### Runtime vision verification

This could inspect image pixels, including a composite image, but has variable
latency and higher cost. It is outside the one-second reliability target and is
not part of this change.

#### Send the global Top-2 images

This supports multi-part questions but can send a correct image and a similar
wrong image for the same answer point. It is rejected. The design permits one
winner per distinct evidence unit instead.

## Data Flow

1. Complete the LLM response and send its text.
2. Remove hidden reasoning and non-visible markup from the answer.
3. Split the visible answer into ordered evidence units.
4. Retain at most the first two distinct evidence units that:
   - contain a valid citation;
   - refer to at least one chunk in `reference.chunks`; and
   - have at least one image-bearing candidate in the retrieval pool.
5. Build a shortlist of at most three image-bearing chunks for each retained
   unit.
6. Rerank each shortlist using the original question plus that unit's visible
   text as the query.
7. Evaluate each unit independently with strict acceptance rules.
8. Deduplicate accepted `image_id` values.
9. If more than two unique images survive, retain the two with the strongest
   confidence, then send them in answer order.
10. On timeout or uncertainty, send no unconfirmed image.

Text delivery is not rolled back if image selection fails.

## Visible Answer and Evidence Units

The selector must not score hidden reasoning. It removes complete
`<think>...</think>` blocks before segmentation. Malformed or unclosed think
markup causes image selection to fail closed for that response.

The visible answer is split using:

- paragraph boundaries;
- Markdown list-item boundaries;
- sentence boundaries when several citations occur in one paragraph.

An evidence unit is eligible only when it contains at least one citation that
resolves to an ID in `reference.chunks`. Empty text, citation-only text,
boilerplate, and duplicate normalized units are ignored.

Two images may be sent only when they come from two different eligible evidence
units. Two chunks competing for the same evidence unit can never both be sent.

## Candidate Construction

For each eligible evidence unit:

1. Start with image-bearing chunks explicitly cited by the unit.
2. Add the highest-ranked image-bearing chunks from `reference.chunks` as
   competitors.
3. Preserve original retrieval order and stop at three candidates.

If including competitors would exceed the limit, cited candidates are retained
first and remaining slots are filled by retrieval rank.

The competing chunks are comparison-only. A chunk may be sent only if it was
explicitly cited by that evidence unit. Therefore:

- a wrong cited chunk can lose to a stronger uncited competitor, causing
  rejection;
- an uncited chunk is never substituted and sent silently;
- a relevant chunk outside `reference.chunks` is invisible, so the selector
  omits the image instead of performing another retrieval.

Before reranking, a candidate must pass all hard gates:

- non-empty chunk ID;
- non-empty `image_id`;
- non-empty chunk content;
- finite original similarity fields;
- membership in `reference.chunks`;
- quote/reference-image behavior enabled for the channel;
- confirmed successful text delivery.

## Semantic Reranking

For each retained evidence unit, construct:

```text
Original user question:
{question}

Relevant answer point:
{visible evidence unit without citation markers}
```

The reranker documents are the chunk contents in that unit's shortlist.

At most two evidence units are reranked. Their reranker calls execute
concurrently as one bounded semantic-verification round. Each call compares one
query with no more than three documents.

This design deliberately does not generate fresh embeddings. It reuses the
normal retrieval result for coarse relevance and spends the latency budget on
one cross-encoder-style semantic verification round.

## Acceptance Rules

Each evidence unit is evaluated independently. Its winner is accepted only
when all of the following are true:

1. The winning chunk is explicitly cited by the evidence unit.
2. The winning chunk passed every hard gate.
3. The winner is the rerank Top-1 for that unit.
4. The winner's rerank score is at least `0.75`.
5. When a runner-up exists, the Top-1 minus Top-2 rerank margin is at least
   `0.10`.
6. The original retrieval score is not below the dialog's configured
   similarity threshold.

The initial `0.75` score and `0.10` margin are precision-first release defaults.
They must remain configuration values so a labeled regression set can tune
them without changing the algorithm.

If a shortlist contains only one candidate, there is no artificial margin.
The candidate must still satisfy the absolute rerank threshold and every other
gate.

## Selecting Up to Two Images

Accepted winners are deduplicated by `image_id`.

- Zero accepted winners: send no image.
- One accepted winner: send one image.
- Two accepted winners from distinct evidence units: send two images.
- The same image accepted for two units: send it once.
- Two candidates from one unit: send only the accepted Top-1.

If more than two distinct units could qualify in future, rank accepted winners
by `(rerank score, rerank margin)` descending, retain two, and restore evidence
unit order before sending. The initial implementation processes at most two
eligible units, so this rule mainly defines stable future behavior.

Composite images remain unchanged. The selector evaluates the chunk text, not
individual regions inside the stored image.

## Latency and Failure Behavior

Image selection has a `0.9` second hard deadline measured from the start of
post-response evidence resolution. The budget covers local parsing, candidate
construction, reranking, and the final decision. It does not guarantee WeCom
network upload time.

Recommended budget:

- visible-answer parsing and hard gates: up to 50 ms;
- concurrent rerank round: up to 750 ms;
- decision and scheduling reserve: 100 ms.

There are no retries inside this deadline.

Fail closed and send no unconfirmed image when:

- the deadline expires;
- the reranker is unavailable;
- reranker output dimensions are invalid;
- scores contain non-finite values;
- think markup is malformed;
- citations cannot be resolved;
- the winner is uncited;
- the score or margin gate fails;
- loading or uploading a selected image fails.

One evidence unit failing does not invalidate a separately confirmed unit. If
one unit passes before the shared deadline and another returns a normal
low-confidence result, the confirmed image may be sent. A global timeout or
reranker exception rejects all unresolved units.

## Components and Responsibilities

### Evidence parser

- strips hidden reasoning and citation markers;
- returns ordered evidence units and their cited chunk IDs;
- contains no retrieval or model code.

### Candidate builder

- consumes only `reference.chunks`;
- applies hard gates;
- builds deterministic per-unit shortlists;
- preserves original retrieval order.

### Semantic verifier

- builds the question-plus-answer-point query;
- executes at most two concurrent reranker calls;
- returns per-unit ranked scores;
- owns no sending behavior.

### Decision policy

- applies score, margin, citation, and deduplication rules;
- returns zero, one, or two selected chunk/image IDs;
- records rejection reasons.

### Channel integration

- sends text first;
- starts evidence resolution only after confirmed text delivery;
- loads and sends accepted stored images in answer order;
- leaves existing composite-image handling unchanged.

## Observability

Emit one structured summary log per response with:

- message and dialog identifiers;
- total reference chunk count;
- eligible evidence-unit count;
- candidate chunk IDs per unit;
- original retrieval scores;
- rerank scores and margins;
- accepted chunk and image IDs;
- per-unit rejection reasons;
- total decision duration;
- timeout or model-error status.

Do not log full user questions, answer text, image bytes, or document contents at
info level.

Required rejection reason codes:

- `no_visible_evidence_units`;
- `no_image_candidates`;
- `citation_not_found`;
- `cited_candidate_not_top1`;
- `below_rerank_threshold`;
- `below_score_margin`;
- `duplicate_image`;
- `malformed_think_markup`;
- `rerank_timeout`;
- `rerank_error`;
- `image_load_error`;
- `image_send_error`.

## Configuration

Add or retain explicit evidence-selection configuration for:

- `max_evidence_units = 2`;
- `shortlist_size = 3`;
- `max_images = 2`;
- `min_rerank_score = 0.75`;
- `min_score_margin = 0.10`;
- `timeout_seconds = 0.9`.

Values are server-side configuration. This change does not require a frontend
control.

## Testing

### Unit tests

- hidden reasoning is excluded from evidence units;
- malformed think markup fails closed;
- citation-only and duplicate units are ignored;
- candidates come only from `reference.chunks`;
- cited candidates are retained when competitors fill the shortlist;
- uncited competitors can block but can never be sent;
- low absolute score is rejected;
- insufficient Top-1/Top-2 margin is rejected;
- a one-candidate shortlist uses the absolute threshold without a fake margin;
- duplicate image IDs are sent once;
- one evidence unit produces at most one image;
- two distinct evidence units can produce two images;
- timeout and malformed reranker output fail closed.

### Regression tests

- "check approval progress" does not send the "My Agent" image;
- the formerly correct and incorrect candidates that both passed old thresholds
  are separated by the question-plus-answer-point rerank query;
- a multi-part question about approval progress and proxy setup can send two
  independently verified images;
- two similar candidates for one answer point never produce two images;
- a composite image is sent unchanged;
- a failed second unit does not suppress a normally completed first unit;
- a global timeout sends no unresolved image.

### Integration tests

- text is sent before evidence resolution starts;
- no image is sent when text delivery is unconfirmed;
- one accepted image produces one channel send;
- two accepted images produce two ordered channel sends;
- duplicate image IDs produce one channel send;
- upload failure is logged without retrying or changing the text response.

## Rollout

1. Run the real incident replay and the labeled positive/negative image cases in
   the backend test environment.
2. Record precision, image-send rate, rejection reasons, and p50/p95 decision
   latency.
3. Require zero known false-positive image sends in the labeled regression set
   before enabling the policy.
4. Enable for the controlled WeCom path first.
5. Tune only the configurable score and margin thresholds if the false-negative
   rate is too high; do not relax citation, distinct-unit, or fail-closed rules.

## Success Criteria

- The known approval-progress/My-Agent false positive is rejected.
- No labeled negative case sends an image.
- A multi-part labeled case can send two independently supported images.
- One answer point never sends two images.
- Image-selection p95 is at or below 900 ms, excluding WeCom upload time.
- Text delivery remains independent of evidence-selection success.
