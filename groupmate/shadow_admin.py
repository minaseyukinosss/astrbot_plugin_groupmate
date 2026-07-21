"""Safe, framework-free rendering for shadow decision administration."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Callable, Dict, Iterable, List, Optional


EMPTY_MESSAGE = "当前群暂无影子决策记录。"
GROUP_ONLY_MESSAGE = "只能在群聊中查看影子决策记录。"
READ_ERROR_MESSAGE = "影子决策记录暂时无法读取。"
LABEL_COMMAND = (
    "标注：/groupmate_shadow_label <决策ID> "
    "<必须回复|可以回复|必须沉默|跳过>"
)

_ACTION_LABELS = {
    "respond": "回复",
    "ignore": "沉默",
    "bypass": "旁路",
}
_REVIEW_LABELS = {
    "unlabeled": "未标注",
    "must_respond": "必须回复",
    "may_respond": "可以回复",
    "must_silence": "必须沉默",
    "skipped": "跳过",
}
_REDACTED_SENDER = re.compile(r"^成员\d+$")


def normalize_recent_limit(value: Any, default: int = 5) -> int:
    try:
        limit = int(default if value is None else value)
    except (TypeError, ValueError):
        limit = int(default)
    return max(1, min(10, limit))


def render_shadow_decisions(rows: Iterable[Dict[str, Any]]) -> str:
    decisions = list(rows)
    if not decisions:
        return EMPTY_MESSAGE

    lines: List[str] = ["当前群最近 {} 条影子决策：".format(len(decisions))]
    for index, row in enumerate(decisions, 1):
        action = _ACTION_LABELS.get(str(row.get("action", ""))) or _safe_short(
            row.get("action")
        )
        label = _REVIEW_LABELS.get(str(row.get("label", ""))) or _safe_short(
            row.get("label")
        )
        try:
            confidence = float(row.get("confidence", 0.0))
        except (TypeError, ValueError):
            confidence = 0.0
        judgment = "判断: {} {:.2f} | 原因: {} | 标签: {}".format(
            action,
            confidence,
            _safe_short(row.get("reason_code")),
            label,
        )
        if bool(row.get("would_rate_limit")):
            judgment += " | 会被限流"
        lines.extend(
            [
                "",
                "[{}] {}".format(index, _local_timestamp(row.get("created_at"))),
                "ID: {}".format(_complete_decision_id(row.get("decision_id"))),
                judgment,
                "消息: {}".format(_message_summary(row.get("latest_message"))),
            ]
        )
    lines.extend(["", LABEL_COMMAND])
    return "\n".join(lines)


def shadow_recent_response(
    group_id: Optional[str],
    requested_limit: Any,
    lookup: Callable[[str, int], Iterable[Dict[str, Any]]],
) -> str:
    if group_id is None or not str(group_id).strip():
        return GROUP_ONLY_MESSAGE
    try:
        rows = lookup(str(group_id), normalize_recent_limit(requested_limit))
    except Exception:
        return READ_ERROR_MESSAGE
    return render_shadow_decisions(rows)


def _safe_short(value: Any, limit: int = 40) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if not text:
        return "未知"
    if len(text) > limit:
        return text[: max(0, limit - 3)] + "..."
    return text


def _complete_decision_id(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip() or "未知"


def _local_timestamp(value: Any) -> str:
    try:
        return datetime.fromtimestamp(int(value)).strftime("%Y-%m-%d %H:%M:%S")
    except (OSError, OverflowError, TypeError, ValueError):
        return "时间未知"


def _message_summary(value: Any) -> str:
    if not isinstance(value, dict):
        return "未保存文本"
    text = value.get("text")
    if not isinstance(text, str):
        return "未保存文本"
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return "未保存文本"
    sender = value.get("sender")
    if isinstance(sender, str) and _REDACTED_SENDER.fullmatch(sender):
        text = "{}：{}".format(sender, text)
    if len(text) > 80:
        return text[:77] + "..."
    return text
