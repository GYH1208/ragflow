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
import pytest
from peewee import SqliteDatabase

from api.apps.services import dataset_api_service
from api.db import KNOWLEDGEBASE_FOLDER_NAME, FileType
from api.db.db_models import File, Knowledgebase, Tenant
from api.db.services.file_service import FileService
from api.db.services.knowledgebase_service import KnowledgebaseService
from common.constants import FileSource


def _folder(file_id, parent_id, name):
    return {
        "id": file_id,
        "parent_id": parent_id,
        "tenant_id": "tenant-1",
        "created_by": "tenant-1",
        "name": name,
        "type": FileType.FOLDER.value,
        "size": 0,
        "location": "",
        "source_type": FileSource.KNOWLEDGEBASE,
    }


def _tenant():
    return {
        "id": "tenant-1",
        "name": "Tenant",
        "llm_id": "chat-1",
        "embd_id": "embedding-1",
        "asr_id": "asr-1",
        "img2txt_id": "vision-1",
        "rerank_id": "rerank-1",
        "parser_ids": "naive",
    }


@pytest.fixture()
def file_database():
    database = SqliteDatabase(":memory:")
    with database.bind_ctx([File, Knowledgebase, Tenant]):
        database.connect()
        database.create_tables([File, Knowledgebase, Tenant])
        Tenant.create(**_tenant())
        try:
            yield database
        finally:
            database.close()


def test_rename_kb_root_keeps_the_existing_tree_attached(file_database):
    database = file_database
    with database.atomic():
        File.create(**_folder("tenant-root", "tenant-root", "/"))
        File.create(
            **_folder(
                "kb-parent",
                "tenant-root",
                KNOWLEDGEBASE_FOLDER_NAME,
            )
        )
        File.create(**_folder("kb-root", "kb-parent", "原知识库"))
        File.create(
            id="document-file",
            parent_id="kb-root",
            tenant_id="tenant-1",
            created_by="tenant-1",
            name="报价单.docx",
            type="doc",
            size=10,
            location="报价单.docx",
            source_type=FileSource.KNOWLEDGEBASE,
        )

    with database.atomic():
        error = FileService.rename_kb_root_in_transaction(
            "tenant-1",
            "原知识库",
            "售后报价单",
        )

    assert error is None
    assert File.get_by_id("kb-root").name == "售后报价单"
    assert File.get_by_id("document-file").parent_id == "kb-root"


def test_rename_kb_root_rejects_an_existing_target_folder(file_database):
    database = file_database
    with database.atomic():
        File.create(**_folder("tenant-root", "tenant-root", "/"))
        File.create(
            **_folder(
                "kb-parent",
                "tenant-root",
                KNOWLEDGEBASE_FOLDER_NAME,
            )
        )
        File.create(**_folder("source-root", "kb-parent", "原知识库"))
        File.create(**_folder("target-root", "kb-parent", "售后报价单"))

    with database.atomic():
        error = FileService.rename_kb_root_in_transaction(
            "tenant-1",
            "原知识库",
            "售后报价单",
        )

    assert error == "A knowledge base folder with the target name already exists."
    assert File.get_by_id("source-root").name == "原知识库"
    assert File.get_by_id("target-root").name == "售后报价单"


def test_rename_kb_root_rejects_an_existing_target_file(file_database):
    database = file_database
    with database.atomic():
        File.create(**_folder("tenant-root", "tenant-root", "/"))
        File.create(**_folder("kb-parent", "tenant-root", KNOWLEDGEBASE_FOLDER_NAME))
        File.create(**_folder("source-root", "kb-parent", "原知识库"))
        File.create(
            id="target-file",
            parent_id="kb-parent",
            tenant_id="tenant-1",
            created_by="tenant-1",
            name="售后报价单",
            type="doc",
            size=1,
            location="售后报价单",
            source_type=FileSource.KNOWLEDGEBASE,
        )

    with database.atomic():
        error = FileService.rename_kb_root_in_transaction(
            "tenant-1",
            "原知识库",
            "售后报价单",
        )

    assert error == "A knowledge base folder with the target name already exists."
    assert File.get_by_id("source-root").name == "原知识库"


