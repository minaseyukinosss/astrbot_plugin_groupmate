"""Dynamic group-card fun feature.

The feature is deliberately stateful but shallow: it changes only the bot's
own group card and records a short-lived explanation for later questions.
It must stay out of core memory, reply ownership, and relationship learning.
"""

from __future__ import annotations

from collections import Counter
import time
from typing import Any, Dict, Iterable, Mapping, Optional, Sequence, Tuple

from ...models import ChatMessage
from ..contracts import (
    FunActionPort,
    FunFeatureContext,
    FunFeaturePlan,
)


class DynamicCardFeature:
    feature_id = "dynamic_card"

    def __init__(self, settings) -> None:
        self.settings = settings

    def due(self, context: FunFeatureContext) -> bool:
        if not getattr(self.settings, "enabled", False):
            return False
        if context.paused and not context.force:
            return False
        active = context.active_event
        if context.force:
            return True
        if active is None:
            return True
        private = active.private_context or {}
        next_refresh_at = int(private.get("next_refresh_at") or 0)
        if next_refresh_at > 0:
            return int(context.now) >= next_refresh_at
        interval = int(getattr(self.settings, "min_interval_minutes", 90) or 90) * 60
        return int(context.now) - int(active.created_at or 0) >= max(60, interval)

    def plan(self, context: FunFeatureContext) -> Optional[FunFeaturePlan]:
        if not self.due(context):
            return None
        recent = tuple(context.recent_messages or ())[-24:]
        suffix, scene, scene_info = self._select_suffix(
            recent,
            force=context.force,
            now=context.now,
        )
        if not suffix:
            return None
        base_name = str(getattr(self.settings, "base_name", "") or "爱弥斯").strip()
        separator = str(getattr(self.settings, "separator", "") or "丨").strip() or "丨"
        card = "{}{}{}".format(base_name[:12], separator[:2], suffix)
        next_refresh_at = self._next_refresh_at(context, scene, suffix)
        return FunFeaturePlan(
            feature_id=self.feature_id,
            group_id=context.group_id,
            action_kind="set_own_group_card",
            public_value=card[:32],
            private_context={
                "card_suffix": suffix,
                "scene": scene,
                "source_summary": scene_info.get("source_summary") or "爱弥斯顺手换了一个星炬学院日常状态牌",
                "visible_cause": scene_info.get("visible_cause") or "星炬学院日常状态刷新",
                "answer_angle": scene_info.get("answer_angle") or "只把名片解释成爱弥斯在星炬学院的日常状态，不解释成在说某个人",
                "reply_cues": tuple(scene_info.get("reply_cues") or ()),
                "social_intent": "把爱弥斯自己的群名片当星炬学院生活状态牌，不指向任何群友",
                "reply_boundary": "只接星炬学院日常梗；不猜人、不点名、不复盘群友、不解释系统机制",
                "target_policy": "none",
                "next_refresh_at": next_refresh_at,
            },
            participants=(),
            expires_at=max(next_refresh_at + 600, int(context.now) + 3 * 3600),
        )

    async def apply(self, plan: FunFeaturePlan, actions: FunActionPort) -> str:
        if plan.action_kind != "set_own_group_card":
            return "unsupported_action"
        return await actions.set_own_group_card(plan.group_id, plan.public_value)

    @staticmethod
    def _select_suffix(
        messages: Sequence[ChatMessage],
        *,
        force: bool = False,
        now: int = 0,
    ) -> Tuple[str, str, Mapping[str, Any]]:
        human = [item for item in messages if not item.is_bot]
        if not human:
            if force:
                return _rhythm_choice(now, messages)
            return "", "", {}
        latest = human[-12:]
        texts = [str(item.text or "") for item in latest]
        sender_count = len({item.sender_id for item in latest if item.sender_id})
        mention_count = sum(1 for item in latest if item.mentions_bot or "爱弥斯" in str(item.text or ""))
        punctuation = sum(text.count("?") + text.count("？") + text.count("!") + text.count("！") for text in texts)
        repeated = _has_repeatish_text(texts)
        if mention_count >= 2:
            return _scene_choice("summoned", latest, now=now)
        if repeated:
            return _scene_choice("repeat_play", latest, now=now)
        if len(latest) >= 8 and sender_count >= 3:
            return _scene_choice("busy_chat", latest, now=now)
        if len(latest) >= 6 and sender_count >= 2 and punctuation >= 3:
            return _scene_choice("signal_rain", latest, now=now)
        if len(latest) >= 5:
            return _scene_choice("onlooker", latest, now=now)
        if force or len(latest) >= 2:
            return _rhythm_choice(now, latest)
        return "", "", {}

    def _next_refresh_at(self, context: FunFeatureContext, scene: str, suffix: str) -> int:
        min_minutes = int(getattr(self.settings, "min_interval_minutes", 90) or 90)
        max_minutes = int(getattr(self.settings, "max_interval_minutes", 180) or 180)
        min_minutes = max(30, min_minutes)
        max_minutes = max(min_minutes, max_minutes)
        spread = max_minutes - min_minutes
        if spread <= 0:
            offset_minutes = min_minutes
        else:
            seed = "{}:{}:{}:{}".format(context.group_id, context.now, scene, suffix)
            offset_minutes = min_minutes + (sum(ord(ch) for ch in seed) % (spread + 1))
        return int(context.now) + offset_minutes * 60

