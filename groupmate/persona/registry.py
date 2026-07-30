"""Persona registry and resolved runtime context."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Dict, Sequence, Tuple

from ..core.relationships import RelationshipEntry
from .aemeath.behavior_profile import (
    AEMEATH_PARTICIPATION_PROFILE,
    PersonaParticipationProfile,
)
from .aemeath.provider import AemeathPersonaProvider


@dataclass(frozen=True)
class PersonaDefinition:
    """Static code-owned definition of one persona."""

    persona_id: str
    display_name: str
    default_aliases: Tuple[str, ...]
    participation_profile: PersonaParticipationProfile
    provider_factory: Callable[[Sequence[RelationshipEntry]], object]


@dataclass(frozen=True)
class PersonaContext:
    """Resolved persona identity plus deployment-owned seeds."""

    definition: PersonaDefinition
    aliases: Tuple[str, ...]
    relationship_seeds: Tuple[RelationshipEntry, ...]
    prompt_provider: object

    @property
    def persona_id(self) -> str:
        return self.definition.persona_id

    @property
    def display_name(self) -> str:
        return self.definition.display_name


class PersonaRegistry:
    """Resolve explicit persona IDs without cross-persona fallback."""

    def __init__(
        self,
        definitions: Sequence[PersonaDefinition],
        *,
        current_persona_id: str,
    ) -> None:
        registry: Dict[str, PersonaDefinition] = {}
        for definition in definitions:
            persona_id = str(definition.persona_id or "").strip()
            if not persona_id:
                raise ValueError("persona_id is required")
            if persona_id in registry:
                raise ValueError(f"duplicate persona_id: {persona_id}")
            registry[persona_id] = definition
        self._definitions = registry
        normalized_current = str(current_persona_id or "").strip()
        if normalized_current not in registry:
            raise ValueError(f"unknown current persona_id: {normalized_current}")
        self._current_persona_id = normalized_current

    @property
    def current_persona_id(self) -> str:
        return self._current_persona_id

    def resolve(
        self,
        persona_id: str,
        *,
        aliases: Sequence[str],
        relationships: Sequence[RelationshipEntry],
    ) -> PersonaContext:
        normalized = str(persona_id or "").strip()
        if not normalized:
            raise ValueError("persona_id is required")
        try:
            definition = self._definitions[normalized]
        except KeyError as exc:
            raise ValueError(f"unknown persona_id: {normalized}") from exc

        resolved_aliases = tuple(str(alias).strip() for alias in aliases if str(alias).strip())
        relationship_seeds = tuple(relationships)
        return PersonaContext(
            definition=definition,
            aliases=resolved_aliases,
            relationship_seeds=relationship_seeds,
            prompt_provider=definition.provider_factory(relationship_seeds),
        )


_DEFAULT_REGISTRY = PersonaRegistry(
    (
        PersonaDefinition(
            persona_id="aemeath",
            display_name="爱弥斯",
            default_aliases=("爱弥斯", "小爱", "飞行雪绒"),
            participation_profile=AEMEATH_PARTICIPATION_PROFILE,
            provider_factory=lambda relationships: AemeathPersonaProvider(
                relationships=relationships
            ),
        ),
    ),
    current_persona_id="aemeath",
)


def default_persona_registry() -> PersonaRegistry:
    """Return the code-owned persona registry."""

    return _DEFAULT_REGISTRY


__all__ = [
    "PersonaContext",
    "PersonaDefinition",
    "PersonaRegistry",
    "default_persona_registry",
]
