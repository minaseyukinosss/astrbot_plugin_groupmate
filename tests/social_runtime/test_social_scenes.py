import asyncio

import pytest

from groupmate.social_runtime.chorus import ChorusEvidence
from groupmate.social_runtime.contracts import SocialEventEnvelope
from groupmate.social_runtime.social_context import SceneContextBuilder
from groupmate.social_runtime.social_scenes import (
    ChorusTarget,
    ChorusTone,
    SocialScene,
    SocialSceneInterpreter,
    TargetScope,
)


def test_social_scene_requires_concrete_subject_and_bounded_confidence():
    with pytest.raises(ValueError, match="literal_subject"):
        SocialScene.create(
            scene_kind="technical_help",
            target_scope="INDIVIDUAL",
            target_id="u1",
            literal_subject="",
            user_move="asks_for_diagnosis",
            continuity_event_ids=("m1",),
            confidence=0.9,
        )

    with pytest.raises(ValueError, match="confidence"):
        SocialScene.create(
            scene_kind="technical_help",
            target_scope="INDIVIDUAL",
            target_id="u1",
            literal_subject="报错",
            user_move="asks_for_diagnosis",
            continuity_event_ids=("m1",),
            confidence=1.1,
        )


def test_individual_scene_requires_target_but_group_scene_does_not():
    with pytest.raises(ValueError, match="target_id"):
        SocialScene.create(
            scene_kind="direct_greeting",
            target_scope="INDIVIDUAL",
            target_id=None,
            literal_subject="问候",
            user_move="greets",
            continuity_event_ids=("m1",),
            confidence=0.9,
        )

    scene = SocialScene.create(
        scene_kind="group_pile_on",
        target_scope="GROUP",
        target_id=None,
        literal_subject="群体起哄",
        user_move="group_teases_self",
        continuity_event_ids=("m1", "m2"),
        confidence=0.8,
    )
    assert scene.target_scope is TargetScope.GROUP


def test_self_targeted_chorus_keeps_evidence_but_not_member_id():
    scene = SocialScene.create(
        scene_kind="group_chorus",
        target_scope="GROUP",
        target_id=None,
        literal_subject="爱弥斯",
        user_move="chorus_about_self",
        continuity_event_ids=("m1", "m2"),
        repetition_count=2,
        chorus_target="SELF",
        chorus_target_id=None,
        chorus_chain_id="chorus:abc",
        chorus_payload="爱弥斯又嘴硬",
        chorus_event_ids=("m1", "m2"),
        chorus_participant_ids=("u1", "u2"),
        chorus_tone="SAFE_BANTER",
        confidence=0.95,
    )
    assert scene.chorus_target is ChorusTarget.SELF
    assert scene.chorus_tone is ChorusTone.SAFE_BANTER
    assert scene.chorus_target_id is None


def test_member_chorus_requires_known_target_and_complete_evidence():
    with pytest.raises(ValueError, match="chorus_target_id"):
        SocialScene.create(
            scene_kind="group_chorus",
            target_scope="GROUP",
            target_id=None,
            literal_subject="群友",
            user_move="chorus_about_member",
            continuity_event_ids=("m1", "m2"),
            repetition_count=2,
            chorus_target="MEMBER",
            chorus_chain_id="chorus:abc",
            chorus_payload="小林今天请客",
            chorus_event_ids=("m1", "m2"),
            chorus_participant_ids=("u1", "u2"),
            chorus_tone="SAFE_BANTER",
            confidence=0.95,
        )

    with pytest.raises(ValueError, match="subset"):
        SocialScene.create(
            scene_kind="group_chorus",
            target_scope="GROUP",
            target_id=None,
            literal_subject="小林",
            user_move="chorus_about_member",
            continuity_event_ids=("m1", "m2"),
            repetition_count=2,
            chorus_target="MEMBER",
            chorus_target_id="u9",
            chorus_chain_id="chorus:abc",
            chorus_payload="小林今天请客",
            chorus_event_ids=("m1", "invented"),
            chorus_participant_ids=("u1", "u2"),
            chorus_tone="SAFE_BANTER",
            confidence=0.95,
        )


class FixedSceneModel:
    def __init__(self, payload):
        self.payload = payload

    async def classify_scene(self, facts):
        del facts
        return self.payload


def _event(event_id, text, *, actor_id="u1", occurred_at=100):
    return SocialEventEnvelope.create(
        event_id=event_id,
        event_type="platform.message",
        occurred_at=occurred_at,
        received_at=occurred_at,
        persona_id="aemeath",
        group_id="g1",
        actor_id=actor_id,
        source_message_id=event_id,
        correlation_id=f"c:{event_id}",
        causation_id=None,
        payload={"text": text, "origin_kind": "USER_TEXT"},
    )


