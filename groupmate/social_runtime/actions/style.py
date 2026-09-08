"""Frozen persona style decisions for one generated social response."""

from __future__ import annotations

from dataclasses import dataclass

from ..persona.modes import PersonaModeState
from ..social_moves import KnowledgePolicy, SocialMove, SocialMovePlan
from ..social_scenes import ResponseAct, SocialScene
from ..society.relationships import RelationshipProjection
from ..stances import Attitude, Boundary, StanceDecision


@dataclass(frozen=True)
class PersonaStyleSnapshot:
    """The small, safe persona slice needed to direct one response."""

    persona_id: str
    default_address: str | None
    expression: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.persona_id.strip():
            raise ValueError("persona_id is required")


@dataclass(frozen=True)
class StyleContext:
    """All inputs are frozen before generation starts."""

    persona: PersonaStyleSnapshot
    mode: PersonaModeState
    relationship: RelationshipProjection | None
    culture_patterns: tuple[str, ...]
    recent_outputs: tuple[str, ...]
    token_budget: int
    scene: SocialScene | None = None
    stance: StanceDecision | None = None
    move: SocialMovePlan | None = None
    act: str | None = None

    def __post_init__(self) -> None:
        if self.move is None and not str(self.act or "").strip():
            raise ValueError("move or legacy act is required")
        if self.token_budget <= 0:
            raise ValueError("token_budget must be positive")


@dataclass(frozen=True)
class StyleDirective:
    """Fixed contract between style selection and text generation."""

    mode: str
    act: str
    posture: str
    address: str | None
    max_chars: int
    max_sentences: int
    max_segments: int
    warmth: int
    playfulness: int
    directness: int
    particle_budget: int
    punctuation_budget: int
    media_policy: str
    avoid_patterns: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.max_chars <= 0 or self.max_sentences <= 0 or self.max_segments <= 0:
            raise ValueError("style output limits must be positive")
        for name in ("warmth", "playfulness", "directness"):
            if not 0 <= getattr(self, name) <= 100:
                raise ValueError(f"{name} must be between 0 and 100")
        if self.particle_budget < 0 or self.punctuation_budget < 0:
            raise ValueError("style budgets cannot be negative")


class StyleDirector:
    """Derives tone only; relationships never authorize actions or tools."""

    _DIRECT_ANSWER_MAX_SEGMENTS = 3

    def direct(self, context: StyleContext) -> StyleDirective:
        act = (
            context.move.primary_move.value.lower()
            if context.move is not None
            else str(context.act).strip()
        )
        relation = context.relationship
        relationship_warmth = relation.warmth if relation is not None else 0
        relationship_play = relation.play_acceptance if relation is not None else 0
        warmth = self._clamp(50 + relationship_warmth // 4)
        playfulness = self._clamp(10 + relationship_play // 3)
        directness = 70
        posture = "friendly" if warmth >= 50 else "neutral"

        stance = context.stance
        if stance is not None:
            if stance.attitude is Attitude.WARM:
                warmth = self._clamp(warmth + 15)
            elif stance.attitude is Attitude.AMUSED:
                playfulness = self._clamp(playfulness + 25)
            elif stance.attitude is Attitude.FOCUSED:
                posture = "focused"
                directness = max(directness, 85)
                playfulness = 0
            elif stance.attitude is Attitude.GUARDED:
                posture = "reserved"
                warmth = self._clamp(warmth - 20)
                directness = max(directness, 80)
                playfulness = 0
            elif stance.attitude is Attitude.IRRITATED:
                posture = "reserved"
                directness = 95
                playfulness = 0
            if stance.boundary in {Boundary.FIRM, Boundary.FINAL}:
                posture = "firm"
                directness = 95
                playfulness = 0

        if "warm" in context.mode.modifiers:
            warmth = self._clamp(warmth + 15)
        if "playful" in context.mode.modifiers:
            playfulness = self._clamp(playfulness + 25)
        if "irritated" in context.mode.modifiers:
            directness = self._clamp(directness + 15)
            playfulness = 0
            posture = "reserved"
        if context.mode.primary == "boundary":
            posture = "firm"
            playfulness = 0
            directness = 95

        full_budget = min(320, max(40, context.token_budget * 3))
        needs_factual_room = bool(
            context.move is not None
            and context.move.knowledge_policy is not KnowledgePolicy.NONE
        )
        max_chars = full_budget if needs_factual_room else min(72, full_budget)
        max_sentences = 4 if needs_factual_room else 3
        max_segments = self._DIRECT_ANSWER_MAX_SEGMENTS if act == "direct_answer" else 2
        if context.move is not None and context.move.primary_move in {
            SocialMove.FIRM_BOUNDARY,
            SocialMove.REFUSE,
            SocialMove.JOIN_CHORUS,
            SocialMove.SILENCE,
        }:
            max_segments = 1
            max_sentences = min(max_sentences, 2)
        particle_budget = 2 if playfulness else 1
        punctuation_budget = 3
        response_act = context.move.response_act if context.move is not None else None
        if response_act in {
            ResponseAct.ACKNOWLEDGE,
            ResponseAct.REACT,
            ResponseAct.CLOSE,
        }:
            particle_budget = max(particle_budget, 3)
        if "drowsy" in context.mode.modifiers:
            max_chars = max(30, max_chars // 2)
            max_sentences = max(1, max_sentences // 2)
            max_segments = min(max_segments, 2)
            particle_budget = min(particle_budget, 1)
            punctuation_budget = min(punctuation_budget, 1)

        return StyleDirective(
            mode=context.mode.primary,
            act=act,
            posture=posture,
            address=context.persona.default_address,
            max_chars=max_chars,
            max_sentences=max_sentences,
            max_segments=max_segments,
            warmth=warmth,
            playfulness=playfulness,
            directness=directness,
            particle_budget=particle_budget,
            punctuation_budget=punctuation_budget,
            media_policy="text_only",
            avoid_patterns=self._avoid_patterns(context),
        )

    @staticmethod
    def _clamp(value: int) -> int:
        return max(0, min(100, value))

    @staticmethod
    def _avoid_patterns(context: StyleContext) -> tuple[str, ...]:
        move_avoidances = (
            context.move.must_not_say if context.move is not None else ()
        )
        values = context.culture_patterns + context.recent_outputs + move_avoidances
        return tuple(value for value in dict.fromkeys(values) if value.strip())


__all__ = (
    "PersonaStyleSnapshot",
    "StyleContext",
    "StyleDirective",
    "StyleDirector",
)
