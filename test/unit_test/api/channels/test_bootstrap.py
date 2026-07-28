from types import SimpleNamespace

import pytest

from api.channels import bootstrap
from api.channels.core.base import IncomingMessage, OutgoingFile, OutgoingImage
from rag.nlp.evidence import EvidenceResolution


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
        supports_reference_images = True
        supports_source_files = True
        hides_reference_markers = True

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
