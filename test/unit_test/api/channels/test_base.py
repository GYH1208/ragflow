import pytest

from api.channels.core.base import Channel, OutgoingFile, OutgoingImage, OutgoingMessage


class RecordingChannel(Channel):
    channel_id = "recording"
    account_id = "account"

    def __init__(self):
        super().__init__()
        self.messages = []

    async def start(self):
        pass

    async def stop(self):
        pass

    async def send(self, message):
        self.messages.append(message)
        return True


def test_channel_keeps_reference_markers_by_default():
    assert Channel.hides_reference_markers is False


@pytest.mark.asyncio
async def test_default_streaming_contract_only_sends_the_final_message():
    channel = RecordingChannel()
    partial = OutgoingMessage(chat_id="chat-1", text="partial")
    final = OutgoingMessage(chat_id="chat-1", text="final")

    await channel.send_stream(partial, stream_id="stream-1", finish=False)
    final_result = await channel.send_stream(
        final,
        stream_id="stream-1",
        finish=True,
    )

    assert channel.supports_streaming is False
    assert channel.messages == [final]
    assert final_result is True


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
