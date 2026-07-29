import sys
from types import ModuleType, SimpleNamespace

import pytest

from api.channels import bootstrap
from api.channels.core.base import IncomingMessage, OutgoingFile, OutgoingImage


class RecordingStreamingChannel:
    channel_id = "wecom"
    account_id = "account-1"
    supports_streaming = True
    supports_reference_images = True
    supports_source_files = False
    hides_reference_markers = True

    def __init__(self):
        self.stream_updates = []
        self.messages = []

    async def send_stream(self, message, stream_id, finish):
        self.stream_updates.append((message, stream_id, finish))

    async def send(self, message):
        self.messages.append(message)


class FakeConversation:
    def __init__(self):
        self.id = "conversation-1"
        self.message = []
        self.reference = []

    def to_dict(self):
        return {
            "id": self.id,
            "message": self.message,
            "reference": self.reference,
        }


def install_handler_service_stubs(
    monkeypatch,
    *,
    conversation,
    dialog,
    async_chat,
    persisted,
    structure_answer=None,
):
    if structure_answer is None:

        def structure_answer(conv, ans, message_id, session_id):
            if ans.get("final"):
                conv.reference[-1] = ans["reference"]
            return ans

    class FakeChatChannelService:
        get_by_id = staticmethod(lambda account_id: (True, SimpleNamespace(chat_id="dialog-1")))

    class FakeDialogService:
        get_by_id = staticmethod(lambda dialog_id: (True, dialog))

    class FakeConversationService:
        get_or_create_for_channel = staticmethod(lambda dialog_id, account_id, chat_id: conversation)
        update_by_id = staticmethod(lambda conversation_id, payload: persisted.append((conversation_id, payload)))

    chat_channel_module = ModuleType("api.db.services.chat_channel_service")
    chat_channel_module.ChatChannelService = FakeChatChannelService
    conversation_module = ModuleType("api.db.services.conversation_service")
    conversation_module.ConversationService = FakeConversationService
    conversation_module.structure_answer = structure_answer
    dialog_module = ModuleType("api.db.services.dialog_service")
    dialog_module.DialogService = FakeDialogService
    dialog_module.async_chat = async_chat
    misc_module = ModuleType("common.misc_utils")
    misc_module.get_uuid = lambda: "generated-id"
    monkeypatch.setitem(
        sys.modules,
        "api.db.services.chat_channel_service",
        chat_channel_module,
    )
    monkeypatch.setitem(
        sys.modules,
        "api.db.services.conversation_service",
        conversation_module,
    )
    monkeypatch.setitem(sys.modules, "api.db.services.dialog_service", dialog_module)
    monkeypatch.setitem(sys.modules, "common.misc_utils", misc_module)


def test_prepares_clean_text_and_cited_images_in_first_citation_order():
    chunks = [
        {"image_id": "bucket-zero.jpg"},
        {"image_id": ""},
        {"image_id": "bucket-two.jpg"},
        {"image_id": "bucket-zero.jpg"},
    ]
    answer = "先看 [ID:2]，再看 [0]，重复 [ID:2]，无图 [ID:1]，越界 [ID:9]。"

    assert bootstrap._prepare_cited_output(answer, chunks) == (
        "先看，再看，重复，无图，越界。",
        [OutgoingImage("bucket-two.jpg"), OutgoingImage("bucket-zero.jpg")],
        [],
    )


def test_prepares_arabic_and_persian_digit_citations():
    chunks = [{"image_id": "zero"}, {"image_id": "one"}, {"image_id": "two"}]

    assert bootstrap._prepare_cited_output("引用 [ID:٢] [ID:۱]。", chunks) == (
        "引用。",
        [OutgoingImage("two"), OutgoingImage("one")],
        [],
    )


def test_preserves_markdown_newlines_and_indentation():
    answer = "1. 第一项 [ID:0]\n   - 子项 [ID:1]\n\n```text\n原文\n```"

    assert bootstrap._prepare_cited_output(answer, [{}, {}])[0] == "1. 第一项\n   - 子项\n\n```text\n原文\n```"


def test_hides_markers_when_chunk_container_is_invalid():
    assert bootstrap._prepare_cited_output("正文 [ID:0]。", None) == ("正文。", [], [])
    assert bootstrap._prepare_cited_output("正文 [ID:0]。", {}) == ("正文。", [], [])


def test_prepares_cited_source_files_in_first_citation_order():
    chunks = [
        {"document_id": "doc-0", "document_name": "policy.pdf", "dataset_id": "kb-1"},
        {"document_id": "doc-1", "document_name": "guide.docx", "dataset_id": "kb-2"},
        {"document_id": "doc-0", "document_name": "policy.pdf", "dataset_id": "kb-1"},
        {"document_id": "doc-3", "document_name": "secret.xlsx", "dataset_id": "other-kb"},
    ]

    _, _, files = bootstrap._prepare_cited_output(
        "先看 [ID:1]，再看 [ID:0]，重复 [ID:2]，越权 [ID:3]。",
        chunks,
        include_source_files=True,
        allowed_dataset_ids=["kb-1", "kb-2"],
    )

    assert files == [
        OutgoingFile(document_id="doc-1", filename="guide.docx"),
        OutgoingFile(document_id="doc-0", filename="policy.pdf"),
    ]


