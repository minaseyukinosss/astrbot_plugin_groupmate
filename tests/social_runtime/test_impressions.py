from __future__ import annotations

from groupmate.social_runtime.society.impressions import ImpressionRegistry


def test_tombstoned_impression_is_not_automatically_recreated():
    registry = ImpressionRegistry()
    impression = registry.propose(
        persona_id="aemeath",
        group_id="g1",
        subject_id="u1",
        statement="喜欢在深夜讨论代码",
        evidence_event_ids=("e1",),
        expires_at=1000,
    )
    registry.tombstone(impression.impression_id)

    recreated = registry.propose(
        persona_id="aemeath",
        group_id="g1",
        subject_id="u1",
        statement="  喜欢在深夜讨论代码  ",
        evidence_event_ids=("e2",),
        expires_at=1000,
    )

    assert recreated is None
