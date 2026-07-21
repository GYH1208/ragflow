import asyncio
import base64
import hashlib
import json
from unittest.mock import AsyncMock

import pytest

from api.channels.core.base import OutgoingFile, OutgoingImage, OutgoingMessage
from api.channels.wecom.channel import WeComAccount, WeComChannel


def make_channel():
    channel = WeComChannel(
        WeComAccount(
            account_id="acc",
            connection_type="websocket",
            bot_id="bot",
            secret="secret",
        )
    )
    channel._ws = AsyncMock()
    channel._ws.closed = False
    channel._ws_send_lock = asyncio.Lock()
    return channel


def test_wecom_hides_reference_markers():
    assert make_channel().hides_reference_markers is True
    assert make_channel().supports_source_files is True


@pytest.mark.asyncio
async def test_ws_request_resolves_matching_response():
    channel = make_channel()
    task = asyncio.create_task(channel._ws_request("aibot_send_msg", {"chatid": "chat"}))
    await asyncio.sleep(0)
    sent = channel._ws.send_json.await_args.args[0]
    response = {
        "cmd": "aibot_send_msg",
        "headers": sent["headers"],
        "errcode": 0,
        "body": {"ok": True},
    }
    await channel._handle_ws_payload(json.dumps(response))

    assert await task == response


@pytest.mark.asyncio
async def test_ws_request_raises_for_protocol_error():
    channel = make_channel()
    task = asyncio.create_task(channel._ws_request("aibot_send_msg", {}))
    await asyncio.sleep(0)
    sent = channel._ws.send_json.await_args.args[0]
    await channel._handle_ws_payload(
        json.dumps(
            {
                "headers": sent["headers"],
                "errcode": 40001,
                "errmsg": "bad request",
            }
        )
    )

    with pytest.raises(RuntimeError, match="40001"):
        await task


@pytest.mark.asyncio
async def test_ws_request_rejects_ack_without_errcode():
    channel = make_channel()
    task = asyncio.create_task(channel._ws_request("aibot_send_msg", {}))
    await asyncio.sleep(0)
    sent = channel._ws.send_json.await_args.args[0]

    await channel._handle_ws_payload(json.dumps({"headers": sent["headers"]}))

    with pytest.raises(RuntimeError, match="errcode"):
        await task


@pytest.mark.asyncio
async def test_ws_request_rejects_non_numeric_errcode():
    channel = make_channel()
    task = asyncio.create_task(channel._ws_request("aibot_send_msg", {}))
    await asyncio.sleep(0)
    sent = channel._ws.send_json.await_args.args[0]

    await channel._handle_ws_payload(json.dumps({"headers": sent["headers"], "errcode": "invalid"}))

    with pytest.raises(RuntimeError, match="errcode"):
        await task


@pytest.mark.asyncio
async def test_ws_payload_ignores_non_object_json():
    channel = make_channel()
    await channel._handle_ws_payload(json.dumps([]))


@pytest.mark.asyncio
async def test_disconnect_fails_and_clears_pending_requests():
    channel = make_channel()
    task = asyncio.create_task(channel._ws_request("aibot_send_msg", {}))
    await asyncio.sleep(0)

    channel._fail_pending_requests(ConnectionError("disconnected"))

    with pytest.raises(ConnectionError, match="disconnected"):
        await task
    assert channel._ws_pending == {}


@pytest.mark.asyncio
async def test_message_callback_does_not_block_ack_processing():
    channel = make_channel()
    handler_done = asyncio.Event()

    async def handler(message):
        await channel.send(OutgoingMessage(chat_id=message.chat_id, text="answer"))
        handler_done.set()

    channel.set_message_handler(handler)
    callback = {
        "cmd": "aibot_msg_callback",
        "headers": {"req_id": "callback-1"},
        "body": {
            "msgtype": "text",
            "from": {"userid": "user-1"},
            "chatid": "chat-1",
            "chattype": "group",
            "text": {"content": "question"},
        },
    }

    await asyncio.wait_for(channel._handle_ws_payload(json.dumps(callback)), 0.1)
    for _ in range(10):
        if channel._ws.send_json.await_count:
            break
        await asyncio.sleep(0)
    sent = channel._ws.send_json.await_args.args[0]
    await channel._handle_ws_payload(
        json.dumps(
            {
                "cmd": "aibot_send_msg",
                "headers": sent["headers"],
                "errcode": 0,
            }
        )
    )

    await asyncio.wait_for(handler_done.wait(), 0.1)


