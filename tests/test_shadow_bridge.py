import asyncio

from groupmate.astrbot_adapter import AstrBotBridge
from groupmate.config import PluginSettings
from groupmate.evaluation.models import ShadowRecord
from groupmate.evaluation.shadow import ShadowWorkflow
from groupmate.workflow import CognitiveWorkflow


class FakeContext:
    pass


def shadow_record(decision_id, group_hash, created_at):
    return ShadowRecord(
        decision_id=decision_id,
        group_hash=group_hash,
        sender_hash="sender-hash",
        trigger="candidate",
        action="ignore",
        confidence=0.2,
        reason_code="not_useful",
        would_rate_limit=False,
        features={"message_count": 1},
        context=None,
        model_id="model-a",
        policy_version="1",
        latency_ms=1.0,
        error_code=None,
        created_at=created_at,
        expires_at=created_at + 100,
    )


def test_bridge_uses_shadow_workflow_when_enabled(tmp_path):
    async def scenario():
        bridge = AstrBotBridge(
            FakeContext(),
            PluginSettings(shadow_mode=True, decision_provider="judge"),
            tmp_path,
        )
        workflow = bridge._workflow_for("g1")
        assert isinstance(workflow, ShadowWorkflow)
        assert not hasattr(workflow, "generation_model")
        assert bridge.status()["shadow_mode"] is True
        assert (tmp_path / "shadow_hmac.key").exists()
        await bridge.close()

    asyncio.run(scenario())


def test_bridge_keeps_formal_cognitive_workflow_by_default(tmp_path):
    async def scenario():
        bridge = AstrBotBridge(FakeContext(), PluginSettings(), tmp_path)
        assert isinstance(bridge._workflow_for("g1"), CognitiveWorkflow)
        assert bridge.status()["shadow_mode"] is False
        await bridge.close()

    asyncio.run(scenario())


def test_bridge_maps_chinese_shadow_labels(tmp_path):
    async def scenario():
        bridge = AstrBotBridge(FakeContext(), PluginSettings(shadow_mode=True), tmp_path)
        assert bridge.label_shadow_decision("missing", "必须回复") is False
        assert bridge.normalize_shadow_label("必须回复") == "must_respond"
        assert bridge.normalize_shadow_label("可以回复") == "may_respond"
        assert bridge.normalize_shadow_label("必须沉默") == "must_silence"
        assert bridge.normalize_shadow_label("跳过") == "skipped"
        assert bridge.normalize_shadow_label("随便") is None
        await bridge.close()

    asyncio.run(scenario())


def test_bridge_reads_recent_shadow_decisions_for_current_group_only(tmp_path):
    async def scenario():
        bridge = AstrBotBridge(FakeContext(), PluginSettings(shadow_mode=True), tmp_path)
        bridge.memory.save_shadow_decision(
            shadow_record("group-one", bridge._shadow_hasher.digest("g1"), 10)
        )
        bridge.memory.save_shadow_decision(
            shadow_record("group-two", bridge._shadow_hasher.digest("g2"), 20)
        )

        rows = bridge.recent_shadow_decisions("g1", 5)

        assert [row["decision_id"] for row in rows] == ["group-one"]
        await bridge.close()

    asyncio.run(scenario())


def test_bridge_read_does_not_create_missing_shadow_key(tmp_path):
    async def scenario():
        bridge = AstrBotBridge(FakeContext(), PluginSettings(shadow_mode=False), tmp_path)

        assert bridge.recent_shadow_decisions("g1", 5) == []
        assert not (tmp_path / "shadow_hmac.key").exists()
        await bridge.close()

    asyncio.run(scenario())
