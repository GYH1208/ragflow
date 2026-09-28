# RAGFlow Scoped API Keys Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add named full-access and retrieval-only API keys so WorkBuddy can retrieve from an administrator-selected set of knowledge bases without receiving a broadly privileged RAGFlow key.

**Architecture:** Existing and newly created full-access keys remain in `api_token`; retrieval-only keys live in a separate `scoped_api_token` table so old or rolled-back backends reject them instead of treating them as unrestricted. Authentication records the key kind in Quart request context, applies deny-by-default route scopes to retrieval keys, enforces dataset allowlists in the three MCP read paths, and layers Redis rate limiting plus atomic usage summaries around the request.

**Tech Stack:** Python 3.10+, Quart, Peewee, Redis Lua scripts, pytest, React 18, TypeScript, React Query, React Hook Form, Tailwind CSS, Jest/Testing Library, MCP SSE/HTTP connector.

**Spec:** `docs/superpowers/specs/2026-09-28-ragflow-api-key-permissions-design.md`

## Global Constraints

- Support exactly two public key types: `full_access` and `retrieval`; key type cannot be changed after creation.
- Keep all historical `api_token` values valid and report them as `full_access` with `legacy = true`.
- Store retrieval keys only in `scoped_api_token`, generate them with `ragflow-rk-`, and never let an old backend interpret them as full-access keys.
- Require a nonblank name of at most 64 characters for every newly created key.
- Require at least one tenant-owned dataset for retrieval keys; support multiple datasets and reject the whole request if any requested dataset is unauthorized.
- Retrieval-key expiry choices are exactly 30, 90, 180, 365 days, or `null` for no expiry; the UI defaults to 90 days.
- Limit only accepted retrieval attempts to 60 per minute per retrieval key. Dataset and document metadata reads count as calls but do not consume retrieval quota.
- Return HTTP `401` for missing/disabled/expired keys, `403` for disallowed routes or datasets, `429` for quota exhaustion, and `503` when Redis cannot enforce quota.
- Keep key values plaintext and visible in the first release. Do not add hashing, one-time reveal, rotation, IP allowlists, per-key custom rates, detailed audit events, employee identity, or session attribution.
- Keep the accepted compatibility behavior that a full-access API key may call existing key-management APIs; never allow a retrieval key to do so.
- Record only `total_calls`, `retrieval_calls`, `last_used_at`, and `last_result`; never store question text or request bodies for analytics.
- Add Simplified Chinese and English UI copy. Other locales may use the existing fallback behavior.
- Add no new runtime dependency.

## Review Focus

- A historical full-access token whose random suffix happens to begin with `rk-` must still resolve from `api_token` when no `scoped_api_token` row exists; prefix routing must not break it.
- Dataset filtering must happen before count and pagination, so a scoped key never receives an unauthorized row and receives a correct `total` across pages.
- Missing, duplicate, malformed, deleted, mixed-authorized, and cross-tenant dataset IDs must have deterministic behavior: deduplicate authorized IDs, use the allowlist when omitted, and reject any unauthorized ID without partial retrieval.
- JWT requests and full-access keys must retain existing behavior while retrieval keys are denied on every route that lacks the explicit `knowledge:retrieve` scope.
- Redis failure, concurrent usage updates, and statistics write failure must not silently bypass quota, lose atomic increments, leak plaintext tokens into Redis keys, or turn a successful retrieval into an error solely because telemetry failed.

---

### Task 1: Persist full-access metadata and retrieval-only keys

**Files:**
- Modify: `api/db/__init__.py`
- Modify: `api/db/db_models.py`
- Modify: `api/db/services/api_service.py`
- Create: `test/unit_test/api/db/test_api_token_models.py`
- Create: `test/unit_test/api/db/services/test_api_token_service.py`

