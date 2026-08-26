"""Stable-ID social graph projection for one group."""

from __future__ import annotations

from dataclasses import dataclass

from .contracts import MemberIdentity, SocialEdge


_RELATION_LABELS = {
    "frequent_interaction": "经常互动",
    "familiar": "熟悉",
    "supportive": "彼此支持",
    "technical_peer": "技术同伴",
    "teasing": "互相打趣",
    "conflict": "存在冲突",
    "avoidance": "倾向回避",
    "custom": "稳定互动",
}


@dataclass(frozen=True)
class SocialGraph:
    persona_id: str
    group_id: str
    nodes: tuple[MemberIdentity, ...]
    edges: tuple[SocialEdge, ...]
    relation_rows: tuple[str, ...]

    @classmethod
    def build(
        cls,
        *,
        persona_id: str,
        group_id: str,
        identities: tuple[MemberIdentity, ...],
        edges: tuple[SocialEdge, ...],
        now: int,
    ) -> "SocialGraph":
        scoped_nodes = tuple(
            identity
            for identity in identities
            if identity.persona_id == persona_id
        )
        by_id = {identity.actor_id: identity for identity in scoped_nodes}
        scoped_edges = tuple(
            edge
            for edge in edges
            if edge.persona_id == persona_id
            and edge.group_id == group_id
            and edge.status == "confirmed"
            and (edge.valid_until is None or edge.valid_until > now)
            and edge.source_member_id in by_id
            and edge.target_member_id in by_id
            and edge.source_member_id != edge.target_member_id
        )
        rows = tuple(
            cls._row(edge, by_id)
            for edge in sorted(
                scoped_edges,
                key=lambda item: (
                    -item.strength,
                    -item.confidence,
                    item.edge_id,
                ),
            )
        )
        return cls(
            persona_id=str(persona_id),
            group_id=str(group_id),
            nodes=scoped_nodes,
            edges=scoped_edges,
            relation_rows=rows,
        )

    @staticmethod
    def _row(
        edge: SocialEdge, by_id: dict[str, MemberIdentity]
    ) -> str:
        source = by_id[edge.source_member_id].display_name
        target = by_id[edge.target_member_id].display_name
        arrow = "↔" if edge.direction == "bidirectional" else "→"
        relation = _RELATION_LABELS.get(edge.relation_type, "稳定互动")
        return f"{source} {arrow} {target}：{relation}"


__all__ = ("SocialGraph",)
