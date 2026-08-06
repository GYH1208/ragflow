import sys
from types import ModuleType, SimpleNamespace

import pytest

from api.channels import bootstrap
from api.channels.core.base import IncomingMessage, OutgoingFile, OutgoingImage
from rag.nlp.evidence import EvidenceResolution


class RecordingStreamingChannel:
    channel_id = "wecom"
    account_id = "account-1"
    supports_streaming = True
    supports_reference_images = True
    supports_source_files = False
    hides_reference_markers = True

    def __init__(self, events=None, final_stream_result=True):
        self.stream_updates = []
        self.messages = []
        self.events = events if events is not None else []
        self.final_stream_result = final_stream_result

    def allows_reference_image(self, chunk):
        return True

    async def send_stream(self, message, stream_id, finish):
        if finish:
            self.events.append("stream:final")
        elif self.stream_updates:
            self.events.append("stream:delta")
        else:
            self.events.append("stream:placeholder")
        self.stream_updates.append((message, stream_id, finish))
        return self.final_stream_result if finish else True

    async def send(self, message):
        if message.images:
            self.events.append("send:images")
        self.messages.append(message)
        return True


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
    events=None,
):
    events = events if events is not None else []
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
        update_reference_evidence = staticmethod(
            lambda conversation_id, message_id, chunk_ids: True
        )

    class FakeEvidenceService:
        @classmethod
        async def resolve_for_dialog(
            cls,
            dialog,
            question,
            answer,
            chunks,
        ):
            events.append("evidence:resolve")
            used_chunk_ids = [
                str(chunk.get("id") or "")
                for chunk in chunks
                if isinstance(chunk, dict)
                and chunk.get("id")
                and chunk.get("image_id")
            ][:2]
            return EvidenceResolution(
                used_chunk_ids,
                [],
                [],
                "resolved" if used_chunk_ids else "no_match",
                1.0,
            )

    chat_channel_module = ModuleType("api.db.services.chat_channel_service")
    chat_channel_module.ChatChannelService = FakeChatChannelService
    conversation_module = ModuleType("api.db.services.conversation_service")
    conversation_module.ConversationService = FakeConversationService
    conversation_module.structure_answer = structure_answer
    dialog_module = ModuleType("api.db.services.dialog_service")
    dialog_module.DialogService = FakeDialogService
    dialog_module.async_chat = async_chat
    evidence_module = ModuleType("api.db.services.evidence_service")
    evidence_module.EvidenceService = FakeEvidenceService
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
    monkeypatch.setitem(
        sys.modules,
        "api.db.services.evidence_service",
        evidence_module,
    )
    monkeypatch.setitem(sys.modules, "common.misc_utils", misc_module)


def test_select_channel_history_keeps_only_two_recent_user_messages():
    messages = [
        {"role": "system", "content": "system"},
        {"role": "user", "content": "旧问题"},
        {"role": "assistant", "content": "错误联系人：IT-陶正浩"},
        {"role": "user", "content": "上一问"},
        {"role": "assistant", "content": "错误联系人：IT-刘尧"},
        {"role": "user", "content": "当前问题", "id": "current"},
    ]

    assert bootstrap._select_channel_history(messages) == [
        {"role": "user", "content": "上一问"},
        {"role": "user", "content": "当前问题", "id": "current"},
    ]


def test_select_channel_history_ignores_empty_and_invalid_messages():
    messages = [
        {"role": "user", "content": ""},
        {"role": "assistant", "content": "旧回答"},
        {"role": "user", "content": "当前问题"},
        {"role": "tool", "content": "工具结果"},
    ]

    assert bootstrap._select_channel_history(messages) == [
        {"role": "user", "content": "当前问题"},
    ]


