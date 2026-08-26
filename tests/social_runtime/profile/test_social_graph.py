from __future__ import annotations

from groupmate.social_runtime.profile.contracts import MemberIdentity, SocialEdge
from groupmate.social_runtime.profile.graph import SocialGraph


def _identity(actor_id: str, name: str) -> MemberIdentity:
    return MemberIdentity(
        persona_id="persona",
        platform="qq",
        actor_id=actor_id,
        display_name=name,
        updated_at=100,
    )


def _edge(*, status: str = "confirmed", valid_until=None) -> SocialEdge:
    return SocialEdge(
        edge_id=f"edge-{status}",
        persona_id="persona",
        group_id="group-1",
        source_member_id="u-ling",
        target_member_id="u-sai",
        relation_type="technical_peer",
        direction="bidirectional",
        strength=0.72,
        confidence=0.91,
        source_event_ids=("event-1", "event-2", "event-3"),
        status=status,
        valid_from=100,
        valid_until=valid_until,
        last_observed_at=180,
    )


def test_graph_keeps_similar_nicknames_as_separate_stable_nodes():
    graph = SocialGraph.build(
        persona_id="persona",
        group_id="group-1",
        identities=(
            _identity("u-ling", "玲151"),
            _identity("u-sai", "小赛151"),
        ),
        edges=(_edge(),),
        now=200,
    )

    assert {node.actor_id for node in graph.nodes} == {"u-ling", "u-sai"}
    assert graph.edges[0].source_member_id == "u-ling"
    assert graph.edges[0].target_member_id == "u-sai"
    assert graph.relation_rows == ("玲151 ↔ 小赛151：技术同伴",)


def test_graph_omits_proposed_expired_and_cross_scope_edges():
    cross_group = SocialEdge(**{**_edge().__dict__, "edge_id": "cross", "group_id": "group-2"})

    graph = SocialGraph.build(
        persona_id="persona",
        group_id="group-1",
        identities=(
            _identity("u-ling", "玲151"),
            _identity("u-sai", "小赛151"),
        ),
        edges=(
            _edge(status="proposed"),
            _edge(valid_until=150),
            cross_group,
        ),
        now=200,
    )

    assert graph.edges == ()
    assert graph.relation_rows == ()
