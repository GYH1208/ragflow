#
#  Copyright 2025 The InfiniFlow Authors. All Rights Reserved.
#
#  Licensed under the Apache License, Version 2.0 (the "License");
#  you may not use this file except in compliance with the License.
#  You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
#  Unless required by applicable law or agreed to in writing, software
#  distributed under the License is distributed on an "AS IS" BASIS,
#  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#  See the License for the specific language governing permissions and
#  limitations under the License.
#

"""Request-shape tests for Elasticsearch BM25/KNN hybrid retrieval."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from common import settings  # noqa: F401,E402 - initialize backend modules in project order
from common.doc_store.doc_store_base import FusionExpr, MatchDenseExpr, MatchTextExpr
from rag.utils import es_conn


def _resolve_es_connection_class():
    """Unwrap the class hidden by the project's @singleton decorator."""
    candidate = es_conn.ESConnection
    if isinstance(candidate, type):
        return candidate
    for cell in getattr(candidate, "__closure__", None) or ():
        if isinstance(cell.cell_contents, type):
            return cell.cell_contents
    raise RuntimeError("Could not locate the ESConnection class")


def _make_connection():
    cls = _resolve_es_connection_class()
    conn = cls.__new__(cls)
    conn.es = MagicMock()
    conn.logger = MagicMock()
    conn.es.search.return_value = {
        "hits": {"total": {"value": 0}, "hits": []},
        "timed_out": False,
    }
    return conn


def _text_expr():
    return MatchTextExpr(
        fields=["question_tks^20", "content_ltks^2"],
        matching_text="换社康后今天能用吗",
        topn=10,
        extra_options={"minimum_should_match": 0.3},
    )


def _dense_expr():
    return MatchDenseExpr(
        vector_column_name="q_8_vec",
        embedding_data=[0.1] * 8,
        embedding_data_type="float",
        distance_type="cosine",
        topn=64,
        extra_options={"similarity": 0.17},
    )


def _fusion_expr():
    return FusionExpr(
        method="weighted_sum",
        topn=64,
        fusion_params={"weights": "0.05,0.95"},
    )


def _call_search(conn, match_expressions):
    conn.search(
        select_fields=["content_ltks"],
        highlight_fields=[],
        condition={"available_int": 1, "doc_id": ["doc-1"]},
        match_expressions=match_expressions,
        order_by=None,
        offset=0,
        limit=64,
        index_names=["ragflow_test"],
        knowledgebase_ids=["kb-1"],
    )
    return conn.es.search.call_args.kwargs["body"]


def test_hybrid_search_unions_text_and_knn_candidates():
    conn = _make_connection()

    body = _call_search(conn, [_text_expr(), _dense_expr(), _fusion_expr()])

    assert "query_string" in str(body["query"])
    assert body["query"]["bool"]["boost"] == pytest.approx(0.05)
    assert body["knn"]["boost"] == pytest.approx(0.95)
    assert "query_string" not in str(body["knn"]["filter"])
    assert "kb_id" in str(body["knn"]["filter"])
    assert "doc_id" in str(body["knn"]["filter"])
    assert "available_int" in str(body["knn"]["filter"])


def test_text_only_search_does_not_add_knn():
    conn = _make_connection()

    body = _call_search(conn, [_text_expr()])

    assert "query_string" in str(body["query"])
    assert "knn" not in body


def test_knn_only_search_keeps_structural_filter_without_changing_score():
    conn = _make_connection()

    body = _call_search(conn, [_dense_expr()])

    assert "query" in body
    assert "knn" in body
    assert "boost" not in body["knn"]
    assert "query_string" not in str(body["knn"]["filter"])
    assert "kb_id" in str(body["knn"]["filter"])
    assert "doc_id" in str(body["knn"]["filter"])
    assert "available_int" in str(body["knn"]["filter"])


def test_get_fields_includes_elasticsearch_metadata_score():
    conn = _make_connection()
    result = {
        "hits": {
            "total": {"value": 1},
            "hits": [
                {
                    "_id": "entity-1",
                    "_score": 0.87,
                    "_source": {
                        "entity_kwd": "年假",
                        "kb_id": "kb-hr",
                    },
                }
            ],
        }
    }

    fields = conn.get_fields(result, ["entity_kwd", "kb_id", "_score"])

    assert fields == {
        "entity-1": {
            "entity_kwd": "年假",
            "kb_id": "kb-hr",
            "_score": "0.87",
        }
    }
