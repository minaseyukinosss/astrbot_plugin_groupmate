"""Deterministic platform identity and nickname history."""

from __future__ import annotations

from typing import Mapping

from ..contracts import SocialEventEnvelope
from .contracts import MemberAlias, MemberIdentity
from .repository import ProfileRepository


class IdentityService:
    def __init__(self, repository: ProfileRepository) -> None:
        self.repository = repository

    def observe(self, event: SocialEventEnvelope) -> MemberIdentity:
        group_id = str(event.group_id or "").strip()
        actor_id = str(event.actor_id or "").strip()
        if not group_id or not actor_id:
            raise ValueError("member identity requires group and actor scope")
        payload = event.payload
        sender = payload.get("sender")
        sender_map = sender if isinstance(sender, Mapping) else {}
        return self.remember_actor(
            persona_id=event.persona_id,
            group_id=group_id,
            platform=str(payload.get("platform") or "qq"),
            actor_id=actor_id,
            display_name=str(sender_map.get("name") or "群成员"),
            updated_at=int(event.received_at),
            source_event_id=event.event_id,
        )

    def remember_actor(
        self,
        *,
        persona_id: str,
        group_id: str,
        platform: str,
        actor_id: str,
        display_name: str,
        updated_at: int,
        avatar_ref: str | None = None,
        source_event_id: str | None = None,
        system_roles: tuple[str, ...] = (),
    ) -> MemberIdentity:
        normalized_name = " ".join(str(display_name or "群成员").split())[:48]
        identity = MemberIdentity(
            persona_id=str(persona_id),
            platform=str(platform or "qq"),
            actor_id=str(actor_id),
            display_name=normalized_name or "群成员",
            avatar_ref=avatar_ref,
            system_roles=system_roles,
            updated_at=int(updated_at),
        )
        self.repository.upsert_identity(identity)
        self.repository.remember_alias(
            MemberAlias(
                persona_id=identity.persona_id,
                group_id=str(group_id),
                actor_id=identity.actor_id,
                alias=identity.display_name,
                alias_type="platform_name",
                confidence=1.0,
                source_event_id=source_event_id,
                status="confirmed",
                first_seen_at=identity.updated_at,
                last_seen_at=identity.updated_at,
            )
        )
        return self.resolve(
            identity.persona_id, identity.platform, identity.actor_id
        ) or identity

    def resolve(
        self, persona_id: str, platform: str, actor_id: str
    ) -> MemberIdentity | None:
        return self.repository.identity(persona_id, platform, actor_id)

    def aliases(
        self, persona_id: str, group_id: str, actor_id: str
    ) -> tuple[MemberAlias, ...]:
        return self.repository.aliases(persona_id, group_id, actor_id)


__all__ = ("IdentityService",)
