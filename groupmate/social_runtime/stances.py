"""Immutable persona stance contracts, kept separate from authorization."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Iterable


MAX_STANCE_REASONS = 32


class Attitude(str, Enum):
    WARM = "WARM"
    AMUSED = "AMUSED"
    NEUTRAL = "NEUTRAL"
    FOCUSED = "FOCUSED"
    GUARDED = "GUARDED"
    IRRITATED = "IRRITATED"


class Willingness(str, Enum):
    EAGER = "EAGER"
    WILLING = "WILLING"
    LIMITED = "LIMITED"
    UNWILLING = "UNWILLING"
    REQUIRED_MINIMUM = "REQUIRED_MINIMUM"


class Boundary(str, Enum):
    NONE = "NONE"
    SOFT = "SOFT"
    FIRM = "FIRM"
    FINAL = "FINAL"


class Concession(str, Enum):
    NONE = "NONE"
    PARTIAL = "PARTIAL"
    FULL = "FULL"


class Effort(str, Enum):
    MINIMAL = "MINIMAL"
    NORMAL = "NORMAL"
    EXTRA = "EXTRA"


class Initiative(str, Enum):
    AVOID = "AVOID"
    ALLOW = "ALLOW"
    PREFER = "PREFER"


def _reason_ids(values: Iterable[object]) -> tuple[str, ...]:
    result = tuple(
        dict.fromkeys(str(value).strip() for value in values if str(value).strip())
    )
    if len(result) > MAX_STANCE_REASONS:
        raise ValueError(f"reason_event_ids exceeds {MAX_STANCE_REASONS} items")
    return result


@dataclass(frozen=True)
class PermissionSnapshot:
    """Read-only authorization result; relationship policy may not rewrite it."""

    allowed: bool
    reason_code: str

    def __post_init__(self) -> None:
        if not isinstance(self.allowed, bool):
            raise ValueError("allowed must be a boolean")
        reason_code = str(self.reason_code or "").strip()
        if not reason_code:
            raise ValueError("reason_code must not be empty")
        object.__setattr__(self, "reason_code", reason_code)


@dataclass(frozen=True)
class StanceDecision:
    attitude: Attitude
    willingness: Willingness
    boundary: Boundary
    concession: Concession
    effort: Effort
    initiative: Initiative
    reason_event_ids: tuple[str, ...]
    permission: PermissionSnapshot

    def __post_init__(self) -> None:
        object.__setattr__(self, "attitude", Attitude(self.attitude))
        object.__setattr__(self, "willingness", Willingness(self.willingness))
        object.__setattr__(self, "boundary", Boundary(self.boundary))
        object.__setattr__(self, "concession", Concession(self.concession))
        object.__setattr__(self, "effort", Effort(self.effort))
        object.__setattr__(self, "initiative", Initiative(self.initiative))
        object.__setattr__(self, "reason_event_ids", _reason_ids(self.reason_event_ids))
        if not isinstance(self.permission, PermissionSnapshot):
            raise ValueError("permission must be a PermissionSnapshot")

    @classmethod
    def create(cls, **values: object) -> "StanceDecision":
        return cls(**values)


__all__ = (
    "Attitude",
    "Boundary",
    "Concession",
    "Effort",
    "Initiative",
    "PermissionSnapshot",
    "StanceDecision",
    "Willingness",
)
