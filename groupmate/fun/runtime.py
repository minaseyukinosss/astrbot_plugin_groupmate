"""Runtime orchestration for optional fun features."""

from __future__ import annotations

import asyncio
import random
import time
from typing import Callable, Iterable, Optional, Sequence, Tuple
from uuid import uuid4

from ..models import ChatMessage
from .contracts import FunFeatureContext, FunFeatureEvent
from .contracts import FunFeature as FunFeatureProtocol
from .governor import FunGovernor


class FunRuntime:
    def __init__(
        self,
        *,
        persona_id: str,
        memory,
        actions,
        settings,
        group_ids_getter: Callable[[], Iterable[str]],
        recent_messages_getter: Callable[[str], Sequence[ChatMessage]],
        paused_getter: Callable[[], bool],
        features: Sequence[FunFeatureProtocol],
        clock: Optional[Callable[[], int]] = None,
        governor: Optional[FunGovernor] = None,
        tick_seconds: float = 60.0,
        random_source: Optional[random.Random] = None,
    ) -> None:
        self.persona_id = str(persona_id)
        self.memory = memory
        self.actions = actions
        self.settings = settings
        self.group_ids_getter = group_ids_getter
        self.recent_messages_getter = recent_messages_getter
        self.paused_getter = paused_getter
        self.features = tuple(features or ())
        self.clock = clock or (lambda: int(time.time()))
        self.governor = governor or FunGovernor()
        self.tick_seconds = max(5.0, float(tick_seconds))
        self.random = random_source or random.Random()
        self._task = None
        self._closed = False

    async def start(self) -> None:
        if self._task is not None or not getattr(self.settings, "enabled", False):
            return
        self._closed = False
        self._task = asyncio.create_task(self._run())

    async def close(self) -> None:
        self._closed = True
        task = self._task
        self._task = None
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def refresh_dynamic_card(self, group_id: str, *, force: bool = True) -> Optional[FunFeatureEvent]:
        feature = self._feature("dynamic_card")
        if feature is None:
            return None
        return await self._run_feature_for_group(feature, str(group_id), force=force)

    def active_context_for_message(self, message: ChatMessage) -> str:
        dynamic_card = getattr(self.settings, "dynamic_card", None)
        if not getattr(dynamic_card, "enabled", False):
            return ""
        text = str(getattr(message, "text", "") or "")
        if not _looks_like_card_question(text):
            return ""
        event = self.memory.latest_fun_feature_event(
            self.persona_id,
            str(message.group_id),
            "dynamic_card",
            now=self.clock(),
        )
        if event is None:
            return ""
        private = event.private_context or {}
        cues = _format_reply_cues(private.get("reply_cues"))
        lines = [
            "<fun_feature_context>",
            "当前群名片：{}".format(event.public_value),
            "状态性质：爱弥斯自己的星炬学院生活状态牌；不是任务、长期记忆或对群友的评价。",
            "换牌原因：{}".format(private.get("visible_cause") or private.get("scene") or "星炬学院作息或群聊气氛变化"),
            "来源：{}".format(private.get("source_summary") or "爱弥斯顺手换了一个星炬学院日常状态牌"),
            "目的：{}".format(private.get("social_intent") or "表达爱弥斯在星炬学院的临时生活状态"),
            "回答口径：{}".format(private.get("answer_angle") or "只把它说成爱弥斯在星炬学院的日常状态，不解释成在说某个人"),
            "可借用口语碎片：{}".format(cues or "短、轻、像顺手翻了个学生状态牌"),
            "边界：{}".format(private.get("reply_boundary") or "不猜人、不点名、不复盘群友、不解释系统机制"),
            "输出要求：像爱弥斯用星炬学院日常接一句自己的状态梗，12-28字；不要客服腔，不要说“根据/功能/状态”，不要追溯谁触发。",
            "</fun_feature_context>",
        ]
        return "\n".join(lines)

    def status(self) -> dict:
        recent = []
        for event in self.memory.list_fun_feature_events(
            self.persona_id,
            limit=20,
        ):
            recent.append(
                {
                    "event_id": event.event_id,
                    "feature_id": event.feature_id,
                    "group_id": event.group_id,
                    "action_kind": event.action_kind,
                    "public_value": event.public_value,
                    "private_context": event.private_context,
                    "participants": [
                        {
                            "user_id": item.user_id,
                            "display_name": item.display_name,
                            "role": item.role,
                            "confidence": item.confidence,
                            "visibility": item.visibility,
                        }
                        for item in event.participants
                    ],
                    "created_at": event.created_at,
                    "expires_at": event.expires_at,
                    "status": event.status,
                    "error_code": event.error_code,
                }
            )
        return {
            "enabled": bool(getattr(self.settings, "enabled", False)),
            "features": {
                feature.feature_id: {
                    "available": True,
                }
                for feature in self.features
            },
            "recent": recent,
        }

    def _feature(self, feature_id: str):
        for feature in self.features:
            if feature.feature_id == feature_id:
                return feature
        return None

    async def _run(self) -> None:
        while not self._closed:
            try:
                await self.run_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                pass
            await asyncio.sleep(self.tick_seconds)

    async def run_once(self) -> None:
        if not getattr(self.settings, "enabled", False):
            return
        for group_id in tuple(dict.fromkeys(str(item) for item in self.group_ids_getter() if str(item).strip())):
            for feature in self.features:
                await self._run_feature_for_group(feature, group_id, force=False)

    async def _run_feature_for_group(self, feature, group_id: str, *, force: bool) -> Optional[FunFeatureEvent]:
        now = int(self.clock())
        active = self.memory.latest_fun_feature_event(
            self.persona_id,
            str(group_id),
            feature.feature_id,
            now=now,
        )
        context = FunFeatureContext(
            persona_id=self.persona_id,
            group_id=str(group_id),
            now=now,
            paused=bool(self.paused_getter()),
            recent_messages=tuple(self.recent_messages_getter(str(group_id)) or ()),
            active_event=active,
            force=force,
        )
        if not feature.due(context):
            return None
        plan = feature.plan(context)
        if plan is None:
            return None
        accepted, error = self.governor.validate(plan)
        if not accepted:
            return self._record(plan, now, "failed", error)
        apply_error = await feature.apply(plan, self.actions)
        if apply_error:
            return self._record(plan, now, "failed", apply_error)
        return self._record(plan, now, "active", "")

    def _record(self, plan, now: int, status: str, error_code: str) -> FunFeatureEvent:
        event = FunFeatureEvent(
            event_id=uuid4().hex,
            feature_id=plan.feature_id,
            persona_id=self.persona_id,
            group_id=plan.group_id,
            action_kind=plan.action_kind,
            public_value=plan.public_value,
            private_context=dict(plan.private_context or {}),
            participants=plan.participants,
            created_at=now,
            expires_at=plan.expires_at,
            status=status,
            error_code=error_code,
        )
        return self.memory.append_fun_feature_event(event)


