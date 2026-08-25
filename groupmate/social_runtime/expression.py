"""Bounded Persona-specific expression planning after participation is approved."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from .persona.canon import PersonaCanon, PersonaMaterialSelector
from .society.relationships import (
    PublicAffection,
    RelationshipStage,
)


@dataclass(frozen=True)
class ExpressionPlan:
    reaction_stance: str
    core_response_goal: str
    persona_cues: tuple[str, ...]
    boundary_style: str
    followup_hook: str
    message_count: int
    capability_request: str | None = None
    relationship_stage: str = RelationshipStage.STRANGER.value
    explicit_material: str | None = None
    material_reason: str = "no_relevant_material"
    persona_avoidances: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "persona_cues", tuple(self.persona_cues))
        object.__setattr__(
            self, "persona_avoidances", tuple(self.persona_avoidances)
        )
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

    def __init__(
        self, material_selector: PersonaMaterialSelector | None = None
    ) -> None:
        self._material_selector = material_selector or PersonaMaterialSelector()

    def plan(
        self,
        *,
        lane: str,
        act: str,
        source_text: str,
        persona_profile: Mapping[str, object],
        relationship: PublicAffection | None = None,
        recent_outputs: tuple[str, ...] = (),
    ) -> ExpressionPlan:
        identity = self._section(persona_profile, "identity")
        expression = self._section(persona_profile, "expression")
        participation = self._section(persona_profile, "participation")
        canon_value = persona_profile.get("canon")
        canon = PersonaCanon.from_mapping(
            canon_value if isinstance(canon_value, Mapping) else None
        )
        material = self._material_selector.select(
            canon,
            source_text=source_text,
            recent_outputs=tuple(recent_outputs),
        )
        affection = relationship or PublicAffection(
            0.0, RelationshipStage.STRANGER
        )
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
        boundary_style = self._boundary_style(
            affection.stage,
            str(participation.get("stay_silent_when") or "明确且友好")[:120],
        )
        return ExpressionPlan(
            reaction_stance=reaction,
            core_response_goal=str(act or "respond_conservatively")[:80],
            persona_cues=cues,
            boundary_style=boundary_style,
            followup_hook=(
                "optional_if_natural"
                if lane in {"DIRECT_FAST", "CONTINUATION"}
                else "only_if_it_adds_value"
            ),
            message_count=2 if relationship else 1,
            relationship_stage=affection.stage.value,
            explicit_material=(
                material.fact.text if material.fact is not None else None
            ),
            material_reason=material.reason,
            persona_avoidances=canon.current_snapshot().avoidances,
        )

    @staticmethod
    def _boundary_style(
        stage: RelationshipStage, default: str
    ) -> str:
        if stage is RelationshipStage.GUARDED:
            return "冷静、明确拒绝迎合；只依据相关且已证实的行为指出边界"
        if stage is RelationshipStage.DISTANT:
            return "克制、保持距离，不假装关系亲密"
        return default

    @staticmethod
    def _section(
        persona_profile: Mapping[str, object], name: str
    ) -> Mapping[str, object]:
        value = persona_profile.get(name, {})
        return value if isinstance(value, Mapping) else {}


__all__ = ("ExpressionPlan", "ExpressionPlanner")