**Interfaces:**
- Produces: `APIKeyType(FULL_ACCESS="full_access", RETRIEVAL="retrieval")` and `APIKeyLastResult(SUCCESS, DENIED, RATE_LIMITED, ERROR)` in `api/db/__init__.py`.
- Produces: extended `APIToken` and new `ScopedAPIToken` Peewee models.
- Produces: `ScopedAPITokenService(CommonService)` and `APIKeyUsageService.record(token: str, key_type: APIKeyType, result: APIKeyLastResult, retrieval_started: bool, used_at: datetime) -> None`.

- [ ] **Step 1: Write failing model-contract tests**

Add tests asserting that `APIToken` exposes `name`, `is_legacy`, `enabled`, `total_calls`, `retrieval_calls`, `last_used_at`, and `last_result`; `ScopedAPIToken` uses table `scoped_api_token`, a `(tenant_id, token)` composite primary key, `JSONField` dataset IDs, UTC-compatible `expires_at`, the same status/statistics fields, and standard timestamps.

- [ ] **Step 2: Run the model tests and verify failure**

Run: `uv run pytest test/unit_test/api/db/test_api_token_models.py -q`

Expected: FAIL because the fields and `ScopedAPIToken` do not exist.

- [ ] **Step 3: Add enums, models, and additive migration**

Implement the model fields with these exact defaults: `name="旧版 API Key"`, `is_legacy=False` in the model, `enabled=True`, counters `0`, nullable last-use/result, and nullable expiry. In `migrate_db()`, add existing-table columns safely with migration defaults that mark existing rows `is_legacy=True`; rely on `init_database_tables()` model discovery to create `scoped_api_token` before migrations.

- [ ] **Step 4: Write failing service tests for atomic usage updates**

Test both key types, simultaneous `total_calls`/`retrieval_calls` increments, result/time replacement, missing-token no-op behavior, and exception propagation from the low-level update method so the request layer can decide to swallow telemetry failures.

- [ ] **Step 5: Implement token services and run focused tests**

Use Peewee arithmetic updates rather than read-modify-write. `retrieval_calls` increments only when `retrieval_started=True`.

Run: `uv run pytest test/unit_test/api/db/test_api_token_models.py test/unit_test/api/db/services/test_api_token_service.py -q`

Expected: PASS.

- [ ] **Step 6: Commit the persistence unit**

```bash
git add api/db/__init__.py api/db/db_models.py api/db/services/api_service.py test/unit_test/api/db/test_api_token_models.py test/unit_test/api/db/services/test_api_token_service.py
git commit -m "feat: add scoped API key persistence"
```

### Task 2: Resolve key context and enforce deny-by-default scopes

**Files:**
- Create: `api/apps/api_key_auth.py`
- Modify: `api/apps/__init__.py`
- Create: `test/unit_test/api/apps/test_api_key_auth.py`
- Modify: `test/testcases/test_web_api/test_system_app/test_apps_init_unit.py`

**Interfaces:**
- Consumes: Task 1 models and enums.
- Produces: `KNOWLEDGE_RETRIEVE_SCOPE = "knowledge:retrieve"`.
- Produces: immutable `APIKeyContext(key_type, tenant_id, token, record, allowed_dataset_ids)`.
- Produces: `resolve_api_key(token: str, now: datetime | None = None) -> APIKeyContext | None`, `allowed_dataset_ids() -> frozenset[str] | None`, and `require_dataset_access(dataset_ids: Iterable[str]) -> list[str]`.
- Changes: `login_required(func=None, auth_types=None, api_scope: str | None = None)`; JWT/full-access behavior is unchanged, while a valid retrieval key requires an exact route scope.

- [ ] **Step 1: Write failing policy tests**

Cover full-access resolution, disabled full-access keys, retrieval resolution, disabled and expired retrieval keys, malformed allowlists, duplicate dataset IDs, a missing scoped row with fallback to a historical `APIToken` whose value starts `ragflow-rk-`, and no fallback from a real scoped row to broader permissions.

- [ ] **Step 2: Run the policy tests and verify failure**

Run: `uv run pytest test/unit_test/api/apps/test_api_key_auth.py -q`

Expected: FAIL because `api.apps.api_key_auth` does not exist.

- [ ] **Step 3: Implement context resolution and dataset checks**

