import asyncio

from groupmate.astrbot_adapter import AstrBotBridge
from groupmate.config import PluginSettings
from groupmate.evaluation.shadow import ShadowWorkflow
from groupmate.workflow import CognitiveWorkflow


class FakeContext:
    pass


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
