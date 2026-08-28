import pytest
from peewee import IntegrityError, OperationalError, SqliteDatabase

from api.db import TenantPermission, UserTenantRole
from api.db.db_models import Document, File, Knowledgebase, Team, TeamMember, Tenant, UserTenant
from api.db.team_migration import backfill_default_teams


class PostgresAbortEmulatingSqliteDatabase(SqliteDatabase):
    """Make a duplicate insert poison the transaction unless it used a savepoint."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._savepoint_depth = 0
        self._transaction_aborted = False

    def execute_sql(self, sql, params=None, commit=None):
        statement = sql.lstrip().upper()
        if statement.startswith("SAVEPOINT"):
            self._savepoint_depth += 1
        if self._transaction_aborted and not statement.startswith(("ROLLBACK", "RELEASE SAVEPOINT")):
            raise OperationalError("current transaction is aborted")
        try:
            return super().execute_sql(sql, params, commit)
        except IntegrityError:
            if self._savepoint_depth == 0:
                self._transaction_aborted = True
            raise
        finally:
            if statement.startswith("ROLLBACK TO SAVEPOINT"):
                self._transaction_aborted = False
            elif statement.startswith("RELEASE SAVEPOINT"):
                self._savepoint_depth -= 1


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


def test_backfill_reactivates_invalid_default_team_only_for_unbackfilled_records(temporary_team_database):
    for tenant_id in ("tenant-recover", "tenant-deleted"):
        Tenant.create(
            id=tenant_id,
            name=tenant_id,
            llm_id="chat-1",
            embd_id="embedding-1",
            asr_id="asr-1",
            img2txt_id="image-1",
            rerank_id="rerank-1",
            parser_ids="naive",
        )
    Team.insert_many(
        [
            {"id": "team-recover", "tenant_id": "tenant-recover", "name": "默认团队", "created_by": "owner-1", "status": "0"},
            {"id": "team-deleted", "tenant_id": "tenant-deleted", "name": "默认团队", "created_by": "owner-1", "status": "0"},
        ]
    ).execute()
    UserTenant.insert_many(
        [
            {"id": "recover-member", "tenant_id": "tenant-recover", "user_id": "user-recover", "role": UserTenantRole.NORMAL.value, "invited_by": "owner-1"},
            {"id": "deleted-member", "tenant_id": "tenant-deleted", "user_id": "user-deleted", "role": UserTenantRole.NORMAL.value, "invited_by": "owner-1"},
        ]
    ).execute()
    TeamMember.create(
        id="existing-deleted-member",
        team_id="team-deleted",
        user_id="user-deleted",
        state="active",
        invited_by="owner-1",
        status="0",
    )
    Knowledgebase.create(
        id="kb-recover",
        tenant_id="tenant-recover",
        name="recoverable",
        embd_id="embedding-1",
        created_by="owner-1",
        permission=TenantPermission.TEAM.value,
    )

    result = backfill_default_teams()

    assert result == {"teams_created": 0, "members_created": 1, "datasets_assigned": 1}
    assert Team.get_by_id("team-recover").status == "1"
    assert Knowledgebase.get_by_id("kb-recover").team_id == "team-recover"
    assert Team.get_by_id("team-deleted").status == "0"


def test_backfill_survives_team_and_member_unique_key_races_with_savepoints(monkeypatch):
    from api.db import team_migration

    database = PostgresAbortEmulatingSqliteDatabase(":memory:")
    models = [Tenant, UserTenant, Knowledgebase, Team, TeamMember]
    with database.bind_ctx(models), database.connection_context():
        database.create_tables(models)
        monkeypatch.setattr(team_migration, "DB", database)
        Tenant.create(
            id="tenant-race",
            name="tenant-race",
            llm_id="chat-1",
            embd_id="embedding-1",
            asr_id="asr-1",
            img2txt_id="image-1",
            rerank_id="rerank-1",
            parser_ids="naive",
        )
        UserTenant.create(
            id="race-user-tenant",
            tenant_id="tenant-race",
            user_id="user-race",
            role=UserTenantRole.NORMAL.value,
            invited_by="owner-1",
        )
        Team.create(id="team-race", tenant_id="tenant-race", name="默认团队", created_by="owner-1")
        TeamMember.create(
            id="member-race",
            team_id="team-race",
            user_id="user-race",
            state="active",
            invited_by="owner-1",
        )
        Knowledgebase.create(
            id="kb-race",
            tenant_id="tenant-race",
            name="race knowledge base",
            embd_id="embedding-1",
            created_by="owner-1",
            permission=TenantPermission.TEAM.value,
        )

        original_team_lookup = Team.get_or_none
        hidden_team_lookups = 2

        def hide_team_once(*args, **kwargs):
            nonlocal hidden_team_lookups
            if hidden_team_lookups and kwargs.get("tenant_id") == "tenant-race":
                hidden_team_lookups -= 1
                return None
            return original_team_lookup(*args, **kwargs)

        original_member_lookup = TeamMember.get_or_none
        hidden_member_lookup = True

        def hide_member_once(*args, **kwargs):
            nonlocal hidden_member_lookup
            if hidden_member_lookup and kwargs.get("team_id") == "team-race":
                hidden_member_lookup = False
                return None
            return original_member_lookup(*args, **kwargs)

        monkeypatch.setattr(Team, "get_or_none", staticmethod(hide_team_once))
        monkeypatch.setattr(TeamMember, "get_or_none", staticmethod(hide_member_once))

        result = backfill_default_teams()

        assert result == {"teams_created": 0, "members_created": 0, "datasets_assigned": 1}
        assert Knowledgebase.get_by_id("kb-race").team_id == "team-race"
