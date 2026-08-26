"""Deterministic, evidence-backed member profile snapshots."""

from __future__ import annotations

from collections import Counter

from .contracts import (
    MemberIdentity,
    ProfileEpisode,
    ProfileFact,
    ProfileSnapshot,
    SocialEdge,
)


_GENERIC_PORTRAITS = frozenset(
    {
        "友善",
        "活跃",
        "认真",
        "热情",
        "善良",
        "开朗",
        "友善活跃",
        "认真热情",
    }
)
_PREFERENCE_CATEGORIES = frozenset(
    {"preference", "dislike", "boundary", "interest"}
)
_FINGERPRINT_CATEGORIES = frozenset(
    {"identity", "skill", "speech_style", "behavior_pattern"}
)
_RELATION_LABELS = {
    "frequent_interaction": "经常互动",
    "familiar": "熟悉",
    "supportive": "彼此支持",
    "technical_peer": "技术同伴",
    "teasing": "会互相打趣",
    "conflict": "存在冲突",
    "avoidance": "倾向回避",
    "custom": "有稳定互动",
}


class SnapshotBuilder:
    """Build a compact snapshot without asking a model to rewrite evidence."""

    def build(
        self,
        member: MemberIdentity,
        *,
        group_id: str,
        facts: tuple[ProfileFact, ...],
        episodes: tuple[ProfileEpisode, ...],
        edges: tuple[SocialEdge, ...],
        source_revision: int,
        generated_at: int,
    ) -> ProfileSnapshot:
        scoped_facts = tuple(
            fact
            for fact in facts
            if self._fact_is_current(
                fact,
                member=member,
                group_id=group_id,
                now=generated_at,
            )
            and not self._generic(fact.summary)
        )
        ranked_facts = tuple(
            sorted(
                scoped_facts,
                key=lambda item: (
                    item.confidence,
                    item.evidence_count,
                    item.valid_from,
                    item.fact_id,
                ),
                reverse=True,
            )
        )
        fingerprints = self._unique_summaries(
            (
                fact.summary
                for fact in ranked_facts
                if fact.category in _FINGERPRINT_CATEGORIES
            ),
            limit=6,
        )
        preferences = self._unique_summaries(
            (
                fact.summary
                for fact in ranked_facts
                if fact.category in _PREFERENCE_CATEGORIES
            ),
            limit=4,
        )
        roles = self._unique_summaries(
            (
                fact.summary
                for fact in ranked_facts
                if fact.category == "group_role"
            ),
            limit=4,
        )
        scoped_episodes = tuple(
            sorted(
                (
                    episode
                    for episode in episodes
                    if episode.persona_id == member.persona_id
                    and episode.group_id == group_id
                    and member.actor_id in episode.participants
                    and episode.status == "confirmed"
                ),
                key=lambda item: (
                    item.importance,
                    item.confidence,
                    item.last_reinforced_at,
                    item.episode_id,
                ),
                reverse=True,
            )[:3]
        )
        categories = {fact.category for fact in ranked_facts}
        maturity = self._maturity(
            categories=categories,
            facts=ranked_facts,
            has_episode=bool(scoped_episodes),
        )
        portrait = self._portrait(
            fingerprints=fingerprints,
            preferences=preferences,
            episodes=scoped_episodes,
        )
        return ProfileSnapshot(
            persona_id=member.persona_id,
            group_id=str(group_id),
            subject_id=member.actor_id,
            one_line_portrait=portrait,
            group_roles=roles,
            individual_fingerprints=fingerprints,
            preferences_and_boundaries=preferences,
            representative_episode_ids=tuple(
                item.episode_id for item in scoped_episodes
            ),
            relationship_summary=self._relationship_summary(
                member,
                group_id=group_id,
                edges=edges,
                now=generated_at,
            ),
            maturity=maturity,
            source_revision=max(0, int(source_revision)),
            generated_at=max(0, int(generated_at)),
        )

    @staticmethod
    def _fact_is_current(
        fact: ProfileFact,
        *,
        member: MemberIdentity,
        group_id: str,
        now: int,
    ) -> bool:
        return bool(
            fact.persona_id == member.persona_id
            and fact.group_id == group_id
            and fact.subject_id == member.actor_id
            and fact.status == "confirmed"
            and fact.injectable
            and (fact.valid_until is None or fact.valid_until > now)
        )

    @staticmethod
    def _generic(summary: str) -> bool:
        normalized = "".join(
            character
            for character in str(summary).strip()
            if character not in "，。！？、,.!?；;：:· "
        )
        return normalized in _GENERIC_PORTRAITS

    @staticmethod
    def _unique_summaries(values, *, limit: int) -> tuple[str, ...]:
        return tuple(dict.fromkeys(str(value) for value in values))[:limit]

    @staticmethod
    def _maturity(
        *,
        categories: set[str],
        facts: tuple[ProfileFact, ...],
        has_episode: bool,
    ) -> str:
        if len(categories) < 2:
            return "new"
        repeated_behavior = any(
            item.category == "behavior_pattern" and item.evidence_count >= 3
            for item in facts
        )
        if len(categories) >= 5 and (repeated_behavior or has_episode):
            return "stable"
        return "forming"

    @staticmethod
    def _portrait(
        *,
        fingerprints: tuple[str, ...],
        preferences: tuple[str, ...],
        episodes: tuple[ProfileEpisode, ...],
    ) -> str:
        if fingerprints:
            return fingerprints[0]
        if preferences:
            return preferences[0]
        if episodes:
            return episodes[0].summary
        return "正在形成画像"

    @staticmethod
    def _relationship_summary(
        member: MemberIdentity,
        *,
        group_id: str,
        edges: tuple[SocialEdge, ...],
        now: int,
    ) -> str:
        relations = Counter(
            _RELATION_LABELS.get(edge.relation_type, "有稳定互动")
            for edge in edges
            if edge.persona_id == member.persona_id
            and edge.group_id == group_id
            and edge.status == "confirmed"
            and (edge.valid_until is None or edge.valid_until > now)
            and member.actor_id
            in {edge.source_member_id, edge.target_member_id}
        )
        if not relations:
            return ""
        return "、".join(
            label
            for label, _count in sorted(
                relations.items(), key=lambda item: (-item[1], item[0])
            )[:3]
        )


__all__ = ("SnapshotBuilder",)