`resolve_api_key` queries `ScopedAPIToken` first for the scoped prefix, falls back to `APIToken` only when no scoped row exists, rejects disabled/expired records, and never logs a full token. `require_dataset_access` returns de-duplicated IDs in caller order and raises a focused forbidden exception if any ID is outside a retrieval key allowlist; it is a no-op for JWT/full-access contexts.

- [ ] **Step 4: Write failing `login_required` tests**

Extend the existing dynamic module fixture to provide `ScopedAPIToken`. Assert that `g.api_key_context` is set, JWT and full-access handlers still execute, retrieval keys execute only under `@login_required(api_scope=KNOWLEDGE_RETRIEVE_SCOPE)`, and an unscoped handler returns an HTTP `403` response.

- [ ] **Step 5: Integrate the policy into `_load_user` and `login_required`**

Set `g.api_key_context` and `g.auth_type = AUTH_API` for both API-key types. Convert the policy's unauthorized/forbidden conditions into the exact HTTP codes from the spec without changing the Beta-token or session fallback flows.

- [ ] **Step 6: Run auth regression tests**

Run: `uv run pytest test/unit_test/api/apps/test_api_key_auth.py test/testcases/test_web_api/test_system_app/test_apps_init_unit.py -q`

Expected: PASS.

- [ ] **Step 7: Commit the authentication boundary**

```bash
git add api/apps/api_key_auth.py api/apps/__init__.py test/unit_test/api/apps/test_api_key_auth.py test/testcases/test_web_api/test_system_app/test_apps_init_unit.py
git commit -m "feat: enforce scoped API key authentication"
```

### Task 3: Add retrieval quota and request-level usage summaries

**Files:**
- Modify: `common/constants.py`
- Modify: `api/apps/api_key_auth.py`
- Modify: `api/apps/__init__.py`
- Modify: `test/unit_test/api/apps/test_api_key_auth.py`
- Modify: `test/unit_test/api/db/services/test_api_token_service.py`

**Interfaces:**
- Produces: `RetCode.TOO_MANY_REQUESTS = 429` and `RetCode.SERVICE_UNAVAILABLE = 503`.
- Produces: `consume_retrieval_quota(context: APIKeyContext, now: float | None = None) -> int` returning remaining whole tokens, and `mark_retrieval_started() -> None`.
- Produces: async Quart `after_request` hook that calls `APIKeyUsageService.record(...)` and returns the original response unchanged.

- [ ] **Step 1: Write failing quota tests**

Mock `REDIS_CONN.lua_token_bucket` and assert capacity `60`, refill rate `1`, cost `1`, a Redis key of `api-key:retrieval-rate:<sha256>`, no plaintext token in the key, immediate attempt 61 maps to `429`, distinct keys are isolated, and Redis exceptions map to `503` rather than fail-open.

- [ ] **Step 2: Implement quota enforcement**

Skip quota for JWT/full-access contexts. For retrieval keys, call the existing Lua token bucket and include a retry hint in the `429` response. Do not mark retrieval as started until the quota call succeeds.

- [ ] **Step 3: Write failing response-classification tests**

Exercise HTTP `2xx`, `403`, `429`, `5xx`, and a `200` RAGFlow envelope whose body has nonzero `code`. Assert `success`, `denied`, `rate_limited`, or `error`; total calls always increment for valid keys, while retrieval calls increment only after `mark_retrieval_started()`.

- [ ] **Step 4: Implement the request telemetry hook**

Read only response status and the small JSON envelope, never request bodies. Catch and log `APIKeyUsageService.record` failures so telemetry cannot change the response. Unknown, disabled, and expired keys have no context and therefore no statistics write.

- [ ] **Step 5: Run focused auth/usage tests**

Run: `uv run pytest test/unit_test/api/apps/test_api_key_auth.py test/unit_test/api/db/services/test_api_token_service.py -q`

Expected: PASS.

- [ ] **Step 6: Commit quota and statistics**