@pytest.mark.asyncio
async def test_handler_passes_only_recent_user_messages_to_kb_chat(monkeypatch):
    channel = RecordingStreamingChannel()
    conversation = FakeConversation()
    conversation.message = [
        {"role": "user", "content": "旧问题"},
        {"role": "assistant", "content": "错误联系人：IT-陶正浩"},
        {"role": "user", "content": "上一问"},
        {"role": "assistant", "content": "错误联系人：IT-刘尧"},
    ]
    dialog = SimpleNamespace(
        id="dialog-1",
        kb_ids=["kb-1"],
        prompt_config={"quote": True},
    )
    captured_histories = []

    async def fake_async_chat(_dialog, history, _stream, **_kwargs):
        captured_histories.append(history)
        yield {
            "answer": "当前回答",
            "reference": {"chunks": [], "doc_aggs": []},
            "final": True,
        }

    install_handler_service_stubs(
        monkeypatch,
        conversation=conversation,
        dialog=dialog,
        async_chat=fake_async_chat,
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
            text="当前问题",
        )
    )

    assert captured_histories == [
        [
            {"role": "user", "content": "上一问"},
            {"role": "user", "content": "当前问题", "id": "generated-id"},
        ]
    ]


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


def test_images_for_used_chunks_follows_order_and_deduplicates_image_id():
    chunks = [
        {"id": "c1", "image_id": "img-shared"},
        {"id": "c2", "image_id": "img-two"},
        {"id": "c3", "image_id": "img-shared"},
        {"id": "c4", "image_id": ""},
    ]

    assert bootstrap._images_for_used_chunks(
        chunks,
        ["c2", "missing", "c3", "c1", "c4"],
    ) == [
        OutgoingImage("img-two"),
        OutgoingImage("img-shared"),
    ]


def test_images_for_used_chunks_never_uses_citation_index():
    chunks = [{"id": "stable-id", "image_id": "right"}]

    assert bootstrap._images_for_used_chunks(chunks, ["0"]) == []


def test_images_for_used_chunks_caps_at_two_after_deduplication():
    chunks = [
        {"id": "c1", "image_id": "img-shared"},
        {"id": "c2", "image_id": "img-two"},
        {"id": "c3", "image_id": "img-three"},
        {"id": "c4", "image_id": "img-shared"},
    ]

    images = bootstrap._images_for_used_chunks(
        chunks,
        ["c4", "c2", "c3", "c1"],
    )

    assert images == [
        OutgoingImage("img-shared"),
        OutgoingImage("img-two"),
    ]


def test_images_for_used_chunks_applies_policy_before_image_limit():
    chunks = [
        {
            "id": "pdf",
            "image_id": "pdf-image",
            "document_name": "policy.pdf",
        },
        {
            "id": "faq-1",
            "image_id": "faq-image-1",
            "document_name": "faq.docx",
        },
        {
            "id": "faq-2",
            "image_id": "faq-image-2",
            "document_name": "answers.xlsx",
        },
    ]

    images = bootstrap._images_for_used_chunks(
        chunks,
        ["pdf", "faq-1", "faq-2"],
        image_allowed=lambda chunk: not chunk["document_name"].lower().endswith(".pdf"),
    )

    assert images == [
        OutgoingImage("faq-image-1"),
        OutgoingImage("faq-image-2"),
    ]
    assert chunks[0]["image_id"] == "pdf-image"


