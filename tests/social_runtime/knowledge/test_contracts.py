from __future__ import annotations

import dataclasses
import importlib
import json
import math

import pytest


def _contracts():
    return importlib.import_module("groupmate.social_runtime.knowledge.contracts")


def _entity(contracts, index: int = 1):
    return contracts.ResolvedEntity.create(
        entity_id=f"entity:{index}",
        entity_type="character",
        canonical_name=f"角色{index}",
        canonical_game_id="game:genshin-impact",
        matched_alias=f"别名{index}",
        confidence=0.9,
        supporting_knowledge_ids=(f"seed:entity:{index}",),
    )


def _term(contracts, index: int = 1):
    return contracts.ResolvedTerm.create(
        term_id=f"term:{index}",
        canonical_text=f"术语{index}",
        meaning_summary="稳定且不包含当期版本结论的社区语义",
        game_id="game:genshin-impact",
        term_kind="community",
        confidence=0.85,
        supporting_knowledge_ids=(f"seed:term:{index}",),
    )


def _referent(contracts, index: int = 1):
    return contracts.DiscourseReferent.create(
        surface=f"那个角色{index}",
        referent_kind="entity",
        entity_id=f"entity:{index}",
        confidence=0.8,
    )


def _frame_values(contracts, **overrides):
    values = {
        "frame_id": "knowledge-frame:1",
        "game_ids": ("game:genshin-impact",),
        "resolved_entities": (_entity(contracts),),
        "resolved_terms": (_term(contracts),),
        "discourse_referents": (_referent(contracts),),
        "version_reference": None,
        "conversation_intent_hint": "version_question",
        "ambiguity_codes": (),
        "confidence": 0.9,
        "supporting_knowledge_ids": ("seed:genshin:v1",),
    }
    values.update(overrides)
    return values


def test_topic_frame_freezes_caller_owned_inputs():
    contracts = _contracts()
    game_ids = ["game:genshin-impact"]
    entities = [_entity(contracts)]
    supporting_ids = ["seed:genshin:v1"]

    frame = contracts.TopicUnderstandingFrame.create(
        **_frame_values(
            contracts,
            game_ids=game_ids,
            resolved_entities=entities,
            supporting_knowledge_ids=supporting_ids,
        )
    )
    game_ids.append("game:honkai-star-rail")
    entities.clear()
    supporting_ids.clear()

    assert frame.game_ids == ("game:genshin-impact",)
    assert tuple(item.entity_id for item in frame.resolved_entities) == (
        "entity:1",
    )
    assert frame.supporting_knowledge_ids == ("seed:genshin:v1",)
    with pytest.raises(dataclasses.FrozenInstanceError):
        frame.confidence = 0.1


@pytest.mark.parametrize("frame_id", ["", "   "])
def test_topic_frame_rejects_blank_identity(frame_id):
    contracts = _contracts()

    with pytest.raises(ValueError, match="frame_id"):
        contracts.TopicUnderstandingFrame.create(
            **_frame_values(contracts, frame_id=frame_id)
        )


@pytest.mark.parametrize("confidence", [-0.01, 1.01, math.nan, True])
def test_topic_frame_rejects_invalid_confidence(confidence):
    contracts = _contracts()

    with pytest.raises(ValueError, match="confidence"):
        contracts.TopicUnderstandingFrame.create(
            **_frame_values(contracts, confidence=confidence)
        )


@pytest.mark.parametrize(
    ("field", "values"),
    [
        ("resolved_entities", "entities"),
        ("resolved_terms", "terms"),
        ("discourse_referents", "referents"),
        ("supporting_knowledge_ids", "supporting"),
    ],
)
def test_topic_frame_rejects_oversized_collections(field, values):
    contracts = _contracts()
    oversized = {
        "entities": tuple(_entity(contracts, index) for index in range(9)),
        "terms": tuple(_term(contracts, index) for index in range(13)),
        "referents": tuple(_referent(contracts, index) for index in range(9)),
        "supporting": tuple(f"knowledge:{index}" for index in range(17)),
    }[values]

    with pytest.raises(ValueError, match=field):
        contracts.TopicUnderstandingFrame.create(
            **_frame_values(contracts, **{field: oversized})
        )


