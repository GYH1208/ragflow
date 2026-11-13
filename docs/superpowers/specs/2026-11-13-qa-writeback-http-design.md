# HTTP Q&A writeback

Implement the interface approved in the conversation as an ordinary REST endpoint,
without adding or registering an MCP tool.

`POST /api/v1/datasets/{dataset_id}/documents/{document_id}/qa` accepts a full
API key and creates one searchable Q&A chunk in an accessible, enabled QA document.
Required JSON strings: question (2000 characters), answer (20000), source_system
(64 ASCII letters/digits/underscore/hyphen), external_id (128, case sensitive).
Optional important_keywords: at most 20 strings of at most 100 characters.
Optional keyword_top_n: integer 1–10, default 5. Trim surrounding whitespace and
normalize CRLF. Empty keywords enable automatic extraction; nonempty keywords
skip extraction. Questions and answers are preserved, not summarized or truncated.

Reuse the configured chat model and keyword extraction helper over the whole Q&A.
Bound extraction by 10 seconds; failures/empty output degrade to empty keywords.
Use the dataset's embedding model on the question and store complete Q&A content,
question tokens, keyword fields and its vector. Return code 0 with chunk_id,
dataset_id, document_id, source_system, external_id, created, important_keywords,
and keyword_status (generated/provided/failed). Do not modify existing upload,
parse, chunk, or MCP behavior.

Scope idempotency to tenant/dataset/document/source/external ID, using a stable
hash and SQL primary key. Normalize request fingerprints, including effective
keyword settings. An identical request returns its original result; different
content returns 409. A SQL lease prevents concurrent work. Persist the prepared
chunk/vector before writing the index so retries can recover ambiguous writes
without model calls. Lock ledger, document and KB rows before index publication;
validate the current targets, insert and read back the exact chunk, then commit
the document/KB counters and completion marker in the same SQL transaction.
Keep ownership until an outstanding index writer settles, even on cancellation.
This deliberately holds target row locks through external index I/O to prevent
reparse/takeover races. If a completed chunk was subsequently removed, return a
conflict instead of resurrecting it or claiming success.

New SQL table is created by the existing startup table initializer. No production
database migration, model request, service restart, or deployment during this task.
Unit tests use local fakes/SQLite and mocked provider boundaries.

Errors carry HTTP status, numeric code, message, and data.error_code/retryable.
400/101 invalid arguments or document type; 401 unauthenticated; 403 unauthorized
key; 404 inaccessible/missing target; 409/409 payload conflict; 409/106 busy or
processing; 503/503 model/storage/database unavailable. Keyword-only failures
remain successful writes with keyword_status=failed. Retry with unchanged IDs
and payload after timeout; never retry by inventing another external ID.

Alternative: a wrapper around add_chunk is smaller but does not provide durable
idempotency or recover counters after partial writes. Selected: a separate REST
route, focused writeback service and SQL store. An asynchronous queue is deferred.