async def _run_handler_case(
    monkeypatch,
    *,
    chunks,
    resolution,
    question="用户问题",
    answer="回答正文。[ID:0]",
    text_send_result=True,
    persist_result=True,
):
    events = []
    sent_messages = []
    conversation = SimpleNamespace(
        id="conversation-1",
        message=[],
        reference=[],
    )
    conversation.to_dict = lambda: {
        "id": conversation.id,
        "message": conversation.message,
        "reference": conversation.reference,
    }
    dialog = SimpleNamespace(
        id="dialog-1",
        kb_ids=["kb-1"],
        prompt_config={
            "quote": True,
            "send_source_file": True,
            "system": "{knowledge}",
        },
    )
    channel_connection = SimpleNamespace(chat_id=dialog.id)

    class FakeChatChannelService:
        @staticmethod
        def get_by_id(account_id):
            return True, channel_connection

    class FakeDialogService:
        @staticmethod
        def get_by_id(dialog_id):
            return True, dialog

    class FakeConversationService:
        @staticmethod
        def get_or_create_for_channel(dialog_id, account_id, chat_id):
            return conversation

        @staticmethod
        def update_by_id(conversation_id, data):
            events.append(("save", conversation_id))
            return 1

        @staticmethod
        def update_reference_evidence(
            conversation_id,
            message_id,
            used_chunk_ids,
        ):
            events.append((
                "persist",
                conversation_id,
                message_id,
                used_chunk_ids,
            ))
            return persist_result

    class FakeEvidenceService:
        @classmethod
        async def resolve_for_dialog(
            cls,
            dialog,
            question,
            answer,
            chunks,
        ):
            events.append(("resolve", answer))
            return resolution

    async def fake_async_chat(dialog, history, stream, **kwargs):
        yield {
            "answer": answer,
            "reference": {"chunks": chunks, "doc_aggs": []},
            "final": True,
        }

    send_results = iter([text_send_result, True])

    class FakeChannel:
        account_id = "account-1"
        channel_id = "wecom"
        supports_streaming = False
        supports_reference_images = True
        supports_source_files = True
        hides_reference_markers = True

        def allows_reference_image(self, chunk):
            return True

        async def send(self, outgoing):
            sent_messages.append(outgoing)
            events.append((
                "send",
                outgoing.text,
                outgoing.images,
                outgoing.files,
            ))
            return next(send_results)

    import api.db.services.chat_channel_service as chat_channel_module
    import api.db.services.conversation_service as conversation_module
    import api.db.services.dialog_service as dialog_module
    import api.db.services.evidence_service as evidence_module
    import common.misc_utils as misc_module

    monkeypatch.setattr(
        chat_channel_module,
        "ChatChannelService",
        FakeChatChannelService,
    )
    monkeypatch.setattr(
        conversation_module,
        "ConversationService",
        FakeConversationService,
    )
    monkeypatch.setattr(dialog_module, "DialogService", FakeDialogService)
    monkeypatch.setattr(dialog_module, "async_chat", fake_async_chat)
    monkeypatch.setattr(
        evidence_module,
        "EvidenceService",
        FakeEvidenceService,
    )
    monkeypatch.setattr(misc_module, "get_uuid", lambda: "message-1")

    handler = bootstrap._make_chat_handler(FakeChannel())
    await handler(IncomingMessage(
        channel="wecom",
        account_id="account-1",
        chat_id="chat-1",
        chat_type="single",
        message_id="incoming-1",
        sender_id="user-1",
        text=question,
    ))
    return events, sent_messages


@pytest.mark.asyncio
async def test_handler_sends_text_before_resolving_and_sending_images(
    monkeypatch,
):
    chunks = [
        {
            "id": "chunk-1",
            "content": "证据一",
            "image_id": "image-1",
            "document_id": "doc-1",
            "document_name": "guide.pdf",
            "dataset_id": "kb-1",
        },
        {
            "id": "chunk-2",
            "content": "证据二",
            "image_id": "image-2",
        },
    ]
    resolution = EvidenceResolution(
        ["chunk-1", "chunk-2"],
        [],
        [],
        "resolved",
        12.0,
    )

    events, _ = await _run_handler_case(
        monkeypatch,
        chunks=chunks,
        resolution=resolution,
    )

    assert events == [
        ("save", "conversation-1"),
        (
            "send",
            "回答正文。",
            [],
            [OutgoingFile("doc-1", "guide.pdf")],
        ),
        ("resolve", "回答正文。[ID:0]"),
        (
            "persist",
            "conversation-1",
            "message-1",
            ["chunk-1", "chunk-2"],
        ),
        (
            "send",
            "",
            [OutgoingImage("image-1"), OutgoingImage("image-2")],
            [],
        ),
    ]


