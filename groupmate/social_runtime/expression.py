"""Bounded Persona-specific expression planning after participation is approved."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping


@dataclass(frozen=True)
class ExpressionPlan:
    reaction_stance: str
    core_response_goal: str
    persona_cues: tuple[str, ...]
    boundary_style: str
    followup_hook: str
    message_count: int
    capability_request: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "persona_cues", tuple(self.persona_cues))
        if self.message_count not in {1, 2}:
            raise ValueError("expression message_count must be 1 or 2")
        if len(self.persona_cues) > 6:
            raise ValueError("expression persona cues must be bounded")

    @classmethod
    def conservative(cls) -> "ExpressionPlan":
        return cls(
            reaction_stance="attentive",
            core_response_goal="respond_conservatively",
            persona_cues=(),
            boundary_style="明确且友好",
            followup_hook="only_if_it_adds_value",
            message_count=1,
        )


class ExpressionPlanner:
    _RELATION_WORDS = (
        "不理我",
        "不要我",
        "喜欢",
        "想你",
        "生气",
        "难过",
        "高兴",
        "谢谢",
    )

    def plan(
        self,
        *,
        lane: str,
        act: str,
        source_text: str,
        persona_profile: Mapping[str, object],
    ) -> ExpressionPlan:
        identity = self._section(persona_profile, "identity")
        expression = self._section(persona_profile, "expression")
        participation = self._section(persona_profile, "participation")
        name = str(identity.get("name") or "Groupmate").strip()[:24]
        cues = tuple(
            value
            for value in (
                name,
                str(identity.get("background") or "").strip()[:120],
                str(expression.get("tone") or "").strip()[:120],
                str(expression.get("language_habits") or "").strip()[:120],
            )
            if value
        )
        relationship = any(word in str(source_text) for word in self._RELATION_WORDS)
        reaction = (
            "acknowledge_relationship"
            if relationship
            else "continue_current_exchange"
            if lane == "CONTINUATION"
            else "attentive"
        )
        return ExpressionPlan(
            reaction_stance=reaction,
            core_response_goal=str(act or "respond_conservatively")[:80],
            persona_cues=cues,
            boundary_style=str(
                participation.get("stay_silent_when") or "明确且友好"
            )[:120],
            followup_hook=(
                "optional_if_natural"
                if lane in {"DIRECT_FAST", "CONTINUATION"}
                else "only_if_it_adds_value"
            ),
            message_count=2 if relationship else 1,
        )

    @staticmethod
    def _section(
        persona_profile: Mapping[str, object], name: str
    ) -> Mapping[str, object]:
        value = persona_profile.get(name, {})
        return value if isinstance(value, Mapping) else {}


__all__ = ("ExpressionPlan", "ExpressionPlanner")