@pytest.mark.asyncio
async def test_streaming_channel_sends_cumulative_visible_answer(monkeypatch):
    channel = RecordingStreamingChannel()
    conversation = FakeConversation()
    dialog = SimpleNamespace(
        id="dialog-1",
        kb_ids=["kb-1"],
        prompt_config={"quote": True},
    )
    stream_flags = []
    persisted = []

    async def fake_async_chat(dia, history, stream, **kwargs):
        stream_flags.append(stream)
        yield {"answer": "", "reference": {}, "final": False, "start_to_think": True}
        yield {"answer": "internal reasoning", "reference": {}, "final": False}
        yield {"answer": "", "reference": {}, "final": False, "end_to_think": True}
        yield {"answer": "第一段", "reference": {}, "final": False}
        yield {"answer": "第二段 [ID:0]", "reference": {}, "final": False}
        yield {
            "answer": "",
            "reference": {
                "chunks": [
                    {
                        "image_id": "bucket-image.jpg",
                        "dataset_id": "kb-1",
                    }
                ]
            },
            "final": True,
        }

    install_handler_service_stubs(
        monkeypatch,
        conversation=conversation,
        dialog=dialog,
        async_chat=fake_async_chat,
        persisted=persisted,
    )

    handler = bootstrap._make_chat_handler(channel)
    await handler(
        IncomingMessage(
            channel="wecom",
            account_id="account-1",
            chat_id="chat-1",
            chat_type="p2p",
            message_id="callback-1",
            sender_id="user-1",
            text="问题",
        )
    )

    assert stream_flags == [True]
    assert [update[0].text for update in channel.stream_updates] == [
        "正在查询知识库，请稍候…",
        "第一段",
        "第一段第二段",
        "第一段第二段",
    ]
    assert len({update[1] for update in channel.stream_updates}) == 1
    assert [update[2] for update in channel.stream_updates] == [False, False, False, True]
    assert all("internal reasoning" not in update[0].text for update in channel.stream_updates)
    assert channel.stream_updates[-1][0].images == [OutgoingImage("bucket-image.jpg")]
    assert channel.messages == []
    assert len(persisted) == 1


@pytest.mark.asyncio
async def test_streaming_error_finishes_the_existing_stream(monkeypatch):
    channel = RecordingStreamingChannel()
    conversation = FakeConversation()
    dialog = SimpleNamespace(
        id="dialog-1",
        kb_ids=[],
        prompt_config={},
    )

    async def failing_async_chat(dia, history, stream, **kwargs):
        yield {"answer": "已生成部分", "reference": {}, "final": False}
        raise RuntimeError("generation failed")

    install_handler_service_stubs(
        monkeypatch,
        conversation=conversation,
        dialog=dialog,
        async_chat=failing_async_chat,
        persisted=[],
    )

    handler = bootstrap._make_chat_handler(channel)
    await handler(
        IncomingMessage(
            channel="wecom",
            account_id="account-1",
            chat_id="chat-1",
            chat_type="p2p",
            message_id="callback-1",
            sender_id="user-1",
            text="问题",
        )
    )

    assert [update[2] for update in channel.stream_updates] == [False, False, True]
    assert channel.stream_updates[-1][0].text == "**ERROR**: generation failed"
    assert channel.messages == []


@pytest.mark.asyncio
async def test_streaming_placeholder_error_falls_back_to_complete_message(monkeypatch):
    class PlaceholderFailingChannel(RecordingStreamingChannel):
        async def send_stream(self, message, stream_id, finish):
            raise ConnectionError("stream unavailable")

    channel = PlaceholderFailingChannel()
    conversation = FakeConversation()
    dialog = SimpleNamespace(
        id="dialog-1",
        kb_ids=[],
        prompt_config={},
    )
    stream_flags = []
    persisted = []

    async def fake_async_chat(dia, history, stream, **kwargs):
        stream_flags.append(stream)
        yield {
            "answer": "完整回答",
            "reference": {"chunks": []},
            "final": True,
        }

    install_handler_service_stubs(
        monkeypatch,
        conversation=conversation,
        dialog=dialog,
        async_chat=fake_async_chat,
        persisted=persisted,
    )

    handler = bootstrap._make_chat_handler(channel)
    await handler(
        IncomingMessage(
            channel="wecom",
            account_id="account-1",
            chat_id="chat-1",
            chat_type="p2p",
            message_id="callback-1",
            sender_id="user-1",
            text="问题",
        )
    )

    assert stream_flags == [False]
    assert [message.text for message in channel.messages] == ["完整回答"]
    assert len(persisted) == 1


@pytest.mark.asyncio
async def test_streaming_without_final_event_finishes_with_accumulated_answer(
    monkeypatch,
):
    channel = RecordingStreamingChannel()
    conversation = FakeConversation()
    dialog = SimpleNamespace(
        id="dialog-1",
        kb_ids=[],
        prompt_config={},
    )
    persisted = []

    async def delta_only_async_chat(dia, history, stream, **kwargs):
        yield {"answer": "第一段", "reference": {}, "final": False}
        yield {"answer": "第二段", "reference": {}, "final": False}

    install_handler_service_stubs(
        monkeypatch,
        conversation=conversation,
        dialog=dialog,
        async_chat=delta_only_async_chat,
        persisted=persisted,
    )

    handler = bootstrap._make_chat_handler(channel)
    await handler(
        IncomingMessage(
            channel="wecom",
            account_id="account-1",
            chat_id="chat-1",
            chat_type="p2p",
            message_id="callback-1",
            sender_id="user-1",
            text="问题",
        )
    )

    assert [update[0].text for update in channel.stream_updates] == [
        "正在查询知识库，请稍候…",
        "第一段",
        "第一段第二段",
        "第一段第二段",
    ]
    assert [update[2] for update in channel.stream_updates] == [
        False,
        False,
        False,
        True,
    ]
    assert len(persisted) == 1