```bash
git add common/constants.py api/apps/api_key_auth.py api/apps/__init__.py test/unit_test/api/apps/test_api_key_auth.py test/unit_test/api/db/services/test_api_token_service.py
git commit -m "feat: rate limit and count scoped API calls"
```

### Task 4: Replace immediate token creation with validated key management

**Files:**
- Modify: `api/utils/validation_utils.py`
- Modify: `api/apps/restful_apis/system_api.py`
- Modify: `test/testcases/test_web_api/test_system_app/test_system_routes_unit.py`
- Modify: `test/testcases/restful_api/test_system.py`
- Modify: `test/testcases/conftest.py`
- Modify: `test/benchmark/auth.py`

**Interfaces:**
- Produces: `CreateAPIKeyReq` and `UpdateAPIKeyReq` Pydantic models.
- Produces: unified `GET /api/v1/system/tokens`, validated `POST /api/v1/system/tokens`, `PATCH /api/v1/system/tokens/<token>`, and dual-table `DELETE /api/v1/system/tokens/<token>`.
- Create payload: `{name, key_type, allowed_dataset_ids, expires_in_days}` where `expires_in_days` is `30|90|180|365|null`.
- Update payload: any of `{name, allowed_dataset_ids, expires_in_days, enabled}`; omitted fields remain unchanged and `key_type` is forbidden.

- [ ] **Step 1: Write failing validation and authorization tests**

Test blank/65-character names, unknown types, retrieval with zero datasets, full-access with dataset IDs, invalid expiry, dataset from another tenant, ordinary-member rejection, and retrieval-key rejection on every management route. Preserve the accepted case where a full-access key mapped to the owner may manage keys.

- [ ] **Step 2: Run focused system-route tests and verify failure**

Run: `uv run pytest test/testcases/test_web_api/test_system_app/test_system_routes_unit.py test/testcases/restful_api/test_system.py -q`

Expected: FAIL because the new request contract and routes are absent.

- [ ] **Step 3: Implement deterministic managed-tenant and dataset validation**

Replace direct `[0]` membership indexing with a helper that accepts valid `owner`/`admin` membership or `is_superuser`, preserves the currently managed tenant semantics, and returns `403` when none is available. Require every retrieval dataset to exist, remain valid, and belong to that tenant.

- [ ] **Step 4: Implement unified list/create/update/delete behavior**

Generate full-access keys with the existing generator and scoped keys with `ragflow-rk-` plus URL-safe entropy; retry generation if either table already contains the value. Return a common shape with `key_type`, `legacy`, empty full-access allowlist/expiry, and scoped statistics. Set new full-access `is_legacy=False`; keep Beta tokens only on full-access rows.

- [ ] **Step 5: Update integration fixtures and CRUD assertions**

Change every automated full-access creation request from an empty POST to `{"name": "REST test full-access key", "key_type": "full_access"}`. Expand `test_system_tokens_auth_and_crud` to create both types, list them, patch the scoped key, disable it, and delete both.

- [ ] **Step 6: Run token-management regression tests**

Run: `uv run pytest test/testcases/test_web_api/test_system_app/test_system_routes_unit.py test/testcases/restful_api/test_system.py -q`

Expected: PASS.

- [ ] **Step 7: Commit key management APIs**

```bash
git add api/utils/validation_utils.py api/apps/restful_apis/system_api.py test/testcases/test_web_api/test_system_app/test_system_routes_unit.py test/testcases/restful_api/test_system.py test/testcases/conftest.py test/benchmark/auth.py
git commit -m "feat: manage named API key types"
```

### Task 5: Scope dataset discovery, document metadata, and retrieval

**Files:**
- Modify: `api/db/services/knowledgebase_service.py`
- Modify: `api/apps/services/dataset_api_service.py`
- Modify: `api/apps/restful_apis/dataset_api.py`
- Modify: `api/apps/restful_apis/document_api.py`
- Modify: `api/apps/restful_apis/chunk_api.py`
- Modify: `test/testcases/test_web_api/test_dataset_management/test_dataset_sdk_routes_unit.py`
- Create: `test/unit_test/api/apps/restful_apis/test_scoped_retrieval_routes.py`

