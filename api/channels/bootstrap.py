#
#  Copyright 2024 The InfiniFlow Authors. All Rights Reserved.
#
#  Licensed under the Apache License, Version 2.0 (the "License");
#  you may not use this file except in compliance with the License.
#  You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
#  Unless required by applicable law or agreed to in writing, software
#  distributed under the License is distributed on an "AS IS" BASIS,
#  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#  See the License for the specific language governing permissions and
#  limitations under the License.
#
"""Chat channel runtime, embedded in the RAGFlow API server.

Continuously reconciles the running channel bots against the ``chat_channel``
table: newly added bots are started, deleted ones are stopped, and edited ones
(credential/type change) are restarted — without restarting the server. Inbound
messages are answered with a RAG completion routed through the conversation
wired to that bot. Replaces the standalone ``server.py`` entrypoint.
"""

from __future__ import annotations

import asyncio
import hashlib
import importlib
import json
import logging
import re
import threading

from api.channels.core.base import OutgoingImage

LOGGER = logging.getLogger(__name__)

_CITATION_PATTERN = re.compile(r"\[(?:ID:)?([0-9\u0660-\u0669\u06F0-\u06F9]+)\]")
_CITATION_DIGIT_TRANSLATION = str.maketrans(
    "٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹",
    "01234567890123456789",
)
_HORIZONTAL_WHITESPACE = " \t"
_CITATION_TRAILING_PUNCTUATION = frozenset(",.;:!?，。；：！？、)]}）】")

# Channel packages bundled under api/channels that self-register on import.
_BUNDLED_CHANNELS = (
    "feishu",
    "discord",
    "telegram",
    "line",
    "wecom",
    "qqbot",
    "dingtalk",
    "whatsapp",
)

# How often (seconds) to reconcile running channels against the database.
_RECONCILE_INTERVAL_SECS = 10


def _trim_output_horizontal_suffix(parts: list[str]) -> None:
    while parts:
        trimmed = parts[-1].rstrip(_HORIZONTAL_WHITESPACE)
        if trimmed:
            parts[-1] = trimmed
            return
        parts.pop()


def _prepare_cited_output(answer: str, chunks: object) -> tuple[str, list[OutgoingImage]]:
    text = answer or ""
    valid_chunks = chunks if isinstance(chunks, list) else []
    parts: list[str] = []
    images: list[OutgoingImage] = []
    seen: set[str] = set()
    cursor = 0

    for match in _CITATION_PATTERN.finditer(text):
        segment = text[cursor : match.start()]
        if segment:
            parts.append(segment)
        cursor = match.end()

        index = int(match.group(1).translate(_CITATION_DIGIT_TRANSLATION))
        if index < len(valid_chunks) and isinstance(valid_chunks[index], dict):
            image_id = str(valid_chunks[index].get("image_id") or "")
            if image_id and image_id not in seen:
                seen.add(image_id)
                images.append(OutgoingImage(image_id=image_id))

        space_end = cursor
        while space_end < len(text) and text[space_end] in _HORIZONTAL_WHITESPACE:
            space_end += 1

        next_char = text[space_end : space_end + 1]
        last_char = parts[-1][-1] if parts else ""
        if not next_char or next_char in "\r\n" or next_char in _CITATION_TRAILING_PUNCTUATION:
            _trim_output_horizontal_suffix(parts)
            cursor = space_end
        elif last_char in _HORIZONTAL_WHITESPACE or not last_char or last_char in "\r\n":
            cursor = space_end

    parts.append(text[cursor:])
    return "".join(parts), images


def _register_channels() -> None:
    """Import each bundled channel package so it self-registers a builder.

    Each channel is imported independently: a missing optional dependency only
    disables that one channel instead of taking down the whole channel server.
    """
    for name in _BUNDLED_CHANNELS:
        try:
            importlib.import_module(f"api.channels.{name}")
        except Exception as ex:
            LOGGER.warning("chat channel '%s' unavailable: %s", name, ex)


