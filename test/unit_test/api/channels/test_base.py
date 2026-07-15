from api.channels.core.base import OutgoingImage, OutgoingMessage


def test_outgoing_message_remains_text_only_by_default():
    message = OutgoingMessage(chat_id="chat-1", text="answer")
    assert message.images == []


def test_outgoing_message_accepts_storage_image_references():
    image = OutgoingImage(image_id="bucket-object.jpg")
    message = OutgoingMessage(chat_id="chat-1", text="answer", images=[image])
    assert message.images == [image]