@pytest.mark.asyncio
async def test_handler_sends_two_verified_images_after_text_in_unit_order(
    monkeypatch,
):
    chunks = [
        {
            "id": "approval",
            "content": "审批进度",
            "image_id": "img-approval",
        },
        {
            "id": "proxy",
            "content": "代理设置",
            "image_id": "img-proxy",
        },
    ]
    resolution = EvidenceResolution(
        ["approval", "proxy"],
        [],
        [],
        "resolved",
        20.0,
    )

    events, sent_messages = await _run_handler_case(
        monkeypatch,
        question="怎么查进度和设置代理人？",
        answer="查审批进度。[ID:0]\n设置代理人。[ID:1]",
        chunks=chunks,
        resolution=resolution,
    )

    assert sent_messages[0].text == "查审批进度。\n设置代理人。"
    assert sent_messages[0].images == []
    assert sent_messages[1].text == ""
    assert sent_messages[1].images == [
        OutgoingImage("img-approval"),
        OutgoingImage("img-proxy"),
    ]
    event_names = [event[0] for event in events]
    assert event_names.index("resolve") > event_names.index("send")


@pytest.mark.asyncio
async def test_handler_does_not_resolve_when_text_send_returns_false(
    monkeypatch,
):
    events, sent_messages = await _run_handler_case(
        monkeypatch,
        chunks=[{"id": "c1", "content": "证据", "image_id": "image-1"}],
        resolution=EvidenceResolution(["c1"], [], [], "resolved", 1.0),
        text_send_result=False,
    )

    assert len(sent_messages) == 1
    assert not any(event[0] == "resolve" for event in events)


@pytest.mark.asyncio
async def test_handler_does_not_resolve_without_image_candidates(monkeypatch):
    events, sent_messages = await _run_handler_case(
        monkeypatch,
        chunks=[{"id": "c1", "content": "纯文字证据", "image_id": ""}],
        resolution=EvidenceResolution(["c1"], [], [], "resolved", 1.0),
    )

    assert len(sent_messages) == 1
    assert not any(event[0] == "resolve" for event in events)


@pytest.mark.asyncio
async def test_handler_sends_images_when_evidence_persistence_fails(
    monkeypatch,
):
    events, sent_messages = await _run_handler_case(
        monkeypatch,
        chunks=[{"id": "c1", "content": "证据", "image_id": "image-1"}],
        resolution=EvidenceResolution(["c1"], [], [], "resolved", 1.0),
        persist_result=False,
    )

    assert any(event[0] == "persist" for event in events)
    assert sent_messages[-1].images == [OutgoingImage("image-1")]


@pytest.mark.asyncio
async def test_handler_sends_no_images_on_error_resolution(monkeypatch):
    _, sent_messages = await _run_handler_case(
        monkeypatch,
        chunks=[{"id": "c1", "content": "证据", "image_id": "image-1"}],
        resolution=EvidenceResolution(
            [],
            [],
            [],
            "error",
            10_000.0,
            "timeout",
        ),
    )

    assert len(sent_messages) == 1


@pytest.mark.asyncio
async def test_capability_answer_never_sends_cited_approval_screenshot_without_trusted_evidence(
    monkeypatch,
):
    approval_image_id = "6dd5ae4a802811f18504c1b4e3818882-eab6502820610ded"
    chunks = [
        {
            "id": "travel",
            "content": "差旅与外出管理：酒店、机票、外部宾客、报销规则。",
            "image_id": "",
        },
        {
            "id": "office",
            "content": "办公场地、快递与综合服务。",
            "image_id": "",
        },
        {
            "id": "benefits",
            "content": "员工福利、文化活动与日常办公支持。",
            "image_id": "",
        },
        {
            "id": "finance-approval",
            "content": "财务流程：查询审批进度、报销相关操作。",
            "image_id": approval_image_id,
        },
    ]
    answer = (
        "我可以解答差旅、办公服务、员工福利、日常办公支持等常见问题。\n"
        "财务流程：查询审批进度、报销相关操作。[ID:3]"
    )

    events, sent_messages = await _run_handler_case(
        monkeypatch,
        question="你都有什么功能？",
        answer=answer,
        chunks=chunks,
        resolution=EvidenceResolution(
            [],
            [],
            [0, 1],
            "no_match",
            7.0,
            "below_confidence_threshold",
        ),
    )

    assert len(sent_messages) == 1
    assert sent_messages[0].text == answer.replace("[ID:3]", "").strip()
    assert sent_messages[0].images == []
    assert not any(
        image.image_id == approval_image_id
        for message in sent_messages
        for image in message.images
    )
    assert ("persist", "conversation-1", "message-1", []) in events


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
                        "id": "chunk-image",
                        "content": "第一段第二段",
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
    assert channel.stream_updates[-1][0].images == []
    assert channel.messages[0].text == ""
    assert channel.messages[0].images == [
        OutgoingImage("bucket-image.jpg")
    ]
    assert len(persisted) == 1


