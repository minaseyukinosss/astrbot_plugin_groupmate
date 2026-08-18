"""Administrator-published immutable persona constitution versions."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass


class UnauthorizedConstitutionPublish(PermissionError):
    """Raised when publication lacks explicit administrator authority."""


@dataclass(frozen=True)
class PublishAuthority:
    actor_id: str
    role: str
    signature: str


@dataclass(frozen=True)
class ConstitutionDraft:
    persona_id: str
    identity: tuple[str, ...]
    values: tuple[str, ...]
    boundaries: tuple[str, ...]
    preferences: tuple[str, ...]
    expression: tuple[str, ...]
    safety: tuple[str, ...]
    autonomy: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.persona_id.strip():
            raise ValueError("persona_id must not be empty")
        if not self.identity or not self.values or not self.safety:
            raise ValueError("constitution identity, values, and safety are required")


@dataclass(frozen=True)
class ConstitutionVersion:
    persona_id: str
    version: int
    identity: tuple[str, ...]
    values: tuple[str, ...]
    boundaries: tuple[str, ...]
    preferences: tuple[str, ...]
    expression: tuple[str, ...]
    safety: tuple[str, ...]
    autonomy: tuple[str, ...]
    content_hash: str
    published_by: str
    published_at: int


class ConstitutionPublisher:
    """Small authority boundary; persistence can store emitted versions verbatim."""

    def __init__(self) -> None:
        self._versions: dict[str, list[ConstitutionVersion]] = {}

    def publish(
        self,
        draft: ConstitutionDraft,
        authority: PublishAuthority,
        *,
        now: int,
    ) -> ConstitutionVersion:
        if authority.role != "administrator" or not authority.signature.strip():
            raise UnauthorizedConstitutionPublish(
                "constitution publication requires administrator signature"
            )
        if not authority.actor_id.strip():
            raise UnauthorizedConstitutionPublish("administrator actor_id is required")
        content_hash = self._content_hash(draft)
        versions = self._versions.setdefault(draft.persona_id, [])
        for existing in versions:
            if existing.content_hash == content_hash:
                return existing
        version = ConstitutionVersion(
            **asdict(draft),
            version=len(versions) + 1,
            content_hash=content_hash,
            published_by=authority.actor_id,
            published_at=int(now),
        )
        versions.append(version)
        return version

    @staticmethod
    def _content_hash(draft: ConstitutionDraft) -> str:
        encoded = json.dumps(
            asdict(draft),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()


__all__ = (
    "ConstitutionDraft",
    "ConstitutionPublisher",
    "ConstitutionVersion",
    "PublishAuthority",
    "UnauthorizedConstitutionPublish",
)
