"""Poke throttle cooldown / rate / bystander targeting."""

from groupmate.engine.poke_throttle import PokeThrottle
from groupmate.policies import InteractionPolicy


def test_direct_cooldown_blocks_until_window_passes():
    throttle = PokeThrottle(rng=lambda: 0.0)
    policy = InteractionPolicy(
        poke_react_probability=1.0,
        poke_cooldown_seconds=8,
        poke_session_per_minute=0,
    )

    first = throttle.evaluate_direct(
        persona_id="aemeath",
        group_id="g1",
        sender_id="u1",
        now=100,
        policy=policy,
    )
    throttle.mark_direct_reacted(
        persona_id="aemeath",
        group_id="g1",
        sender_id="u1",
        now=100,
    )
    blocked = throttle.evaluate_direct(
        persona_id="aemeath",
        group_id="g1",
        sender_id="u1",
        now=105,
        policy=policy,
    )
    allowed = throttle.evaluate_direct(
        persona_id="aemeath",
        group_id="g1",
        sender_id="u1",
        now=109,
        policy=policy,
    )

    assert first.allow is True
    assert blocked.allow is False
    assert blocked.reason_code == "poke_cooldown"
    assert allowed.allow is True


def test_session_rate_limit_blocks_after_cap():
    throttle = PokeThrottle(rng=lambda: 0.0)
    policy = InteractionPolicy(
        poke_react_probability=1.0,
        poke_cooldown_seconds=0,
        poke_session_per_minute=2,
    )

    for index, stamp in enumerate((100, 110)):
        decision = throttle.evaluate_direct(
            persona_id="aemeath",
            group_id="g1",
            sender_id="u{}".format(index),
            now=stamp,
            policy=policy,
        )
        assert decision.allow is True
        throttle.mark_direct_reacted(
            persona_id="aemeath",
            group_id="g1",
            sender_id="u{}".format(index),
            now=stamp,
        )

    blocked = throttle.evaluate_direct(
        persona_id="aemeath",
        group_id="g1",
        sender_id="u9",
        now=120,
        policy=policy,
    )
    assert blocked.allow is False
    assert blocked.reason_code == "poke_rate_limited"


def test_bystander_target_strategies():
    throttle = PokeThrottle(rng=lambda: 0.0)
    victim = throttle.pick_bystander_target(
        poker_id="poker",
        victim_id="victim",
        policy=InteractionPolicy(poke_bystander_target="victim"),
    )
    poker = throttle.pick_bystander_target(
        poker_id="poker",
        victim_id="victim",
        policy=InteractionPolicy(poke_bystander_target="poker"),
    )

    assert victim == "victim"
    assert poker == "poker"
