"""Immutable persona stance contracts, kept separate from authorization."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Iterable, Mapping

from .social_scenes import ChorusTarget, ChorusTone, SocialScene
from .society.relationships import RelationshipProjection


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
        normalized = dict(values)
        permission = normalized.get("permission")
        if isinstance(permission, Mapping):
            normalized["permission"] = PermissionSnapshot(**dict(permission))
        return cls(**normalized)


class StancePolicy:
    """Derive 爱弥斯's willingness without granting any system permission."""

    BOUNDARY_PRESSURE_FIRM = 40
    PLAY_ACCEPTANCE_FAMILIAR = 40
    WARMTH_LIMITED_ACCEPT = 35

    def decide(
        self,
        scene: SocialScene,
        *,
        actor_relationship: RelationshipProjection | None,
        subject_relationship: RelationshipProjection | None,
        culture_patterns: Iterable[str],
        permission: PermissionSnapshot,
        mode_modifiers: Iterable[str],
        memory_event_ids: Iterable[str],
    ) -> StanceDecision:
        if not isinstance(permission, PermissionSnapshot):
            raise ValueError("permission must be a PermissionSnapshot")
        reasons = self._reasons(
            scene,
            actor_relationship,
            subject_relationship,
            memory_event_ids,
        )
        if not permission.allowed:
            return self._decision(
                Attitude.GUARDED,
                Willingness.UNWILLING,
                Boundary.FINAL,
                Concession.NONE,
                Effort.MINIMAL,
                Initiative.AVOID,
                reasons,
                permission,
            )
        if scene.scene_kind in {"safety_signal", "danger_signal", "self_harm_signal"}:
            return self._decision(
                Attitude.FOCUSED,
                Willingness.REQUIRED_MINIMUM,
                Boundary.NONE,
                Concession.NONE,
                Effort.MINIMAL,
                Initiative.ALLOW,
                reasons,
                permission,
            )
        if scene.chorus_target is ChorusTarget.MEMBER:
            return self._member_chorus(
                scene,
                subject_relationship,
                set(str(value) for value in culture_patterns),
                set(str(value) for value in mode_modifiers),
                reasons,
                permission,
            )
        if scene.chorus_target is ChorusTarget.SELF:
            attitude = (
                Attitude.AMUSED
                if scene.chorus_tone is ChorusTone.SAFE_BANTER
                else Attitude.GUARDED
            )
            willingness = (
                Willingness.WILLING
                if scene.chorus_tone
                in {ChorusTone.SAFE_BANTER, ChorusTone.SENSITIVE, ChorusTone.ATTACK}
                else Willingness.UNWILLING
            )
            return self._decision(
                attitude,
                willingness,
                Boundary.SOFT if attitude is Attitude.GUARDED else Boundary.NONE,
                Concession.NONE,
                Effort.MINIMAL,
                Initiative.ALLOW if willingness is Willingness.WILLING else Initiative.AVOID,
                reasons,
                permission,
            )
        if scene.chorus_target is ChorusTarget.OTHER:
            # Untargeted exact repeats are joinable when safe; length of the
            # chain is evidence of chorus, not spam against the bot.
            if scene.chorus_tone is ChorusTone.SAFE_BANTER:
                return self._decision(
                    Attitude.AMUSED,
                    Willingness.WILLING,
                    Boundary.NONE,
                    Concession.NONE,
                    Effort.MINIMAL,
                    Initiative.ALLOW,
                    reasons,
                    permission,
                )
            return self._decision(
                Attitude.GUARDED,
                Willingness.UNWILLING,
                Boundary.SOFT,
                Concession.NONE,
                Effort.MINIMAL,
                Initiative.AVOID,
                reasons,
                permission,
            )
        if scene.chorus_target is ChorusTarget.UNKNOWN:
            return self._decision(
                Attitude.GUARDED,
                Willingness.UNWILLING,
                Boundary.SOFT,
                Concession.NONE,
                Effort.MINIMAL,
                Initiative.AVOID,
                reasons,
                permission,
            )
        if (
            actor_relationship is not None
            and actor_relationship.boundary_pressure >= self.BOUNDARY_PRESSURE_FIRM
        ) or scene.repetition_count >= 3:
            return self._decision(
                Attitude.IRRITATED,
                Willingness.UNWILLING,
                Boundary.FIRM,
                Concession.NONE,
                Effort.MINIMAL,
                Initiative.AVOID,
                reasons,
                permission,
            )
        if scene.scene_kind in {"intimacy_request", "playful_negotiation"}:
            return self._play_stance(scene, actor_relationship, reasons, permission)
        if scene.scene_kind in {
            "technical_help",
            "fact_question",
            "technical_constraint",
        }:
            effort = (
                Effort.EXTRA
                if actor_relationship is not None and actor_relationship.trust >= 60
                else Effort.NORMAL
            )
            return self._decision(
                Attitude.FOCUSED,
                Willingness.WILLING,
                Boundary.NONE,
                Concession.NONE,
                effort,
                Initiative.ALLOW,
                reasons,
                permission,
            )
        return self._neutral(
            actor_relationship,
            set(str(value) for value in mode_modifiers),
            reasons,
            permission,
        )

    def _member_chorus(
        self,
        scene: SocialScene,
        subject: RelationshipProjection | None,
        culture: set[str],
        modifiers: set[str],
        reasons: tuple[str, ...],
        permission: PermissionSnapshot,
    ) -> StanceDecision:
        compatible = bool(
            scene.chorus_tone is ChorusTone.SAFE_BANTER
            and subject is not None
            and subject.play_acceptance >= self.PLAY_ACCEPTANCE_FAMILIAR
            and subject.boundary_pressure < self.BOUNDARY_PRESSURE_FIRM
            and "light_member_banter" in culture
            and "avoid_play" not in modifiers
        )
        if not compatible:
            return self._decision(
                Attitude.GUARDED,
                Willingness.UNWILLING,
                Boundary.SOFT,
                Concession.NONE,
                Effort.MINIMAL,
                Initiative.AVOID,
                reasons,
                permission,
            )
        return self._decision(
            Attitude.AMUSED,
            Willingness.WILLING,
            Boundary.NONE,
            Concession.NONE,
            Effort.MINIMAL,
            Initiative.ALLOW,
            reasons,
            permission,
        )

    def _play_stance(
        self,
        scene: SocialScene,
        relationship: RelationshipProjection | None,
        reasons: tuple[str, ...],
        permission: PermissionSnapshot,
    ) -> StanceDecision:
        del scene
        if relationship is not None and (
            relationship.warmth >= self.WARMTH_LIMITED_ACCEPT
            and relationship.play_acceptance >= self.PLAY_ACCEPTANCE_FAMILIAR
        ):
            return self._decision(
                Attitude.AMUSED,
                Willingness.LIMITED,
                Boundary.SOFT,
                Concession.PARTIAL,
                Effort.NORMAL,
                Initiative.ALLOW,
                reasons,
                permission,
            )
        return self._decision(
            Attitude.GUARDED,
            Willingness.UNWILLING,
            Boundary.SOFT,
            Concession.NONE,
            Effort.MINIMAL,
            Initiative.AVOID,
            reasons,
            permission,
        )

    def _neutral(
        self,
        relationship: RelationshipProjection | None,
        modifiers: set[str],
        reasons: tuple[str, ...],
        permission: PermissionSnapshot,
    ) -> StanceDecision:
        warm = relationship is not None and relationship.warmth >= 40
        tired = "low_energy" in modifiers or "tired" in modifiers
        return self._decision(
            Attitude.WARM if warm else Attitude.NEUTRAL,
            Willingness.WILLING,
            Boundary.NONE,
            Concession.NONE,
            Effort.MINIMAL if tired else Effort.NORMAL,
            Initiative.AVOID if tired else Initiative.ALLOW,
            reasons,
            permission,
        )

    @staticmethod
    def _reasons(
        scene: SocialScene,
        actor: RelationshipProjection | None,
        subject: RelationshipProjection | None,
        memory_event_ids: Iterable[str],
    ) -> tuple[str, ...]:
        return _reason_ids(
            (
                *scene.continuity_event_ids,
                *(actor.evidence_event_ids if actor is not None else ()),
                *(subject.evidence_event_ids if subject is not None else ()),
                *tuple(memory_event_ids),
            )
        )

    @staticmethod
    def _decision(
        attitude: Attitude,
        willingness: Willingness,
        boundary: Boundary,
        concession: Concession,
        effort: Effort,
        initiative: Initiative,
        reasons: tuple[str, ...],
        permission: PermissionSnapshot,
    ) -> StanceDecision:
        return StanceDecision(
            attitude,
            willingness,
            boundary,
            concession,
            effort,
            initiative,
            reasons,
            permission,
        )


__all__ = (
    "Attitude",
    "Boundary",
    "Concession",
    "Effort",
    "Initiative",
    "PermissionSnapshot",
    "StanceDecision",
    "StancePolicy",
    "Willingness",
)
