---
sidebar_position: 2
slug: /mcp_tools
sidebar_custom_props: {
  categoryIcon: LucideToolCase
}
---
# RAGFlow MCP tools

The MCP server exposes seven tools:

| Tool | Purpose |
| --- | --- |
| `list_knowledge_bases` | Browse accessible knowledge bases with names, IDs, descriptions, document counts, and pagination. |
| `list_documents` | Browse a preview page of documents in one knowledge base, with parsing status and pagination. |
| `export_document_catalog` | Generate and download a complete Excel document catalog without putting every row in model context. |
| `ragflow_retrieval` | Retrieve relevant chunks by question. Omit `dataset_ids` to search datasets accessible to the current key. |
| `search_documents` | Find documents by filename and return their dataset and document IDs. |
| `get_document_chunks` | Read an identified document's chunks in source order, with pagination. |
| `get_chunk_image` | Read one authorized chunk's image and return native MCP `ImageContent` with source IDs. |

Tool discovery returns static schemas without fetching dataset lists. Retrieval and document reading return image IDs as metadata; images are fetched only through `get_chunk_image`.

## Browse a knowledge base

For “what is in the acoustics library?”, first find the knowledge base:

```json
{
  "name": "list_knowledge_bases",
  "arguments": {"name": "声学文献库", "page": 1, "page_size": 20}
}
```

`name` is an optional **exact** name filter. Omit it to browse all accessible knowledge bases; do not guess an ID from the name. The result contains `knowledge_bases` entries with `dataset_id`, `name`, `description`, and `document_count`. Only libraries accessible to the current caller are returned.

Then browse its documents using the returned `dataset_id`:

```json
{
  "name": "list_documents",
  "arguments": {"dataset_id": "<DATASET_ID>", "page": 1, "page_size": 20}
}
```

Both tools default to `page: 1`, `page_size: 20` (maximum 100). Each fetches one page and returns `pagination` with `page`, `page_size`, `total`, `total_pages`, and `has_more`. For browsing, continue only as needed while `has_more` is true. For a complete directory, use `export_document_catalog` instead of reading every page into model context. An empty or past-end page is valid; a failed request is a tool error, not an empty catalog. Documents use unique document-ID order for stable paging. WorkBuddy knowledge bases use creation-time order with an ID tie-breaker. Lists reflect current backend state rather than a frozen snapshot, so concurrent additions or deletions can change pagination. In ordinary API-key mode, knowledge-base ordering inherits the existing backend behavior without a timestamp tie-breaker; equal creation times may make that listing unstable across pages.

`list_documents` returns `documents` with `dataset_id`, `document_id`, `name`, `type`, `run` (parsing status, such as `UNSTART`, `RUNNING`, `CANCEL`, `DONE`, or `FAIL`), `chunk_count`, `update_date`, `location`, and `path`. `location` is the backend storage location and must not be presented as a folder hierarchy. `path` preserves an explicit backend source path if one is available; otherwise it is empty. The current document list API does not supply a source `path`, so no directory tree is inferred from filenames or storage keys. Unknown optional counts/status remain null, rather than being invented.

Use `get_document_chunks` to read an identified document, `search_documents` to find a known filename, and `ragflow_retrieval` with the selected `dataset_ids` to answer a content question. Browsing only lists metadata; it does not summarize document contents. Listing requests use the current caller's credentials and existing backend access checks, without caching results across users or calls. Both tools participate in the cumulative call counters, preserving existing totals when added.

After installing this MCP change, restart the MCP server and refresh/reconnect its tools in WorkBuddy. No backend change is required for the listing tools.

## Export a complete catalog in WorkBuddy

When the user asks “声学文献库里面都有哪些文献？”, find the library with `list_knowledge_bases`, then call:

```json
{
  "name": "export_document_catalog",
  "arguments": {"dataset_id": "<DATASET_ID>"}
}
```

The first result returns `status: "processing"`, an `export_id`, and `retry_after_seconds`. Wait that interval, then poll **the same export**:

```json
{
  "name": "export_document_catalog",
  "arguments": {"dataset_id": "<DATASET_ID>", "export_id": "<EXPORT_ID>"}
}
```

Do not omit `export_id` on a poll: that starts another export. The start call and each poll count as tool invocations. Polling is bound to the original credential digest and rechecks current dataset access. If the caller's ticket changes, start a new export.

A ready result contains `filename`, `document_count`, up to five preview entries, `download_url`, `expires_at`, file size, MIME type, and SHA-256. WorkBuddy should download the URL **as binary bytes** into its own workspace and present that verified local file using its available file tool (such as `present_files`). If that client cannot download or present files, show the returned download link. Do not pass a remote URL or a server filesystem path as a local attachment. Do not display the entire catalog or XLSX Base64 in the answer. Say how many documents the catalog contains and explicitly label any preview as partial.

The workbook contains a `导出说明` sheet with knowledge-base name/ID, document count and UTC export time, plus a filterable `文献目录` sheet with one row per document: sequence, name, file type, parsing status, update time, original source path, document ID and knowledge-base ID. Paths are empty when unavailable. It contains metadata only, not document bodies or chapter tables of contents. Formula-like names remain literal spreadsheet strings.

### Deployment and limits

`openpyxl>=3.1.5` is a runtime dependency. Restart the MCP server after deploying and refresh/reconnect WorkBuddy so it discovers the seventh tool. Both the MCP client and the user's downloader must be able to reach the download URL. By default it uses the MCP request's origin and endpoint path. Behind a proxy, configure `RAGFLOW_MCP_PUBLIC_URL` as the exact client-reachable MCP URL, for example `https://mcp.example.com/prefix/mcp`, and forward `/prefix/mcp/catalog-download/*` to the same MCP process. Proxy prefixes must be stripped or exposed as an ASGI root path. This feature does not set up public networking or TLS.