**Interfaces:**
- Consumes: `KNOWLEDGE_RETRIEVE_SCOPE`, request-context allowlists, dataset enforcement, quota, and retrieval-start marker from Tasks 2–3.
- Changes: `KnowledgebaseService.get_list(..., allowed_dataset_ids: Collection[str] | None = None)` and `dataset_api_service.list_datasets(tenant_id: str, args: dict, allowed_dataset_ids: Collection[str] | None = None)`.
- Allows retrieval keys only on `GET /datasets`, `GET /datasets/<dataset_id>`, `GET /datasets/<dataset_id>/documents`, and `POST /retrieval`.

- [ ] **Step 1: Write failing pre-pagination filtering tests**

Assert that `allowed_dataset_ids` becomes a Peewee `IN` filter before `.count()` and `.paginate()`, including empty, duplicate, ID-filter, name-filter, and multi-page cases. The returned total must equal authorized rows only.

- [ ] **Step 2: Implement optional service-layer allowlist filtering**

Leave callers that pass `None` unchanged. An empty collection returns no datasets without broadening access. Keep existing team/owner visibility predicates in addition to the allowlist.

- [ ] **Step 3: Write failing route-scope and resource tests**

Test authorized list/detail/document reads, unauthorized dataset detail/documents as HTTP `403`, all non-whitelisted methods/routes as `403`, omitted retrieval IDs resolving to the key allowlist, de-duplication in caller order, mixed allowed/disallowed IDs rejecting the whole request, deleted datasets returning `404`, and full/JWT requests retaining the existing required-`dataset_ids` behavior.

- [ ] **Step 4: Mark and enforce the four allowed routes**

Use `@login_required(api_scope=KNOWLEDGE_RETRIEVE_SCOPE)`. Pass the context allowlist into list services, call `require_dataset_access` before detail/document work, and never mark write routes with the retrieval scope.

- [ ] **Step 5: Enforce retrieval allowlist and quota in the correct order**

For retrieval keys only: default missing/empty `dataset_ids` to the allowlist, complete all cheap request/dataset/document validation, then consume quota, then call `mark_retrieval_started()` immediately before expensive retrieval work. Keep existing embedding-model compatibility and document ownership checks.

- [ ] **Step 6: Run route and dataset regressions**

Run: `uv run pytest test/testcases/test_web_api/test_dataset_management/test_dataset_sdk_routes_unit.py test/unit_test/api/apps/restful_apis/test_scoped_retrieval_routes.py -q`

Expected: PASS.

- [ ] **Step 7: Commit scoped retrieval routes**

```bash
git add api/db/services/knowledgebase_service.py api/apps/services/dataset_api_service.py api/apps/restful_apis/dataset_api.py api/apps/restful_apis/document_api.py api/apps/restful_apis/chunk_api.py test/testcases/test_web_api/test_dataset_management/test_dataset_sdk_routes_unit.py test/unit_test/api/apps/restful_apis/test_scoped_retrieval_routes.py
git commit -m "feat: restrict retrieval keys to allowed datasets"
```

### Task 6: Prove the public API boundary end to end

**Files:**
- Create: `test/testcases/restful_api/test_scoped_api_keys.py`

**Interfaces:**
- Consumes: administrator JWT client, full-access client, and Task 4 key-management contract.
- Produces: black-box proof that a newly issued retrieval key works through the public `/api/v1` boundary and cannot escape its dataset scope.

- [ ] **Step 1: Add failing black-box lifecycle tests**

Create two datasets with the administrator client, create a retrieval key authorized for only one, and instantiate `RestClient(token=scoped_token)`. Assert filtered discovery, allowed document listing, omitted-ID retrieval behavior, explicit allowed retrieval, explicit other-dataset `403`, system/status `403`, write-route `403`, disable `401`, re-enable recovery, expiry behavior, and deletion `401`.

- [ ] **Step 2: Run the new integration module against a test backend**

Run: `uv run pytest test/testcases/restful_api/test_scoped_api_keys.py -q`

