import hashlib
from types import SimpleNamespace

from api.db.services.conversation_service import ConversationService


class _IdField:
    def __eq__(self, value):
        return value


class _ConversationModel:
    id = _IdField()
    rows = {}

    @classmethod
    def get_or_none(cls, conversation_id):
        return cls.rows.get(conversation_id)


class _ConversationService(ConversationService):
    model = _ConversationModel

    @classmethod
    def save(cls, **kwargs):
        cls.model.rows[kwargs["id"]] = SimpleNamespace(**kwargs)


def _get_or_create(dialog_id, channel_id="channel-1", chat_id="user-1"):
    method = ConversationService.get_or_create_for_channel.__wrapped__
    return method(_ConversationService, dialog_id, channel_id, chat_id)


def setup_function():
    _ConversationModel.rows = {}


def test_channel_rebinding_creates_a_conversation_for_the_new_dialog():
    legacy_id = hashlib.md5(b"channel-1:user-1").hexdigest()[:32]
    _ConversationModel.rows[legacy_id] = SimpleNamespace(
        id=legacy_id,
        dialog_id="old-dialog",
        name="legacy",
        message=[{"role": "user", "content": "old history"}],
        reference=[],
    )

    conversation = _get_or_create("new-dialog")

    assert conversation.dialog_id == "new-dialog"
    assert conversation.id != legacy_id
    assert conversation.message == []

    conversation.message.append({"role": "user", "content": "new history"})
    repeated = _get_or_create("new-dialog")

    assert repeated is conversation
    assert repeated.message == [{"role": "user", "content": "new history"}]
    assert len(_ConversationModel.rows) == 2


def test_channel_binding_reuses_matching_legacy_conversation():
    legacy_id = hashlib.md5(b"channel-1:user-1").hexdigest()[:32]
    legacy = SimpleNamespace(
        id=legacy_id,
        dialog_id="current-dialog",
        name="legacy",
        message=[{"role": "user", "content": "existing history"}],
        reference=[],
    )
    _ConversationModel.rows[legacy_id] = legacy

    conversation = _get_or_create("current-dialog")

    assert conversation is legacy