Downloads use a random, file-scoped **bearer capability** valid for 15 minutes after generation. No employee credential is present in the URL. Anyone who obtains the URL can download that one exported snapshot until expiry, even if the exporter's knowledge-base grant is subsequently revoked; treat the link as private and do not publish it outside the user's conversation. Export generation rechecks backend permission on each page and immediately before publishing; polling also rechecks permission. Expiry or restart invalidates the links. Responses are attachments with `private, no-store`; the MCP access logger redacts download tokens. Any reverse proxy must also redact or omit these paths in its logs. No server directory listing is exposed.

Generation runs in the background with two active jobs, at most 16 retained jobs, a 120-second generation timeout, a 50,000-document limit, 10 MiB per workbook and 64 MiB of retained files per process. Job state and file bytes are held in process memory, and expired records are cleaned periodically. Multi-worker deployments need sticky routing or shared job storage; this implementation is for the existing single-process MCP service. Timeout and shutdown signal the workbook builder to stop and wait for its thread to exit before releasing active capacity; an in-progress ZIP save may need to finish during cleanup. Shutdown then removes retained files.

Changed totals, duplicate documents, early empty/incomplete pages, permission errors, timeout, or capacity limits return an explicit failure; no partial catalog is published. Catalogs reflect reads during the export interval, not a transactionally frozen snapshot. Documents changing while the total remains constant may still change the resulting view.

Protocol-level start/poll/download tests verify XLSX delivery, including 5,000-document libraries. Actual attachment rendering depends on the WorkBuddy client; a successful `tools/list` alone is not a positive employee authorization or desktop rendering test. WorkBuddy's [connector guidance](https://open.workbuddy.cn/docs/connector) recommends short MCP requests, which is why generation uses start/poll responses. Its [results documentation](https://www.codebuddy.ai/docs/zh/workbuddy/Results) describes local Excel/CSV artifacts and previews.

## Display a chunk image

First retrieve the relevant chunks or read the document. For a relevant chunk with a non-empty `image_id`, call `get_chunk_image` when the user requests the image or it helps explain the answer. Use the chunk's `id` or `chunk_id` as `chunk_id`, not its `image_id`.

```json
{
  "name": "get_chunk_image",
  "arguments": {
    "dataset_id": "<DATASET_ID>",
    "document_id": "<DOCUMENT_ID>",
    "chunk_id": "<CHUNK_ID>"
  }
}
```

The tool automatically uses the current API key to call `GET /api/v1/datasets/{dataset_id}/documents/{document_id}/chunks/{chunk_id}/image`. The backend validates dataset access and the document/chunk/image binding. In host mode it uses the key on the current MCP request; in self-host mode it uses the configured server key. Credentials and authenticated image URLs are not returned to the client.

The result contains a text block with `dataset_id`, `document_id`, and `chunk_id`, followed by an image block with `type: "image"`, Base64 `data`, and `mimeType`. The Base64 data is in native `ImageContent`, not a text or Markdown block. Supported formats are PNG, JPEG, GIF, WebP, and BMP. The client renders the image without making a separate authenticated request to RAGFlow.

Fetch images on demand rather than fetching every retrieval result, avoid fetching the same chunk repeatedly within one answer, and identify each image's source when presenting it. These are tool-description instructions for the model; they do not enforce a per-answer image quota. Image placement depends on the client and should be verified in the version being used.

### WorkBuddy image attachments

The tested WorkBuddy client displays native MCP images in tool results and can attach them to the final answer through `present_files`, using the corresponding readable image file in its local client cache. This does not establish support for inline images in the answer body. The model may see the image without receiving its Base64 data as readable text.

A WorkBuddy Skill can guide this flow: retrieve relevant chunks, call `get_chunk_image` on demand, confirm a readable client cache file actually corresponds to that returned image, and use `present_files` to attach it with the document source. Do not guess cache paths or substitute images extracted from local documents. Repeated calls can reuse the same cached file, so modification time alone does not establish which image it contains. Preserve the stored format rather than assuming PNG. If the cache file cannot be identified or read, report the limitation. A path on the remote MCP server is not a local WorkBuddy file path.

## Limits and errors

The MCP tool checks the returned image's actual byte count and format. Its default limit is **10 MiB**, configurable through the MCP process environment variable `RAGFLOW_MCP_IMAGE_MAX_BYTES` (a positive integer byte count). The backend has its own limit, `RAGFLOW_CHUNK_IMAGE_MAX_BYTES`; the effective limit is the smaller of the two. Both processes need a restart after configuration changes. Limits apply to image bytes before Base64 encoding, which increases the payload size by about one third. The connector buffers the HTTP response before this check; the cap does not bound download memory. Images are returned as stored, without resizing or compression.

Backend authentication, permission, missing-image, size, and format errors become MCP tool errors. Network failures, malformed responses, and error messages containing credentials or URLs use a generic error. Images are not cached by the MCP server, and each read is authorized by the backend.

After updating the backend and MCP server, restart both and refresh the MCP tools in WorkBuddy. Confirm `get_chunk_image` appears in the tool list, then test an answer with a known image chunk.

For more information, see the [MCP server implementation](https://github.com/infiniflow/ragflow/blob/main/mcp/server/server.py).
