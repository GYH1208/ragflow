"""Keep DOCX pictures usable when no image-to-text model is configured."""

from io import BytesIO
from types import SimpleNamespace

import pytest
from docx import Document
from PIL import Image

from common import settings
from deepdoc.parser import figure_parser
from rag.app import naive
from rag.nlp import doc_tokenize_chunks_with_images
from rag.svr.task_executor_refactor.chunk_service import ChunkService


@pytest.mark.parametrize("lang", ["Chinese", "English"])
@pytest.mark.asyncio
async def test_docx_without_vision_keeps_and_uploads_distinct_images(monkeypatch, lang):
    def no_vision_model(*args, **kwargs):
        raise LookupError("No default image2text model is set.")

    monkeypatch.setattr(figure_parser, "get_tenant_default_model_by_type", no_vision_model)
    document = Document()
    for label, color in [("Product appearance", "red"), ("System diagram", "blue")]:
        document.add_paragraph(label)
        with Image.new("RGB", (32, 32), color) as image, BytesIO() as buffer:
            image.save(buffer, format="PNG")
            buffer.seek(0)
            document.add_picture(buffer)
    with BytesIO() as buffer:
        document.save(buffer)
        chunks = naive.chunk(
            "report.docx",
            binary=buffer.getvalue(),
            lang=lang,
            tenant_id="test-tenant",
            callback=lambda *args, **kwargs: None,
            parser_config={"chunk_token_num": 512, "delimiter": "\n", "image_context_size": 0},
        )

    pictures = [chunk for chunk in chunks if chunk.get("doc_type_kwd") == "image"]
    assert len(pictures) == 2
    assert all(chunk["content_with_weight"].strip() for chunk in pictures)
    assert len({chunk["content_with_weight"] for chunk in pictures}) == 2
    assert {chunk["image"].getpixel((0, 0)) for chunk in pictures} == {(255, 0, 0), (0, 0, 255)}

    stored = {}

    def store_image(*, bucket, fnm, binary, tenant_id):
        stored[(bucket, fnm)] = binary

    monkeypatch.setattr(settings, "STORAGE_IMPL", SimpleNamespace(put=store_image), raising=False)
    context = SimpleNamespace(doc_id="report-id", kb_id="test-kb", tenant_id="test-tenant", pagerank=0, location="report.docx", name="report.docx")
    uploaded = await ChunkService(context)._prepare_docs_and_upload(chunks)
    uploaded_pictures = [chunk for chunk in uploaded if chunk.get("doc_type_kwd") == "image"]

    assert len(stored) == 2
    assert len({chunk["id"] for chunk in uploaded_pictures}) == 2
    assert len({chunk["img_id"] for chunk in uploaded_pictures}) == 2
    for chunk in uploaded_pictures:
        with Image.open(BytesIO(stored[("test-kb", chunk["id"])])) as image:
            assert image.size == (32, 32)


def test_docx_still_discards_empty_chunks_without_images():
    chunks = [{"text": " \n", "image": None, "ck_type": kind} for kind in ["text", "image", "table"]]

    assert doc_tokenize_chunks_with_images(chunks, {}, False) == []


def test_docx_preserves_existing_image_description_and_context():
    with Image.new("RGB", (32, 32), "red") as image:
        chunks = [{"text": "A red square.", "image": image, "ck_type": "image", "context_above": "Before. ", "context_below": " After."}]
        result = doc_tokenize_chunks_with_images(chunks, {}, True)

        assert len(result) == 1
        assert result[0]["image"] is image
        assert result[0]["doc_type_kwd"] == "image"
        assert result[0]["content_with_weight"] == "Before. A red square. After."