def _looks_like_card_question(text: str) -> bool:
    source = str(text or "").strip()
    if not source:
        return False
    compact = source.replace(" ", "")
    direct_keywords = (
        "名片",
        "群名片",
        "群昵称",
        "你改名",
        "你名字",
        "你昵称",
        "为什么叫",
        "为啥叫",
        "为什么改",
        "为啥改",
        "状态牌",
    )
    if any(keyword in compact for keyword in direct_keywords):
        return True
    meaning_keywords = (
        "什么意思",
        "啥意思",
        "什么仪式",
        "召唤仪式",
        "这啥",
    )
    card_cues = ("名字", "昵称", "名片", "叫", "改成", "你这")
    if any(keyword in compact for keyword in meaning_keywords) and any(
        cue in compact for cue in card_cues
    ):
        return True
    sensitive_keywords = (
        "谁急了",
        "谁急",
        "急什么",
        "谁在急",
        "说谁",
        "截谁",
        "截图谁",
        "谁开演",
        "哪开演",
        "演谁",
        "谁上桌",
        "围观谁",
        "复读机",
        "装没看见",
        "战术路过",
        "问号冒烟",
        "阴阳谁",
    )
    return any(keyword in compact for keyword in sensitive_keywords) and any(
        cue in compact for cue in card_cues
    )


def _participant_context(event: FunFeatureEvent, message: ChatMessage) -> Tuple[str, str]:
    participants = tuple(getattr(event, "participants", None) or ())
    if not participants:
        private_summary = (event.private_context or {}).get("participant_summary")
        participants = _participants_from_private_summary(private_summary)
    if not participants:
        return "没有可靠名单；按现场热闹处理，不点名", "追问者未在名单里；不要硬说是谁"

    parts = []
    asker_id = str(getattr(message, "sender_id", "") or "")
    asker_hit = None
    for item in participants[:4]:
        label = str(getattr(item, "display_name", "") or "").strip()
        if not label:
            label = "成员{}".format(str(getattr(item, "user_id", "") or "")[-4:])
        role = str(getattr(item, "role", "") or "刚才在现场的人").strip()
        parts.append("{}({})".format(label[:16], role[:14]))
        if asker_id and str(getattr(item, "user_id", "") or "") == asker_id:
            asker_hit = label
    if asker_hit:
        asker_line = "追问者也是刚才现场里的人；可以轻轻带过，但不要怼 ta"
    else:
        asker_line = "追问者不在主要当事人名单里；只解释现场，不把锅扣给 ta"
    return "；".join(parts), asker_line


def _participants_from_private_summary(raw) -> Tuple[object, ...]:
    if not isinstance(raw, (list, tuple)):
        return ()
    result = []
    for item in raw[:4]:
        if not isinstance(item, dict):
            continue
        result.append(
            _ParticipantView(
                user_id=str(item.get("user_id") or ""),
                display_name=str(item.get("display_name") or item.get("label") or ""),
                role=str(item.get("role") or "刚才在现场的人"),
            )
        )
    return tuple(item for item in result if item.user_id)


class _ParticipantView:
    def __init__(self, user_id: str, display_name: str, role: str) -> None:
        self.user_id = user_id
        self.display_name = display_name
        self.role = role


def _format_reply_cues(raw) -> str:
    if isinstance(raw, str):
        return raw[:120]
    if not isinstance(raw, (list, tuple)):
        return ""
    cues = ["{}".format(item).strip() for item in raw if "{}".format(item).strip()]
    return " / ".join(cues[:3])[:160]