Expected before implementation completion: FAIL at the first scoped-key assertion.

- [ ] **Step 3: Fix only boundary defects revealed by the black-box test**

Keep fixes in the owning Task 2–5 files; do not add test-only bypasses or broaden allowed routes.

- [ ] **Step 4: Run the system, dataset, retrieval, and scoped-key REST modules together**

Run: `uv run pytest test/testcases/restful_api/test_system.py test/testcases/restful_api/test_retrieval.py test/testcases/restful_api/test_scoped_api_keys.py -q`

Expected: PASS.

- [ ] **Step 5: Commit the public boundary proof**

```bash
git add test/testcases/restful_api/test_scoped_api_keys.py
git commit -m "test: cover scoped API keys end to end"
```

### Task 7: Type the frontend API and prevent privileged flows from picking scoped keys

**Files:**
- Modify: `web/src/interfaces/database/chat.ts`
- Modify: `web/src/interfaces/request/system.ts`
- Modify: `web/src/utils/api.ts`
- Modify: `web/src/services/user-service.ts`
- Modify: `web/src/hooks/use-user-setting-request.tsx`
- Create: `web/src/utils/api-key.ts`
- Create: `web/src/utils/api-key.test.ts`
- Modify: `web/src/components/embed-dialog/use-show-embed-dialog.ts`
- Modify: `web/src/components/api-service/hooks.ts`

**Interfaces:**
- Produces: `APIKeyType`, expanded `IToken`, `ICreateAPIKeyRequest`, `IUpdateAPIKeyRequest`, and `selectFullAccessToken(tokens: IToken[]) -> IToken | undefined`.
- Produces: `useUpdateSystemToken()` and typed create/update mutation inputs.
- Treats a server item with missing `key_type` as legacy full-access during mixed-version frontend/backend deployment.

- [ ] **Step 1: Write failing selection-helper tests**

Assert that retrieval keys are skipped even when first, explicit `full_access` wins, legacy records with no `key_type` remain selectable, disabled/expired full keys are skipped, and no candidate returns `undefined`.

- [ ] **Step 2: Implement frontend types, endpoints, hooks, and selector**

Add `PATCH /system/tokens/<token>` support and keep delete behavior. Make `beta` optional because retrieval rows have none.

- [ ] **Step 3: Replace every `tokenList[0]` privileged selection**

Use `selectFullAccessToken` in both duplicated preflight hooks. Preserve current empty/Beta error messages and return a clear missing-full-access result rather than falling back to a retrieval key.

- [ ] **Step 4: Run frontend helper and type checks**

Run: `cd web && npm test -- --runInBand src/utils/api-key.test.ts`

Run: `cd web && npm run type-check`

Expected: PASS.

- [ ] **Step 5: Commit frontend data plumbing**

```bash
git add web/src/interfaces/database/chat.ts web/src/interfaces/request/system.ts web/src/utils/api.ts web/src/services/user-service.ts web/src/hooks/use-user-setting-request.tsx web/src/utils/api-key.ts web/src/utils/api-key.test.ts web/src/components/embed-dialog/use-show-embed-dialog.ts web/src/components/api-service/hooks.ts
git commit -m "feat: type and select API key permissions"
```

### Task 8: Build the named key creation and management UI

**Files:**
- Create: `web/src/components/api-service/chat-api-key-modal/api-key-form.tsx`
- Create: `web/src/components/api-service/chat-api-key-modal/api-key-form.test.tsx`
- Modify: `web/src/components/api-service/chat-api-key-modal/index.tsx`
- Modify: `web/src/components/api-service/chat-api-key-modal/index.test.tsx`
- Modify: `web/src/components/api-service/hooks.ts`
- Modify: `web/src/components/api-service/chat-overview-modal/api-content.tsx`
- Modify: `web/src/components/api-service/chat-overview-modal/backend-service-api.tsx`
- Modify: `web/src/locales/zh.ts`
- Modify: `web/src/locales/en.ts`

