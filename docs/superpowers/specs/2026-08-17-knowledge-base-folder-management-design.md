# Knowledge Base Folder Management Design

## Background

The knowledge-base upload dialog can select a local folder, and the browser exposes each selected file's relative path through `webkitRelativePath`. The current knowledge-base upload request appends only the `File` objects to `FormData`, so each multipart item retains only its basename. The backend then creates every knowledge-base `File` record directly below the knowledge-base folder. Consequently, the knowledge-base document table can only show a flat list, even when the source was a nested folder.

RAGFlow already has a hierarchical file model:

- `File.parent_id` represents folders and their children.
- `File2Document` links a managed file to its knowledge-base `Document`.
- The `/files` UI already provides folder navigation, breadcrumbs, creation, rename, move, and recursive deletion.

Knowledge-base-originated files are currently treated as read-only in `/files`, and knowledge-base document APIs remain flat. The new design reuses the existing hierarchy without making generic file-storage behavior authoritative for knowledge-base parsing.

## Goals

1. Preserve the full directory tree when a user uploads a folder to a knowledge base.
2. Provide file-manager-style folder navigation inside the knowledge-base document page.
3. Support creating, renaming, moving, and recursively deleting knowledge-base folders.
4. Keep existing knowledge-base parsing, retrieval, metadata, and enable/disable behavior for document files.
5. Ensure folder organization changes do not reset, restart, or otherwise affect document parsing.
6. Preserve existing flat documents and single-file uploads without migration requirements.

## Non-goals

- Moving knowledge-base storage objects when a file or folder is reorganized.
- Treating folders as parseable documents.
- Adding a bulk action that parses every descendant of a selected folder.
- Migrating historical flat documents into inferred folders.
- Replacing the standalone `/files` page or forcing users to upload there before linking content to a knowledge base.

## User Experience

### Folder upload

When the user selects a folder such as:

```text
2、二级文件/
  制度文件/
    A.docx
  表单/
    A.docx
```

the knowledge-base root displays one folder row named `2、二级文件`. Opening it displays `制度文件` and `表单`; opening either subfolder displays its documents. Different folders may contain files with the same basename. Names must be unique only among siblings in the same folder.

The upload preview should display each file's relative path, not only its basename, so the user can confirm the selected structure before saving.

### Navigation

- Folder rows appear before document rows.
- Clicking a folder enters that folder.
- A breadcrumb shows the path from the knowledge-base root to the current folder.
- Clearing or following the breadcrumb returns to ancestor folders without losing knowledge-base filters that remain applicable.
- Historical documents without nested `File` parents appear at the knowledge-base root.

### Row behavior

Document rows retain the existing knowledge-base columns and controls, including enabled state, chunk count, metadata, parser, parsing status, and document actions.

Folder rows show folder-specific information and actions only. They do not show enabled state, chunk count, metadata, parser, or parse controls.

Folder actions are:

- create a subfolder;
- rename;
- move;
- recursively delete.

When folders are selected, the bulk toolbar offers only move and delete. It does not offer parse, enable/disable, metadata, or other document-only actions.

### Search

Normal browsing lists only the current folder's direct children. Entering a search term switches the table to a knowledge-base-wide document search:

- results contain documents, not folder rows;
- every result displays its full relative folder path;
- files with identical basenames are distinguishable by path;
- opening a result navigates to its containing folder and locates the file;
- clearing the search restores the current folder listing.

## Architecture

### Source of truth

The existing `File` hierarchy is the source of truth for organization:

- the knowledge base has an existing root `File` folder;
- nested directories are represented by `File` records of type `folder`;
- document files are represented by non-folder `File` records;
- each document file remains linked to exactly one `Document` through `File2Document`.

The `Document` remains the source of truth for parsing and retrieval. No folder record is added to `Document`.

### Parsing and storage isolation

Knowledge-base parsing resolves content through `File2DocumentService.get_storage_address()`. For knowledge-base-originated files, the returned storage address is `Document.kb_id` plus `Document.location`. It does not depend on `File.parent_id`.

The following invariants are mandatory:

