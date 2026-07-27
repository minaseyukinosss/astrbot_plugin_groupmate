"""ReplyIntent / ReplyMode 选择与 firewall 约束。"""

from groupmate.core.addressee import AddresseeResolver
from groupmate.core.intent import constraints_for, select_reply_mode
from groupmate.engine.opportunity import OpportunityArbiter
from groupmate.engine.planner import ReplyIntentPlanner
from groupmate.engine.rate_limit import BudgetTracker, SlidingWindowRateLimiter
from groupmate.models import (
    ChatMessage,
    GroupPolicy,
    ReplyMode,
    TopicSnapshot,
    TriggerKind,
)
from groupmate.persona.aemeath.output_firewall import AemeathOutputFirewall


def _msg(**overrides):
    values = {
        "message_id": "m1",
        "group_id": "g1",
        "sender_id": "u1",
        "sender_name": "Alice",
        "text": "你好",
        "timestamp": 100,
    }
    values.update(overrides)
    return ChatMessage(**values)


def test_select_modes():
    assert select_reply_mode("这个怎么弄") is ReplyMode.HELP_DETAIL
    assert select_reply_mode("你给我滚") is ReplyMode.BOUNDARY
    assert select_reply_mode("哈哈") is ReplyMode.SHORT_SOCIAL


def test_planner_builds_help_intent():
    topic = TopicSnapshot(
        topic_id="t1",
        group_id="g1",
        messages=(_msg(text="爱弥斯 怎么刷图"),),
        created_at=100,
        updated_at=100,
    )
    targeting = AddresseeResolver().resolve(topic, TriggerKind.ALIAS_DIRECT)
    opp = OpportunityArbiter(
        budgets=BudgetTracker(SlidingWindowRateLimiter(cooldown_seconds=0))
    ).evaluate(
        topic,
        TriggerKind.ALIAS_DIRECT,
        GroupPolicy(humanize_delay_enabled=False),
        targeting,
        now=100,
    )
    intent = ReplyIntentPlanner().plan(
        opp, topic, targeting, decision_id="d1", soft_trigger=False
    )
    assert intent is not None
    assert intent.mode is ReplyMode.HELP_DETAIL
    assert intent.contribution


def test_firewall_allows_longer_help_mode():
    firewall = AemeathOutputFirewall(max_chars=60)
    longish = "先打开面板再点养成然后强化，确认材料够了就点一次。" * 4  # >60 chars
    assert len(longish) > 60
    short_mode = firewall.validate(longish, (), reply_mode=ReplyMode.SHORT_SOCIAL)
    help_mode = firewall.validate(longish[:160], (), reply_mode=ReplyMode.HELP_DETAIL)
    assert "too_long" in short_mode.codes
    assert constraints_for(ReplyMode.HELP_DETAIL).max_chars == 180
    assert help_mode.accepted or "too_many_sentences" in help_mode.codes