_SCENES: Dict[str, Dict[str, Any]] = {
    "academy_early": {
        "source_summary": "现在是星炬学院熄灯后的安静时段，爱弥斯像电子幽灵一样在校园系统里夜巡",
        "visible_cause": "星炬学院夜间状态刷新",
        "answer_angle": "说成自己在夜巡、查日志或偷偷续一关卡带，不关联任何群友",
        "reply_cues": (
            "熄灯后也能巡一下",
            "校园系统还亮着",
            "只是偷偷续一关",
        ),
        "normal": ("电子幽灵夜巡", "卡带续关中"),
    },
    "academy_morning": {
        "source_summary": "现在是星炬学院早间时段，爱弥斯正在赶去拉贝尔学部上课",
        "visible_cause": "星炬学院早间状态刷新",
        "answer_angle": "说成自己刚到校、赶早课或在翻学生档案，不关联任何群友",
        "reply_cues": (
            "早课要开始啦",
            "拉贝尔学部签到",
            "学生档案已打开",
        ),
        "normal": ("拉贝尔早课中", "学生档案打开"),
    },
    "academy_class": {
        "source_summary": "现在是星炬学院正课时段，爱弥斯在拉贝尔学部听课或做同步理论笔记",
        "visible_cause": "星炬学院课表状态刷新",
        "answer_angle": "说成自己在上课、记笔记或同步理论走神，不关联任何群友",
        "reply_cues": (
            "这节是同步理论",
            "笔记先记一半",
            "教授看过来了",
        ),
        "normal": ("拉贝尔听课中", "同步课走神中"),
    },
    "academy_noon": {
        "source_summary": "现在是星炬学院午饭时段，爱弥斯在食堂补能，可能还拿着特调嘻嘻果冻",
        "visible_cause": "星炬学院午间状态刷新",
        "answer_angle": "说成自己在食堂、午饭或嘻嘻果冻时间，不关联任何群友",
        "reply_cues": (
            "食堂今天也很神秘",
            "嘻嘻果冻补能",
            "午饭先救一下",
        ),
        "normal": ("嘻嘻果冻时间", "食堂补能中"),
    },
    "academy_afternoon": {
        "source_summary": "现在是星炬学院下午实践时段，爱弥斯可能在模拟舱训练、图书馆复习或写论文",
        "visible_cause": "星炬学院下午状态刷新",
        "answer_angle": "说成自己在模拟舱、图书馆或论文进度里切换，不关联任何群友",
        "reply_cues": (
            "模拟舱排队中",
            "图书馆复习",
            "论文先开个头",
        ),
        "normal": ("模拟舱训练中", "论文进度1%"),
    },
    "academy_evening": {
        "source_summary": "现在是星炬学院晚间自由时段，爱弥斯会去天台放纸飞机、练飞行雪绒的歌或拆隧者手办",
        "visible_cause": "星炬学院晚间状态刷新",
        "answer_angle": "说成自己在天台、纸飞机、练歌或手办时间，不关联任何群友",
        "reply_cues": (
            "纸飞机试飞",
            "飞行雪绒排练",
            "隧者手办擦灰",
        ),
        "normal": ("飞行雪绒排练", "纸飞机试飞中"),
    },
    "academy_late": {
        "source_summary": "现在是星炬学院夜间收尾时段，爱弥斯在校园里慢慢收起今天的热闹",
        "visible_cause": "星炬学院夜间状态刷新",
        "answer_angle": "说成自己在收尾、整理笔记或准备夜间巡课，不关联任何群友",
        "reply_cues": (
            "今天的笔记收一下",
            "星炬灯还亮着",
            "准备夜巡",
        ),
        "normal": ("夜间巡课前", "今日笔记封存"),
    },
    "summoned": {
        "source_summary": "爱弥斯刚才被连续叫到，像是从星炬学院课间被喊回来了",
        "visible_cause": "连续点名或提到爱弥斯",
        "answer_angle": "说成自己从课间或校园广播里听见了；不要猜是谁叫的",
        "reply_cues": (
            "我从课间过来了",
            "校园广播响了",
            "别急 我在",
        ),
        "normal": ("校园广播响应", "课间被叫到"),
    },
    "repeat_play": {
        "source_summary": "群里刚才出现短句复读或队形，爱弥斯把它当成星炬学院课堂记录",
        "visible_cause": "短句复读或队形太明显",
        "answer_angle": "说成自己在做课堂记录或队形观测；不要说谁带头、谁跟队",
        "reply_cues": (
            "课堂记录一下",
            "队形有点整齐",
            "同步率还挺高",
        ),
        "normal": ("同步率观测中", "队形记录员"),
    },
    "signal_rain": {
        "source_summary": "群里刚才问号、感叹号或短促接话变多，爱弥斯把它当成拉贝尔波形有点活跃",
        "visible_cause": "问号、感叹号或短促接话变多",
        "answer_angle": "说成自己在看波形或接收信号；不要判断谁急、谁有问题",
        "reply_cues": (
            "波形动起来了",
            "信号有点密",
            "先看一眼曲线",
        ),
        "normal": ("拉贝尔波动中", "信号观测中"),
    },
    "busy_chat": {
        "source_summary": "刚才群里多人连续说话，爱弥斯像在星炬学院走廊里追上课铃一样追消息",
        "visible_cause": "多人连续聊天",
        "answer_angle": "说成自己在追走廊消息或课间节奏，不评价任何人",
        "reply_cues": (
            "走廊突然热闹",
            "我在追进度",
            "课间消息太快",
        ),
        "normal": ("课间消息追赶中", "星炬走廊旁听"),
    },
    "onlooker": {
        "source_summary": "刚才群里有一小段动静，爱弥斯像从星炬学院走廊路过时看了一眼",
        "visible_cause": "短时间内话题跳转明显",
        "answer_angle": "说成自己从走廊路过或课间旁听，不复盘具体发生了什么",
        "reply_cues": (
            "我刚从走廊路过",
            "先旁听一下",
            "课间有点快",
        ),
        "normal": ("刚从走廊路过", "星炬旁听模式"),
    },
    "manual_refresh": {
        "source_summary": "手动刷新星炬学院日常状态牌，没有特指现场任何人",
        "visible_cause": "手动刷新",
        "answer_angle": "说成自己顺手翻了一下学生状态牌，不引出当事人",
        "reply_cues": (
            "学生状态牌翻一下",
            "没有说谁",
            "今天在星炬",
        ),
        "normal": ("星炬在校中", "学生状态更新"),
    },
}


