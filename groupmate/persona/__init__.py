"""人格包（Persona Pack）层：可替换角色，不含调度逻辑。"""

from .registry import (
    PersonaContext,
    PersonaDefinition,
    PersonaRegistry,
    default_persona_registry,
)

__all__ = [
    "PersonaContext",
    "PersonaDefinition",
    "PersonaRegistry",
    "default_persona_registry",
]
