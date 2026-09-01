from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

from groupmate.adapters.astrbot_bridge import AstrBotSocialRuntimeBridge
from groupmate.social_runtime.contracts import RuntimeMode
from groupmate.social_runtime.knowledge.contracts import (
    KnowledgeFact,
    KnowledgeSnapshot,
    StrictFactFragment,
)
from groupmate.social_runtime.knowledge.grounding import KnowledgeFactRenderer


_KINDS = {
    "stable",
    "current_release",
    "next_undisclosed",
    "preview",
    "rumor_none",
    "rumor_observed",
    "conflict",
    "search_failure",
}
_GAMES = {
    "game:delta-force",
    "game:wuthering-waves",
    "game:honkai-star-rail",
    "game:zenless-zone-zero",
}


def _cases():
    path = Path(__file__).parents[2] / "scenarios" / "game_version_grounding.jsonl"
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def test_four_game_grounding_corpus_has_all_safety_outcomes():
    cases = _cases()

    assert len(cases) == 32
    assert {case["game_id"] for case in cases} == _GAMES
    assert "原神" not in json.dumps(cases, ensure_ascii=False)
    for game_id in _GAMES:
        assert {
            case["kind"] for case in cases if case["game_id"] == game_id
        } == _KINDS


def test_frozen_corpus_renders_qualifiers_without_promoting_uncertainty():
    expected = {
        "current_release": "官方已确认上线：",
        "next_undisclosed": "官方来源未发现相关公布",
        "preview": "官方前瞻已公布：",
        "rumor_none": "公开检索未找到可靠信息",
        "rumor_observed": "非官方消息：",
        "conflict": "现有来源信息相互冲突，暂时无法确认。",
    }
    for index, case in enumerate(_cases()):
        if case["kind"] not in expected:
            continue
        fact = KnowledgeFact.create(
            knowledge_id=f"knowledge:scenario:{index}",
            game_entity_id=case["game_id"],
            entity_id="version:scenario",
            safe_summary=case["summary"],
            risk_class="version_state",
            evidence_level=case["evidence_level"],
            qualifier=case["qualifier"],
            checked_at=90,
            expires_at=140,
            source_ids=(f"source:scenario:{index}",),
            version_slot_id="slot:scenario",
            region="global",
            platform="all",
            status="active",
            version_state_revision=1,
        )
        fragment = StrictFactFragment.create(
            fragment_id=f"fragment:scenario:{index}",
            knowledge_id=fact.knowledge_id,
            text=fact.safe_summary,
            risk_class=fact.risk_class,
        )
        snapshot = KnowledgeSnapshot.create(
            snapshot_id=f"snapshot:scenario:{index}",
            topic_frame_id=f"frame:scenario:{index}",
            allowed_knowledge_facts=(fact,),
            strict_fact_fragments=(fragment,),
            source_ids=fact.source_ids,
            checked_at=100,
            expires_at=130,
            version_state_revision=1,
        )
        if case["kind"] == "conflict":
            snapshot = replace(
                snapshot,
                allowed_knowledge_facts=(replace(fact, status="disputed"),),
            )

        text = KnowledgeFactRenderer().render(snapshot)[0].text

        assert expected[case["kind"]] in text
        if case["kind"] == "rumor_observed":
            assert "不能视为官方确认" in text


def test_knowledge_rollout_is_shadow_preview_or_direct_send_only():
    action = AstrBotSocialRuntimeBridge._knowledge_reply_rollout_action

    assert action(RuntimeMode.SHADOW, "DIRECT_FAST", "strict") == "PREVIEW"
    assert action(RuntimeMode.SHADOW, "CONTINUATION", "grounded") == "PREVIEW"
    assert action(RuntimeMode.SOCIAL_RUNTIME, "DIRECT_FAST", "strict") == "SEND"
    assert action(RuntimeMode.SOCIAL_RUNTIME, "CONTINUATION", "grounded") == "SEND"
    assert action(RuntimeMode.SHADOW, "AMBIENT", "strict") == "BLOCK"
    assert action(RuntimeMode.SOCIAL_RUNTIME, "AMBIENT", "grounded") == "BLOCK"
    assert action(RuntimeMode.SHADOW, "AMBIENT", "strict", True) == "PREVIEW"
    assert action(RuntimeMode.SOCIAL_RUNTIME, "AMBIENT", "grounded", True) == "SEND"
    assert action(RuntimeMode.SOCIAL_RUNTIME, "AMBIENT", "none") == "SEND"
