# Q&A Chunk Metadata Update Validation Design

## Problem

Updating metadata such as important keywords on an automatically generated image chunk in a Q&A document fails with `Q&A must be separated by TAB/ENTER key.` The update endpoint validates the existing chunk content as a Q&A pair even when the request does not modify content.

## Design

Q&A structure validation and normalization will run only when an update request explicitly contains the `content` field. Metadata-only updates will preserve the existing content and skip Q&A structure validation. Requests that explicitly modify content will retain the current requirement that the content contain exactly one question and one answer separated by a newline or tab.

No parser, indexing, embedding, or image-storage behavior will change.

## Tests

- A Q&A image chunk with non-Q&A existing content can update important keywords when `content` is omitted.
- A Q&A chunk update that explicitly supplies invalid content continues to return the existing validation error.