**Interfaces:**
- Consumes: Task 7 typed hooks, `useFetchKnowledgeList()`, existing `MultiSelect`, and tenant/user role information.
- Produces: create/edit form values `{name, key_type, allowed_dataset_ids, expires_in_days, enabled}` and a unified plaintext key table.

- [ ] **Step 1: Write failing form behavior tests**

Test required/max-length name, required type, retrieval-only knowledge-base multiselect, at-least-one dataset, default 90-day expiry, all approved expiry choices including forever, field clearing when switching to full access, and the full-access warning.

- [ ] **Step 2: Implement the focused form component**

Use existing form primitives and `MultiSelect`; do not introduce a UI dependency. The component returns only validated create/update payload fields and never permits type changes while editing.

- [ ] **Step 3: Write failing management-list tests**

Cover legacy/full/retrieval badges, plaintext copy value, knowledge-base names/count, active/disabled/expired state, totals, recent result, create form opening, edit submission, enable/disable, delete, and create remaining available with existing rows.

- [ ] **Step 4: Implement the modal list and actions**

Keep loading, empty, mutation-pending, and failure states. Refresh the unified token query after create/update/delete. A full-access row shows empty scope/expiry values and a high-privilege warning; a legacy row shows the explicit legacy badge.

- [ ] **Step 5: Hide key management from non-admin UI identities**

In `ApiContent`, compute management visibility from `tenantInfo.role in {owner, admin}` or `userInfo.is_superuser`; pass it to `BackendServiceApi` so API documentation stays visible but the Key button is absent for normal members. Backend checks remain authoritative.

- [ ] **Step 6: Add Chinese and English copy and run frontend verification**

Run: `cd web && npm test -- --runInBand src/components/api-service/chat-api-key-modal/api-key-form.test.tsx src/components/api-service/chat-api-key-modal/index.test.tsx src/utils/api-key.test.ts`

Run: `cd web && npm run type-check && npm run lint`

Expected: PASS.

- [ ] **Step 7: Commit the management UI**

```bash
git add web/src/components/api-service/chat-api-key-modal/api-key-form.tsx web/src/components/api-service/chat-api-key-modal/api-key-form.test.tsx web/src/components/api-service/chat-api-key-modal/index.tsx web/src/components/api-service/chat-api-key-modal/index.test.tsx web/src/components/api-service/hooks.ts web/src/components/api-service/chat-overview-modal/api-content.tsx web/src/components/api-service/chat-overview-modal/backend-service-api.tsx web/src/locales/zh.ts web/src/locales/en.ts
git commit -m "feat: add scoped API key management UI"
```

### Task 9: Surface MCP auth failures, document the contract, and complete rollout checks

**Files:**
- Modify: `mcp/server/server.py`
- Modify: `test/unit_test/mcp/test_server_datasets.py`
- Create: `test/unit_test/mcp/test_server_retrieval_errors.py`
- Modify: `docs/references/http_api_reference.md`

**Interfaces:**
- Consumes: unchanged MCP Authorization forwarding and the new HTTP `401/403/429/503` responses.
- Produces: MCP tool errors that preserve the backend's safe `message` and distinguish authentication, permission, rate-limit, and dependency failures without exposing token values.

- [ ] **Step 1: Write failing MCP error-propagation tests**

Mock dataset and retrieval responses for `401`, `403`, `429`, and `503`. Assert that `list_tools`/`ragflow_retrieval` surface the backend message, never include the API key, and keep successful pagination and metadata behavior unchanged.

- [ ] **Step 2: Implement a shared safe response-error extractor**

Add `_response_error_message(response, default)` and use it from dataset and retrieval paths. Do not log Authorization headers or response bodies containing credentials.

- [ ] **Step 3: Document key-management requests and scoped behavior**

Update the HTTP reference with the two key types, create/list/update/delete shapes, retrieval allowlist semantics, expiry, fixed rate, error codes, plaintext warning, and the rule that one retrieval request is not a user session.

- [ ] **Step 4: Run focused backend, MCP, and frontend suites**

