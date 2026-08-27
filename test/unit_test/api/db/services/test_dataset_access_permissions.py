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
import sys
import types
import warnings
from types import SimpleNamespace

import pytest
from peewee import SqliteDatabase

# xgboost imports pkg_resources and emits a deprecation warning that is promoted
# to error in our pytest configuration; ignore it for this unit test module.
warnings.filterwarnings(
    "ignore",
    message="pkg_resources is deprecated as an API.*",
    category=UserWarning,
)


def _install_cv2_stub_if_unavailable():
    try:
        import cv2  # noqa: F401
        return
    except Exception:
        pass

    stub = types.ModuleType("cv2")

    stub.INTER_LINEAR = 1
    stub.INTER_CUBIC = 2
    stub.BORDER_CONSTANT = 0
    stub.BORDER_REPLICATE = 1
    stub.COLOR_BGR2RGB = 0
    stub.COLOR_BGR2GRAY = 1
    stub.COLOR_GRAY2BGR = 2
    stub.IMREAD_IGNORE_ORIENTATION = 128
    stub.IMREAD_COLOR = 1
    stub.RETR_LIST = 1
    stub.CHAIN_APPROX_SIMPLE = 2

    def _missing(*_args, **_kwargs):
        raise RuntimeError("cv2 runtime call is unavailable in this test environment")

    def _module_getattr(name):
        if name.isupper():
            return 0
        return _missing

    stub.__getattr__ = _module_getattr
    sys.modules["cv2"] = stub


_install_cv2_stub_if_unavailable()

from api.common.check_team_permission import check_kb_team_permission
from api.db import TeamMemberState, TenantPermission
from api.db.db_models import Knowledgebase, Team, TeamMember
from api.db.services.document_service import DocumentService
from api.db.services.knowledgebase_service import KnowledgebaseService
from api.db.services.team_service import TeamMemberService, TeamService
from common.constants import StatusEnum


def _unwrapped_kb_accessible():
    return KnowledgebaseService.accessible.__func__.__wrapped__


def _unwrapped_doc_accessible():
    return DocumentService.accessible.__func__.__wrapped__


@pytest.fixture()
def kb_team_database():
    database = SqliteDatabase(":memory:")
    models = [Knowledgebase, Team, TeamMember]
    with database.bind_ctx(models), database.connection_context():
        database.create_tables(models)
        yield


def _persist_team(team_id: str, owner_id: str):
    return Team.create(
        id=team_id,
        tenant_id=owner_id,
        name=team_id,
        created_by=owner_id,
        status=StatusEnum.VALID.value,
    )


def _persist_member(member_id: str, team_id: str, user_id: str):
    return TeamMember.create(
        id=member_id,
        team_id=team_id,
        user_id=user_id,
        state=TeamMemberState.ACTIVE.value,
        invited_by="owner-b",
        status=StatusEnum.VALID.value,
    )


def _persist_kb(kb_id: str, owner_id: str, team_id: str):
    return Knowledgebase.create(
        id=kb_id,
        tenant_id=owner_id,
        name=kb_id,
        embd_id="embedding-1",
        created_by=owner_id,
        permission=TenantPermission.TEAM.value,
        team_id=team_id,
        status=StatusEnum.VALID.value,
    )


def test_private_dataset_is_not_accessible_to_other_tenant_member(monkeypatch):
    kb = SimpleNamespace(
        id="kb-private",
        tenant_id="owner-1",
        permission=TenantPermission.ME.value,
        team_id=None,
        status=StatusEnum.VALID.value,
    )

    monkeypatch.setattr(KnowledgebaseService, "get_by_id", classmethod(lambda cls, kb_id: (True, kb)))
    monkeypatch.setattr(TeamMemberService, "active_team_ids", lambda _user_id: ["team-1"])

    assert _unwrapped_kb_accessible()(KnowledgebaseService, "kb-private", "member-2") is False


def test_dataset_owner_can_access_every_valid_dataset(monkeypatch):
    kb = SimpleNamespace(
        id="kb-team",
        tenant_id="owner-1",
        permission=TenantPermission.TEAM.value,
        team_id="team-1",
        status=StatusEnum.VALID.value,
    )

    monkeypatch.setattr(KnowledgebaseService, "get_by_id", classmethod(lambda cls, _kb_id: (True, kb)))

    assert _unwrapped_kb_accessible()(KnowledgebaseService, "kb-team", "owner-1") is True


def test_team_dataset_is_accessible_to_active_team_member(monkeypatch):
    kb = SimpleNamespace(
        id="kb-team",
        tenant_id="owner-1",
        permission=TenantPermission.TEAM.value,
        team_id="team-1",
        status=StatusEnum.VALID.value,
    )

    monkeypatch.setattr(KnowledgebaseService, "get_by_id", classmethod(lambda cls, _kb_id: (True, kb)))
    monkeypatch.setattr(TeamMemberService, "active_team_ids", lambda _user_id: ["team-1"])
    monkeypatch.setattr(TeamService, "get_owned_team", lambda _team_id, _owner_id: object())
    monkeypatch.setattr(
        "api.db.services.knowledgebase_service.TenantService.get_joined_tenants_by_user_id",
        lambda _user_id: [],
    )

    assert _unwrapped_kb_accessible()(KnowledgebaseService, "kb-team", "member-2") is True


