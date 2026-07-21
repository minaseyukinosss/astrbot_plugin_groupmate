from datetime import datetime

from groupmate.shadow_admin import (
    normalize_recent_limit,
    render_shadow_decisions,
    shadow_recent_response,
)


def decision(**overrides):
    values = {
        "decision_id": "complete-decision-id",
        "trigger": "candidate",
        "action": "respond",
        "confidence": 0.836,
        "reason_code": "useful_contribution",
        "would_rate_limit": False,
        "label": "unlabeled",
        "created_at": 0,
        "latest_message": None,
    }
    values.update(overrides)
    return values


def test_recent_limit_defaults_and_clamps_to_command_range():
    assert normalize_recent_limit(None) == 5
    assert normalize_recent_limit(0) == 1
    assert normalize_recent_limit(1) == 1
    assert normalize_recent_limit(10) == 10
    assert normalize_recent_limit(50) == 10


def test_render_shadow_decisions_returns_exact_empty_message():
    assert render_shadow_decisions([]) == "当前群暂无影子决策记录。"


def test_render_shadow_decisions_is_private_and_actionable():
    text = render_shadow_decisions(
        [
            decision(
                would_rate_limit=True,
                latest_message={
                    "sender": "raw-user-id",
                    "text": "  这个配装\n是不是还差点抗性？  ",
                },
            )
        ]
    )

    assert "当前群最近 1 条影子决策" in text
    assert "[1] {}".format(datetime.fromtimestamp(0).strftime("%Y-%m-%d %H:%M:%S")) in text
    assert "ID: complete-decision-id" in text
    assert (
        "判断: 回复 0.84 | 原因: useful_contribution | 标签: 未标注 | 会被限流"
        in text
    )
    assert "消息: 这个配装 是不是还差点抗性？" in text
    assert "raw-user-id" not in text
    assert (
        "标注：/groupmate_shadow_label <决策ID> "
        "<必须回复|可以回复|必须沉默|跳过>"
    ) in text


def test_render_shadow_decisions_keeps_only_valid_redacted_sender():
    text = render_shadow_decisions(
        [decision(latest_message={"sender": "成员12", "text": "你好"})]
    )

    assert "消息: 成员12：你好" in text


def test_render_shadow_decisions_limits_summary_to_eighty_characters():
    text = render_shadow_decisions(
        [decision(latest_message={"sender": "成员1", "text": "字" * 100})]
    )
    message_line = next(line for line in text.splitlines() if line.startswith("消息: "))
    summary = message_line[len("消息: ") :]

    assert len(summary) == 80
    assert summary.endswith("...")


def test_render_shadow_decisions_handles_missing_text_and_unknown_short_values():
    text = render_shadow_decisions(
        [
            decision(
                action="custom action\nvalue",
                reason_code="custom reason\nvalue",
                label="custom label",
                latest_message={"sender": "成员1", "text": "   "},
            )
        ]
    )

    assert "判断: custom action value 0.84" in text
    assert "原因: custom reason value" in text
    assert "标签: custom label" in text
    assert "消息: 未保存文本" in text


def test_render_shadow_decisions_keeps_complete_decision_id():
    decision_id = "d" * 129

    text = render_shadow_decisions([decision(decision_id=decision_id)])

    assert "ID: {}".format(decision_id) in text


def test_shadow_recent_response_requires_a_group_and_normalizes_limit():
    calls = []

    def lookup(group_id, limit):
        calls.append((group_id, limit))
        return []

    assert (
        shadow_recent_response(None, 5, lookup)
        == "只能在群聊中查看影子决策记录。"
    )
    assert shadow_recent_response("group-1", 50, lookup) == "当前群暂无影子决策记录。"
    assert calls == [("group-1", 10)]


def test_shadow_recent_response_hides_lookup_errors():
    def lookup(group_id, limit):
        raise RuntimeError("database path and details")

    assert (
        shadow_recent_response("group-1", 5, lookup)
        == "影子决策记录暂时无法读取。"
    )
