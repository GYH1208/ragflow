# MCP knowledge base and document browsing

Approved in conversation: expose a knowledge-base → document listing, preserve genuine source paths when available, and defer folder-tree traversal. The user requested direct implementation.

Add `list_knowledge_bases(page=1, page_size=20, name=None)` and `list_documents(dataset_id, page=1, page_size=20)` to the existing MCP server. `name` is an optional exact knowledge-base name. Page sizes are 1–100. Return compact entries plus `pagination` containing page, page_size, total, total_pages, and has_more. Fetch one backend page per call; do not enumerate all libraries or documents in tool discovery.

Reuse the existing authorized `/datasets` and `/datasets/{dataset_id}/documents` GET endpoints with the current caller's credentials. Do not cache listing responses or broaden access. Unknown totals must not be represented as known totals. Malformed responses and permission failures must become tool errors.

Knowledge bases return dataset_id, name, description, document_count. Documents return dataset_id, document_id, name, type, run (parsing status), chunk_count, update_date, location (backend storage location), and path (only an explicit backend source path; empty if unavailable). Never derive folder hierarchy from storage location or filenames. Do not return credentials, configuration, thumbnails, or document bodies.

Alternatives considered: a single overloaded `list_knowledge` tool hides whether results are libraries or documents; a full folder tree adds calls and assumes hierarchy that current storage does not guarantee. Choose two explicit paginated listing tools. Register both in cumulative counters without resetting existing totals. Describe when to browse versus search or retrieve, and document examples.
