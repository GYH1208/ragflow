from api.channels.core.base import Channel, OutgoingFile, OutgoingImage, OutgoingMessage


def test_channel_keeps_reference_markers_by_default():
    assert Channel.hides_reference_markers is False


def test_outgoing_message_remains_text_only_by_default():
    message = OutgoingMessage(chat_id="chat-1", text="answer")
    assert message.images == []
    assert message.files == []


def test_outgoing_message_accepts_storage_image_references():
    image = OutgoingImage(image_id="bucket-object.jpg")
    message = OutgoingMessage(chat_id="chat-1", text="answer", images=[image])
    assert message.images == [image]


def test_outgoing_message_accepts_source_files():
    source_file = OutgoingFile(document_id="doc-1", filename="policy.docx")
    message = OutgoingMessage(chat_id="chat-1", text="answer", files=[source_file])
    assert message.files == [source_file]