def test_rename_kb_root_is_a_noop_before_the_file_tree_is_initialized(
    file_database,
):
    with file_database.atomic():
        error = FileService.rename_kb_root_in_transaction(
            "tenant-1",
            "原知识库",
            "售后报价单",
        )

    assert error is None
    assert File.select().count() == 0


def test_rename_kb_root_does_not_guess_a_case_insensitive_source(
    file_database,
):
    database = file_database
    with database.atomic():
        File.create(**_folder("tenant-root", "tenant-root", "/"))
        File.create(
            **_folder(
                "kb-parent",
                "tenant-root",
                KNOWLEDGEBASE_FOLDER_NAME,
            )
        )
        File.create(**_folder("kb-root", "kb-parent", "original"))

    with database.atomic():
        error = FileService.rename_kb_root_in_transaction(
            "tenant-1",
            "Original",
            "Renamed",
        )

    assert error is None
    assert File.get_by_id("kb-root").name == "original"


def test_get_or_create_kb_root_uses_the_name_locked_from_the_dataset(
    file_database,
):
    database = file_database
    with database.atomic():
        Knowledgebase.create(
            id="kb-1",
            tenant_id="tenant-1",
            created_by="tenant-1",
            name="最新名称",
            embd_id="embedding-1",
        )

    root = FileService.get_or_create_kb_root("kb-1", "tenant-1")

    assert root["name"] == "最新名称"
    assert File.get_by_id(root["id"]).name == "最新名称"


def test_two_knowledge_bases_share_one_tenant_root_and_container(file_database):
    database = file_database
    with database.atomic():
        for kb_id, name in (("kb-1", "知识库一"), ("kb-2", "知识库二")):
            Knowledgebase.create(
                id=kb_id,
                tenant_id="tenant-1",
                created_by="tenant-1",
                name=name,
                embd_id="embedding-1",
            )

    FileService.get_or_create_kb_root("kb-1", "tenant-1")
    FileService.get_or_create_kb_root("kb-2", "tenant-1")

    tenant_roots = list(File.select().where(File.parent_id == File.id))
    assert len(tenant_roots) == 1
    containers = list(
        File.select().where(
            File.parent_id == tenant_roots[0].id,
            File.name == KNOWLEDGEBASE_FOLDER_NAME,
        )
    )
    assert len(containers) == 1
    assert {entry.name for entry in File.select().where(File.parent_id == containers[0].id)} == {"知识库一", "知识库二"}


def test_dataset_update_failure_rolls_back_the_real_file_rename(
    file_database,
    monkeypatch,
):
    database = file_database
    with database.atomic():
        Knowledgebase.create(
            id="kb-1",
            tenant_id="tenant-1",
            created_by="tenant-1",
            name="原知识库",
            embd_id="embedding-1",
        )
        File.create(**_folder("tenant-root", "tenant-root", "/"))
        File.create(**_folder("kb-parent", "tenant-root", KNOWLEDGEBASE_FOLDER_NAME))
        File.create(**_folder("kb-root", "kb-parent", "原知识库"))

    snapshot = Knowledgebase.get_by_id("kb-1")
    monkeypatch.setattr(dataset_api_service, "DB", database)
    monkeypatch.setattr(
        KnowledgebaseService,
        "update_by_id_in_transaction",
        classmethod(lambda cls, _kb_id, _values: 0),
    )

    result = dataset_api_service._update_dataset_with_locked_root(
        "tenant-1",
        snapshot,
        {"name": "售后报价单"},
    )

    assert result == (False, "Update dataset error.(Database error)")
    assert File.get_by_id("kb-root").name == "原知识库"
    assert Knowledgebase.get_by_id("kb-1").name == "原知识库"
