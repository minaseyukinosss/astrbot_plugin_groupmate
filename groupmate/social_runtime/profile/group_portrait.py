"""Privacy-safe aggregate portrait of one group."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

from .contracts import ProfileSnapshot, SocialEdge


@dataclass(frozen=True)
class GroupPortrait:
    persona_id: str
    group_id: str
    summary: str
    common_topics: tuple[str, ...]
    activity_rhythm: str
    role_counts: dict[str, int]
    relation_counts: dict[str, int]
    member_count: int
    source_revision: int
    generated_at: int


class GroupPortraitBuilder:
    def build(
        self,
        *,
        persona_id: str,
        group_id: str,
        member_snapshots: tuple[ProfileSnapshot, ...],
        culture: tuple[str, ...],
        topic_counts: dict[str, int],
        activity_hours: tuple[int, ...],
        edges: tuple[SocialEdge, ...],
        source_revision: int,
        generated_at: int,
    ) -> GroupPortrait:
        scoped = tuple(
            snapshot
            for snapshot in member_snapshots
            if snapshot.persona_id == persona_id
            and snapshot.group_id == group_id
        )
        role_counts = Counter(
            role for snapshot in scoped for role in snapshot.group_roles
        )
        relation_counts = Counter(
            edge.relation_type
            for edge in edges
            if edge.persona_id == persona_id
            and edge.group_id == group_id
            and edge.status == "confirmed"
            and (
                edge.valid_until is None
                or edge.valid_until > int(generated_at)
            )
        )
        topics = tuple(
            topic
            for topic, count in sorted(
                (
                    (str(topic).strip(), max(0, int(count)))
                    for topic, count in topic_counts.items()
                    if str(topic).strip() and int(count) > 0
                ),
                key=lambda item: (-item[1], item[0]),
            )[:6]
        )
        safe_culture = tuple(
            dict.fromkeys(
                " ".join(str(item).split())[:80]
                for item in culture
                if " ".join(str(item).split())
            )
        )[:4]
        if safe_culture:
            summary = "这个群" + "、".join(safe_culture)
        elif scoped:
            # Report aggregate progress without copying private member details.
            summary = f"已形成{len(scoped)}位成员画像，群体认知持续更新"
        else:
            summary = "群体画像正在形成"
        return GroupPortrait(
            persona_id=str(persona_id),
            group_id=str(group_id),
            summary=summary,
            common_topics=topics,
            activity_rhythm=self._activity_rhythm(activity_hours),
            role_counts=dict(sorted(role_counts.items())),
            relation_counts=dict(sorted(relation_counts.items())),
            member_count=len(scoped),
            source_revision=max(0, int(source_revision)),
            generated_at=max(0, int(generated_at)),
        )

    @staticmethod
    def _activity_rhythm(hours: tuple[int, ...]) -> str:
        buckets = Counter()
        for raw in hours:
            hour = int(raw) % 24
            if hour >= 22 or hour <= 5:
                buckets["深夜活跃"] += 1
            elif hour <= 11:
                buckets["上午活跃"] += 1
            elif hour <= 17:
                buckets["下午活跃"] += 1
            else:
                buckets["晚间活跃"] += 1
        if not buckets:
            return "活跃时间尚未形成"
        ordered = sorted(buckets.items(), key=lambda item: (-item[1], item[0]))
        if len(ordered) > 1 and ordered[0][1] == ordered[1][1]:
            return "活跃时间较分散"
        return ordered[0][0]


__all__ = ("GroupPortrait", "GroupPortraitBuilder")