def _context(*, chorus=False, member_refs=None):
    context = SceneContextBuilder(max_chars=800).build(
        source_event=_event("m2", "爱弥斯又嘴硬", actor_id="u2", occurred_at=110),
        context_events=(_event("m1", "爱弥斯又嘴硬", actor_id="u1", occurred_at=100),),
        focus_event_ids=("m1", "m2"),
        target_id="u2",
        topic_id="m1",
        persona_actor_id="aemeath",
        persona_aliases=("爱弥斯",),
        member_refs=member_refs or {"u9": ("小林",)},
        profile=None,
        relationship_memories=(),
    )
    if not chorus:
        return context
    return context.with_chorus(
        ChorusEvidence(
            chain_id="chorus:abc",
            payload="爱弥斯又嘴硬",
            normalized_key="爱弥斯又嘴硬",
            event_ids=("m1", "m2"),
            participant_ids=("u1", "u2"),
            already_joined=False,
        )
    )


def test_interpreter_rejects_invented_evidence_ids():
    model = FixedSceneModel(
        {
            "scene_kind": "repeated_boundary_test",
            "target_scope": "INDIVIDUAL",
            "target_id": "u2",
            "literal_subject": "拥抱请求",
            "user_move": "repeats_intimacy_request",
            "continuity_event_ids": ["invented"],
            "repetition_count": 3,
            "constraints": [],
            "information_gaps": [],
            "capability_request": "NONE",
            "confidence": 0.96,
        }
    )

    result = asyncio.run(SocialSceneInterpreter(model).interpret(_context()))

    assert result.diagnostic_code == "scene_evidence_invalid"
    assert result.scene.scene_kind == "conservative_direct"


def test_response_act_keeps_current_scene_without_inventing_history():
    payload = {
        "scene_kind": "成员感谢后的自然回应", "target_scope": "INDIVIDUAL",
        "target_id": "u2", "literal_subject": "谢谢啦", "user_move": "感谢",
        "continuity_event_ids": [], "response_act": "acknowledge", "confidence": 0.9,
    }
    result = asyncio.run(SocialSceneInterpreter(FixedSceneModel(payload)).interpret(_context()))
    assert result.diagnostic_code is None
    assert result.scene.scene_kind == "成员感谢后的自然回应"
    assert result.scene.response_act == "acknowledge"
    assert result.scene.continuity_event_ids == ("m2",)
    for changed, diagnostic in (
        ({"continuity_event_ids": ["invented"]}, "scene_evidence_invalid"),
        ({"response_act": "invented"}, "scene_model_invalid"),
    ):
        invalid = asyncio.run(SocialSceneInterpreter(FixedSceneModel({**payload, **changed})).interpret(_context()))
        assert invalid.diagnostic_code == diagnostic


def test_scene_fallback_reports_invalid_field_without_leaking_model_text():
    model = FixedSceneModel({
        "scene_kind": "chat", "target_scope": "private-secret-invalid",
        "target_id": "u2", "literal_subject": "private text",
        "user_move": "asks", "continuity_event_ids": ["m2"],
        "confidence": 0.9,
    })
    result = asyncio.run(SocialSceneInterpreter(model).interpret(_context()))
    assert result.diagnostic_code == "scene_model_invalid"
    assert result.diagnostic["field"] == "target_scope"
    assert result.diagnostic["code"] == "scene_model_invalid"
    assert "private" not in str(result.diagnostic)


def test_interpreter_freezes_self_chorus_evidence_instead_of_model_payload():
    model = FixedSceneModel(
        {
            "scene_kind": "group_chorus",
            "target_scope": "GROUP",
            "target_id": None,
            "literal_subject": "爱弥斯",
            "user_move": "chorus_about_self",
            "continuity_event_ids": ["m1", "m2"],
            "chorus_target": "SELF",
            "chorus_target_id": None,
            "chorus_tone": "SAFE_BANTER",
            "chorus_payload": "模型试图改写的文本",
            "confidence": 0.97,
        }
    )

    result = asyncio.run(SocialSceneInterpreter(model).interpret(_context(chorus=True)))

    assert result.diagnostic_code is None
    assert result.scene.chorus_payload == "爱弥斯又嘴硬"
    assert result.scene.chorus_event_ids == ("m1", "m2")
    assert result.scene.chorus_chain_id == "chorus:abc"


def test_member_chorus_target_must_resolve_to_current_group_member():
    model = FixedSceneModel(
        {
            "scene_kind": "group_chorus",
            "target_scope": "GROUP",
            "target_id": None,
            "literal_subject": "某个群友",
            "user_move": "chorus_about_member",
            "continuity_event_ids": ["m1", "m2"],
            "chorus_target": "MEMBER",
            "chorus_target_id": "not-in-group",
            "chorus_tone": "SAFE_BANTER",
            "confidence": 0.97,
        }
    )

    result = asyncio.run(SocialSceneInterpreter(model).interpret(_context(chorus=True)))

    assert result.diagnostic_code == "chorus_member_invalid"
    assert result.scene.chorus_target is ChorusTarget.UNKNOWN
