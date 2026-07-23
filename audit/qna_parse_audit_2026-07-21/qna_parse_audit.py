"""Read-only audit of all active RAGFlow documents using the Q&A parser.

The script compares three layers:
1. the visible text and image references in each source DOCX;
2. a fresh run of the current Q&A parser;
3. the chunks currently stored in Elasticsearch.

The expected question counts and boundary findings are the result of reviewing
the visible numbering/TOC in the six source files on 2026-07-21. They are kept
explicit here so the report is reproducible and the judgment calls are visible.
"""

from __future__ import annotations

import json
import re
import unicodedata
from collections import Counter
from io import BytesIO

from docx import Document as OpenDocx

from api.db.db_models import Document, Knowledgebase
from api.db.services.file2document_service import File2DocumentService
from common import settings
from common.constants import ParserType
from common.doc_store.doc_store_base import OrderByExpr
from rag.app import qa
from rag.nlp import search


AUDIT_SPECS = {
    "3dafc07e80c911f18504c1b4e3818882": {
        "expected_questions": 39,
        "missing_boundaries": 11,
        "spurious_chunks": 0,
        "severity": "高",
        "finding": "11 个编号问题未成为独立 chunk；修订插入文本还造成答案局部缺失。",
    },
    "cedf0fca84b911f1be906f0cd9927c05": {
        "expected_questions": 65,
        "missing_boundaries": 10,
        "spurious_chunks": 1,
        "severity": "高",
        "finding": "10 个问题未独立切分，11 个现有问题答案为空，另有多处答案截断。",
    },
    "3ddd1db280c911f18504c1b4e3818882": {
        "expected_questions": 39,
        "missing_boundaries": 2,
        "spurious_chunks": 0,
        "severity": "中",
        "finding": "2.3、2.4 使用 Normal 样式，被并入上一条答案。",
    },
    "1f3120fc80c911f18504c1b4e3818882": {
        "expected_questions": 85,
        "missing_boundaries": 3,
        "spurious_chunks": 0,
        "severity": "中",
        "finding": "目录中的办公地点、园区部门分布、园区前台联系人三题在正文中没有答案。",
    },
    "1f3cb21480c911f18504c1b4e3818882": {
        "expected_questions": 4,
        "missing_boundaries": 0,
        "spurious_chunks": 0,
        "severity": "低",
        "finding": "4 条问答均完整，图片关联完整。",
    },
    "6586494481b711f1be263d0b3d27d4df": {
        "expected_questions": 28,
        "missing_boundaries": 0,
        "spurious_chunks": 0,
        "severity": "低",
        "finding": "内容完整，但同一欠票查询问答重复出现 2 次。",
    },
}


def _callback(*_args, **_kwargs):
    return None


def _compact(value: object) -> str:
    text = unicodedata.normalize("NFKC", str(value or ""))
    return re.sub(r"\s+", "", text)


def _visible_text(paragraph) -> str:
    return "".join(paragraph._element.xpath(".//w:t/text()")).strip()


def _split_qa(content: str) -> tuple[str, str] | None:
    if "\t" not in content:
        return None
    question, answer = content.split("\t", 1)
    return qa.rmPrefix(question).strip(), qa.rmPrefix(answer).strip()


def _question_key(question: str) -> str:
    leaf = question.splitlines()[-1].strip().lower()
    leaf = re.sub(r"^[（(]?\d+(?:[.、．]\d+)*[）)]?\s*[、.．:]?\s*", "", leaf)
    return re.sub(r"[\W_]", "", unicodedata.normalize("NFKC", leaf))


