# MCP chunk image downloads

The agreed change keeps `get_chunk_image`'s native image response and adds an
explicit file delivery path. WorkBuddy currently spends minutes finding a file
it has already cached. The MCP server cannot know a client's filesystem path.

## Approach

Use the existing catalog export pattern: after the backend authorizes and
returns an image, issue a random, short-lived download capability. Add download
metadata to the existing source text block; preserve the native image block.
Cache discovery alone depends on WorkBuddy internals; changing WorkBuddy itself
is outside this repository. Neither is the chosen delivery mechanism.

`GET /mcp/image-download/{token}` returns the exact validated bytes as an
attachment. Metadata includes `download_url`, `filename`, `mime_type`,
`size_bytes`, `sha256`, `expires_at`, and delivery instructions. Filenames use
validated document/chunk IDs and the detected extension, avoiding extra queries.
The public MCP endpoint comes from `RAGFLOW_MCP_PUBLIC_URL` or the HTTP request
at `/mcp`. SSE needs an explicit public URL. Proxy prefixes must be preserved.

Links are bearer capabilities for one image snapshot, valid for 15 minutes;
no user credential is stored in them or in the cache. Existing links remain
usable until expiry even if source permissions change; new tool calls always
recheck backend access. Downloads need no employee headers. Tokens are redacted
from the MCP access log; reverse proxies must likewise redact their paths.

Bound the cache to 128 images and 64 MiB, prune expired entries every minute
and on access, and clear it on shutdown. Do not evict valid links to make room.
On missing public URL or capacity exhaustion, preserve native image delivery
and report `download_error` without exposing secrets. Existing backend errors,
format validation and image limits remain unchanged. No new dependencies.

Single process, like catalog exports; multi-worker use needs sticky routing or
shared storage. This branch implements and verifies code, documents deployment
and provides replacement WorkBuddy Skill rules. It does not modify the user's
Windows Skill or deploy/restart production automatically.

## Acceptance

Protocol tests prove original image preservation, byte/hash equality, correct
auth forwarding, denied access, format/size checks, expiry, unknown tokens,
capacity recovery, shutdown cleanup, prefix handling and log redaction. Existing
MCP tests stay green. Desktop rendering and end-to-end latency must subsequently
be measured in WorkBuddy with a new image.
