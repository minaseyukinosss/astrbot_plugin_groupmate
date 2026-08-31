from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

import pytest

from groupmate.social_runtime.knowledge.contracts import (
    KnowledgeFact,
    KnowledgeSnapshot,
    StrictFactFragment,
)
from groupmate.social_runtime.knowledge.grounding import (
    GroundedReplyReviewer,
    KnowledgeFactRenderer,
    StrictReplyAssembler,
)
from groupmate.social_runtime.social_moves import SocialMovePlan
from groupmate.social_runtime.social_review import RealizedReply


def _snapshot(
    *,
    qualifier="official_preview",
    risk_class="version_state",
    evidence_level="official",
    status="active",
    knowledge_id="knowledge:version",
    summary="下一版本官方前瞻已经公布。",
    revision=3,
):
    stable = risk_class == "stable_semantic"
    fact = KnowledgeFact.create(
        knowledge_id=knowledge_id,
        game_entity_id="game:genshin-impact",
        entity_id="term:resin" if stable else "version:next",
        safe_summary=summary,
        risk_class=risk_class,
        evidence_level=evidence_level,
        qualifier=qualifier,
        checked_at=90,
        expires_at=140,
        source_ids=("source:official",),
        version_slot_id=None if stable else "slot:next",
        region=None if stable else "global",
        platform=None if stable else "all",
        status=status,
        version_state_revision=0 if stable else revision,
    )
    fragments = () if stable else (
        StrictFactFragment.create(
            fragment_id=f"fragment:{knowledge_id}",
            knowledge_id=knowledge_id,
            text=summary,
            risk_class=risk_class,
        ),
    )
    return KnowledgeSnapshot.create(
        snapshot_id=f"snapshot:{knowledge_id}",
        topic_frame_id="knowledge-frame:1",
        allowed_knowledge_facts=(fact,),
        strict_fact_fragments=fragments,
        source_ids=fact.source_ids,
        checked_at=100,
        expires_at=130,
        version_state_revision=0 if stable else revision,
    )


@pytest.mark.parametrize(
    ("qualifier", "evidence", "summary", "expected"),
    (
        ("official_preview", "official", "2.0前瞻已公布。", "官方前瞻已公布："),
        ("official_released", "official", "2.0已经上线。", "官方已确认上线："),
        ("rumor", "unofficial", "测试服出现新角色。", "非官方消息："),
        ("negative_official", "official", "没有新公告。", "官方来源未发现相关公布"),
        ("negative_search", "secondary", "没有可靠结果。", "公开检索未找到可靠信息"),
    ),
)
def test_renderer_uses_qualifier_safe_wording_and_local_source_urls(
    qualifier, evidence, summary, expected
):
    snapshot = _snapshot(
        qualifier=qualifier,
        evidence_level=evidence,
        summary=summary,
    )
    renderer = KnowledgeFactRenderer(
        source_urls={
            "source:official": "https://example.com/news",
            "source:not-in-snapshot": "https://attacker.example/leak",
        }
    )

    rendered = renderer.render(snapshot)

    assert len(rendered) == 1
    assert expected in rendered[0].text
    assert "https://example.com/news" in rendered[0].text
    assert "attacker.example" not in rendered[0].text


def test_renderer_never_turns_stale_or_disputed_facts_into_affirmative_claims():
    active = _snapshot()
    stale_fact = replace(active.allowed_knowledge_facts[0], status="stale")
    stale = replace(active, allowed_knowledge_facts=(stale_fact,))
    disputed_fact = replace(active.allowed_knowledge_facts[0], status="disputed")
    disputed = replace(active, allowed_knowledge_facts=(disputed_fact,))

    with pytest.raises(ValueError, match="stale"):
        KnowledgeFactRenderer().render(stale)
    text = KnowledgeFactRenderer().render(disputed)[0].text
    assert text == "现有来源信息相互冲突，暂时无法确认。"


