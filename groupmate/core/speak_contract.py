"""开口契约：模型可合法沉默，框架过滤后不发送。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import FrozenSet, Tuple

# 与 gsuid_core SILENCE_MARKERS 对齐的可识别标记
SILENCE_MARKERS: FrozenSet[str] = frozenset(
    {
        "<SILENCE>",
        "</SILENCE>",
        "SILENCE",
        "[SILENCE]",
        "（沉默）",
        "(沉默)",
    }
)


def is_silence(text: str) -> bool:
    cleaned = (text or "").strip()
    if not cleaned:
        return True
    upper = cleaned.upper()
    if cleaned in SILENCE_MARKERS or upper in {m.upper() for m in SILENCE_MARKERS}:
        return True
    # 允许整段仅含沉默标记与空白
    compact = "".join(cleaned.split())
    if compact.upper() in {"<SILENCE>", "[SILENCE]", "SILENCE", "（沉默）", "(沉默)"}:
        return True
    if compact.startswith("<SILENCE>") and compact.endswith("</SILENCE>"):
        return True
    return False


@dataclass(frozen=True)
class SpeakDecision:
    should_send: bool
    text: str
    reason: str


class SpeakContract:
    """解析生成结果：SILENCE → 不发送。"""

    HARD_TRIGGER_NOTE = (
        "【开口纪律】对方在直接找你或续聊。只回群聊短句：默认一条，至多两条极短；"
        "只叫名字→只应声（在呢/诶/嗯），禁止反问、禁止扯无关图片或编造长篇日常；"
        "「在干嘛」→一句短答即可，禁止括号/星号动作旁白，禁止小说分镜。"
    )

    SOFT_TRIGGER_NOTE = (
        "【开口纪律】若话不冲你、只是路过提及名字、或你没有一句自然短反应可加，"
        "只输出 <SILENCE>，不要解释。被直接呼叫或明显续聊时不要沉默；"
        "一旦开口同样禁止舞台旁白与长篇编造。"
    )

    @classmethod
    def soft_path(cls) -> bool:
        return True

    @classmethod
    def note_for(cls, soft_trigger: bool) -> str:
        return cls.SOFT_TRIGGER_NOTE if soft_trigger else cls.HARD_TRIGGER_NOTE

    @classmethod
    def resolve(cls, raw_text: str) -> SpeakDecision:
        text = (raw_text or "").strip()
        if is_silence(text):
            return SpeakDecision(should_send=False, text="", reason="model_silence")
        return SpeakDecision(should_send=True, text=text, reason="speak")
