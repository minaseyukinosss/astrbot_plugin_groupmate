"""Sliding-window participation limits and generation/cost budgets."""

from __future__ import annotations

from collections import deque
from typing import Deque, Optional, Sequence, Tuple


class SlidingWindowRateLimiter:
    def __init__(
        self,
        hourly_limit: int = 6,
        cooldown_seconds: int = 600,
        window_seconds: int = 3600,
    ) -> None:
        if hourly_limit < 1:
            raise ValueError("hourly_limit must be positive")
        self.hourly_limit = hourly_limit
        self.cooldown_seconds = max(0, cooldown_seconds)
        self.window_seconds = max(1, window_seconds)
        self._sent_at: Deque[int] = deque()

    def allow(self, now: int) -> bool:
        self._prune(now)
        if len(self._sent_at) >= self.hourly_limit:
            return False
        if self._sent_at and now - self._sent_at[-1] < self.cooldown_seconds:
            return False
        return True

    def record(self, timestamp: int) -> None:
        self._prune(timestamp)
        self._sent_at.append(timestamp)

    def replace(self, timestamps: Sequence[int], now: Optional[int] = None) -> None:
        self._sent_at.clear()
        for stamp in timestamps:
            self._sent_at.append(int(stamp))
        if now is not None:
            self._prune(int(now))

    def load(self, timestamps: Sequence[int], now: Optional[int] = None) -> None:
        self.replace(timestamps, now=now)

    def snapshot(self, now: int) -> Tuple[int, ...]:
        self._prune(now)
        return tuple(self._sent_at)

    def _prune(self, now: int) -> None:
        cutoff = now - self.window_seconds
        while self._sent_at and self._sent_at[0] <= cutoff:
            self._sent_at.popleft()


class BudgetTracker:
    """三类额度：generation / send / cost（send 委托 SlidingWindowRateLimiter）。"""

    def __init__(
        self,
        send_limiter: SlidingWindowRateLimiter,
        *,
        generation_hourly_limit: int = 30,
        cost_hourly_limit: int = 12,
        window_seconds: int = 3600,
    ) -> None:
        self.send = send_limiter
        self.generation_hourly_limit = max(1, int(generation_hourly_limit))
        self.cost_hourly_limit = max(1, int(cost_hourly_limit))
        self.window_seconds = max(1, int(window_seconds))
        self._generation_at: Deque[int] = deque()
        self._cost_at: Deque[int] = deque()

    def allow_send(self, now: int) -> bool:
        return self.send.allow(now)

    def record_send(self, now: int) -> None:
        self.send.record(now)

    def allow_generation(self, now: int) -> bool:
        self._prune(self._generation_at, now)
        return len(self._generation_at) < self.generation_hourly_limit

    def record_generation(self, now: int) -> None:
        self._prune(self._generation_at, now)
        self._generation_at.append(int(now))

    def allow_cost(self, now: int) -> bool:
        self._prune(self._cost_at, now)
        return len(self._cost_at) < self.cost_hourly_limit

    def record_cost(self, now: int) -> None:
        self._prune(self._cost_at, now)
        self._cost_at.append(int(now))

    def generation_count(self, now: int) -> int:
        self._prune(self._generation_at, now)
        return len(self._generation_at)

    def cost_count(self, now: int) -> int:
        self._prune(self._cost_at, now)
        return len(self._cost_at)

    def _prune(self, bucket: Deque[int], now: int) -> None:
        cutoff = int(now) - self.window_seconds
        while bucket and bucket[0] <= cutoff:
            bucket.popleft()