def test_team_dataset_rejects_member_of_different_team(monkeypatch):
    kb = SimpleNamespace(
        id="kb-team",
        tenant_id="owner-1",
        permission=TenantPermission.TEAM.value,
        team_id="team-1",
        status=StatusEnum.VALID.value,
    )

    monkeypatch.setattr(KnowledgebaseService, "get_by_id", classmethod(lambda cls, _kb_id: (True, kb)))
    monkeypatch.setattr(TeamMemberService, "active_team_ids", lambda _user_id: ["team-2"])
    monkeypatch.setattr(
        "api.db.services.knowledgebase_service.TenantService.get_joined_tenants_by_user_id",
        lambda _user_id: [{"tenant_id": "owner-1"}],
    )

    assert _unwrapped_kb_accessible()(KnowledgebaseService, "kb-team", "member-2") is False


def test_team_dataset_rejects_invited_or_invalid_team_member(monkeypatch):
    kb = SimpleNamespace(
        id="kb-team",
        tenant_id="owner-1",
        permission=TenantPermission.TEAM.value,
        team_id="team-1",
        status=StatusEnum.VALID.value,
    )

    monkeypatch.setattr(KnowledgebaseService, "get_by_id", classmethod(lambda cls, _kb_id: (True, kb)))
    monkeypatch.setattr(TeamMemberService, "active_team_ids", lambda _user_id: [])
    monkeypatch.setattr(
        "api.db.services.knowledgebase_service.TenantService.get_joined_tenants_by_user_id",
        lambda _user_id: [{"tenant_id": "owner-1"}],
    )

    assert _unwrapped_kb_accessible()(KnowledgebaseService, "kb-team", "member-2") is False


def test_legacy_user_tenant_membership_does_not_grant_dataset_access(monkeypatch):
    kb = SimpleNamespace(
        id="kb-team",
        tenant_id="owner-1",
        permission=TenantPermission.TEAM.value,
        team_id="team-1",
        status=StatusEnum.VALID.value,
    )

    monkeypatch.setattr(KnowledgebaseService, "get_by_id", classmethod(lambda cls, _kb_id: (True, kb)))
    monkeypatch.setattr(TeamMemberService, "active_team_ids", lambda _user_id: [])
    monkeypatch.setattr(
        "api.db.services.knowledgebase_service.TenantService.get_joined_tenants_by_user_id",
        lambda _user_id: [{"tenant_id": "owner-1"}],
    )

    assert _unwrapped_kb_accessible()(KnowledgebaseService, "kb-team", "legacy-member") is False


def test_compatibility_helper_uses_team_authorization_for_dict_and_model(monkeypatch):
    kb_dict = {
        "id": "kb-team-dict",
        "tenant_id": "owner-1",
        "permission": TenantPermission.TEAM.value,
        "team_id": "team-1",
        "status": StatusEnum.VALID.value,
    }
    kb_model = Knowledgebase(
        id="kb-team-model",
        tenant_id="owner-1",
        permission=TenantPermission.TEAM.value,
        team_id="team-1",
        status=StatusEnum.VALID.value,
    )
    monkeypatch.setattr(TeamMemberService, "active_team_ids", lambda _user_id: ["team-1"])
    monkeypatch.setattr(TeamService, "get_owned_team", lambda _team_id, _owner_id: object())
    monkeypatch.setattr(
        "api.db.services.user_service.TenantService.get_joined_tenants_by_user_id",
        lambda _user_id: [],
    )

    assert check_kb_team_permission(kb_dict, "member-2") is True
    assert check_kb_team_permission(kb_model, "member-2") is True


def test_cross_owner_persisted_team_assignment_is_hidden_from_dataset_detail(kb_team_database):
    _persist_team("team-b", "owner-b")
    _persist_member("membership-b", "team-b", "member-b")
    _persist_kb("kb-cross-owner", "owner-a", "team-b")

    assert KnowledgebaseService.get_kb_by_id("kb-cross-owner", "member-b") == []
    assert KnowledgebaseService.get_kb_by_id("kb-cross-owner", "owner-a")[0]["id"] == "kb-cross-owner"


def test_cross_owner_persisted_team_assignment_is_hidden_from_dataset_list(kb_team_database):
    _persist_team("team-b", "owner-b")
    _persist_member("membership-b", "team-b", "member-b")
    _persist_kb("kb-cross-owner", "owner-a", "team-b")
    _persist_kb("kb-same-owner", "owner-b", "team-b")

    active_team_ids = TeamMemberService.active_team_ids("member-b")
    datasets, total = KnowledgebaseService.get_list(
        active_team_ids,
        "member-b",
        1,
        20,
        "create_time",
        False,
        None,
        None,
        None,
    )

    assert [dataset["id"] for dataset in datasets] == ["kb-same-owner"]
    assert total == 1

    owner_datasets, owner_total = KnowledgebaseService.get_list(
        [],
        "owner-a",
        1,
        20,
        "create_time",
        False,
        None,
        None,
        None,
    )
    assert [dataset["id"] for dataset in owner_datasets] == ["kb-cross-owner"]
    assert owner_total == 1


def test_document_access_respects_dataset_permission(monkeypatch):
    doc = SimpleNamespace(id="doc-1", kb_id="kb-private")

    monkeypatch.setattr(DocumentService, "get_by_id", classmethod(lambda cls, doc_id: (True, doc)))
    monkeypatch.setattr(KnowledgebaseService, "accessible", classmethod(lambda cls, kb_id, user_id: False))

    assert _unwrapped_doc_accessible()(DocumentService, "doc-1", "member-2") is False
