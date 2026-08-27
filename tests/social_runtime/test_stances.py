import pytest

from groupmate.social_runtime.stances import (
    Attitude,
    Boundary,
    Concession,
    Effort,
    Initiative,
    PermissionSnapshot,
    StanceDecision,
    Willingness,
)


def test_stance_decision_freezes_permission_and_deduplicates_reasons():
    decision = StanceDecision.create(
        attitude="FOCUSED",
        willingness="WILLING",
        boundary="NONE",
        concession="NONE",
        effort="NORMAL",
        initiative="ALLOW",
        reason_event_ids=("m1", "m1", "m2"),
        permission=PermissionSnapshot(allowed=True, reason_code="social_reply"),
    )

    assert decision.attitude is Attitude.FOCUSED
    assert decision.willingness is Willingness.WILLING
    assert decision.boundary is Boundary.NONE
    assert decision.concession is Concession.NONE
    assert decision.effort is Effort.NORMAL
    assert decision.initiative is Initiative.ALLOW
    assert decision.reason_event_ids == ("m1", "m2")


def test_permission_snapshot_requires_a_stable_reason_code():
    with pytest.raises(ValueError, match="reason_code"):
        PermissionSnapshot(allowed=False, reason_code="")


def test_stance_rejects_non_permission_input():
    with pytest.raises(ValueError, match="permission"):
        StanceDecision.create(
            attitude="NEUTRAL",
            willingness="UNWILLING",
            boundary="FINAL",
            concession="NONE",
            effort="MINIMAL",
            initiative="AVOID",
            reason_event_ids=("m1",),
            permission={"allowed": False, "reason_code": "blocked"},
        )
