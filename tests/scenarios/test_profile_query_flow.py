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


def test_member_can_correct_delete_and_disable_own_profile(tmp_path):
    async def scenario():
        bridge = _bridge(tmp_path)
        await bridge.start()
        repository = bridge.manager.profile_retriever.repository
        repository.put_fact(_fact())
        repository.put_snapshot(_snapshot())
        corrected = await bridge.prepare_profile_command(
            _Event("纠正画像 1 现在不喝冷饮", "correct-1")
        )
        view_after_correction = await bridge.prepare_profile_command(
            _Event("查看我的画像", "query-after-correction")
        )
        facts_after_correction = repository.facts(
            "groupmate:default", "group-1", "member-1"
        )
        disabled = await bridge.prepare_profile_command(
            _Event("停止画像个性化", "disable-1")
        )
        context_while_disabled = bridge.manager.member_profile_context(
            bridge.translator.translate(_Event("普通消息", "message-1"))
        )
        deletion = await bridge.prepare_profile_command(
            _Event("删除画像 1", "delete-1")
        )
        await bridge.close()
        return (
            corrected,
            view_after_correction,
            facts_after_correction,
            disabled,
            context_while_disabled,
            deletion,
        )

    corrected, view, facts, disabled, context, deletion = asyncio.run(scenario())

    assert "已纠正" in corrected.text
    assert "现在不喝冷饮" in view.text
    assert "偏好与边界：喜欢冷饮" not in view.text
    assert [(item.summary, item.status) for item in facts] == [
        ("喜欢冷饮", "superseded"),
        ("现在不喝冷饮", "confirmed"),
    ]
    assert "已停止" in disabled.text
    assert context == ""
    assert "已删除" in deletion.text
