from __future__ import annotations

import asyncio

from groupmate.adapters.astrbot_bridge import AstrBotSocialRuntimeBridge
from groupmate.settings import SocialRuntimeSettings
from groupmate.social_runtime.profile.contracts import ProfileFact, ProfileSnapshot


class _Event(dict):
    def __init__(self, text: str, event_id: str):
        super().__init__(
            message_id=event_id,
            group_id="group-1",
            user_id="member-1",
            self_id="bot-1",
            time=200,
            sender={"card": "复读斥候"},
            message=[{"type": "text", "data": {"text": text}}],
        )


def _fact(summary="喜欢冷饮"):
    return ProfileFact(
        fact_id="fact-1",
        persona_id="groupmate:default",
        group_id="group-1",
        subject_id="member-1",
        category="preference",
        summary=summary,
        source_kind="self_statement",
        source_actor_id="member-1",
        source_event_ids=("old-event",),
        confidence=0.94,
        status="confirmed",
        evidence_count=1,
        valid_from=100,
        injectable=True,
    )


def _snapshot():
    return ProfileSnapshot(
        persona_id="groupmate:default",
        group_id="group-1",
        subject_id="member-1",
        one_line_portrait="会持续追问到问题真正落地",
        group_roles=("体验把关者",),
        individual_fingerprints=("会持续追问到问题真正落地",),
        preferences_and_boundaries=("喜欢冷饮", "不接受看不懂的结果"),
        representative_episode_ids=(),
        relationship_summary="",
        maturity="forming",
        source_revision=1,
        generated_at=150,
    )


def _bridge(tmp_path):
    settings = SocialRuntimeSettings.from_mapping(
        {
            "enabled_groups": ["group-1"],
            "runtime_mode": "SHADOW",
            "generation_provider": "provider:text",
            "profile_enabled": False,
        }
    )
    return AstrBotSocialRuntimeBridge(object(), settings, tmp_path, clock=lambda: 200)


def test_profile_query_reads_only_current_member_without_model(tmp_path):
    async def scenario():
        bridge = _bridge(tmp_path)
        await bridge.start()
        repository = bridge.manager.profile_retriever.repository
        repository.put_fact(_fact())
        repository.put_snapshot(_snapshot())
        result = await bridge.prepare_profile_command(_Event("查看我的画像", "query-1"))
        await bridge.close()
        return result

    result = asyncio.run(scenario())

    assert result is not None
    assert "我的画像" in result.text
    assert "会持续追问到问题真正落地" in result.text
    assert "[1] 喜欢冷饮" in result.text
    assert "纠正画像" not in result.text
    assert "删除画像" not in result.text


def test_former_member_write_commands_do_not_mutate_profile(tmp_path):
    async def scenario():
        bridge = _bridge(tmp_path)
        await bridge.start()
        repository = bridge.manager.profile_retriever.repository
        repository.put_fact(_fact())
        repository.put_snapshot(_snapshot())
        corrected = await bridge.prepare_profile_command(
            _Event("纠正画像 1 现在不喝冷饮", "correct-1")
        )
        disabled = await bridge.prepare_profile_command(
            _Event("停止画像个性化", "disable-1")
        )
        deletion = await bridge.prepare_profile_command(
            _Event("删除画像 1", "delete-1")
        )
        facts = repository.facts("groupmate:default", "group-1", "member-1")
        personalization_enabled = repository.personalization_enabled(
            "groupmate:default", "group-1", "member-1"
        )
        await bridge.close()
        return corrected, disabled, deletion, facts, personalization_enabled

    corrected, disabled, deletion, facts, enabled = asyncio.run(scenario())

    assert corrected is disabled is deletion is None
    assert [(item.summary, item.status) for item in facts] == [
        ("喜欢冷饮", "confirmed")
    ]
    assert enabled is True
