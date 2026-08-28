import pytest
from peewee import IntegrityError, SqliteDatabase

from api.db import TeamMemberState
from api.db.db_models import Knowledgebase, Team, TeamMember


def test_team_schema_constraints():
    assert TeamMemberState.INVITED.value == "invited"
    assert TeamMemberState.ACTIVE.value == "active"
    assert (("tenant_id", "name"), True) in Team._meta.indexes
    assert (("team_id", "user_id"), True) in TeamMember._meta.indexes
    assert Knowledgebase.team_id.null is True
    assert Knowledgebase.team_id.index is True


def test_team_models_enforce_unique_membership_and_allow_unassigned_knowledgebases():
    database = SqliteDatabase(":memory:")
    models = [Team, TeamMember, Knowledgebase]

    with database.bind_ctx(models), database.connection_context():
        database.create_tables(models)
        Team.create(id="team-1", tenant_id="tenant-1", name="默认团队", created_by="owner-1")
        TeamMember.create(
            id="member-1",
            team_id="team-1",
            user_id="user-1",
            state=TeamMemberState.ACTIVE.value,
            invited_by="owner-1",
        )
        Knowledgebase.create(
            id="kb-1",
            tenant_id="tenant-1",
            name="shared knowledge base",
            embd_id="embedding-1",
            created_by="owner-1",
        )

        with pytest.raises(IntegrityError):
            Team.create(id="team-2", tenant_id="tenant-1", name="默认团队", created_by="owner-1")
        with pytest.raises(IntegrityError):
            TeamMember.create(
                id="member-2",
                team_id="team-1",
                user_id="user-1",
                state=TeamMemberState.INVITED.value,
                invited_by="owner-1",
            )

        assert Knowledgebase.get_by_id("kb-1").team_id is None
