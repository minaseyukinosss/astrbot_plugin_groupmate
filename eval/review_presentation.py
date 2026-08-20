"""Plain-language presentation of evaluation labels for human review."""

from __future__ import annotations

from typing import Mapping

from .schema import EvaluationLabel


_INTENT_LABELS = {
    "respond": "回应当前成员的问题或话题",
    "interrupt": "不要在不合适的时机插话",
    "misaddress": "不要回应错对象",
}
_MODALITY_LABELS = {
    "text": "使用文字回应",
    "media": "可以使用图片或其他媒体回应",
}
_SENSITIVITY_LABELS = {
    "group": "只依据当前群聊语境判断",
}


def _present_code(code: str, labels: Mapping[str, str]) -> dict[str, str]:
    return {
        "code": code,
        "label": labels.get(code, f"{code}（未定义标签）"),
    }


def _expiry_label(milliseconds: int, *, action: bool) -> str:
    if not action or milliseconds == 0:
        return "无需回应"
    seconds = milliseconds / 1_000
    value = f"{seconds:.3f}".rstrip("0").rstrip(".")
    return f"在 {value} 秒内回应"


def present_review_label(label: Mapping[str, object]) -> dict[str, object]:
    validated = EvaluationLabel.from_dict(label)
    if not validated.action:
        headline = "建议保持沉默"
    elif validated.target:
        headline = f"建议回应 {validated.target}"
    else:
        headline = "建议参与当前话题"
    return {
        "headline": headline,
        "attention": {
            "active": validated.attention,
            "label": (
                "值得留意这条消息"
                if validated.attention
                else "无需进入注意范围"
            ),
        },
        "action": {
            "active": validated.action,
            "label": (
                "应产生可见回应"
                if validated.action
                else "不应产生可见回应"
            ),
        },
        "expiry": {
            "milliseconds": validated.expires_after_ms,
            "label": _expiry_label(
                validated.expires_after_ms,
                action=validated.action,
            ),
        },
        "acceptable": [
            _present_code(code, _INTENT_LABELS)
            for code in validated.acceptable_intents
        ],
        "unacceptable": [
            _present_code(code, _INTENT_LABELS)
            for code in validated.unacceptable_intents
        ],
        "modalities": [
            _present_code(code, _MODALITY_LABELS) for code in validated.modalities
        ],
        "sensitivity": _present_code(
            validated.sensitivity,
            _SENSITIVITY_LABELS,
        ),
    }


__all__ = ("present_review_label",)