@pytest.mark.asyncio
async def test_streaming_finalizes_text_before_resolving_and_sends_verified_images_separately(
    monkeypatch,
):
    events = []
    channel = RecordingStreamingChannel(events=events)
    conversation = FakeConversation()
    dialog = SimpleNamespace(
        id="dialog-1",
        kb_ids=["kb-1"],
        prompt_config={"quote": True},
    )

    async def fake_async_chat(dia, history, stream, **kwargs):
        yield {
            "answer": "回答正文",
            "reference": {},
            "final": False,
        }
        yield {
            "answer": "",
            "reference": {
                "chunks": [
                    {
                        "id": "chunk-a",
                        "content": "回答正文",
                        "image_id": "image-a",
                    },
                    {
                        "id": "chunk-b",
                        "content": "补充证据",
                        "image_id": "image-b",
                    },
                ]
            },
            "final": True,
        }

    install_handler_service_stubs(
        monkeypatch,
        conversation=conversation,
        dialog=dialog,
        async_chat=fake_async_chat,
        persisted=[],
        events=events,
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

    assert events == [
        "stream:placeholder",
        "stream:delta",
        "stream:final",
        "evidence:resolve",
        "send:images",
    ]
    assert channel.stream_updates[-1][0].images == []
    assert channel.messages[0].text == ""
    assert channel.messages[0].images == [
        OutgoingImage("image-a"),
        OutgoingImage("image-b"),
    ]


@pytest.mark.asyncio
async def test_handler_keeps_pdf_reference_but_filters_it_from_channel_images(monkeypatch):
    class PdfFilteringChannel(RecordingStreamingChannel):
        def allows_reference_image(self, chunk):
            filename = str(chunk.get("document_name") or "").lower()
            return bool(filename) and not filename.endswith(".pdf")

    channel = PdfFilteringChannel()
    conversation = FakeConversation()
    dialog = SimpleNamespace(
        id="dialog-1",
        kb_ids=["kb-1"],
        prompt_config={"quote": True},
    )

    async def fake_async_chat(dia, history, stream, **kwargs):
        yield {
            "answer": "回答正文",
            "reference": {
                "chunks": [
                    {
                        "id": "pdf-chunk",
                        "content": "制度内容",
                        "image_id": "pdf-image",
                        "document_name": "policy.pdf",
                    },
                    {
                        "id": "faq-chunk",
                        "content": "问答配图",
                        "image_id": "faq-image",
                        "document_name": "faq.docx",
                    },
                ]
            },
            "final": True,
        }

    install_handler_service_stubs(
        monkeypatch,
        conversation=conversation,
        dialog=dialog,
        async_chat=fake_async_chat,
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

    assert conversation.reference[-1]["chunks"][0]["image_id"] == "pdf-image"
    assert channel.messages[0].images == [OutgoingImage("faq-image")]


@pytest.mark.asyncio
async def test_streaming_final_ack_failure_skips_evidence(monkeypatch):
    events = []
    channel = RecordingStreamingChannel(
        events=events,
        final_stream_result=False,
    )
    conversation = FakeConversation()
    dialog = SimpleNamespace(
        id="dialog-1",
        kb_ids=["kb-1"],
        prompt_config={"quote": True},
    )

    async def fake_async_chat(dia, history, stream, **kwargs):
        yield {
            "answer": "回答正文。[ID:0]",
            "reference": {
                "chunks": [
                    {
                        "id": "chunk-a",
                        "content": "回答正文",
                        "image_id": "image-a",
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
        persisted=[],
        events=events,
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

    assert "evidence:resolve" not in events
    assert channel.messages == []


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
