from copy import deepcopy
from types import SimpleNamespace

import pytest

import api.db.services.conversation_service as conversation_module


class _Conversation(SimpleNamespace):
    def to_dict(self):
        return {
            "id": self.id,
            "dialog_id": self.dialog_id,
            "message": self.message,
            "reference": self.reference,
        }


@pytest.mark.asyncio
async def test_async_completion_stamps_the_new_user_question(monkeypatch):
    dialog = SimpleNamespace(
        id="dialog-1",
        kb_ids=[],
        prompt_config={"prologue": "hello"},
    )
    conversation = _Conversation(
        id="conversation-1",
        dialog_id="dialog-1",
        message=[{"role": "user", "content": "legacy"}],
        reference=[],
    )
    captured_messages = []

    monkeypatch.setattr(
        conversation_module.DialogService,
        "query",
        lambda **_kwargs: [dialog],
    )
    monkeypatch.setattr(
        conversation_module.DialogService,
        "get_by_id",
        lambda _dialog_id: (True, dialog),
    )
    monkeypatch.setattr(
        conversation_module.ConversationService,
        "query",
        lambda **_kwargs: [conversation],
    )
    monkeypatch.setattr(
        conversation_module.ConversationService,
        "update_by_id",
        lambda *_args, **_kwargs: True,
    )

    async def fake_async_chat(_dialog, messages, _stream, **_kwargs):
        captured_messages.append(deepcopy(messages))
        yield {
            "answer": "answer",
            "reference": {"chunks": [], "doc_aggs": []},
            "final": True,
        }

    monkeypatch.setattr(conversation_module, "async_chat", fake_async_chat)

    answers = [
        item
        async for item in conversation_module.async_completion(
            "tenant-1",
            "dialog-1",
            "new question",
            session_id="conversation-1",
            stream=False,
        )
    ]

    new_question = captured_messages[0][-1]
    assert new_question["content"] == "new question"
    assert isinstance(new_question.get("created_at"), (int, float))
    assert "created_at" not in captured_messages[0][0]
    assert answers[0]["answer"] == "answer"


@pytest.mark.asyncio
async def test_async_iframe_completion_stamps_the_new_user_question(
    monkeypatch,
):
    dialog = SimpleNamespace(
        id="dialog-1",
        tenant_id="tenant-1",
        status="1",
        prompt_config={"prologue": "hello"},
    )
    conversation = _Conversation(
        id="conversation-1",
        dialog_id="dialog-1",
        message=[{"role": "user", "content": "legacy"}],
        reference=[],
    )
    captured_messages = []

    monkeypatch.setattr(
        conversation_module.DialogService,
        "get_by_id",
        lambda _dialog_id: (True, dialog),
    )
    monkeypatch.setattr(
        conversation_module.API4ConversationService,
        "get_by_id",
        lambda _conversation_id: (True, conversation),
    )
    monkeypatch.setattr(
        conversation_module.API4ConversationService,
        "append_message",
        lambda *_args, **_kwargs: True,
    )

    async def fake_async_chat(_dialog, messages, _stream, **_kwargs):
        captured_messages.append(deepcopy(messages))
        yield {
            "answer": "answer",
            "reference": {"chunks": [], "doc_aggs": []},
            "final": True,
        }

    monkeypatch.setattr(conversation_module, "async_chat", fake_async_chat)

    answers = [
        item
        async for item in conversation_module.async_iframe_completion(
            "dialog-1",
            "new question",
            session_id="conversation-1",
            stream=False,
            tenant_id="tenant-1",
        )
    ]

    new_question = captured_messages[0][-1]
    assert new_question["content"] == "new question"
    assert isinstance(new_question.get("created_at"), (int, float))
    assert "created_at" not in captured_messages[0][0]
    assert answers[0]["answer"] == "answer"
