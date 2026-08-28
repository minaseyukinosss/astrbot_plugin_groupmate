from __future__ import annotations

import pytest

from groupmate.social_runtime.persistence.repositories import (
    SQLiteSocietyRepository,
    ScopeRequiredError,
)
from groupmate.social_runtime.persistence.schema import connect_database
from groupmate.social_runtime.society.impressions import ImpressionRegistry


@pytest.mark.parametrize(
    ("persona_id", "group_id", "subject_id"),
    [("", "g1", "u1"), ("aemeath", "", "u1"), ("aemeath", "g1", "")],
)
def test_society_queries_require_full_persona_group_subject_scope(
    tmp_path, persona_id, group_id, subject_id
):
    repository = SQLiteSocietyRepository(tmp_path / "groupmate-social-runtime-v2.db")
    with pytest.raises(ScopeRequiredError):
        repository.load_relationship(persona_id, group_id, subject_id)


def test_impression_queries_never_cross_group_scope(tmp_path):
    repository = SQLiteSocietyRepository(tmp_path / "groupmate-social-runtime-v2.db")
    impression = ImpressionRegistry().propose(
        persona_id="aemeath",
        group_id="g1",
        subject_id="u1",
        statement="喜欢讨论代码",
        evidence_event_ids=("e1",),
        expires_at=1000,
    )
    repository.save_impression(impression)

    assert repository.list_impressions("aemeath", "g1", "u1") == (impression,)
    assert repository.list_impressions("aemeath", "g2", "u1") == ()
    with pytest.raises(ScopeRequiredError):
        repository.list_impressions("aemeath", "", "u1")


def test_group_knowledge_tables_have_group_scope_without_persona_dimension(
    tmp_path,
):
    from groupmate.social_runtime.knowledge.repository import KnowledgeRepository

    path = tmp_path / "groupmate-social-runtime-v2.db"
    KnowledgeRepository(path)

    with connect_database(path) as db:
        for table in (
            "group_knowledge_aliases",
            "group_conventions",
            "group_topic_affinity",
            "group_topic_mentions",
        ):
            columns = {
                str(row[1]) for row in db.execute(f"PRAGMA table_info({table})")
            }
            assert "group_id" in columns
            assert "persona_id" not in columns