def test_strict_assembler_accepts_only_connectors_and_frozen_fragments():
    snapshot = _snapshot()
    assembler = StrictReplyAssembler(
        KnowledgeFactRenderer(), allowed_addresses=("小爱",)
    )
    fragment_id = snapshot.strict_fact_fragments[0].fragment_id

    reply = assembler.assemble(
        (
            {"kind": "text", "text": "小爱，那目前只能说："},
            {"kind": "knowledge_fragment", "fragment_id": fragment_id},
        ),
        snapshot,
        ("knowledge:version",),
        declared_used_knowledge_ids=("knowledge:version",),
    )

    assert reply.text.startswith("小爱，那目前只能说：")
    assert "官方前瞻已公布" in reply.text
    assert reply.used_knowledge_ids == ("knowledge:version",)
    assert reply.covered_fact_ids == ()

    attacks = (
        (({"kind": "knowledge_fragment", "fragment_id": "fragment:unknown"},), "unknown"),
        (({"kind": "text", "text": "2.0版本明天上线"},), "text"),
        (({"kind": "text", "text": "https://model.example/source"},), "URL"),
        ((
            {"kind": "text", "text": "原神这边呢："},
            {"kind": "knowledge_fragment", "fragment_id": fragment_id},
        ), "knowledge assertion"),
        (({"kind": "knowledge_fragment", "fragment_id": fragment_id, "text": "改写"},), "field"),
        ((
            {"kind": "knowledge_fragment", "fragment_id": fragment_id},
            {"kind": "knowledge_fragment", "fragment_id": fragment_id},
        ), "duplicate"),
    )
    for parts, code in attacks:
        with pytest.raises(ValueError, match=code):
            assembler.assemble(
                parts,
                snapshot,
                ("knowledge:version",),
                declared_used_knowledge_ids=("knowledge:version",),
            )


def test_grounded_reviewer_closes_ids_ttl_revision_and_qualifier_confusion():
    stable = _snapshot(
        qualifier="stable",
        risk_class="stable_semantic",
        evidence_level="bundled",
        knowledge_id="knowledge:stable",
        summary="树脂是游戏内的一种体力资源。",
        revision=0,
    )
    move = SocialMovePlan.create(
        primary_move="DIRECT_ANSWER",
        knowledge_policy="grounded",
        may_use_knowledge_ids=("knowledge:stable",),
    )
    plan = SimpleNamespace(move=move, knowledge_snapshot=stable)
    reviewer = GroundedReplyReviewer()
    valid = RealizedReply(
        "树脂就是游戏里的体力资源。",
        (),
        (),
        (),
        used_knowledge_ids=("knowledge:stable",),
    )

    assert reviewer.review(valid, plan, now=110, current_revision=0).accepted
    assert "unknown_knowledge_id" in reviewer.review(
        replace(valid, used_knowledge_ids=("knowledge:invented",)),
        plan,
        now=110,
        current_revision=0,
    ).violations
    assert "knowledge_snapshot_expired" in reviewer.review(
        valid, plan, now=130, current_revision=0
    ).violations
    assert "knowledge_revision_changed" in reviewer.review(
        valid, plan, now=110, current_revision=1
    ).violations
    assert "unsupported_knowledge_assertion" in reviewer.review(
        replace(valid, text="树脂是体力资源，2.0版本明天上线。"),
        plan,
        now=110,
        current_revision=0,
    ).violations
    assert "unsupported_knowledge_assertion" in reviewer.review(
        replace(valid, text="树脂是体力资源，它还能兑换角色。"),
        plan,
        now=110,
        current_revision=0,
    ).violations

    rumor = _snapshot(
        qualifier="rumor",
        evidence_level="unofficial",
        summary="测试服出现新角色。",
    )
    strict_move = SocialMovePlan.create(
        primary_move="DIRECT_ANSWER",
        knowledge_policy="strict",
        must_use_knowledge_ids=("knowledge:version",),
    )
    rumor_plan = SimpleNamespace(move=strict_move, knowledge_snapshot=rumor)
    confused = RealizedReply(
        "官方确认测试服会有新角色。",
        (),
        (),
        (),
        used_knowledge_ids=("knowledge:version",),
    )
    assert "rumor_as_official" in reviewer.review(
        confused, rumor_plan, now=110, current_revision=3
    ).violations
