from __future__ import annotations

import dataclasses

import pytest

from groupmate.social_runtime.knowledge.contracts import (
    KnowledgeFact,
    KnowledgeNeed,
    KnowledgeSnapshot,
    TopicUnderstandingFrame,
    VersionReference,
)
from groupmate.social_runtime.knowledge.snapshot import KnowledgeSnapshotBuilder


def test_snapshot_freezes_only_fresh_current_scoped_facts():
    now = 100
    frame = TopicUnderstandingFrame.create(
        frame_id="frame:1",
        game_ids=("game:genshin-impact",),
        resolved_entities=(),
        resolved_terms=(),
        discourse_referents=(),
        version_reference=VersionReference.create(
            game_id="game:genshin-impact",
            relative_kind="next",
            disclosure_kind="official",
            region="国服",
            platform="PC",
            confidence=0.9,
        ),
        conversation_intent_hint="version_question",
        ambiguity_codes=("risk:version_state",),
        confidence=0.9,
        supporting_knowledge_ids=(),
    )
    need = KnowledgeNeed.create(
        outcome="fresh_evidence_required",
        gap_codes=("risk:version_state",),
        entity_ids=("game:genshin-impact",),
        query_intents=("official_next_version",),
        expires_at=150,
    )

    def fact(knowledge_id: str, **overrides):
        values = {
            "knowledge_id": knowledge_id,
            "game_entity_id": "game:genshin-impact",
            "entity_id": "version:next",
            "safe_summary": "下一版本官方前瞻已经公布。",
            "risk_class": "version_state",
            "evidence_level": "official",
            "qualifier": "official_preview",
            "checked_at": 90,
            "expires_at": 140,
            "source_ids": ("source:official",),
            "version_slot_id": "slot:next",
            "region": "国服",
            "platform": "PC",
            "status": "active",
            "version_state_revision": 3,
        }
        values.update(overrides)
        return KnowledgeFact.create(**values)

    stable = fact(
        "fact:stable",
        entity_id="term:resin",
        safe_summary="树脂是游戏内的一种体力资源。",
        risk_class="stable_semantic",
        evidence_level="bundled",
        qualifier="stable",
        expires_at=180,
        source_ids=("source:seed",),
        version_slot_id=None,
        region=None,
        platform=None,
        version_state_revision=0,
    )
    accepted = fact("fact:official")
    rejected = (
        fact("fact:expired", expires_at=100),
        fact("fact:disputed", status="disputed"),
        fact("fact:wrong-version", version_slot_id="slot:old"),
        fact("fact:wrong-region", region="国际服"),
        fact("fact:wrong-platform", platform="PS5"),
        fact("fact:wrong-game", game_entity_id="game:other"),
        fact("fact:wrong-revision", version_state_revision=2),
    )
    hits = [accepted, stable, *rejected]
    builder = KnowledgeSnapshotBuilder()
    snapshot = builder.build(
        frame,
        need,
        hits,
        now,
        version_slot_id="slot:next",
        version_state_revision=3,
    )

    assert tuple(item.knowledge_id for item in snapshot.allowed_knowledge_facts) == (
        "fact:official",
        "fact:stable",
    )
    assert tuple(item.knowledge_id for item in snapshot.strict_fact_fragments) == (
        "fact:official",
    )
    assert snapshot.source_ids == ("source:official", "source:seed")
    assert snapshot.expires_at == 140
    assert snapshot.version_state_revision == 3
    assert builder.build(
        frame,
        need,
        reversed(hits),
        now,
        version_slot_id="slot:next",
        version_state_revision=3,
    ).snapshot_id == snapshot.snapshot_id
    with pytest.raises(ValueError, match="conflicting duplicate"):
        builder.build(
            frame,
            need,
            (accepted, dataclasses.replace(accepted, safe_summary="冲突内容")),
            now,
            version_slot_id="slot:next",
            version_state_revision=3,
        )

    hits.clear()
    assert snapshot.allowed_knowledge_facts[0].knowledge_id == "fact:official"
    with pytest.raises(dataclasses.FrozenInstanceError):
        snapshot.expires_at = 999

    invalid_facts = (
        {"qualifier": "invented"},
        {"expires_at": 90},
        {"source_ids": ()},
        {"qualifier": "stable"},
        {"version_slot_id": None},
    )
    for overrides in invalid_facts:
        with pytest.raises(ValueError):
            fact("fact:invalid", **overrides)

    with pytest.raises(ValueError, match="source IDs"):
        KnowledgeSnapshot.create(
            snapshot_id="snapshot:incomplete-sources",
            topic_frame_id=snapshot.topic_frame_id,
            allowed_knowledge_facts=snapshot.allowed_knowledge_facts,
            strict_fact_fragments=snapshot.strict_fact_fragments,
            source_ids=(),
            checked_at=snapshot.checked_at,
            expires_at=snapshot.expires_at,
            version_state_revision=snapshot.version_state_revision,
        )
    with pytest.raises(ValueError, match="revision"):
        KnowledgeSnapshot.create(
            snapshot_id="snapshot:wrong-revision",
            topic_frame_id=snapshot.topic_frame_id,
            allowed_knowledge_facts=snapshot.allowed_knowledge_facts,
            strict_fact_fragments=snapshot.strict_fact_fragments,
            source_ids=snapshot.source_ids,
            checked_at=snapshot.checked_at,
            expires_at=snapshot.expires_at,
            version_state_revision=2,
        )
    with pytest.raises(ValueError, match="fragment text"):
        KnowledgeSnapshot.create(
            snapshot_id="snapshot:rewritten-fragment",
            topic_frame_id=snapshot.topic_frame_id,
            allowed_knowledge_facts=snapshot.allowed_knowledge_facts,
            strict_fact_fragments=(
                dataclasses.replace(
                    snapshot.strict_fact_fragments[0], text="模型改写的版本结论"
                ),
            ),
            source_ids=snapshot.source_ids,
            checked_at=snapshot.checked_at,
            expires_at=snapshot.expires_at,
            version_state_revision=snapshot.version_state_revision,
        )