def _paragraph_coverage(source_doc, parsed_chunks: list[dict]) -> dict:
    indexed = _compact("\n".join(row.get("content_with_weight", "") for row in parsed_chunks))
    full = partial = missing = skipped = 0
    partial_missing_chars = missing_chars = 0
    xml_text_differences = 0

    for paragraph in source_doc.paragraphs:
        style = (paragraph.style.name if paragraph.style else "") or ""
        visible = _visible_text(paragraph)
        api_text = paragraph.text.strip()

        if _compact(visible) != _compact(api_text):
            xml_text_differences += 1

        if (
            not visible
            or style.lower().startswith("toc")
            or re.fullmatch(r"[=\-—_\s]+", visible)
        ):
            skipped += 1
            continue

        visible_value = _compact(qa.rmPrefix(visible))
        if not visible_value:
            skipped += 1
        elif visible_value in indexed:
            full += 1
        else:
            api_value = _compact(qa.rmPrefix(api_text))
            if len(api_value) >= 2 and api_value in indexed:
                partial += 1
                partial_missing_chars += max(0, len(visible_value) - len(api_value))
            else:
                missing += 1
                missing_chars += len(visible_value)

    return {
        "xml_text_differences": xml_text_differences,
        "fully_covered_paragraphs": full,
        "partially_covered_paragraphs": partial,
        "missing_paragraphs": missing,
        "skipped_paragraphs": skipped,
        "partial_missing_chars": partial_missing_chars,
        "missing_chars": missing_chars,
    }


def _source_image_references(source_doc) -> int:
    return sum(
        len(paragraph._element.xpath(".//a:blip/@r:embed"))
        for paragraph in source_doc.paragraphs
    )


def _parsed_image_blobs(parsed_chunks: list[dict]) -> tuple[int, int]:
    chunks = blobs = 0
    for row in parsed_chunks:
        image = row.get("image")
        if not image:
            continue
        chunks += 1
        blobs += len(getattr(image, "_blobs", []))
    return chunks, blobs


def _chunk_signatures(
    chunks: list[dict], *, parsed: bool, normalize_qa_prefixes: bool = True
) -> Counter:
    """Compare chunk text and whether each chunk carries an image."""
    def normalized_content(row: dict) -> str:
        content = row.get("content_with_weight", "")
        pair = _split_qa(content)
        if normalize_qa_prefixes and pair:
            return "\t".join(_compact(value) for value in pair)
        return _compact(content)

    return Counter(
        (
            normalized_content(row),
            bool(row.get("image") if parsed else row.get("img_id")),
        )
        for row in chunks
    )


