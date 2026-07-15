# WeCom WebSocket Reference Images Design

## Goal

When a RAG answer is delivered through a WeCom channel configured for WebSocket connectivity, send the cropped images belonging to the chunks actually cited in the answer. Keep the existing Markdown answer and send the cited images afterward in first-citation order.

## Scope

- Only the WeCom WebSocket connection mode gains image delivery.
- Only chunks cited with the current `[ID:n]` citation syntax are eligible.
- Citation indices are zero-based, matching the existing chat UI and retrieval pipeline.
- A cited chunk is sent only when its formatted reference contains a non-empty `image_id`.
- Duplicate `image_id` values are sent once, at their first citation position.
- Retrieved but uncited images are not sent.
- The stored crop is sent without rendering the web UI's CSS-only `Fig. n` label into the bitmap.
- Existing text-only behavior remains unchanged for every other channel and for WeCom webhook mode.

## Architecture

### Outgoing message model

Extend the common outgoing message model with an optional list of storage-backed image references. An image reference contains the composite RAGFlow `image_id`; channel implementations that do not support images can continue to use only the existing text fields.

### Citation selection

After `async_chat` returns its final answer, the channel bootstrap handler retains the formatted `reference.chunks` produced by `structure_answer`. A focused helper scans the answer for `[ID:n]` markers, normalizes supported Arabic/Persian digits, validates indices, filters chunks without an `image_id`, and removes duplicates without changing first-citation order.

The resulting image references are attached to the same `OutgoingMessage` as the Markdown answer. Selection is independent of WeCom protocol handling and can be unit tested without a live connection or object store.

### Storage loading

The WeCom adapter resolves each composite `image_id` into its bucket and object key using the existing storage identifier convention, then reads the original bytes from `settings.STORAGE_IMPL`. Invalid identifiers, missing objects, and unsupported data are logged and skipped without failing the text reply or later images.

### WebSocket request-response routing

The current receive loop logs command responses but cannot return them to a sender. Add a pending-request map keyed by `headers.req_id`. A request helper will:

1. Allocate a unique request ID.
2. Register a Future before sending the frame under the existing WebSocket send lock.
3. Wait for the matching response with a bounded timeout.
4. Raise a protocol error when `errcode` is non-zero or the response shape is invalid.
5. Always remove the pending entry.

The receive handler resolves matching Futures before applying existing callback/event handling. When the socket disconnects or the channel stops, all pending Futures are failed and cleared.

### Media upload and delivery

For each selected image, the adapter performs the official long-connection upload sequence:

1. `aibot_upload_media_init` with type `image`, filename, byte size, chunk count, and MD5.
2. `aibot_upload_media_chunk` for each Base64-encoded chunk, numbered from 1.
3. `aibot_upload_media_finish` to receive the temporary `media_id`.
4. `aibot_send_msg` to the same `chatid` with `msgtype: image` and the returned `media_id`.

The Markdown answer is sent first. Images are uploaded and sent sequentially to preserve citation order and avoid unnecessary concurrent WebSocket traffic. Failure of one image is logged and does not prevent subsequent images from being attempted.

## Data Flow

```text
async_chat final answer
  -> structure_answer formats reference.chunks
  -> extract cited image_id values from answer
  -> OutgoingMessage(text, images)
  -> aibot_send_msg(markdown)
  -> for each image_id:
       STORAGE_IMPL.get
       -> upload init
       -> upload chunks
       -> upload finish (media_id)
       -> aibot_send_msg(image)
```

## Error Handling

- An invalid or out-of-range citation is ignored.
- A citation without an image is ignored.
- Missing image storage data logs an error and skips that image.
- WebSocket request timeout, malformed acknowledgement, or non-zero `errcode` fails only the current operation.
- An image upload/send failure does not retract or duplicate the already-sent Markdown answer.
- Disconnect and stop paths fail pending request Futures so callers do not wait until timeout.
- Existing reconnect behavior remains responsible for establishing a new socket; the first version does not retry a partially uploaded image across reconnects.

## Testing

Add isolated unit tests covering:

- Citation parsing, supported digit normalization, index validation, ordering, and image deduplication.
- Backward-compatible construction of text-only outgoing messages.
- WebSocket response correlation and non-zero error handling.
- Upload init/chunk/finish frame shapes, Base64 encoding, MD5, and media ID extraction.
- Image message frame shape and preservation of text-before-images ordering.
- Missing storage objects and per-image failure isolation.
- Cleanup of pending requests on disconnect or channel stop.

No live WeCom credentials or external network access are required for unit tests.

## Acceptance Criteria

1. A WebSocket WeCom answer citing image-backed chunks sends its Markdown text followed by exactly those images.
2. Images appear in first-citation order and duplicates are sent once.
3. Uncited images and cited non-image chunks are not sent.
4. Text replies continue to work when no cited image exists or image delivery fails.
5. Other channel adapters require no behavior change.
6. Automated tests validate the citation and WebSocket media protocol paths.