@pytest.mark.asyncio
async def test_upload_image_uses_init_chunks_and_finish(monkeypatch):
    channel = make_channel()
    requests = []

    async def request(cmd, body):
        requests.append((cmd, body))
        if cmd == "aibot_upload_media_init":
            return {"body": {"upload_id": "upload-1"}}
        if cmd == "aibot_upload_media_finish":
            return {"body": {"media_id": "media-1"}}
        return {"body": {}}

    monkeypatch.setattr(channel, "_ws_request", request)
    monkeypatch.setattr("api.channels.wecom.channel.WECOM_MEDIA_CHUNK_SIZE", 3)

    assert await channel._upload_websocket_image(b"abcdefg", "reference.jpg") == "media-1"
    assert requests[0] == (
        "aibot_upload_media_init",
        {
            "type": "image",
            "filename": "reference.jpg",
            "total_size": 7,
            "total_chunks": 3,
            "md5": hashlib.md5(b"abcdefg").hexdigest(),
        },
    )
    assert [request[1]["chunk_index"] for request in requests[1:4]] == [0, 1, 2]
    assert base64.b64decode(requests[1][1]["base64_data"]) == b"abc"
    assert requests[-1] == (
        "aibot_upload_media_finish",
        {"upload_id": "upload-1"},
    )


@pytest.mark.asyncio
async def test_upload_file_uses_file_media_type(monkeypatch):
    channel = make_channel()
    requests = []

    async def request(cmd, body):
        requests.append((cmd, body))
        if cmd == "aibot_upload_media_init":
            return {"body": {"upload_id": "upload-1"}}
        if cmd == "aibot_upload_media_finish":
            return {"body": {"media_id": "media-1"}}
        return {"body": {}}

    monkeypatch.setattr(channel, "_ws_request", request)

    assert await channel._upload_websocket_media(b"document", "guide.docx", "file") == "media-1"
    assert requests[0][1]["type"] == "file"
    assert requests[0][1]["filename"] == "guide.docx"


@pytest.mark.asyncio
async def test_upload_image_requires_media_id(monkeypatch):
    channel = make_channel()

    async def request(cmd, body):
        if cmd == "aibot_upload_media_init":
            return {"body": {"upload_id": "upload-1"}}
        return {"body": {}}

    monkeypatch.setattr(channel, "_ws_request", request)

    with pytest.raises(RuntimeError, match="media_id"):
        await channel._upload_websocket_image(b"image", "reference.jpg")


@pytest.mark.asyncio
async def test_upload_image_rejects_more_than_100_chunks(monkeypatch):
    channel = make_channel()

    async def request(cmd, body):
        if cmd == "aibot_upload_media_init":
            return {"body": {"upload_id": "upload-1"}}
        if cmd == "aibot_upload_media_finish":
            return {"body": {"media_id": "media-1"}}
        return {"body": {}}

    monkeypatch.setattr(channel, "_ws_request", request)
    monkeypatch.setattr("api.channels.wecom.channel.WECOM_MEDIA_CHUNK_SIZE", 1)

    with pytest.raises(ValueError, match="100"):
        await channel._upload_websocket_image(b"x" * 101, "reference.jpg")


@pytest.mark.asyncio
async def test_send_websocket_image_uses_media_id(monkeypatch):
    channel = make_channel()
    request = AsyncMock(return_value={"body": {}})
    monkeypatch.setattr(channel, "_ws_request", request)

    await channel._send_websocket_image("chat-1", "media-1")

    request.assert_awaited_once_with(
        "aibot_send_msg",
        {
            "chatid": "chat-1",
            "msgtype": "image",
            "image": {"media_id": "media-1"},
        },
    )


@pytest.mark.asyncio
async def test_send_websocket_file_uses_media_id(monkeypatch):
    channel = make_channel()
    request = AsyncMock(return_value={"body": {}})
    monkeypatch.setattr(channel, "_ws_request", request)

    await channel._send_websocket_media("chat-1", "media-1", "file")

    request.assert_awaited_once_with(
        "aibot_send_msg",
        {
            "chatid": "chat-1",
            "msgtype": "file",
            "file": {"media_id": "media-1"},
        },
    )


