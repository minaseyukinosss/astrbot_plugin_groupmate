"""Pure three-track game-release transitions and verified-slot binding."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Callable, Iterable, Protocol

from .contracts import TopicUnderstandingFrame, VersionReference, VersionSlot


_TRACK_STATES = {
    "release": ("future", "current", "past"),
    "official": ("none", "teaser", "preview", "notice", "released"),
    "rumor": ("none_observed", "weak", "corroborated", "conflicted", "stale"),
}
_NEXT_STATES = {
    "release": {
        "future": {"current"},
        "current": {"past"},
        "past": set(),
    },
    "official": {
        "none": {"teaser"},
        "teaser": {"preview"},
        "preview": {"notice"},
        "notice": {"released"},
        "released": set(),
    },
    "rumor": {
        "none_observed": {"weak"},
        "weak": {"corroborated"},
        "corroborated": {"conflicted"},
        "conflicted": {"stale"},
        "stale": set(),
    },
}


def _text(value: object, name: str, maximum: int) -> str:
    normalized = " ".join(str(value or "").split())
    if not normalized:
        raise ValueError(f"{name} must not be empty")
    if len(normalized) > maximum:
        raise ValueError(f"{name} exceeds {maximum} characters")
    return normalized


def _timestamp(value: object, name: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{name} must not be negative")
    try:
        normalized = int(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{name} must not be negative") from error
    if normalized < 0:
        raise ValueError(f"{name} must not be negative")
    return normalized


class ReleaseStateReader(Protocol):
    def load_release_state(
        self, game_id: str, region: str, platform: str
    ) -> tuple[VersionSlot, ...]:
        """Return stored slots for one fully-qualified release aggregate."""


@dataclass(frozen=True)
class ReleaseEvidence:
    evidence_id: str
    track: str
    target_state: str
    observed_at: int
    official_label: str | None

    @classmethod
    def create(cls, **values: object) -> "ReleaseEvidence":
        track = _text(values.get("track"), "track", 16)
        if track not in _TRACK_STATES:
            raise ValueError("track is unsupported")
        target_state = _text(
            values.get("target_state"), "target_state", 24
        )
        if target_state not in _TRACK_STATES[track]:
            raise ValueError("target_state is unsupported for track")
        official_label = values.get("official_label")
        return cls(
            evidence_id=_text(values.get("evidence_id"), "evidence_id", 128),
            track=track,
            target_state=target_state,
            observed_at=_timestamp(values.get("observed_at"), "observed_at"),
            official_label=(
                None
                if official_label is None
                else _text(official_label, "official_label", 80)
            ),
        )


@dataclass(frozen=True)
class ReleaseTransition:
    old_state: VersionSlot
    state: VersionSlot
    accepted: bool
    reason_code: str
    old_revision: int
    new_revision: int
    evidence_ids: tuple[str, ...]
    revalidation_required: bool

    @property
    def new_state(self) -> VersionSlot:
        return self.state


@dataclass(frozen=True)
class ResolvedVersionReference:
    game_id: str | None
    relative_kind: str | None
    disclosure_kind: str | None
    region: str | None
    platform: str | None
    version_slot_id: str | None
    official_label: str | None
    ambiguity_code: str | None
    revalidation_required: bool

    @property
    def is_resolved(self) -> bool:
        return self.version_slot_id is not None and self.ambiguity_code is None


class GameReleaseStateService:
    """Resolve verified slots and evolve only the evidence-named track."""

    def __init__(
        self,
        state_source: ReleaseStateReader
        | Callable[[str, str, str], Iterable[VersionSlot]]
        | Iterable[VersionSlot]
        | None = None,
    ) -> None:
        self._reader: Callable[[str, str, str], Iterable[VersionSlot]] | None = None
        self._slots: tuple[VersionSlot, ...] = ()
        if state_source is None:
            return
        if hasattr(state_source, "load_release_state"):
            self._reader = state_source.load_release_state  # type: ignore[union-attr]
        elif callable(state_source):
            self._reader = state_source
        else:
            slots = tuple(state_source)
            if not all(isinstance(item, VersionSlot) for item in slots):
                raise TypeError("state_source must contain VersionSlot")
            self._slots = slots

    def apply_evidence(
        self, state: VersionSlot, evidence: ReleaseEvidence
    ) -> ReleaseTransition:
        if not isinstance(state, VersionSlot):
            raise TypeError("state must be VersionSlot")
        if not isinstance(evidence, ReleaseEvidence):
            raise TypeError("evidence must be ReleaseEvidence")
        current = getattr(state, f"{evidence.track}_state")
        last_checked_at = self._last_checked_at(state, evidence.track)
        if (
            last_checked_at is not None
            and evidence.observed_at < last_checked_at
        ):
            return self._transition(
                state,
                state,
                False,
                f"stale_{evidence.track}_evidence",
                evidence,
                False,
            )
        if (
            evidence.track == "release"
            and evidence.target_state == "current"
            and state.release_at is not None
            and evidence.observed_at < state.release_at
        ):
            return self._transition(
                state,
                state,
                False,
                "release_before_verified_release_at",
                evidence,
                False,
            )
        if (
            evidence.track == "official"
            and evidence.target_state == "released"
            and state.release_at is not None
            and evidence.observed_at < state.release_at
        ):
            return self._transition(
                state,
                state,
                False,
                "official_release_before_verified_release_at",
                evidence,
                False,
            )
        revalidation_required = bool(
            state.release_at is not None
            and evidence.observed_at >= state.release_at
            and state.official_state != "released"
        )
        if evidence.target_state == current:
            return self._transition(
                state,
                state,
                True,
                "revalidation_required"
                if revalidation_required
                else "evidence_state_unchanged",
                evidence,
                revalidation_required,
            )
        if evidence.target_state not in _NEXT_STATES[evidence.track][current]:
            return self._transition(
                state,
                state,
                False,
                f"invalid_{evidence.track}_transition",
                evidence,
                revalidation_required,
            )
        if (
            evidence.track == "official"
            and evidence.target_state == "released"
            and state.release_state not in {"current", "past"}
        ):
            return self._transition(
                state,
                state,
                False,
                "official_release_requires_verified_release_track",
                evidence,
                revalidation_required,
            )
        values = asdict(state)
        values[f"{evidence.track}_state"] = evidence.target_state
        values["revision"] = state.revision + 1
        values["fresh_until"] = max(state.fresh_until, evidence.observed_at + 1)
        if evidence.track == "official":
            values["official_checked_at"] = evidence.observed_at
            values["announced_at"] = state.announced_at or evidence.observed_at
            values["official_label"] = (
                evidence.official_label or state.official_label
            )
        elif evidence.track == "rumor":
            values["rumor_checked_at"] = evidence.observed_at
        else:
            values["release_checked_at"] = evidence.observed_at
        new_state = VersionSlot.create(**values)
        return self._transition(
            state,
            new_state,
            True,
            "track_advanced",
            evidence,
            revalidation_required,
        )

    def resolve_reference(
        self,
        frame: TopicUnderstandingFrame,
        message_time: int,
        region: str | None,
        platform: str | None,
    ) -> ResolvedVersionReference:
        message_at = _timestamp(message_time, "message_time")
        reference = frame.version_reference
        if reference is None:
            return ResolvedVersionReference(
                None, None, None, region, platform, None, None,
                "no_version_reference", False,
            )
        if not region or not platform:
            return self._unresolved(
                reference, region, platform, "ambiguous_region_or_platform"
            )
        slots, reader_code = self._load_slots(
            reference.game_id, region, platform
        )
        if reader_code is not None:
            return self._unresolved(reference, region, platform, reader_code)
        desired_state = {
            "current": "current",
            "new": "current",
            "next": "future",
            "recent_update": "current",
            "previous": "past",
        }.get(reference.relative_kind)
        if desired_state is None:
            return self._unresolved(
                reference, region, platform, "ambiguous_relative_reference"
            )
        matching_slots = tuple(
            slot for slot in slots if slot.release_state == desired_state
        )
        if reference.relative_kind == "recent_update":
            matching_slots = tuple(
                slot
                for slot in matching_slots
                if slot.official_state == "released"
            )
        candidates = []
        safety_codes = []
        for slot in matching_slots:
            safety_code = self._slot_safety_code(slot, message_at)
            if safety_code is None:
                candidates.append(slot)
            else:
                safety_codes.append(safety_code)
        if len(candidates) != 1:
            if not candidates and safety_codes:
                return self._unresolved(
                    reference, region, platform, safety_codes[0]
                )
            return self._unresolved(
                reference,
                region,
                platform,
                "ambiguous_next_slot"
                if reference.relative_kind == "next"
                else "ambiguous_version_slot",
            )
        slot = candidates[0]
        boundary = bool(
            slot.release_at is not None
            and message_at >= slot.release_at
            and slot.official_state != "released"
        )
        if boundary:
            return self._unresolved(
                reference, region, platform, "revalidation_required", True
            )
        return ResolvedVersionReference(
            reference.game_id,
            reference.relative_kind,
            reference.disclosure_kind,
            region,
            platform,
            slot.version_slot_id,
            slot.official_label,
            None,
            False,
        )

    def _load_slots(
        self, game_id: str, region: str, platform: str
    ) -> tuple[tuple[VersionSlot, ...], str | None]:
        if self._reader is not None:
            try:
                values = tuple(self._reader(game_id, region, platform))
            except TypeError:
                return (), "invalid_reader_result"
            if not all(isinstance(slot, VersionSlot) for slot in values):
                return (), "invalid_reader_result"
        else:
            values = self._slots
        return (
            tuple(
                slot
                for slot in values
                if (
                    slot.game_entity_id,
                    slot.region,
                    slot.platform,
                ) == (game_id, region, platform)
            ),
            None,
        )

    @staticmethod
    def _slot_safety_code(
        slot: VersionSlot, message_at: int
    ) -> str | None:
        if slot.status != "active" or slot.official_state == "none":
            return "unverified_version_slot"
        if slot.official_checked_at is None:
            return "unverified_version_slot"
        if slot.official_checked_at > message_at:
            return "future_official_check"
        if slot.effective_until is not None and slot.effective_until <= message_at:
            return "expired_version_slot"
        if slot.fresh_until <= message_at:
            return "stale_version_slot"
        return None

    @staticmethod
    def _last_checked_at(state: VersionSlot, track: str) -> int | None:
        if track == "official":
            return state.official_checked_at
        if track == "rumor":
            return state.rumor_checked_at
        return state.release_checked_at

    @staticmethod
    def _transition(
        old_state: VersionSlot,
        state: VersionSlot,
        accepted: bool,
        reason_code: str,
        evidence: ReleaseEvidence,
        revalidation_required: bool,
    ) -> ReleaseTransition:
        return ReleaseTransition(
            old_state=old_state,
            state=state,
            accepted=accepted,
            reason_code=reason_code,
            old_revision=old_state.revision,
            new_revision=state.revision,
            evidence_ids=(evidence.evidence_id,),
            revalidation_required=revalidation_required,
        )

    @staticmethod
    def _unresolved(
        reference: VersionReference,
        region: str | None,
        platform: str | None,
        ambiguity_code: str,
        revalidation_required: bool = False,
    ) -> ResolvedVersionReference:
        return ResolvedVersionReference(
            reference.game_id,
            reference.relative_kind,
            reference.disclosure_kind,
            region,
            platform,
            None,
            None,
            ambiguity_code,
            revalidation_required,
        )


__all__ = (
    "GameReleaseStateService",
    "ReleaseEvidence",
    "ReleaseStateReader",
    "ReleaseTransition",
    "ResolvedVersionReference",
)
