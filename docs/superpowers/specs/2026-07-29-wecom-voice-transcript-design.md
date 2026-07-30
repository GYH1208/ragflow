# WeCom Voice Transcript Handling

## Goal

Allow the existing WeCom AI Bot WebSocket channel to answer native voice
messages by treating WeCom's built-in transcription as ordinary user text.

## Scope

This change applies to the WebSocket `aibot_msg_callback` path. A callback with
`msgtype: "voice"` supplies its transcript in `voice.content`. The channel will
read that value and pass it to the existing `_handle_text_message` method.

Text callbacks keep using `text.content`. Other message types remain ignored.
The generic RAGFlow speech-to-text model is not called because WeCom has already
performed the transcription.

## Data Flow

1. `_handle_ws_message` receives the callback body.
2. For `text`, it selects `body.text.content`.
3. For `voice`, it selects `body.voice.content`.
4. It preserves the existing sender, chat, request ID, chat type, and raw
   callback metadata.
5. It passes the selected content through `_handle_text_message`, which rejects
   empty content and dispatches a normal `IncomingMessage`.
6. The existing conversation and reply pipeline handles the message unchanged.

## Error Handling

Missing or empty `voice.content` follows the existing empty-text behavior and
does not dispatch a conversation. Unsupported message types remain ignored.
No audio download or secondary transcription fallback is introduced.

## Testing

Add an asynchronous regression test that sends a representative `voice`
callback to the real `_handle_ws_message` implementation and asserts that the
registered message handler receives:

- the transcript as `IncomingMessage.text`;
- the original sender, chat, callback request ID, and group chat type;
- the original callback object as raw metadata.

The test must fail against the current text-only implementation before the
production change is applied. Existing WeCom channel tests must continue to
pass.