def test_version_reference_requires_one_resolved_game():
    contracts = _contracts()

    with pytest.raises(ValueError, match="game_id"):
        contracts.VersionReference.create(
            game_id="",
            relative_kind="next",
            disclosure_kind="official",
            region=None,
            platform=None,
            confidence=0.8,
        )


@pytest.mark.parametrize(
    ("scope_kind", "group_id", "message"),
    [
        ("global", "group-1", "global observation cannot carry group_id"),
        ("group", None, "group observation requires group_id"),
    ],
)
def test_observation_enforces_global_and_group_scope(scope_kind, group_id, message):
    contracts = _contracts()

    with pytest.raises(ValueError, match=message):
        contracts.KnowledgeObservation.create(
            observation_id="observation:1",
            origin_class="human_chat",
            scope_kind=scope_kind,
            group_id=group_id,
            author_ref="author:opaque",
            source_event_id="event:1",
            source_id=None,
            entity_hint="原神",
            safe_summary="群友在自然聊天中提到原神",
            content_hash="a" * 64,
            occurred_at=100,
            recorded_at=101,
            status="pending",
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("origin_class", "forum_guess"),
        ("scope_kind", "persona"),
        ("status", "trusted"),
    ],
)
def test_observation_rejects_unknown_enumerated_values(field, value):
    contracts = _contracts()
    values = {
        "observation_id": "observation:1",
        "origin_class": "human_chat",
        "scope_kind": "group",
        "group_id": "group-1",
        "author_ref": "author:opaque",
        "source_event_id": "event:1",
        "source_id": None,
        "entity_hint": "原神",
        "safe_summary": "群友在自然聊天中提到原神",
        "content_hash": "a" * 64,
        "occurred_at": 100,
        "recorded_at": 101,
        "status": "pending",
    }
    values[field] = value

    with pytest.raises(ValueError, match=field):
        contracts.KnowledgeObservation.create(**values)


def test_observation_rejects_unsafe_summary_length():
    contracts = _contracts()

    with pytest.raises(ValueError, match="safe_summary"):
        contracts.KnowledgeObservation.create(
            observation_id="observation:1",
            origin_class="human_chat",
            scope_kind="group",
            group_id="group-1",
            author_ref="author:opaque",
            source_event_id="event:1",
            source_id=None,
            entity_hint="原神",
            safe_summary="长" * 241,
            content_hash="a" * 64,
            occurred_at=100,
            recorded_at=101,
            status="pending",
        )


def test_prompt_facts_are_json_serializable_bounded_and_private():
    contracts = _contracts()
    terms = tuple(
        contracts.ResolvedTerm.create(
            term_id=f"term:{index}",
            canonical_text=f"术语{index}",
            meaning_summary="稳" * 160,
            game_id="game:genshin-impact",
            term_kind="community",
            confidence=0.8,
            supporting_knowledge_ids=(f"seed:term:{index}",),
        )
        for index in range(12)
    )
    frame = contracts.TopicUnderstandingFrame.create(
        **_frame_values(contracts, resolved_terms=terms)
    )

    prompt_facts = frame.to_prompt_facts()
    encoded = json.dumps(prompt_facts, ensure_ascii=False, sort_keys=True)

    assert len(encoded) <= 1800
    assert "author_ref" not in encoded
    assert "source_event_id" not in encoded
    assert "internal_score" not in encoded
    assert prompt_facts["frame_id"] == "knowledge-frame:1"


def test_knowledge_need_rejects_unknown_outcome_and_expired_shape():
    contracts = _contracts()

    with pytest.raises(ValueError, match="outcome"):
        contracts.KnowledgeNeed.create(
            outcome="guess",
            gap_codes=(),
            entity_ids=(),
            query_intents=(),
            expires_at=100,
        )
    with pytest.raises(ValueError, match="expires_at"):
        contracts.KnowledgeNeed.create(
            outcome="none",
            gap_codes=(),
            entity_ids=(),
            query_intents=(),
            expires_at=-1,
        )
