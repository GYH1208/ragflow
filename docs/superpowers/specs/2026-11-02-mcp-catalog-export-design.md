# WorkBuddy complete document catalog export

The user approved a complete Excel directory of a knowledge base, with one row per document, source paths only when actually available, and requested direct implementation and deployment to the existing WorkBuddy MCP server.

## Interface and delivery

Add `export_document_catalog(dataset_id, export_id=None)`. The initial request authorizes the selected dataset with the current caller and creates a bounded background job. A short response includes status and export_id; polling the same tool with both IDs reauthorizes the dataset and binds the job to the original credential digest. Completion returns a compact summary, small preview, filename, document total, and expiring HTTP download URL. Fetch the binary file into the WorkBuddy workspace and present it using the client's available file presentation capability. A direct user download link is the fallback. Never return a server-local path as a client file, nor put the entire directory or XLSX Base64 into model context.

Download URLs are unguessable, file-scoped bearer capabilities valid for 15 minutes after completion, like a presigned download link; anyone holding one can download until expiry. They contain no employee ticket. Export state and bytes stay in bounded process memory; restart invalidates them. Logs redact download tokens. No directory listing is exposed at the download endpoint. Set no-store and attachment headers. URLs derive from the MCP request's validated origin; an optional RAGFLOW_MCP_PUBLIC_URL supports proxy deployment. Plain HTTP remains as in the current internal deployment; public deployments should use HTTPS.

## Correctness and bounds

Reuse backend permissions on every page and poll, and recheck permission before publishing the result. Fetch all document pages in unique ID order with 100 entries per request. Reject totals that change, duplicates, incomplete pages, malformed replies, and errors; never label a partial file complete. A metadata export is not a transactionally frozen database snapshot.

Maximum 50,000 documents, 120-second generation timeout, two simultaneous jobs, at most 16 retained jobs and 64 MiB retained files, 10 MiB per workbook. Admission and retention failures are explicit. Do not retain raw credentials in completed job records. Credential changes require a new export. Periodically clear expired records; cancel outstanding jobs during server shutdown.

Workbook: overview sheet with knowledge base name, ID, total, timezone-labelled export time and snapshot note; directory sheet with sequence number, name, file type, parsing status, update time, source path, document ID and dataset ID. Header styling, frozen header and filters. Write metadata strings as literal text so filenames cannot become formulas; sanitize XML-invalid controls. Keep storage location distinct from the source path.

## Alternatives

Direct embedded XLSX blobs rely on unverified WorkBuddy client handling and inflate tool context. Sending all pages to the model repeats many calls. A short-lived download with small polling responses works with normal file downloads and avoids requiring the model to obtain employee credentials. This is the chosen implementation.

## Verification

MCP protocol tests cover discovery, authorization, polling isolation, export counts, complete 5,000-row Excel, stable pagination, formula safety, mismatched totals and expiry. HTTP tests fetch and open the actual workbook without accessing host-local paths. Verify live service discovery and preserve all existing tool counters. Actual desktop WorkBuddy rendering requires a user session and must not be claimed solely from protocol tests.