1. Creating, moving, or renaming a folder never changes any descendant `Document.location`.
2. Moving a knowledge-base file changes only its organizational `File.parent_id`.
3. Moving a knowledge-base file does not move its storage object.
4. Folder operations never change `Document.run`, progress, chunk count, token count, parser configuration, metadata, or indexed chunk content.
5. Folder operations never enqueue parsing tasks.
6. Deletion is the only folder operation that removes documents or parsing results.

The generic file-manager move implementation moves storage objects between folder-based buckets. Knowledge-base files require a dedicated branch that preserves the knowledge-base storage address and must not use the generic storage-move behavior unchanged.

### Upload data flow

1. The browser selects files with `webkitdirectory` and provides `webkitRelativePath` for each file.
2. The frontend submits one relative path for every multipart file, preserving item order and using the basename as the multipart filename.
3. The backend validates that the number of paths equals the number of files.
4. Each relative path is normalized and split into directory segments plus a basename.
5. The first segment, representing the selected top-level folder, is retained.
6. The backend resolves or creates folder records beneath the knowledge-base root, using sibling-scoped uniqueness.
7. The backend creates the `Document`, stores its content in the existing knowledge-base bucket, creates the leaf `File` under the resolved folder, and creates the `File2Document` link.
8. If "parse on creation" is enabled, the existing document IDs are passed to the existing parse action exactly as they are for flat uploads.

Single-file uploads omit or send an empty relative path and continue to create the document at the knowledge-base root.

### Path validation

Relative paths are untrusted input. Normalization must:

- convert backslashes to forward slashes;
- reject absolute paths, drive-prefixed paths, NUL characters, and `.` or `..` traversal segments;
- remove empty segments caused by duplicate separators;
- retain valid Unicode, including Chinese directory and file names;
- enforce existing database byte-length limits on each segment;
- enforce a bounded directory depth;
- verify that the final path basename matches the uploaded file basename;
- reject a request whose file and path counts differ.

The existing `sanitize_path()` behavior is not suitable because it strips characters outside an ASCII allowlist. The new validation must reject unsafe structure without deleting valid Unicode characters or silently changing path identity.

### Listing APIs

The knowledge-base page needs a hierarchical listing operation that accepts:

- knowledge-base ID;
- current folder ID, defaulting to the knowledge-base root;
- pagination and sort parameters;
- existing applicable document filters.

The response contains direct child folders and direct child documents. Document entries include the current document-list fields plus their file ID and parent folder ID. Folder entries contain a discriminant identifying them as folders and omit document-only state.

The server verifies that the requested folder belongs to the requested knowledge base and that the user has permission for that knowledge base. A folder ID from another knowledge base or another tenant is rejected.

The global search operation continues to query documents across the whole knowledge base, joins through `File2Document`, and returns the folder ancestry required to render and navigate the result path.

### Folder operations

Folder creation, rename, and move use sibling-scoped name validation. Moves reject:

- moving a folder into itself;
- moving a folder into one of its descendants;
- moving an entry into a folder from another knowledge base or tenant;
- creating a sibling name collision at the destination.

Renaming a folder changes only the folder `File.name`.

Moving a folder updates its organizational parent without rewriting descendant records or storage locations.

Renaming a document file uses the existing knowledge-base rename behavior so that:

- `File.name` and `Document.name` remain synchronized;
- the document-store title fields are updated;
- the file extension cannot change;
- no reparse is triggered;
- `Document.location` remains unchanged.

### Recursive deletion

Before deletion, the API calculates the number of descendant documents and the UI presents that count in a destructive confirmation message.

After permission and ownership preflight checks pass, recursive deletion processes descendants through existing knowledge-base document deletion services so that documents, tasks, chunks, thumbnails, storage objects, file links, and file records are removed consistently. Folder records are removed after their children.

Because the database, object storage, and document store cannot share one transaction, deletion returns structured per-entry failures if an external cleanup step fails. The UI refreshes the directory and reports partial failure instead of claiming complete success. A retry must be safe for entries already removed.

### Existing `/files` integration

