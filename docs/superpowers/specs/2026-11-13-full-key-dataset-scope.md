# Dataset scope for full-access API keys

Add an optional dataset allowlist to full-access API keys. Existing keys and API callers that omit the allowlist retain account-level access. A nonempty `allowed_dataset_ids` limits a full key to those datasets; an empty list explicitly means all datasets accessible to its owner. Retrieval keys still require at least one dataset and remain read-only.

Persist the list on `APIToken` and add an idempotent startup migration with an empty-list default. Creation, editing and serialization support the list, validate ownership of every selected dataset, and preserve the existing ban on full-key expiry settings. Malformed persisted scope rejects authentication rather than granting unrestricted access.

Enforce the scope in the common authentication guard, before handlers run. Allow dataset-addressed REST operations after checking the path dataset; filter dataset discovery before pagination; check every dataset for bulk deletion, searches and aggregations. A restricted full key can upload, parse, inspect and modify documents/chunks and write Q&A within its scope. Authorized document-only read aliases resolve the document's dataset before access. Unhandled routes, global file operations, dataset creation and key/account/agent management are denied to restricted full keys. Beta authentication must not bypass a full key's dataset scope. Preserve normal ownership/team checks and existing JWT, unrestricted full-key and retrieval-key behavior.

The UI offers all accessible datasets or selected datasets for full keys. New full keys default to selected scope and require a selection; editing old keys shows all accessible datasets. The list renders the selected datasets or an explicit all-accessible label. Selecting all sends `[]`; switching key types must not accidentally broaden a selected scope. Read-only keys retain their existing expiry form.

Update the local ignored handoff guide and tracked Q&A reference. Do not restart the running service, alter production data/schema, change existing key scopes, commit, push or merge without a separate request.

Follow-up authorization on 2026-11-13: the user requested “Ok，提交，部署”, authorizing a local commit and deployment. Existing key scopes remain unchanged; push/merge are not included.