def _fingerprint(channel: str, credential: dict) -> str:
    """Stable hash of the parts that require a channel restart when changed."""
    payload = json.dumps(
        {"channel": channel, "credential": credential},
        sort_keys=True,
        default=str,
    )
    return hashlib.md5(payload.encode("utf-8")).hexdigest()


def _desired_channels() -> dict:
    """Return {chat_channel.id: (channel_type, credential, fingerprint)} for enabled bots."""
    from api.db.services.chat_channel_service import ChatChannelService

    desired: dict = {}
    for row in ChatChannelService.list_active():
        credential = (row.config or {}).get("credential", {}) or {}
        desired[row.id] = (row.channel, credential, _fingerprint(row.channel, credential))
    return desired


def _build_one(account_id: str, channel: str, credential: dict):
    """Build a single Channel instance, or None if the type has no builder."""
    from api.channels.core.registry import build_channels

    # account_id == chat_channel.id.
    instances = build_channels({"channels": {channel: {"accounts": {account_id: credential}}}})
    return instances[0] if instances else None


def _make_chat_handler(ch):
    """Build the inbound-message handler bound to a single channel.

    Mirrors the non-streaming path of ``session_completion``: the message is
    appended to a per-end-user conversation under the dialog connected to the
    bot, a RAG completion is run against that dialog, and the answer is sent
    back. The connected dialog is resolved per message, so connection changes
    take effect immediately without restarting the channel. Channels with no
    connected dialog ignore inbound messages.
    """
    from api.channels.core.base import IncomingMessage, OutgoingMessage

    from api.db.services.chat_channel_service import ChatChannelService
    from api.db.services.conversation_service import ConversationService, structure_answer
    from api.db.services.dialog_service import DialogService, async_chat
    from common.misc_utils import get_uuid

    async def handle(msg: IncomingMessage) -> None:
        if not (msg.text or "").strip():
            return

        # account_id == chat_channel.id; re-read so a re-connected dialog applies live.
        e, cc = ChatChannelService.get_by_id(ch.account_id)
        if not e or not cc.chat_id:
            LOGGER.info(
                "[%s:%s] no dialog connected; ignoring message",
                ch.channel_id,
                ch.account_id,
            )
            return

        e, dia = DialogService.get_by_id(cc.chat_id)
        if not e:
            LOGGER.warning("[%s:%s] connected dialog not found: %s", ch.channel_id, ch.account_id, cc.chat_id)
            return

        conv = ConversationService.get_or_create_for_channel(cc.chat_id, ch.account_id, msg.chat_id)
        if conv is None:
            LOGGER.warning("[%s:%s] failed to get conversation for chat %s", ch.channel_id, ch.account_id, msg.chat_id)
            return

        message_id = get_uuid()
        if not conv.message:
            conv.message = []
        conv.message.append({"role": "user", "content": msg.text, "id": message_id})
        if not conv.reference:
            conv.reference = []
        conv.reference = [r for r in conv.reference if r]
        conv.reference.append({"chunks": [], "doc_aggs": []})

        history = []
        for m in conv.message:
            if m["role"] == "system":
                continue
            if m["role"] == "assistant" and not history:
                continue
            history.append(m)

        answer_text = ""
        answer_images = []
        try:
            chat_kwargs = {"quote": bool(ch.supports_reference_images)}
            if "{knowledge}" in (dia.prompt_config or {}).get("system", ""):
                chat_kwargs["knowledge"] = ""
            async for ans in async_chat(dia, history, False, **chat_kwargs):
                structure_answer(conv, ans, message_id, conv.id)
                raw_answer = (ans or {}).get("answer", "") or ""
                reference = (ans or {}).get("reference") or {}
                prepared_text, cited_images = _prepare_cited_output(raw_answer, reference.get("chunks"))
                answer_text = prepared_text if ch.hides_reference_markers else raw_answer
                answer_images = cited_images if ch.supports_reference_images else []
                ConversationService.update_by_id(conv.id, conv.to_dict())
                break
        except Exception as ex:
            LOGGER.exception("[%s:%s] completion failed: %s", ch.channel_id, ch.account_id, ex)
            answer_text = f"**ERROR**: {ex}"

        if answer_text:
            await ch.send(
                OutgoingMessage(
                    chat_id=msg.chat_id,
                    text=answer_text,
                    reply_to_message_id=msg.message_id or None,
                    images=answer_images,
                )
            )

    return handle


