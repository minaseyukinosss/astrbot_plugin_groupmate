from __future__ import annotations

import pytest

from groupmate.adapters.affection_query import is_affection_query
from groupmate.adapters.astrbot_bridge import AstrBotSocialRuntimeBridge
from groupmate.settings import SocialRuntimeSettings
from groupmate.social_runtime.contracts import RuntimeMode
from groupmate.social_runtime.society.relationships import (
    PublicAffection,
    RelationshipStage,
)


class FakeSociety:
    def relationship_snapshot(self, persona_id, group_id, subject_id):
        assert persona_id == "groupmate:default"
        assert group_id == "g-1"
        return None, PublicAffection(12.3, RelationshipStage.KNOWS)


class FakeManager:
    def __init__(self):
        self.society = FakeSociety()
        self.ingested = []

    def group_mode(self, group_id):
        return RuntimeMode.SHADOW if group_id == "g-1" else RuntimeMode.OFF


@pytest.mark.parametrize(
    "text",
    (
        "查看好感度",
        "查询好感度",
        "  查看好感度\n",
        "查询好感度　",
    ),
)
def test_affection_query_match_is_exact_after_platform_normalization(text):
    assert is_affection_query(text) is True


@pytest.mark.parametrize("text", ("查看 好感度", "帮我查看好感度", "查看好感度呀", "好感度"))
def test_affection_query_does_not_fuzzily_claim_chat(text):
    assert is_affection_query(text) is False


def test_bridge_prepares_query_without_entering_social_or_model_chain(tmp_path):
    settings = SocialRuntimeSettings.from_mapping(
        {
            "enabled_groups": ["g-1"],
            "runtime_mode": "SHADOW",
            "generation_provider": "provider:text",
        }
    )
    bridge = AstrBotSocialRuntimeBridge(object(), settings, tmp_path, clock=lambda: 100)
    manager = FakeManager()
    bridge._manager = manager
    bridge._started = True
    event = {
        "message_id": "query-1",
        "group_id": "g-1",
        "group_name": "测试群",
        "user_id": "10001234",
        "self_id": "bot-1",
        "time": 100,
        "sender": {"card": "夏夏"},
        "message": [{"type": "text", "data": {"text": "查看好感度"}}],
    }

    result = bridge.prepare_affection_query(event)

    assert result is not None
    assert result.leaderboard.group_name == "测试群"
    assert result.leaderboard.requester.display_name == "夏夏"
    assert result.leaderboard.requester.score == 12.3
    assert result.pages
    assert manager.ingested == []
