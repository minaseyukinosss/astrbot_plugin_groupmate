"""Deterministic dataset replay driven by message timestamps, never wall-clock sleeps."""

from __future__ import annotations


class VirtualClock:
    def __init__(self, initial: int = 0) -> None:
        self._now = int(initial)

    def now(self) -> int:
        return self._now

    def advance_to(self, timestamp: int) -> int:
        self._now = max(self._now, int(timestamp))
        return self._now


class OfflineReplayRunner:
    def __init__(self, evaluator, clock=None) -> None:
        self.evaluator = evaluator
        self.clock = clock or VirtualClock()

    async def run(self, cases):
        predictions = []
        for case in cases:
            for message in case.messages:
                self.clock.advance_to(message.timestamp)
            predictions.append(await self.evaluator.evaluate(case))
        return tuple(predictions)
