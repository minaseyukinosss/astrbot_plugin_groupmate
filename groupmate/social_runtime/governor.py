"""Deterministic hard-gated social intention governor."""

from __future__ import annotations

from dataclasses import dataclass

from .intentions import CandidateIntention


@dataclass(frozen=True)
class RejectedIntention:
    intention_id: str
    reason_codes: tuple[str, ...]


@dataclass(frozen=True)
class GovernorContext:
    now: int
    scene_version: int
    allowed_target_ids: tuple[str, ...]
    allowed_topic_ids: tuple[str, ...]
    privacy_allowed: bool
    boundary_active: bool
    paused: bool
    platform_available: bool
    capability_allowed: bool
    force_observe: bool
    rate_limited_until: int | None
    minimum_utility: float


@dataclass(frozen=True)
class GovernorResult:
    outcome: str
    selected_intention_ids: tuple[str, ...]
    rejected: tuple[RejectedIntention, ...]
    reason_codes: tuple[str, ...]
    reconsider_at: int | None
    constraints: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.outcome not in {"ACT", "DEFER", "OBSERVE", "SILENCE"}:
            raise ValueError("unknown governor outcome")
        if self.outcome == "ACT" and not self.selected_intention_ids:
            raise ValueError("ACT requires selected intention IDs")
        if self.outcome != "ACT" and self.selected_intention_ids:
            raise ValueError("non-ACT outcome cannot select intentions")
        if self.outcome == "DEFER" and self.reconsider_at is None:
            raise ValueError("DEFER requires reconsider_at")
        if self.outcome != "DEFER" and self.reconsider_at is not None:
            raise ValueError("only DEFER may carry reconsider_at")


class SocialGovernor:
    def decide(
        self,
        candidates: tuple[CandidateIntention, ...],
        context: GovernorContext,
    ) -> GovernorResult:
        constraints = self._active_constraints(context)
        rejected: list[RejectedIntention] = []
        eligible: list[CandidateIntention] = []
        reasons: list[str] = []

        if context.force_observe:
            rejected = [
                RejectedIntention(item.intention_id, ("forced_observe",))
                for item in candidates
                if item.kind != "OBSERVE"
            ]
            return GovernorResult(
                "OBSERVE", (), tuple(rejected), ("forced_observe",), None, constraints
            )

        for candidate in candidates:
            hard_reasons = self._hard_reasons(candidate, context)
            if hard_reasons:
                rejected.append(
                    RejectedIntention(candidate.intention_id, hard_reasons)
                )
                self._extend_unique(reasons, hard_reasons)
            else:
                eligible.append(candidate)

        if not eligible:
            return GovernorResult(
                "SILENCE",
                (),
                tuple(rejected),
                tuple(reasons or ("no_eligible_intention",)),
                None,
                constraints,
            )

        if all(item.kind == "OBSERVE" for item in eligible):
            return GovernorResult(
                "OBSERVE", (), tuple(rejected), ("observe_intention",), None, constraints
            )

        if context.rate_limited_until is not None and context.rate_limited_until > context.now:
            return GovernorResult(
                "DEFER",
                (),
                tuple(rejected),
                ("rate_limited",),
                context.rate_limited_until,
                constraints,
            )

        ranked = sorted(
            (item for item in eligible if item.kind != "OBSERVE"),
            key=lambda item: (-self.utility(item), item.intention_id),
        )
        top = ranked[0]
        if self.utility(top) < context.minimum_utility:
            below = tuple(
                RejectedIntention(item.intention_id, ("utility_below_threshold",))
                for item in ranked
            )
            return GovernorResult(
                "SILENCE",
                (),
                tuple(rejected) + below,
                ("utility_below_threshold",),
                None,
                constraints,
            )

        selected = [top]
        for candidate in ranked[1:]:
            if self._compatible(top, candidate):
                selected.append(candidate)
                continue
            reason = (
                "different_target"
                if candidate.target_id != top.target_id
                else "different_topic"
                if candidate.topic_id != top.topic_id
                else "lower_utility"
            )
            rejected.append(RejectedIntention(candidate.intention_id, (reason,)))
        return GovernorResult(
            "ACT",
            tuple(item.intention_id for item in selected),
            tuple(rejected),
            ("selected_by_social_utility",),
            None,
            constraints,
        )

    @staticmethod
    def utility(candidate: CandidateIntention) -> float:
        positive = (
            candidate.obligation
            + candidate.relevance
            + candidate.relational_value
            + candidate.continuity_value
            + candidate.novelty
            + candidate.urgency
            + candidate.persona_fit
            + candidate.state_fit
            + candidate.information_gain
        )
        costs = (
            candidate.disruption_cost
            + candidate.uncertainty_cost
            + candidate.repetition_cost
            + candidate.resource_cost
            + candidate.risk
        )
        return positive - costs

    @staticmethod
    def _hard_reasons(
        candidate: CandidateIntention, context: GovernorContext
    ) -> tuple[str, ...]:
        reasons = []
        if context.paused:
            reasons.append("runtime_paused")
        if not context.platform_available:
            reasons.append("platform_unavailable")
        if not context.privacy_allowed and candidate.kind != "BOUNDARY":
            reasons.append("privacy_blocked")
        if context.boundary_active and candidate.kind not in {"BOUNDARY", "OBSERVE"}:
            reasons.append("boundary_active")
        if candidate.expires_at <= context.now:
            reasons.append("candidate_expired")
        if candidate.target_id and candidate.target_id not in context.allowed_target_ids:
            reasons.append("wrong_target")
        if candidate.topic_id and candidate.topic_id not in context.allowed_topic_ids:
            reasons.append("wrong_topic")
        if candidate.kind in {"CAPABILITY", "ACCEPT_TASK"} and not context.capability_allowed:
            reasons.append("capability_not_allowed")
        return tuple(reasons)

    @staticmethod
    def _compatible(first: CandidateIntention, second: CandidateIntention) -> bool:
        if first.target_id != second.target_id or first.topic_id != second.topic_id:
            return False
        kinds = {first.kind, second.kind}
        return kinds == {"CARE", "HELP"} or "LIGHT_MEDIA" in kinds

    @staticmethod
    def _active_constraints(context: GovernorContext) -> tuple[str, ...]:
        active = ["hard_gate_v1"]
        if context.paused:
            active.append("runtime_paused")
        if context.boundary_active:
            active.append("boundary_active")
        if not context.privacy_allowed:
            active.append("privacy_blocked")
        return tuple(active)

    @staticmethod
    def _extend_unique(target: list[str], values: tuple[str, ...]) -> None:
        for value in values:
            if value not in target:
                target.append(value)


__all__ = (
    "GovernorContext",
    "GovernorResult",
    "RejectedIntention",
    "SocialGovernor",
)
