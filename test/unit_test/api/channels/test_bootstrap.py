from api.channels.bootstrap import _extract_cited_images
from api.channels.core.base import OutgoingImage


def test_extracts_only_cited_images_in_first_citation_order():
    chunks = [
        {"image_id": "bucket-zero.jpg"},
        {"image_id": ""},
        {"image_id": "bucket-two.jpg"},
        {"image_id": "bucket-zero.jpg"},
    ]
    answer = "先看 [ID:2]，再看 [0]，重复 [ID:2]，无图 [ID:1]，越界 [ID:9]"

    assert _extract_cited_images(answer, chunks) == [
        OutgoingImage("bucket-two.jpg"),
        OutgoingImage("bucket-zero.jpg"),
    ]


def test_extracts_citations_written_with_arabic_and_persian_digits():
    chunks = [{"image_id": "zero"}, {"image_id": "one"}, {"image_id": "two"}]
    assert _extract_cited_images("[ID:٢] [ID:۱]", chunks) == [
        OutgoingImage("two"),
        OutgoingImage("one"),
    ]


def test_returns_no_images_for_invalid_chunk_container():
    assert _extract_cited_images("[ID:0]", None) == []
    assert _extract_cited_images("[ID:0]", {}) == []
