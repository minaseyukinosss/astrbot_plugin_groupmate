from __future__ import annotations

from groupmate.social_runtime.actions.style import (
    PersonaStyleSnapshot,
    StyleContext,
    StyleDirector,
)
from groupmate.social_runtime.persona.modes import PersonaModeState
from groupmate.social_runtime.social_moves import SocialMovePlan
from groupmate.social_runtime.social_scenes import SocialScene
from groupmate.social_runtime.stances import PermissionSnapshot, StanceDecision
from groupmate.social_runtime.society.relationships import RelationshipProjection


def _persona() -> PersonaStyleSnapshot:
    return PersonaStyleSnapshot(
        persona_id="persona-1",
        default_address="朋友",
        expression=("warm",),
    )


def _relationship(**overrides) -> RelationshipProjection:
    values = {
        "persona_id": "persona-1",
        "group_id": "group-1",
        "subject_id": "user-1",
    }
    values.update(overrides)
    return RelationshipProjection(**values)


def _context(**overrides) -> StyleContext:
    scene = SocialScene.create(
        scene_kind="fact_question",
        target_scope="INDIVIDUAL",
        target_id="user-1",
        literal_subject="插件用途",
        user_move="asks_fact",
        continuity_event_ids=("m1",),
        confidence=0.9,
    )
    stance = StanceDecision.create(
        attitude="FOCUSED",
        willingness="WILLING",
        boundary="NONE",
        concession="NONE",
        effort="NORMAL",
        initiative="ALLOW",
        reason_event_ids=("m1",),
        permission=PermissionSnapshot(True, "social_reply"),
    )
    move = SocialMovePlan.create(primary_move="DIRECT_ANSWER")
    values = {
        "persona": _persona(),
        "mode": PersonaModeState.social(),
        "relationship": _relationship(warmth=30, play_acceptance=20),
        "culture_patterns": ("梗不要复读",),
        "recent_outputs": ("上一次的回复",),
        "scene": scene,
        "stance": stance,
        "move": move,
        "token_budget": 80,
    }
    values.update(overrides)
    return StyleContext(**values)


def test_direct_answer_style_is_limited_to_three_segments():
    directive = StyleDirector().direct(_context())

    assert directive.act == "direct_answer"
    assert directive.max_segments == 3
    assert directive.max_sentences >= directive.max_segments
    assert directive.address == "朋友"


def test_drowsy_mode_shortens_the_direct_answer_budget():
    awake = StyleDirector().direct(_context())
    drowsy = StyleDirector().direct(
        _context(
            mode=PersonaModeState(
                primary="social",
                modifiers=("drowsy",),
                activated_by=("clock-1",),
                expires_at=None,
            )
        )
    )

    assert drowsy.max_chars < awake.max_chars
    assert drowsy.max_sentences < awake.max_sentences


def test_boundary_mode_forbids_playfulness_even_with_a_playful_relationship():
    directive = StyleDirector().direct(
        _context(
            mode=PersonaModeState("boundary", (), ("boundary-1",), None),
            relationship=_relationship(play_acceptance=100, warmth=100),
        )
    )

    assert directive.mode == "boundary"
    assert directive.playfulness == 0
    assert directive.posture == "firm"


def test_relationship_changes_tone_without_granting_capability_permission():
    distant = StyleDirector().direct(_context(relationship=_relationship(warmth=-30)))
    close = StyleDirector().direct(_context(relationship=_relationship(warmth=80)))

    assert close.warmth > distant.warmth
    assert not hasattr(close, "capability_permission")


def test_firm_social_move_overrides_friendly_relationship_surface():
    firm_stance = StanceDecision.create(
        attitude="IRRITATED",
        willingness="UNWILLING",
        boundary="FIRM",
        concession="NONE",
        effort="MINIMAL",
        initiative="AVOID",
        reason_event_ids=("m1",),
        permission=PermissionSnapshot(True, "social_reply"),
    )
    directive = StyleDirector().direct(
        _context(
            relationship=_relationship(warmth=100, play_acceptance=100),
            stance=firm_stance,
            move=SocialMovePlan.create(primary_move="FIRM_BOUNDARY"),
        )
    )
    assert directive.act == "firm_boundary"
    assert directive.posture == "firm"
    assert directive.playfulness == 0
