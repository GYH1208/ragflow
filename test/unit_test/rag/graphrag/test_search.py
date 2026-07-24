#
#  Copyright 2026 The InfiniFlow Authors. All Rights Reserved.
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

import importlib.util
import logging
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, sentinel

import pytest

# graphrag/conftest.py replaces infrastructure-heavy modules with mocks. Give
# the search module a real base class so this test can exercise KGSearch itself.
nlp_search = sys.modules["rag.nlp.search"]
mocked_dealer = nlp_search.Dealer
mocked_index_name = nlp_search.index_name
nlp_search.Dealer = object
nlp_search.index_name = lambda tenant_id: f"ragflow_{tenant_id}"

try:
    spec = importlib.util.spec_from_file_location(
        "_graphrag_search_under_test",
        Path(__file__).parents[4] / "rag" / "graphrag" / "search.py",
    )
    assert spec is not None and spec.loader is not None
    search_module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(search_module)
    KGSearch = search_module.KGSearch
finally:
    nlp_search.Dealer = mocked_dealer
    nlp_search.index_name = mocked_index_name


def make_kg_search():
    search = KGSearch.__new__(KGSearch)
    search.dataStore = MagicMock()
    return search


@pytest.mark.asyncio
async def test_get_relevant_entities_awaits_vector_search():
    search = make_kg_search()
    search.get_vector = AsyncMock(return_value=sentinel.entity_vector)
    search.dataStore.search.return_value = sentinel.entity_search_result
    search._ent_info_from_ = MagicMock(return_value=sentinel.entities)

    result = await search.get_relevant_ents_by_keywords(
        ["医保", "一档"],
        {"available_int": 1},
        ["ragflow_tenant"],
        ["kb_id"],
        sentinel.embedding_model,
    )

    search.get_vector.assert_awaited_once_with("医保, 一档", sentinel.embedding_model, 1024, 0.3)
    assert search.dataStore.search.call_args.args[3] == [sentinel.entity_vector]
    search._ent_info_from_.assert_called_once_with(sentinel.entity_search_result, 0.3)
    assert result is sentinel.entities


@pytest.mark.asyncio
async def test_get_relevant_entities_logs_raw_candidates(caplog):
    search = make_kg_search()
    search.get_vector = AsyncMock(return_value=sentinel.entity_vector)
    search.dataStore.search.return_value = sentinel.entity_search_result
    search.dataStore.get_total.return_value = 7
    search.dataStore.get_fields.return_value = {
        "entity-1": {"entity_kwd": "年假", "_score": "0.87", "kb_id": "kb-hr"},
        "entity-2": {"entity_kwd": "调休假", "_score": "0.61", "kb_id": "kb-hr"},
    }
    search._ent_info_from_ = MagicMock(return_value=sentinel.entities)

    with caplog.at_level(logging.INFO):
        await search.get_relevant_ents_by_keywords(
            ["带薪年假", "10天", "80小时"],
            {"available_int": 1},
            ["ragflow_tenant"],
            ["kb-hr"],
            sentinel.embedding_model,
        )

    message = next(message for message in caplog.messages if "KG raw retrieval" in message)
    assert "kind=entity" in message
    assert "raw_total=7" in message
    assert "returned=2" in message
    assert "threshold=0.3000" in message
    assert '"entity": "年假"' in message
    assert '"score": 0.87' in message
    assert '"kb_id": "kb-hr"' in message
    assert "kb_id" in search.dataStore.search.call_args.args[0]
    assert "_score" in search.dataStore.search.call_args.args[0]


@pytest.mark.asyncio
async def test_get_relevant_relations_awaits_vector_search():
    search = make_kg_search()
    search.get_vector = AsyncMock(return_value=sentinel.relation_vector)
    search.dataStore.search.return_value = sentinel.relation_search_result
    search._relation_info_from_ = MagicMock(return_value=sentinel.relations)

    result = await search.get_relevant_relations_by_txt(
        "这个月社保档位什么时候生效",
        {"available_int": 1},
        ["ragflow_tenant"],
        ["kb_id"],
        sentinel.embedding_model,
    )

    search.get_vector.assert_awaited_once_with(
        "这个月社保档位什么时候生效",
        sentinel.embedding_model,
        1024,
        0.3,
    )
    assert search.dataStore.search.call_args.args[3] == [sentinel.relation_vector]
    search._relation_info_from_.assert_called_once_with(sentinel.relation_search_result, 0.3)
    assert result is sentinel.relations


@pytest.mark.asyncio
async def test_get_relevant_relations_logs_raw_candidates(caplog):
    search = make_kg_search()
    search.get_vector = AsyncMock(return_value=sentinel.relation_vector)
    search.dataStore.search.return_value = sentinel.relation_search_result
    search.dataStore.get_total.return_value = 3
    search.dataStore.get_fields.return_value = {
        "relation-1": {
            "from_entity_kwd": "私车公用",
            "to_entity_kwd": "机动车第三者责任险",
            "_score": "0.79",
            "kb_id": "kb-admin",
        }
    }
    search._relation_info_from_ = MagicMock(return_value=sentinel.relations)

    with caplog.at_level(logging.INFO):
        await search.get_relevant_relations_by_txt(
            "私车公用的保险最低要求",
            {"available_int": 1},
            ["ragflow_tenant"],
            ["kb-admin"],
            sentinel.embedding_model,
        )

    message = next(message for message in caplog.messages if "KG raw retrieval" in message)
    assert "kind=relation" in message
    assert "raw_total=3" in message
    assert "returned=1" in message
    assert "threshold=0.3000" in message
    assert '"from_entity": "私车公用"' in message
    assert '"to_entity": "机动车第三者责任险"' in message
    assert '"score": 0.79' in message
    assert '"kb_id": "kb-admin"' in message
    assert "kb_id" in search.dataStore.search.call_args.args[0]