def run_audit() -> dict:
    settings.init_settings()
    docs = list(
        Document.select(
            Document,
            Knowledgebase.name.alias("kb_name"),
            Knowledgebase.tenant_id,
            Knowledgebase.language.alias("kb_language"),
        )
        .join(Knowledgebase, on=(Document.kb_id == Knowledgebase.id))
        .where((Document.parser_id == ParserType.QA.value) & (Document.status == "1"))
        .order_by(Knowledgebase.name, Document.name)
        .dicts()
    )

    rows = []
    for doc in docs:
        spec = AUDIT_SPECS[doc["id"]]
        bucket, name = File2DocumentService.get_storage_address(doc_id=doc["id"])
        blob = settings.STORAGE_IMPL.get(bucket, name)
        source_doc = OpenDocx(BytesIO(blob))
        fresh = qa.chunk(
            doc["name"],
            binary=blob,
            lang=doc["kb_language"] or "Chinese",
            callback=_callback,
        )

        result = settings.docStoreConn.search(
            ["content_with_weight", "img_id", "doc_type_kwd"],
            [],
            {"doc_id": doc["id"]},
            [],
            OrderByExpr(),
            0,
            10_000,
            search.index_name(doc["tenant_id"]),
            [doc["kb_id"]],
        )
        chunk_ids = settings.docStoreConn.get_doc_ids(result)
        fields = settings.docStoreConn.get_fields(
            result, ["content_with_weight", "img_id", "doc_type_kwd"]
        )
        stored = [{"id": chunk_id, **fields.get(chunk_id, {})} for chunk_id in chunk_ids]

        fresh_qa = [row for row in fresh if _split_qa(row.get("content_with_weight", ""))]
        fresh_tables = len(fresh) - len(fresh_qa)
        stored_qa = [row for row in stored if _split_qa(row.get("content_with_weight", ""))]
        stored_tables = len(stored) - len(stored_qa)

        empty_text_answers = 0
        prefix_counts = Counter()
        question_counts = Counter()
        for row in stored_qa:
            content = row.get("content_with_weight", "")
            question, answer = _split_qa(content)
            prefix_counts[
                "中文" if content.startswith("问题") else "英文" if content.lower().startswith("question") else "其他"
            ] += 1
            question_counts[_question_key(question)] += 1
            if not answer and not row.get("img_id"):
                empty_text_answers += 1

        duplicate_chunks = sum(count - 1 for count in question_counts.values() if count > 1)
        valid_question_chunks = len(stored_qa) - spec["spurious_chunks"] - duplicate_chunks
        empty_answers = max(0, empty_text_answers - spec["spurious_chunks"])
        answerable_questions = valid_question_chunks - empty_answers
        parsed_image_chunks, parsed_image_blobs = _parsed_image_blobs(fresh)
        source_image_refs = _source_image_references(source_doc)

        rows.append(
            {
                "knowledge_base": doc["kb_name"],
                "document": doc["name"],
                "document_id": doc["id"],
                "expected_questions": spec["expected_questions"],
                "stored_qa_chunks": len(stored_qa),
                "stored_table_chunks": stored_tables,
                "valid_unique_question_chunks": valid_question_chunks,
                "answerable_questions": answerable_questions,
                "missing_question_boundaries": spec["missing_boundaries"],
                "empty_answers": empty_answers,
                "duplicate_chunks": duplicate_chunks,
                "spurious_chunks": spec["spurious_chunks"],
                "database_chunk_count": doc["chunk_num"],
                "elasticsearch_chunk_count": len(stored),
                "fresh_parser_chunk_count": len(fresh),
                "fresh_matches_elasticsearch": (
                    _chunk_signatures(fresh, parsed=True)
                    == _chunk_signatures(stored, parsed=False)
                ),
                "fresh_exact_text_matches_elasticsearch": (
                    _chunk_signatures(
                        fresh, parsed=True, normalize_qa_prefixes=False
                    )
                    == _chunk_signatures(
                        stored, parsed=False, normalize_qa_prefixes=False
                    )
                ),
                "source_image_references": source_image_refs,
                "parsed_image_blobs": parsed_image_blobs,
                "parsed_image_chunks": parsed_image_chunks,
                "image_reference_coverage": (
                    parsed_image_blobs / source_image_refs if source_image_refs else 1.0
                ),
                "prefix_counts": dict(prefix_counts),
                "severity": spec["severity"],
                "finding": spec["finding"],
                **_paragraph_coverage(source_doc, fresh),
            }
        )

    summary = {
        "knowledge_bases": len({row["knowledge_base"] for row in rows}),
        "documents": len(rows),
        "expected_questions": sum(row["expected_questions"] for row in rows),
        "stored_qa_chunks": sum(row["stored_qa_chunks"] for row in rows),
        "stored_table_chunks": sum(row["stored_table_chunks"] for row in rows),
        "valid_unique_question_chunks": sum(
            row["valid_unique_question_chunks"] for row in rows
        ),
        "answerable_questions": sum(row["answerable_questions"] for row in rows),
        "missing_question_boundaries": sum(
            row["missing_question_boundaries"] for row in rows
        ),
        "empty_answers": sum(row["empty_answers"] for row in rows),
        "duplicate_chunks": sum(row["duplicate_chunks"] for row in rows),
        "spurious_chunks": sum(row["spurious_chunks"] for row in rows),
        "database_chunk_count": sum(row["database_chunk_count"] for row in rows),
        "elasticsearch_chunk_count": sum(
            row["elasticsearch_chunk_count"] for row in rows
        ),
        "source_image_references": sum(row["source_image_references"] for row in rows),
        "parsed_image_blobs": sum(row["parsed_image_blobs"] for row in rows),
    }
    summary["question_boundary_coverage"] = (
        summary["valid_unique_question_chunks"] / summary["expected_questions"]
    )
    summary["answerable_coverage"] = (
        summary["answerable_questions"] / summary["expected_questions"]
    )
    summary["image_reference_coverage"] = (
        summary["parsed_image_blobs"] / summary["source_image_references"]
    )

    return {"summary": summary, "documents": rows}


if __name__ == "__main__":
    print(json.dumps(run_audit(), ensure_ascii=False, indent=2))
