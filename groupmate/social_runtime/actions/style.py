"""Frozen persona style decisions for one generated social response."""

from __future__ import annotations

from dataclasses import dataclass

from groupmate.social_runtime.persona.modes import PersonaModeState
from groupmate.social_runtime.society.relationships import RelationshipProjection


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
    act: str
    token_budget: int

    def __post_init__(self) -> None:
        if not self.act.strip():
            raise ValueError("act is required")
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
        relation = context.relationship
        relationship_warmth = relation.warmth if relation is not None else 0
        relationship_play = relation.play_acceptance if relation is not None else 0
        warmth = self._clamp(50 + relationship_warmth // 4)
        playfulness = self._clamp(10 + relationship_play // 3)
        directness = 70
        posture = "friendly" if warmth >= 50 else "neutral"

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

        max_chars = min(320, max(40, context.token_budget * 3))
        max_sentences = 6
        max_segments = self._DIRECT_ANSWER_MAX_SEGMENTS if context.act == "direct_answer" else 2
        particle_budget = 2 if playfulness else 1
        punctuation_budget = 3
        if "drowsy" in context.mode.modifiers:
            max_chars = max(30, max_chars // 2)
            max_sentences = max(1, max_sentences // 2)
            max_segments = min(max_segments, 2)
            particle_budget = min(particle_budget, 1)
            punctuation_budget = min(punctuation_budget, 1)

        return StyleDirective(
            mode=context.mode.primary,
            act=context.act,
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
        values = context.culture_patterns + context.recent_outputs
        return tuple(value for value in dict.fromkeys(values) if value.strip())


__all__ = (
    "PersonaStyleSnapshot",
    "StyleContext",
    "StyleDirective",
    "StyleDirector",
)
