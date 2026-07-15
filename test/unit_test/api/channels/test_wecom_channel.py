import asyncio
import base64
import hashlib
import json
from unittest.mock import AsyncMock

import pytest

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


@pytest.mark.asyncio
async def test_ws_request_resolves_matching_response():
    channel = make_channel()
    task = asyncio.create_task(
        channel._ws_request("aibot_send_msg", {"chatid": "chat"})
    )
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
async def test_disconnect_fails_and_clears_pending_requests():
    channel = make_channel()
    task = asyncio.create_task(channel._ws_request("aibot_send_msg", {}))
    await asyncio.sleep(0)

    channel._fail_pending_requests(ConnectionError("disconnected"))

    with pytest.raises(ConnectionError, match="disconnected"):
        await task
    assert channel._ws_pending == {}


@pytest.mark.asyncio
async def test_upload_image_uses_init_chunks_and_finish(monkeypatch):
    channel = make_channel()
    requests = []

    async def request(cmd, body, timeout=10):
        requests.append((cmd, body))
        if cmd == "aibot_upload_media_init":
            return {"body": {"upload_id": "upload-1"}}
        if cmd == "aibot_upload_media_finish":
            return {"body": {"media_id": "media-1"}}
        return {"body": {}}

    monkeypatch.setattr(channel, "_ws_request", request)
    monkeypatch.setattr("api.channels.wecom.channel.WECOM_MEDIA_CHUNK_SIZE", 3)

    assert (
        await channel._upload_websocket_image(b"abcdefg", "reference.jpg")
        == "media-1"
    )
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
    assert [request[1]["chunk_index"] for request in requests[1:4]] == [1, 2, 3]
    assert base64.b64decode(requests[1][1]["base64_data"]) == b"abc"
    assert requests[-1] == (
        "aibot_upload_media_finish",
        {"upload_id": "upload-1"},
    )


@pytest.mark.asyncio
async def test_upload_image_requires_media_id(monkeypatch):
    channel = make_channel()

    async def request(cmd, body, timeout=10):
        if cmd == "aibot_upload_media_init":
            return {"body": {"upload_id": "upload-1"}}
        return {"body": {}}

    monkeypatch.setattr(channel, "_ws_request", request)

    with pytest.raises(RuntimeError, match="media_id"):
        await channel._upload_websocket_image(b"image", "reference.jpg")


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
