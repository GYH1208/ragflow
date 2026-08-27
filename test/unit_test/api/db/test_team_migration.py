import pytest
from peewee import SqliteDatabase

from api.db import TenantPermission, UserTenantRole
from api.db.db_models import Document, File, Knowledgebase, Team, TeamMember, Tenant, UserTenant
from api.db.team_migration import backfill_default_teams


@pytest.fixture
def temporary_team_database(monkeypatch):
    from api.db import team_migration

    database = SqliteDatabase(":memory:")
    models = [Tenant, UserTenant, Knowledgebase, Team, TeamMember, Document, File]
    with database.bind_ctx(models), database.connection_context():
        database.create_tables(models)
        monkeypatch.setattr(team_migration, "DB", database)
        yield database


def test_backfill_default_teams_is_idempotent_and_preserves_legacy_records(temporary_team_database):
    Tenant.create(
        id="tenant-1",
        name="active tenant",
        llm_id="chat-1",
        embd_id="embedding-1",
        asr_id="asr-1",
        img2txt_id="image-1",
        rerank_id="rerank-1",
        parser_ids="naive",
    )
    Tenant.create(
        id="tenant-disabled",
        name="disabled tenant",
        llm_id="chat-1",
        embd_id="embedding-1",
        asr_id="asr-1",
        img2txt_id="image-1",
        rerank_id="rerank-1",
        parser_ids="naive",
        status="0",
    )
    UserTenant.insert_many(
        [
            {"id": "owner", "tenant_id": "tenant-1", "user_id": "owner-1", "role": UserTenantRole.OWNER.value, "invited_by": "owner-1"},
            {"id": "normal", "tenant_id": "tenant-1", "user_id": "user-active", "role": UserTenantRole.NORMAL.value, "invited_by": "owner-1"},
            {"id": "invite", "tenant_id": "tenant-1", "user_id": "user-invited", "role": UserTenantRole.INVITE.value, "invited_by": "owner-1"},
            {"id": "removed", "tenant_id": "tenant-1", "user_id": "user-removed", "role": UserTenantRole.NORMAL.value, "invited_by": "owner-1", "status": "0"},
            {"id": "disabled-tenant", "tenant_id": "tenant-disabled", "user_id": "user-disabled", "role": UserTenantRole.NORMAL.value, "invited_by": "owner-1"},
        ]
    ).execute()
    Knowledgebase.insert_many(
        [
            {"id": "kb-shared", "tenant_id": "tenant-1", "name": "shared", "embd_id": "embedding-1", "created_by": "owner-1", "permission": TenantPermission.TEAM.value},
            {"id": "kb-private", "tenant_id": "tenant-1", "name": "private", "embd_id": "embedding-1", "created_by": "owner-1", "permission": TenantPermission.ME.value},
            {"id": "kb-disabled", "tenant_id": "tenant-disabled", "name": "disabled", "embd_id": "embedding-1", "created_by": "owner-1", "permission": TenantPermission.TEAM.value},
        ]
    ).execute()
    Document.create(id="document-1", kb_id="kb-shared", parser_id="naive", type="pdf", created_by="owner-1", suffix="pdf", name="legacy.pdf", location="document-location")
    File.create(id="file-1", parent_id="root", tenant_id="tenant-1", created_by="owner-1", name="legacy.pdf", location="file-location", type="pdf")

    user_tenants_before = list(UserTenant.select().order_by(UserTenant.id).dicts())
    knowledgebase_tenants_before = {knowledgebase.id: knowledgebase.tenant_id for knowledgebase in Knowledgebase.select()}
    document_before = Document.get_by_id("document-1").to_dict().copy()
    file_before = File.get_by_id("file-1").to_dict().copy()

    first = backfill_default_teams()
    second = backfill_default_teams()

    assert first == {"teams_created": 1, "members_created": 2, "datasets_assigned": 1}
    assert second == {"teams_created": 0, "members_created": 0, "datasets_assigned": 0}
    assert [team.name for team in Team.select()] == ["默认团队"]
    assert [member.state for member in TeamMember.select().order_by(TeamMember.user_id)] == ["active", "invited"]
    assert Knowledgebase.get_by_id("kb-shared").team_id == Team.get().id
    assert Knowledgebase.get_by_id("kb-private").team_id is None
    assert Knowledgebase.get_by_id("kb-disabled").team_id is None
    assert list(UserTenant.select().order_by(UserTenant.id).dicts()) == user_tenants_before
    assert {knowledgebase.id: knowledgebase.tenant_id for knowledgebase in Knowledgebase.select()} == knowledgebase_tenants_before
    assert Document.get_by_id("document-1").to_dict() == document_before
    assert File.get_by_id("file-1").to_dict() == file_before
