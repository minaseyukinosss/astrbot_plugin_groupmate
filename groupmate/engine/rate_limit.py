"""Sliding-window participation limits for spontaneous messages."""

from __future__ import annotations

from collections import deque
from typing import Deque, Tuple


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

    def snapshot(self, now: int) -> Tuple[int, ...]:
        self._prune(now)
        return tuple(self._sent_at)

    def _prune(self, now: int) -> None:
        cutoff = now - self.window_seconds
        while self._sent_at and self._sent_at[0] <= cutoff:
            self._sent_at.popleft()

