---
sidebar_position: 2
slug: /mcp_tools
sidebar_custom_props: {
  categoryIcon: LucideToolCase
}
---
# RAGFlow MCP tools

The MCP server exposes four tools:

| Tool | Purpose |
| --- | --- |
| `ragflow_retrieval` | Retrieve relevant chunks by question. Omit `dataset_ids` to search datasets accessible to the current key. |
| `search_documents` | Find documents by filename and return their dataset and document IDs. |
| `get_document_chunks` | Read an identified document's chunks in source order, with pagination. |
| `get_chunk_image` | Read one authorized chunk's image and return native MCP `ImageContent` with source IDs. |

Tool discovery returns static schemas without fetching dataset lists. Retrieval and document reading return image IDs as metadata; images are fetched only through `get_chunk_image`.

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

After updating the backend and MCP server, restart both and refresh the MCP tools in WorkBuddy. Confirm `get_chunk_image` appears alongside the existing three tools, then test an answer with a known image chunk.

For more information, see the [MCP server implementation](https://github.com/infiniflow/ragflow/blob/main/mcp/server/server.py).