Shared presentation components and generic folder-selection utilities should be reused where their contracts fit. Knowledge-base operations must use knowledge-base-aware APIs or explicit knowledge-base branches because existing `/files` code intentionally hides or skips mutation of `FileSource.KNOWLEDGEBASE` entries.

The standalone `/files` behavior is unchanged by this feature.

## Error Handling

- Invalid or mismatched relative paths reject the affected upload before any record is created for that file.
- Unsupported files retain the current partial-upload response semantics; successfully uploaded siblings keep their folder structure.
- Concurrent attempts to create the same folder resolve to one sibling folder through database-level or transaction-protected uniqueness handling.
- A failed file upload must not leave an empty chain of newly created folders unless another successful file uses that chain. Request-created unused folders are cleaned up.
- Move and rename validate all constraints before mutating state.
- UI error messages identify the affected path, not only the basename.
- Parsing jobs already in progress continue through a move or folder rename because storage addresses remain stable.
- Deleting a running document uses the existing cancellation and cleanup behavior.

## Testing Strategy

### Frontend unit and component tests

- Folder selection submits one relative path for every file.
- Upload preview renders relative paths.
- Folder rows and document rows render their distinct columns and actions.
- Navigation and breadcrumbs update the current folder.
- Folder selection exposes only move and delete bulk actions.
- Global search displays paths, distinguishes duplicate basenames, and navigates to the containing folder.
- Clearing search restores the previous folder listing.

### Backend unit tests

- Unicode paths, including Chinese names, remain unchanged after validation.
- Slash and backslash inputs normalize consistently.
- Absolute paths, drive prefixes, NULs, traversal, excessive segment lengths, excessive depth, basename mismatch, and path/file count mismatch are rejected.
- Folder resolution creates the expected tree and reuses existing sibling folders.
- During upload, a basename collision within the same folder is resolved with the existing numbered duplicate-name convention. Identical basenames in different folders remain unchanged. Interactive create, rename, and move operations reject sibling collisions instead of silently renaming the entry.
- Cross-knowledge-base and cross-tenant folder IDs are rejected.
- Folder cycle moves and destination collisions are rejected.

### Parsing isolation tests

For a parsed document, capture `Document.location`, parsing state, chunk count, token count, parser configuration, metadata, and indexed chunks before each organizational operation. Verify that folder rename, folder move, and file move leave every captured parsing field unchanged and do not enqueue a task.

Verify that file rename updates the database and indexed title fields without changing the storage address or recreating chunks.

### Deletion tests

- Recursive deletion removes nested documents, tasks, chunks, thumbnails, storage objects, `File2Document` links, file records, and folder records.
- A permission failure during preflight performs no mutation.
- External cleanup failures return structured partial results and are retry-safe.
- Deleting a folder containing a running document follows existing task cancellation semantics.

### Integration tests

- Upload a multi-level folder with duplicate basenames in different branches and valid Chinese names.
- Verify the resulting folder hierarchy through the listing API and UI.
- Enable parse-on-creation and verify that all supported documents reach the expected parse status with valid chunks.
- Move and rename parent folders after parsing, then verify document preview, download, retrieval, and citations still work.
- Verify historical flat documents and new single-file uploads remain visible at the knowledge-base root.

## Acceptance Criteria

1. Uploading a selected folder preserves its top-level folder and all nested directories in the knowledge-base UI.
2. Users can navigate with folder rows and breadcrumbs and can create, rename, move, and recursively delete folders.
3. Knowledge-base-wide search returns document paths and can locate a result in its containing folder.
4. Folder rows never expose document parsing controls.
5. Moving or renaming files or folders does not reparse documents and does not change knowledge-base storage addresses.
6. File rename keeps the document database and retrieval index title synchronized without reparse.
7. Recursive deletion cleans up all descendant knowledge-base resources and reports partial failures honestly.
8. Unicode folder names are preserved and unsafe paths are rejected.
9. Existing flat documents and single-file upload behavior remain compatible.
10. Automated frontend, backend, parsing-isolation, deletion, and integration tests cover the behaviors above.
