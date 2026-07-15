import asyncio
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
