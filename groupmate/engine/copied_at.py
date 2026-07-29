"""Copied-text @ handling that never enters participation decisions."""

from __future__ import annotations

from ..models import TriggerKind

_DEFAULT_ALIAS = "爱弥斯"
_TIP_TEMPLATE = "复制出来的 @ 不算数哦，要叫{name}的话，用真正的 @。"


def is_copied_at(trigger: TriggerKind) -> bool:
    """is_copied_at（是否复制文本 @）：只匹配 COPIED_AT 触发。"""

    return trigger is TriggerKind.COPIED_AT


def copied_at_tip(alias: str) -> str:
    """copied_at_tip（复制 @ 提示）：固定爱弥斯风格短提示。"""

    name = str(alias or "").strip() or _DEFAULT_ALIAS
    return _TIP_TEMPLATE.format(name=name)
