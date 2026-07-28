from contextlib import nullcontext
from types import SimpleNamespace

import api.db.services.conversation_service as conversation_module
from api.db.services.conversation_service import (
    ConversationService,
    structure_answer,
)


def test_structure_answer_records_message_id_on_reference():
    conv = SimpleNamespace(
        message=[],
        reference=[{"chunks": [], "doc_aggs": []}],
    )
    ans = {
        "answer": "回答",
        "reference": {"chunks": [{"id": "c1"}], "doc_aggs": []},
        "final": True,
    }

    structure_answer(conv, ans, "message-2", "conversation-1")

    assert conv.reference[-1]["message_id"] == "message-2"


def test_merge_updates_only_matching_reference():
    references = [
        {"message_id": "message-1", "chunks": [{"id": "old"}]},
        {"message_id": "message-2", "chunks": [{"id": "new"}]},
    ]

    updated, found = ConversationService._merge_reference_evidence(
        references,
        "message-1",
        ["old", "old"],
    )

    assert found is True
    assert updated[0]["used_chunk_ids"] == ["old"]
    assert "used_chunk_ids" not in updated[1]
    assert "used_chunk_ids" not in references[0]


def test_merge_never_guesses_last_reference_when_message_is_missing():
    updated, found = ConversationService._merge_reference_evidence(
        [{"chunks": [{"id": "legacy"}]}],
        "missing",
        ["legacy"],
    )

    assert found is False
    assert updated == [{"chunks": [{"id": "legacy"}]}]


class _IdField:
    def __eq__(self, value):
        return value


class _Select:
    def __init__(self, model):
        self.model = model

    def where(self, conversation_id):
        self.conversation_id = conversation_id
        return self

    def for_update(self):
        self.model.for_update_called = True
        return self

    def first(self):
        return self.model.row


class _Update:
    def __init__(self, model, payload):
        self.model = model
        self.payload = payload

    def where(self, conversation_id):
        self.conversation_id = conversation_id
        return self

    def execute(self):
        self.model.saved_reference = self.payload["reference"]
        return 1


class _ConversationModel:
    id = _IdField()
    row = None
    saved_reference = None
    for_update_called = False

    @classmethod
    def select(cls):
        return _Select(cls)

    @classmethod
    def update(cls, **payload):
        return _Update(cls, payload)


class _ConversationService(ConversationService):
    model = _ConversationModel


def _update_reference_evidence(
    conversation_id,
    message_id,
    used_chunk_ids,
):
    method = ConversationService.update_reference_evidence.__wrapped__
    return method(
        _ConversationService,
        conversation_id,
        message_id,
        used_chunk_ids,
    )


def test_update_reference_evidence_locks_and_preserves_concurrent_reference(
    monkeypatch,
):
    monkeypatch.setattr(conversation_module.DB, "atomic", nullcontext)
    _ConversationModel.row = SimpleNamespace(
        reference=[
            {"message_id": "message-1", "chunks": [{"id": "c1"}]},
            {"message_id": "message-2", "chunks": [{"id": "c2"}]},
            {
                "message_id": "message-3",
                "chunks": [{"id": "concurrent"}],
            },
        ]
    )
    _ConversationModel.saved_reference = None
    _ConversationModel.for_update_called = False

    assert (
        _update_reference_evidence(
            "conversation-1",
            "message-1",
            ["c1"],
        )
        is True
    )

    assert _ConversationModel.for_update_called is True
    assert _ConversationModel.saved_reference[2] == {
        "message_id": "message-3",
        "chunks": [{"id": "concurrent"}],
    }
    assert _ConversationModel.saved_reference[0]["used_chunk_ids"] == ["c1"]


def test_update_reference_evidence_does_not_write_when_message_is_missing(
    monkeypatch,
):
    monkeypatch.setattr(conversation_module.DB, "atomic", nullcontext)
    _ConversationModel.row = SimpleNamespace(
        reference=[
            {"message_id": "message-1", "chunks": [{"id": "c1"}]},
        ]
    )
    _ConversationModel.saved_reference = None

    assert (
        _update_reference_evidence(
            "conversation-1",
            "missing",
            ["c1"],
        )
        is False
    )
    assert _ConversationModel.saved_reference is None