Run: `uv run pytest test/unit_test/api/apps/test_api_key_auth.py test/unit_test/api/db/services/test_api_token_service.py test/unit_test/api/apps/restful_apis/test_scoped_retrieval_routes.py test/unit_test/mcp/test_server_datasets.py test/unit_test/mcp/test_server_retrieval_errors.py -q`

Run: `uv run pytest test/testcases/test_web_api/test_system_app/test_apps_init_unit.py test/testcases/test_web_api/test_system_app/test_system_routes_unit.py test/testcases/test_web_api/test_dataset_management/test_dataset_sdk_routes_unit.py -q`

Run: `cd web && npm test -- --runInBand src/utils/api-key.test.ts src/components/api-service/chat-api-key-modal/api-key-form.test.tsx src/components/api-service/chat-api-key-modal/index.test.tsx`

Expected: PASS.

- [ ] **Step 5: Run static checks**

Run: `uv run ruff check api/apps/api_key_auth.py api/apps/__init__.py api/apps/restful_apis/system_api.py api/apps/restful_apis/dataset_api.py api/apps/restful_apis/document_api.py api/apps/restful_apis/chunk_api.py api/apps/services/dataset_api_service.py api/db/db_models.py api/db/services/api_service.py api/db/services/knowledgebase_service.py`

Run: `cd web && npm run type-check && npm run lint`

Expected: PASS.

- [ ] **Step 6: Perform deployment-safety acceptance**

On a staging database, verify migration/backfill, start one old backend against the migrated schema and confirm `ragflow-rk-*` returns `401`, upgrade every backend, create a scoped WorkBuddy key, and verify connection/tool discovery/retrieval. Confirm an unauthorized dataset and `/system/status` return `403`, the 61st immediate retrieval returns `429`, disabling returns `401`, and re-enabling recovers.

- [ ] **Step 7: Commit docs and MCP handling**

```bash
git add mcp/server/server.py test/unit_test/mcp/test_server_datasets.py test/unit_test/mcp/test_server_retrieval_errors.py docs/references/http_api_reference.md
git commit -m "docs: finalize scoped API key rollout"
```

### Task 10: Final security and regression review

**Files:**
- Review all files changed by Tasks 1–9.
- Modify only files required by findings.

**Interfaces:**
- Produces: a release candidate whose permission boundary, rollback behavior, and WorkBuddy flow match the approved spec.

- [ ] **Step 1: Audit route scope coverage**

Enumerate every route marked `knowledge:retrieve`; verify the list contains only the four approved read/retrieval routes and that no write, system, Chat, Agent, model, or key-management route carries the scope.

- [ ] **Step 2: Audit credential exposure**

Search logs, Redis keys, exceptions, test snapshots, and frontend console calls for raw Token output. Plaintext is allowed only in the authenticated key-management response/UI and the Authorization header sent by clients.

- [ ] **Step 3: Run the complete relevant regression matrix**

Run: `uv run pytest test/unit_test/api/apps test/unit_test/api/db/services/test_api_token_service.py test/unit_test/mcp test/testcases/test_web_api/test_system_app test/testcases/test_web_api/test_dataset_management/test_dataset_sdk_routes_unit.py -q`

Run against a running test stack: `uv run pytest test/testcases/restful_api/test_system.py test/testcases/restful_api/test_retrieval.py test/testcases/restful_api/test_scoped_api_keys.py -q`

Run: `cd web && npm test -- --runInBand src/utils/api-key.test.ts src/components/api-service/chat-api-key-modal && npm run type-check && npm run lint`

Expected: PASS.

- [ ] **Step 4: Review the final diff and accepted risks**

Confirm there is no key hashing, employee identity system, detailed request log, reverse proxy, or restriction of full-access keys from key-management APIs. Confirm the UI and docs explicitly warn that full-access keys are plaintext administrator credentials and must not be distributed.

- [ ] **Step 5: Record the review outcome**

If the review finds a defect, return it to the task that owns the affected interface, add a focused regression test there, rerun that task's verification commands, and commit the exact corrected paths with `fix: harden scoped API key boundaries`. If no defect is found, leave the already verified tree unchanged.
