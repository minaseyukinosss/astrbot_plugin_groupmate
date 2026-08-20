"""Shared priority admission primitive for bounded cognitive work."""

from __future__ import annotations

from dataclasses import dataclass, field
import heapq
from typing import Generic, TypeVar


T = TypeVar("T")


_LANE_PRIORITY = {
    "FAST": 0,
    "TEMPORAL": 1,
    "AMBIENT": 2,
}


@dataclass(order=True, frozen=True)
class WorkerAdmission(Generic[T]):
    priority: int
    sequence: int
    lane: str = field(compare=False)
    payload: T = field(compare=False)


class WorkerAdmissionQueue(Generic[T]):
    """FIFO within a lane; hard direct work always precedes queued Ambient work."""

    def __init__(self) -> None:
        self._items: list[WorkerAdmission[T]] = []
        self._sequence = 0

    def enqueue(self, lane: str, payload: T) -> WorkerAdmission[T]:
        normalized = str(lane).upper()
        if normalized not in _LANE_PRIORITY:
            raise ValueError("worker lane must be FAST, TEMPORAL, or AMBIENT")
        admission = WorkerAdmission(
            _LANE_PRIORITY[normalized], self._sequence, normalized, payload
        )
        self._sequence += 1
        heapq.heappush(self._items, admission)
        return admission

    def peek(self) -> WorkerAdmission[T] | None:
        return self._items[0] if self._items else None

    def dequeue(self) -> WorkerAdmission[T]:
        if not self._items:
            raise IndexError("worker admission queue is empty")
        return heapq.heappop(self._items)

    def discard(self, admission: WorkerAdmission[T]) -> None:
        try:
            self._items.remove(admission)
        except ValueError:
            return
        heapq.heapify(self._items)

    def has_lane(self, lane: str) -> bool:
        normalized = str(lane).upper()
        return any(item.lane == normalized for item in self._items)

    def __len__(self) -> int:
        return len(self._items)


__all__ = ("WorkerAdmission", "WorkerAdmissionQueue")