@pytest.mark.asyncio
async def test_websocket_sends_text_then_images_in_order(monkeypatch):
    channel = make_channel()
    events = []

    async def request(cmd, body):
        events.append((cmd, body))
        return {"body": {}}

    async def send_image(chat_id, media_id):
        events.append(("image", media_id))

    monkeypatch.setattr(channel, "_ws_request", request)
    monkeypatch.setattr(channel, "_load_stored_image", lambda image_id: image_id.encode())
    monkeypatch.setattr(
        channel,
        "_upload_websocket_image",
        AsyncMock(side_effect=["media-a", "media-b"]),
    )
    monkeypatch.setattr(channel, "_send_websocket_image", send_image)

    await channel.send(
        OutgoingMessage(
            chat_id="chat-1",
            text="answer",
            images=[OutgoingImage("image-a"), OutgoingImage("image-b")],
        )
    )

    assert events[0][1]["msgtype"] == "markdown"
    assert events[1:] == [("image", "media-a"), ("image", "media-b")]


@pytest.mark.asyncio
async def test_image_failure_does_not_block_later_images(monkeypatch):
    channel = make_channel()
    request = AsyncMock(return_value={"body": {}})
    monkeypatch.setattr(channel, "_ws_request", request)
    monkeypatch.setattr(channel, "_load_stored_image", lambda image_id: image_id.encode())
    monkeypatch.setattr(
        channel,
        "_upload_websocket_image",
        AsyncMock(side_effect=[RuntimeError("failed"), "media-b"]),
    )
    send_image = AsyncMock()
    monkeypatch.setattr(channel, "_send_websocket_image", send_image)

    await channel.send(
        OutgoingMessage(
            chat_id="chat-1",
            text="answer",
            images=[OutgoingImage("image-a"), OutgoingImage("image-b")],
        )
    )

    send_image.assert_awaited_once_with("chat-1", "media-b")


@pytest.mark.asyncio
async def test_missing_stored_image_is_skipped(monkeypatch):
    channel = make_channel()
    request = AsyncMock(return_value={"body": {}})
    monkeypatch.setattr(channel, "_ws_request", request)
    monkeypatch.setattr(channel, "_load_stored_image", lambda image_id: None)
    upload = AsyncMock()
    monkeypatch.setattr(channel, "_upload_websocket_image", upload)

    await channel.send(
        OutgoingMessage(
            chat_id="chat-1",
            text="answer",
            images=[OutgoingImage("missing")],
        )
    )

    upload.assert_not_awaited()


@pytest.mark.asyncio
async def test_text_only_message_sends_only_markdown(monkeypatch):
    channel = make_channel()
    request = AsyncMock(return_value={"body": {}})
    monkeypatch.setattr(channel, "_ws_request", request)

    await channel.send(OutgoingMessage(chat_id="chat-1", text="answer"))

    request.assert_awaited_once_with(
        "aibot_send_msg",
        {
            "chatid": "chat-1",
            "msgtype": "markdown",
            "markdown": {"content": "answer"},
        },
    )


@pytest.mark.asyncio
async def test_websocket_sends_source_files_after_text_and_images(monkeypatch):
    channel = make_channel()
    events = []

    async def request(cmd, body):
        events.append((cmd, body))
        return {"body": {}}

    async def upload_media(data, filename, media_type):
        events.append(("upload", filename, media_type, data))
        return "file-media"

    async def send_media(chat_id, media_id, media_type):
        events.append(("send", chat_id, media_id, media_type))

    monkeypatch.setattr(channel, "_ws_request", request)
    monkeypatch.setattr(channel, "_load_stored_file", lambda document_id: b"source-data")
    monkeypatch.setattr(channel, "_upload_websocket_media", upload_media)
    monkeypatch.setattr(channel, "_send_websocket_media", send_media)

    await channel.send(
        OutgoingMessage(
            chat_id="chat-1",
            text="answer",
            files=[OutgoingFile(document_id="doc-1", filename="guide.docx")],
        )
    )

    assert events[0][1]["msgtype"] == "markdown"
    assert events[1:] == [
        ("upload", "guide.docx", "file", b"source-data"),
        ("send", "chat-1", "file-media", "file"),
    ]
