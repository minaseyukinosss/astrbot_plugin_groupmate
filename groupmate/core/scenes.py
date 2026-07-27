"""Deterministic interaction scenes derived from observable user behavior."""

from __future__ import annotations

import re
from dataclasses import dataclass

from ..models import ChatMessage, InteractionScene, QuoteMode, TriggerKind


_TASK_REQUEST = re.compile(
    r"(?:帮我|帮忙|麻烦你|请你|给我).{0,16}(?:看|查|找|搜|画|生成|识别|解析|翻译|评估|审核|改|设置|删除|发送|导出|执行|处理|整理|下载|上传|绑定)"
)
_SOCIAL_RESPONSE = re.compile(
    r"(?:谢谢|感谢|抱歉|对不起|厉害|真棒|好强|喜欢你|爱你|摸摸|捏捏|亲亲|给你|送你|吃点|喝点|🥛|🍯|🍨)"
)


@dataclass(frozen=True)
class ScenePolicy:
    hard_priority: bool
    quote_mode: QuoteMode

    def should_quote(self, *, interleaved: bool = False) -> bool:
        if self.quote_mode is QuoteMode.ALWAYS:
            return True
        if self.quote_mode is QuoteMode.WHEN_INTERLEAVED:
            return bool(interleaved)
        return False


_POLICIES = {
    InteractionScene.DIRECT_ADDRESS: ScenePolicy(True, QuoteMode.ALWAYS),
    InteractionScene.REPLY_TO_BOT: ScenePolicy(True, QuoteMode.ALWAYS),
    InteractionScene.ACTIVE_CONTINUATION: ScenePolicy(
        True, QuoteMode.WHEN_INTERLEAVED
    ),
    InteractionScene.SOCIAL_RESPONSE: ScenePolicy(
        False, QuoteMode.WHEN_INTERLEAVED
    ),
    InteractionScene.AMBIENT_CONTRIBUTION: ScenePolicy(False, QuoteMode.NEVER),
    InteractionScene.TASK_REQUEST: ScenePolicy(True, QuoteMode.ALWAYS),
}
_HARD_TRIGGERS = frozenset(
    {
        TriggerKind.NATIVE_DIRECT,
        TriggerKind.ALIAS_DIRECT,
        TriggerKind.COPIED_AT,
        TriggerKind.CONTINUATION,
    }
)


def classify_scene(trigger: TriggerKind, message: ChatMessage) -> InteractionScene:
    if message.reply_to_bot:
        return InteractionScene.REPLY_TO_BOT
    if trigger in (
        TriggerKind.NATIVE_DIRECT,
        TriggerKind.ALIAS_DIRECT,
        TriggerKind.COPIED_AT,
    ) and _TASK_REQUEST.search(message.text or ""):
        return InteractionScene.TASK_REQUEST
    if trigger is TriggerKind.CONTINUATION:
        return InteractionScene.ACTIVE_CONTINUATION
    if trigger in (
        TriggerKind.NATIVE_DIRECT,
        TriggerKind.ALIAS_DIRECT,
        TriggerKind.COPIED_AT,
    ) and _SOCIAL_RESPONSE.search(message.text or ""):
        return InteractionScene.SOCIAL_RESPONSE
    if trigger in (
        TriggerKind.NATIVE_DIRECT,
        TriggerKind.ALIAS_DIRECT,
        TriggerKind.COPIED_AT,
    ):
        return InteractionScene.DIRECT_ADDRESS
    if trigger is TriggerKind.ALIAS_MENTION and _SOCIAL_RESPONSE.search(
        message.text or ""
    ):
        return InteractionScene.SOCIAL_RESPONSE
    return InteractionScene.AMBIENT_CONTRIBUTION


def policy_for_scene(scene: InteractionScene) -> ScenePolicy:
    return _POLICIES[scene]


def is_hard_scene(scene: InteractionScene, trigger: TriggerKind) -> bool:
    return _POLICIES[scene].hard_priority or trigger in _HARD_TRIGGERS