async def _stop_channel(running: dict, account_id: str) -> None:
    entry = running.pop(account_id, None)
    if not entry:
        return
    ch = entry["ch"]
    try:
        await ch.stop()
        LOGGER.info("stopped chat channel %s:%s", ch.channel_id, account_id)
    except Exception as ex:
        LOGGER.error("failed to stop chat channel %s: %s", account_id, ex)


async def _start_channel(running: dict, account_id: str, channel: str, credential: dict, fp: str) -> bool:
    """Build, wire and start one channel. Returns True on success.

    Any failure (e.g. invalid credentials) is contained here so a single bad bot
    config never aborts the reconcile pass for the other channels.
    """
    try:
        ch = _build_one(account_id, channel, credential)
    except Exception as ex:
        LOGGER.error(
            "failed to build chat channel %s (%s); check its credentials: %s",
            account_id,
            channel,
            ex,
        )
        return False
    if ch is None:
        return False

    ch.set_message_handler(_make_chat_handler(ch))
    try:
        await ch.start()
    except Exception as ex:
        LOGGER.error("failed to start chat channel %s (%s): %s", account_id, channel, ex)
        return False

    running[account_id] = {"ch": ch, "fp": fp}
    LOGGER.info("started chat channel %s:%s", ch.channel_id, account_id)
    return True


async def _reconcile(running: dict, failed: dict) -> None:
    """Diff desired (DB) vs running channels and apply start/stop/restart.

    ``failed`` remembers configs that could not be started so they are not
    retried (and re-logged) every tick until their credentials change.
    """
    desired = await asyncio.to_thread(_desired_channels)

    # Stop channels that were removed or whose credentials/type changed.
    for account_id in list(running.keys()):
        changed = account_id in desired and desired[account_id][2] != running[account_id]["fp"]
        if account_id not in desired or changed:
            await _stop_channel(running, account_id)

    # Drop remembered failures that are gone or whose config changed, so an
    # edited (hopefully fixed) bot is retried.
    for account_id in list(failed.keys()):
        if account_id not in desired or desired[account_id][2] != failed[account_id]:
            failed.pop(account_id, None)

    active_whatsapp = any(channel == "whatsapp" for channel, _, _ in desired.values())
    if not active_whatsapp:
        active_whatsapp = any(entry["ch"].channel_id == "whatsapp" for entry in running.values())
    from api.channels.whatsapp.gateway import sync_whatsapp_gateway

    try:
        await sync_whatsapp_gateway(active_whatsapp)
    except Exception:
        LOGGER.exception("failed to sync WhatsApp gateway enabled=%s", active_whatsapp)

    # Start channels that are new (skip ones already known to fail with this config).
    for account_id, (channel, credential, fp) in desired.items():
        if account_id in running or failed.get(account_id) == fp:
            continue
        if not await _start_channel(running, account_id, channel, credential, fp):
            failed[account_id] = fp


async def run_channels(stop_event: threading.Event) -> None:
    """Reconcile and run channels until ``stop_event`` is set."""
    _register_channels()

    running: dict = {}
    failed: dict = {}
    try:
        while not stop_event.is_set():
            try:
                await _reconcile(running, failed)
            except Exception as ex:
                LOGGER.error("chat channel reconcile failed: %s", ex)

            for _ in range(_RECONCILE_INTERVAL_SECS):
                if stop_event.is_set():
                    break
                await asyncio.sleep(1)
    finally:
        LOGGER.info("Stopping chat channels...")
        for account_id in list(running.keys()):
            await _stop_channel(running, account_id)


def start_channel_server(stop_event: threading.Event) -> None:
    """Thread entrypoint: run the channel event loop, isolating any failure."""
    try:
        asyncio.run(run_channels(stop_event))
    except Exception as ex:
        LOGGER.exception("Chat channel server crashed: %s", ex)
