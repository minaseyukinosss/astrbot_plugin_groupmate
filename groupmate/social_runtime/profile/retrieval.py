"""Bounded, relevance-scoped member context retrieval."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from ..contracts import SocialEventEnvelope
from .contracts import ProfileEpisode, ProfileFact, ProfileSnapshot, SocialEdge
from .repository import ProfileRepository


@dataclass(frozen=True)
class RetrievedMember:
    subject_id: str
    aliases: tuple[str, ...]
    snapshot: ProfileSnapshot | None
    fact_summaries: tuple[str, ...]


@dataclass(frozen=True)
class ProfileRetrieval:
    members: tuple[RetrievedMember, ...]
    facts: tuple[ProfileFact, ...]
    episodes: tuple[ProfileEpisode, ...]
    edges: tuple[SocialEdge, ...]
    prompt_text: str
    ambient_context: Mapping[str, object]


class ProfileRetriever:
    def __init__(self, repository: ProfileRepository) -> None:
        self.repository = repository

    def for_message(
        self,
        event: SocialEventEnvelope,
        *,
        max_chars: int = 1200,
    ) -> ProfileRetrieval:
        group_id = str(event.group_id or "")
        if not group_id or max_chars < 1:
            return ProfileRetrieval((), (), (), (), "", {"members": [], "relations": []})
        subject_ids = self._subject_ids(event)
        if not subject_ids or not self.repository.personalization_enabled(
            event.persona_id, group_id, subject_ids[0]
        ):
            return ProfileRetrieval(
                (), (), (), (), "", {"members": [], "relations": []}
            )
        now = int(event.received_at)
        facts = tuple(
            sorted(
                (
                    fact
                    for subject_id in subject_ids
                    for fact in self.repository.facts(
                        event.persona_id,
                        group_id,
                        subject_id,
                        injectable_only=True,
                    )
                    if fact.valid_until is None or fact.valid_until > now
                ),
                key=lambda item: (
                    subject_ids.index(item.subject_id),
                    -item.confidence,
                    -item.evidence_count,
                    -item.valid_from,
                    item.fact_id,
                ),
            )[:3]
        )
        all_episodes = self.repository.episodes(event.persona_id, group_id)
        episodes = tuple(
            sorted(
                (
                    item
                    for item in all_episodes
                    if item.status == "confirmed"
                    and set(item.participants) & set(subject_ids)
                ),
                key=lambda item: (
                    -item.importance,
                    -item.confidence,
                    -item.last_reinforced_at,
                    item.episode_id,
                ),
            )[:2]
        )
        edges = tuple(
            sorted(
                (
                    item
                    for item in self.repository.edges(event.persona_id, group_id)
                    if item.status == "confirmed"
                    and (item.valid_until is None or item.valid_until > now)
                    and item.source_member_id in subject_ids
                    and item.target_member_id in subject_ids
                ),
                key=lambda item: (
                    -item.strength,
                    -item.confidence,
                    item.edge_id,
                ),
            )[:3]
        )
        members = tuple(
            RetrievedMember(
                subject_id=subject_id,
                aliases=tuple(
                    item.alias
                    for item in self.repository.aliases(
                        event.persona_id, group_id, subject_id
                    )
                    if item.status == "confirmed"
                )[-4:],
                snapshot=self.repository.snapshot(
                    event.persona_id, group_id, subject_id
                ),
                fact_summaries=tuple(
                    item.summary
                    for item in facts
                    if item.subject_id == subject_id
                ),
            )
            for subject_id in subject_ids
        )
        return ProfileRetrieval(
            members=members,
            facts=facts,
            episodes=episodes,
            edges=edges,
            prompt_text=self._prompt_text(
                members,
                facts=facts,
                episodes=episodes,
                edges=edges,
                max_chars=max_chars,
            ),
            ambient_context=self._ambient_context(members, facts, edges),
        )

    def group_member_refs(
        self,
        persona_id: str,
        group_id: str,
        *,
        max_members: int = 64,
    ) -> dict[str, tuple[str, ...]]:
        """Expose confirmed names only; inferred profile traits are not aliases."""

        grouped: dict[str, list[str]] = {}
        for alias in self.repository.aliases_for_group(persona_id, group_id):
            if alias.status != "confirmed":
                continue
            if alias.actor_id not in grouped and len(grouped) >= max(1, int(max_members)):
                break
            values = grouped.setdefault(alias.actor_id, [])
            if alias.alias not in values:
                values.append(alias.alias)
        return {
            actor_id: tuple(aliases[-4:])
            for actor_id, aliases in grouped.items()
            if aliases
        }

    @staticmethod
    def _subject_ids(event: SocialEventEnvelope) -> tuple[str, ...]:
        values = [str(event.actor_id or "").strip()]
        mentions = event.payload.get("mentions")
        if isinstance(mentions, (list, tuple)):
            for item in mentions:
                if isinstance(item, Mapping):
                    value = str(
                        item.get("actor_id")
                        or item.get("target_id")
                        or item.get("id")
                        or item.get("qq")
                        or ""
                    ).strip()
                else:
                    value = str(item or "").strip()
                if value:
                    values.append(value)
        reply_to = str(event.payload.get("reply_to_actor_id") or "").strip()
        if reply_to:
            values.append(reply_to)
        return tuple(dict.fromkeys(value for value in values if value))[:3]

    @staticmethod
    def _prompt_text(
        members: tuple[RetrievedMember, ...],
        *,
        facts: tuple[ProfileFact, ...],
        episodes: tuple[ProfileEpisode, ...],
        edges: tuple[SocialEdge, ...],
        max_chars: int,
    ) -> str:
        labels = {
            member.subject_id: (
                member.aliases[-1] if member.aliases else member.subject_id
            )
            for member in members
        }
        lines: list[str] = []
        for member in members:
            portrait = (
                member.snapshot.one_line_portrait
                if member.snapshot is not None
                and member.snapshot.one_line_portrait != "正在形成画像"
                else ""
            )
            if portrait:
                lines.append(f"成员 {labels[member.subject_id]}：{portrait}")
        lines.extend(f"相关事实：{fact.summary}" for fact in facts)
        lines.extend(f"相关经历：{episode.summary}" for episode in episodes)
        lines.extend(
            "成员关系："
            f"{labels.get(edge.source_member_id, edge.source_member_id)} "
            f"{edge.relation_type} "
            f"{labels.get(edge.target_member_id, edge.target_member_id)}"
            for edge in edges
        )
        selected: list[str] = []
        used = 0
        for line in lines:
            extra = len(line) + (1 if selected else 0)
            if used + extra <= max_chars:
                selected.append(line)
                used += extra
            elif not selected:
                selected.append(line[:max_chars])
                break
        return "\n".join(selected)

    @staticmethod
    def _ambient_context(
        members: tuple[RetrievedMember, ...],
        facts: tuple[ProfileFact, ...],
        edges: tuple[SocialEdge, ...],
    ) -> dict[str, object]:
        return {
            "members": [
                {
                    "subject_id": member.subject_id,
                    "aliases": list(member.aliases[-3:]),
                    "addressing_habits": [
                        fact.summary
                        for fact in facts
                        if fact.subject_id == member.subject_id
                        and fact.category in {"identity", "speech_style"}
                    ][:2],
                }
                for member in members
            ],
            "relations": [
                {
                    "source_member_id": edge.source_member_id,
                    "target_member_id": edge.target_member_id,
                    "relation_type": edge.relation_type,
                }
                for edge in edges
            ],
        }


__all__ = (
    "ProfileRetrieval",
    "ProfileRetriever",
    "RetrievedMember",
)
