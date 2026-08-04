"""ReplyMode 选择与模式表达约束。"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional, Sequence

from ..models import ReplyMode

_BOUNDARY = re.compile(
    r"(老婆|老公|亲一下|摸摸|去死|傻逼|滚|骚扰)",
    re.IGNORECASE,
)
_HELP = re.compile(
    r"(怎么|怎么办|如何|步骤|攻略|教程|帮我|解释|为什么|什么意思)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class ModeConstraints:
    max_chars: int
    max_sentences: int
    length_hint: str
    speak_note: str


_CONSTRAINTS = {
    ReplyMode.SHORT_SOCIAL: ModeConstraints(
        max_chars=60,
        max_sentences=2,
        length_hint="默认 1 条，目标约 15–30 字；至多 2 句极短，硬上限 60 字。",
        speak_note=(
            "【开口纪律·短社交】若话不冲你、只是路过提及名字、或没有一句自然短反应，"
            "只输出 <SILENCE>。一旦开口：一条为主、一件事、目标约 15–30 字；"
            "少全标点，可软收尾呢/呀/啦/哦；禁单字起手再接下文、禁你呢空转、禁旁白长篇。"
        ),
    ),
    ReplyMode.HELP_DETAIL: ModeConstraints(
        max_chars=180,
        max_sentences=4,
        length_hint="允许 120–180 字或少量分段；事实覆盖优先于卖萌；禁止客服开场。",
        speak_note=(
            "【开口纪律·帮助】对方在问怎么做/解释。给可执行短答或步骤；"
            "可稍长但仍像群聊，禁止客服腔和无关总结。"
        ),
    ),
    ReplyMode.BOUNDARY: ModeConstraints(
        max_chars=60,
        max_sentences=2,
        length_hint="简短明确；不羞辱、不长篇说教。",
        speak_note=(
            "【开口纪律·边界】对方越界或冒犯。一句短拒即可；不羞辱、不升级敌意、不长篇。"
        ),
    ),
    ReplyMode.TASK_RESULT: ModeConstraints(
        max_chars=120,
        max_sentences=3,
        length_hint="把任务结果说成人话，不暴露内部过程。",
        speak_note="【开口纪律·任务结果】用角色口吻交代结果；不暴露子智能体过程。",
    ),
}

_HARD_SPEAK_NOTE = (
    "【开口纪律】对方在直接找你或续聊。只回群聊短句：默认一条约 15–30 字，至多两条极短；"
    "少全标点，可软收尾呢/呀/啦/哦；只叫名字→只应声（在呢/嗯），禁止你呢空转与反问；"
    "「在干嘛」→一句短答即可；禁单字起手再接下文，禁括号/星号旁白，禁小说分镜。"
)


def select_reply_mode(text: str, *, soft_trigger: bool = True) -> ReplyMode:
    cleaned = text or ""
    if _BOUNDARY.search(cleaned):
        return ReplyMode.BOUNDARY
    if _HELP.search(cleaned):
        return ReplyMode.HELP_DETAIL
    if not soft_trigger:
        return ReplyMode.SHORT_SOCIAL
    return ReplyMode.SHORT_SOCIAL


def constraints_for(mode: ReplyMode) -> ModeConstraints:
    return _CONSTRAINTS.get(mode, _CONSTRAINTS[ReplyMode.SHORT_SOCIAL])


def speak_note_for(
    mode: Optional[ReplyMode],
    *,
    soft_trigger: bool,
) -> str:
    if not soft_trigger:
        if mode is ReplyMode.HELP_DETAIL:
            return constraints_for(ReplyMode.HELP_DETAIL).speak_note
        if mode is ReplyMode.BOUNDARY:
            return constraints_for(ReplyMode.BOUNDARY).speak_note
        return _HARD_SPEAK_NOTE
    if mode is None:
        return constraints_for(ReplyMode.SHORT_SOCIAL).speak_note
    return constraints_for(mode).speak_note


def max_chars_for_mode(
    mode: Optional[ReplyMode],
) -> int:
    if mode is None:
        return constraints_for(ReplyMode.SHORT_SOCIAL).max_chars
    return max(1, constraints_for(mode).max_chars)


def has_image_capability(image_urls: Sequence[str], mode: ReplyMode) -> bool:
    if mode is ReplyMode.BOUNDARY:
        return False
    return bool(image_urls)
