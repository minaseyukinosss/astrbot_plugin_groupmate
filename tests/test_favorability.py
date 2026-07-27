"""好感档位：分档、注入、持久化与工作流加减分。"""

import asyncio

from groupmate.core.context_assembly import ContextAssembly
from groupmate.core.favorability import (
    TIER_CLOSE,
    TIER_COLD,
    TIER_DISTANT,
    delta_for_turn,
    format_favorability_perception,
    label_for,
    seed_score_for_relationship,
    tier_for,
)
from groupmate.core.history_format import format_relationship_line
from groupmate.engine.rate_limit import SlidingWindowRateLimiter
from groupmate.engine.workflow import CognitiveWorkflow
from groupmate.memory import SQLiteMemoryStore
from groupmate.models import TriggerKind
from groupmate.persona.aemeath import (
    DEFAULT_RELATIONSHIPS,
    PACK_DIR,
    AemeathOutputFirewall,
    AemeathPersonaProvider,
)
from tests.fakes import (
    FakeClock,
    FakeMemoryRepository,
    FakePlatform,
    NullVision,
    StaticGenerationModel,
)


def test_tier_and_label_boundaries():
    assert tier_for(-1) == TIER_COLD
    assert tier_for(0) == TIER_DISTANT
    assert tier_for(49) == TIER_DISTANT
    assert tier_for(50) == TIER_CLOSE
    assert label_for(80) == "熟人/亲昵"
    assert label_for(-10) == "厌恶/警惕"
    assert label_for(20) == "陌生/社交距离"


def test_seed_from_configured_relationship():
    assert seed_score_for_relationship("最亲近") == 80
    assert seed_score_for_relationship("闺蜜") == 60
    assert seed_score_for_relationship("普通群友") is None


def test_delta_for_turn_offense_and_send():
    assert delta_for_turn(sent=True, soft_trigger=False, latest_text="你好") == 2
    assert delta_for_turn(sent=True, soft_trigger=True, latest_text="路过") == 1
    assert delta_for_turn(sent=False, soft_trigger=False, latest_text="滚") == -8
    assert delta_for_turn(sent=True, soft_trigger=False, latest_text="老婆") == -6


def test_perception_line_hides_numeric_score():
    line = format_favorability_perception(
        55, relationship="闺蜜", suggested_address="小A"
    )
    assert "熟人/亲昵" in line
    assert "55" not in line
    assert "闺蜜" in line


def test_relationship_line_includes_favorability_tier():
    line = format_relationship_line(
        "u1",
        "Alice",
        {"u1": ("闺蜜", "小A")},
        favorability=60,
    )
    assert "熟人/亲昵" in line
    assert "念出好感数字" in line


def test_assembly_injects_favorability_into_user(topic_snapshot):
    user = ContextAssembly(
        pack_dir=PACK_DIR,
        relationships=DEFAULT_RELATIONSHIPS,
        character_name="爱弥斯",
    ).build_user(topic_snapshot, [], favorability=80)
    assert "<relationship_line>" in user
    rel = user.split("<relationship_line>")[1].split("</relationship_line>")[0]
    assert "熟人/亲昵" in rel
    assert "80" not in rel


def test_sqlite_favorability_persist_and_adjust(tmp_path):
    store = SQLiteMemoryStore(tmp_path / "fav.db")
    assert store.schema_version() == 9
    assert store.get_favorability("g", "u") is None
    assert store.set_favorability("g", "u", 10, updated_at=1) == 10
    assert store.adjust_favorability("g", "u", 5, updated_at=2) == 15
    assert store.adjust_favorability("g", "u", -200, updated_at=3) == -100
    store.close()


def test_workflow_seeds_and_increments_favorability(topic_snapshot, balanced_policy):
    memory = FakeMemoryRepository()
    workflow = CognitiveWorkflow(
        generation_model=StaticGenerationModel("在呢。"),
        vision=NullVision(),
        platform=FakePlatform(),
        memory=memory,
        persona=AemeathPersonaProvider(),
        output_guard=AemeathOutputFirewall(max_chars=60),
        rate_limiter=SlidingWindowRateLimiter(hourly_limit=6, cooldown_seconds=0),
        clock=FakeClock(200),
    )
    outcome = asyncio.run(
        workflow.evaluate(topic_snapshot, TriggerKind.ALIAS_DIRECT, balanced_policy)
    )
    assert outcome.sent is True
    sender_id = topic_snapshot.latest.sender_id
    # 无配置关系 → 种子 0，硬触发发送 +2
    assert memory.get_favorability(topic_snapshot.group_id, sender_id) == 2


def test_persona_mentions_favorability_logic():
    text = AemeathPersonaProvider().system_text()
    assert "Favorability Logic" in text
    assert "[-100 至 -1]" in text
    assert "[50 至 100]" in text
    assert "厌恶/警惕" in text