def _scene_choice(
    scene: str,
    messages: Sequence[ChatMessage],
    *,
    now: int = 0,
) -> Tuple[str, str, Mapping[str, Any]]:
    info = dict(_SCENES.get(scene) or {})
    if not info:
        return "", "", {}
    suffixes = tuple(info.get("normal") or ())
    if not suffixes:
        return "", "", {}
    seed = int(now or 0) // 3600
    for item in tuple(messages or ())[-8:]:
        seed += int(getattr(item, "timestamp", 0) or 0)
        seed += sum(ord(ch) for ch in str(getattr(item, "sender_id", "") or ""))
        seed += len(str(getattr(item, "text", "") or ""))
    suffix = suffixes[seed % len(suffixes)]
    return str(suffix), scene, info


def _rhythm_choice(
    now: int,
    messages: Sequence[ChatMessage],
) -> Tuple[str, str, Mapping[str, Any]]:
    return _scene_choice(_rhythm_scene(now), messages, now=now)


def _rhythm_scene(now: int) -> str:
    hour = time.localtime(int(now or time.time())).tm_hour
    if hour < 6:
        return "academy_early"
    if hour < 11:
        return "academy_morning"
    if hour < 12:
        return "academy_class"
    if hour < 14:
        return "academy_noon"
    if hour < 18:
        return "academy_afternoon"
    if hour < 22:
        return "academy_evening"
    return "academy_late"


def _has_repeatish_text(texts: Iterable[str]) -> bool:
    cleaned = [" ".join(str(text or "").split()) for text in texts]
    cleaned = [text for text in cleaned if 1 <= len(text) <= 40]
    if not cleaned:
        return False
    counts = Counter(cleaned)
    if any(count >= 3 for count in counts.values()):
        return True
    short = [text for text in cleaned if len(text) <= 3]
    return len(short) >= 4 and len(set(short)) <= 2
