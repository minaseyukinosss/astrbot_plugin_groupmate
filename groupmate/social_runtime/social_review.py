"""Deterministic review of a fully realized social reply."""

from __future__ import annotations

import re
from dataclasses import dataclass

from .social_moves import Ending, RealizationMode, SocialMove
from .social_scenes import ChorusTone


_SERVICE_TAIL = re.compile(
    r"(?:如果你愿意|如果需要|需要的话).{0,12}(?:我可以|可以再|继续帮|继续帮助)"
    r"|(?:还有|还需要).{0,8}(?:帮忙|帮助)吗[？?]?$"
)
_GENERIC_EMPATHY = re.compile(r"^(?:我理解你的|我能理解你|听起来你|我感受到你)")
_INTERNAL_RELATION = re.compile(r"(?:好感度|关系分|boundary_pressure|play_acceptance)")


@dataclass(frozen=True)
class RealizedReply:
    text: str
    covered_fact_ids: tuple[str, ...]
    used_memory_ids: tuple[str, ...]
    used_capability_ids: tuple[str, ...]
    source_event_ids: tuple[str, ...] = ()
    used_knowledge_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        text = str(self.text or "").strip()
        if not text:
            raise ValueError("realized reply text must not be empty")
        object.__setattr__(self, "text", text)
        for field in (
            "covered_fact_ids",
            "used_memory_ids",
            "used_capability_ids",
            "source_event_ids",
            "used_knowledge_ids",
        ):
            values = tuple(
                dict.fromkeys(
                    str(value).strip()
                    for value in getattr(self, field)
                    if str(value).strip()
                )
            )
            object.__setattr__(self, field, values)


@dataclass(frozen=True)
class SocialReview:
    accepted: bool
    violations: tuple[str, ...]


class SocialOutputReviewer:
    """Check evidence and surface behavior before the platform firewall."""

    def review(self, reply: RealizedReply, plan: object) -> SocialReview:
        move = plan.move
        scene = plan.scene
        violations: list[str] = []
        allowed_facts = {
            fact.fact_id for fact in (*move.must_say, *move.may_say)
        }
        required_facts = {fact.fact_id for fact in move.must_say}
        if not set(reply.covered_fact_ids).issubset(allowed_facts):
            violations.append("unknown_fact_id")
        if not required_facts.issubset(reply.covered_fact_ids):
            violations.append("required_fact_missing")

        allowed_memories = set(getattr(plan, "allowed_memory_ids", ()))
        allowed_capabilities = set(getattr(plan, "allowed_capability_ids", ()))
        if not set(reply.used_memory_ids).issubset(allowed_memories):
            violations.append("unknown_memory_id")
        if not set(reply.used_capability_ids).issubset(allowed_capabilities):
            violations.append("unknown_capability_id")
        if not set(reply.source_event_ids).issubset(scene.continuity_event_ids):
            violations.append("unknown_source_event_id")

        text = reply.text
        if _SERVICE_TAIL.search(text):
            violations.append("generic_service_tail")
        if _GENERIC_EMPATHY.search(text):
            violations.append("generic_empathy_preface")
        if _INTERNAL_RELATION.search(text):
            violations.append("internal_relationship_state")
        if move.ending is Ending.QUESTION and not text.endswith(("?", "？")):
            violations.append("question_ending_missing")
        if move.ending is Ending.STOP and _SERVICE_TAIL.search(text):
            if "generic_service_tail" not in violations:
                violations.append("generic_service_tail")
        violations.extend(self._chorus_violations(reply, plan))
        unique = tuple(dict.fromkeys(violations))
        return SocialReview(not unique, unique)

    @staticmethod
    def _chorus_violations(reply: RealizedReply, plan: object) -> tuple[str, ...]:
        move = plan.move
        scene = plan.scene
        if move.primary_move is not SocialMove.JOIN_CHORUS:
            if move.realization_mode is RealizationMode.EXACT_CHORUS:
                return ("chorus_mode_invalid",)
            return ()
        violations: list[str] = []
        if move.realization_mode is not RealizationMode.EXACT_CHORUS:
            violations.append("chorus_mode_invalid")
        if reply.text != move.verbatim_payload or reply.text != scene.chorus_payload:
            violations.append("chorus_payload_changed")
        if move.chorus_chain_id != scene.chorus_chain_id:
            violations.append("chorus_chain_mismatch")
        if reply.source_event_ids != scene.chorus_event_ids:
            violations.append("chorus_source_mismatch")
        if scene.chorus_already_joined:
            violations.append("chorus_already_joined")
        if scene.chorus_tone is not ChorusTone.SAFE_BANTER:
            violations.append("chorus_tone_unsafe")
        return tuple(violations)


__all__ = (
    "RealizedReply",
    "SocialOutputReviewer",
    "SocialReview",
)
