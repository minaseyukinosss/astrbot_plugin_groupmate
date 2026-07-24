from groupmate.engine.rate_limit import SlidingWindowRateLimiter


def test_spontaneous_budget_blocks_seventh_message():
    limiter = SlidingWindowRateLimiter(hourly_limit=6, cooldown_seconds=0)
    for timestamp in range(6):
        limiter.record(timestamp)

    assert limiter.allow(10) is False


def test_old_entries_expire_and_cooldown_is_enforced():
    limiter = SlidingWindowRateLimiter(hourly_limit=6, cooldown_seconds=30)
    limiter.record(0)

    assert limiter.allow(10) is False
    assert limiter.allow(31) is True
    assert limiter.allow(3601) is True

