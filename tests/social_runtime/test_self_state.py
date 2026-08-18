from __future__ import annotations

from dataclasses import replace

from groupmate.social_runtime.contracts import GlobalSelfState
from groupmate.social_runtime.persona.self_state import SelfStatePolicy, StateEvidence


def _evidence(kind, event_id, occurred_at=100, amount=1):
    return StateEvidence(kind, event_id, occurred_at, amount)


def test_single_reaction_and_member_silence_never_create_long_term_state_effect():
    policy = SelfStatePolicy()
    state = GlobalSelfState(persona_id="aemeath")

    reaction = policy.propose(state, (_evidence("reaction", "e1"),), now=100)
    no_reply = policy.propose(state, (_evidence("member_no_reply", "e2"),), now=100)

    assert reaction == ()
    assert no_reply == ()


def test_negative_state_requires_three_independent_events_and_respects_cooldown():
    policy = SelfStatePolicy()
    state = GlobalSelfState(persona_id="aemeath")
    evidence = tuple(
        _evidence("negative_interaction", f"e{index}", 100 + index)
        for index in range(3)
    )

    effects = policy.propose(state, evidence, now=103)
    cooling_down = policy.propose(
        replace(state, last_transition_at=100, version=1),
        evidence,
        now=110,
    )

    assert len(effects) == 1
    assert effects[0].kind == "irritation_delta"
    assert effects[0].amount == 3
    assert effects[0].evidence_event_ids == ("e0", "e1", "e2")
    assert cooling_down == ()


def test_state_effect_identity_is_deterministic_for_causal_dedupe():
    policy = SelfStatePolicy()
    state = GlobalSelfState(persona_id="aemeath")
    evidence = (_evidence("workload", "task-1", amount=20),)

    first = policy.propose(state, evidence, now=100)[0]
    retry = policy.propose(state, evidence, now=100)[0]

    assert retry == first
    assert first.kind == "cognitive_load_delta"
    assert first.amount == 20


def test_workload_is_clamped_and_time_decay_moves_state_toward_baseline():
    policy = SelfStatePolicy()
    loaded = GlobalSelfState(
        persona_id="aemeath",
        cognitive_load=70,
        last_transition_at=100,
        version=4,
    )

    clamped = policy.propose(
        replace(loaded, last_transition_at=0),
        (_evidence("workload", "task-heavy", amount=500),),
        now=200,
    )[0]
    decay = policy.decay(loaded, now=500)[0]

    assert clamped.amount == 50
    assert decay.kind == "cognitive_load_delta"
    assert decay.amount == -10
