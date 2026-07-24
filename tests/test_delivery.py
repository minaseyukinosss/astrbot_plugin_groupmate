from groupmate.engine.delivery import compute_delay_seconds
from groupmate.models import Urgency


def test_compute_delay_direct_wake_stays_short():
    delay = compute_delay_seconds(Urgency.HIGH, "x" * 60, direct_wake=True)
    assert delay <= 0.35


def test_compute_delay_spontaneous_stays_under_one_second():
    delay = compute_delay_seconds(Urgency.NORMAL, "x" * 60)
    assert delay <= 0.9


def test_compute_delay_disabled_is_zero():
    assert compute_delay_seconds(Urgency.NORMAL, "hello", enabled=False) == 0.0
