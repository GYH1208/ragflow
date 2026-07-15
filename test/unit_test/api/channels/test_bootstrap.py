from api.channels import bootstrap
from api.channels.core.base import OutgoingImage


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
    )


def test_prepares_arabic_and_persian_digit_citations():
    chunks = [{"image_id": "zero"}, {"image_id": "one"}, {"image_id": "two"}]

    assert bootstrap._prepare_cited_output("引用 [ID:٢] [ID:۱]。", chunks) == (
        "引用。",
        [OutgoingImage("two"), OutgoingImage("one")],
    )


def test_preserves_markdown_newlines_and_indentation():
    answer = "1. 第一项 [ID:0]\n   - 子项 [ID:1]\n\n```text\n原文\n```"

    assert bootstrap._prepare_cited_output(answer, [{}, {}])[0] == "1. 第一项\n   - 子项\n\n```text\n原文\n```"


def test_hides_markers_when_chunk_container_is_invalid():
    assert bootstrap._prepare_cited_output("正文 [ID:0]。", None) == ("正文。", [])
    assert bootstrap._prepare_cited_output("正文 [ID:0]。", {}) == ("正文。", [])
