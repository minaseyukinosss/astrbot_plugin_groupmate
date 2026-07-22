"""AstrBot/OneBot edge adapters.

The module keeps OneBot parsing usable in offline tests and imports AstrBot only
inside concrete integration methods so the domain remains framework-free.
"""

from __future__ import annotations

import json
import re
from dataclasses import replace
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence

from .external_knowledge import needs_external_knowledge
from .guardrails import AemeathOutputGuard
from .memory import SQLiteMemoryStore
from .models import (
    ChatMessage,
    Decision,
    GroupPolicy,
    TopicSnapshot,
    TriggerKind,
    Urgency,
)
from .persona import BundledPersonaProvider
from .rate_limit import SlidingWindowRateLimiter
from .runtime import GroupRuntimeManager
from .topics import select_active_messages
from .triggers import TriggerRouter
from .workflow import CognitiveWorkflow
from .config import (
    DEBOUNCE_MAX_SECONDS,
    DEBOUNCE_MIN_SECONDS,
    DECISION_THRESHOLD,
    HISTORY_LIMIT,
    HUMANIZE_DELAY_ENABLED,
    MAX_REPLY_SEGMENTS,
    TOPIC_MAX_SECONDS,
)


class OneBotTranslator:
    @staticmethod
    def _coerce_timestamp(value: Any) -> int:
        try:
            timestamp = int(value or 0)
        except (TypeError, ValueError):
            timestamp = 0
        if timestamp <= 0:
            import time

            return int(time.time())
        return timestamp

    @classmethod
    def from_history(cls, raw: Dict[str, Any], bot_id: str) -> ChatMessage:
        segments = raw.get("message") or raw.get("content") or []
        if isinstance(segments, str):
            segments = [{"type": "text", "data": {"text": segments}}]
        text_parts: List[str] = []
        image_urls: List[str] = []
        segment_types: List[str] = []
        reply_id: Optional[str] = None
        mentions_bot = False
        for segment in segments:
            if not isinstance(segment, dict):
                continue
            kind = str(segment.get("type", "")).lower()
            data = segment.get("data") or {}
            segment_types.append(kind)
            if kind in ("text", "plain"):
                text = data.get("text") or segment.get("text") or ""
                if text:
                    text_parts.append(str(text))
            elif kind == "at":
                qq = str(data.get("qq", data.get("user_id", "")))
                if qq == str(bot_id):
                    mentions_bot = True
                name = data.get("name") or data.get("display_name")
                if name:
                    text_parts.append("@" + str(name))
            elif kind == "reply":
                reply_id = str(data.get("id", data.get("message_id", ""))) or None
            elif kind == "image":
                url = data.get("url") or data.get("file")
                if url:
                    image_urls.append(str(url))
            elif kind in ("record", "video", "file"):
                text_parts.append("[{}]".format(kind))

        sender = raw.get("sender") or {}
        sender_id = str(raw.get("user_id", sender.get("user_id", "")))
        group_id = str(raw.get("group_id", ""))
        timestamp = cls._coerce_timestamp(raw.get("time", raw.get("timestamp", 0)))
        reply_to_bot = bool(raw.get("reply_to_bot", False))
        if reply_id and str(raw.get("reply_sender_id", "")) == str(bot_id):
            reply_to_bot = True
        return ChatMessage(
            message_id=str(raw.get("message_id", raw.get("id", ""))),
            group_id=group_id,
            sender_id=sender_id,
            sender_name=str(
                sender.get("card") or sender.get("nickname") or sender_id
            ),
            text="".join(text_parts).strip(),
            timestamp=timestamp,
            reply_to_message_id=reply_id,
            reply_to_bot=reply_to_bot,
            mentions_bot=mentions_bot,
            is_bot=sender_id == str(bot_id),
            image_urls=tuple(dict.fromkeys(image_urls)),
            segment_types=tuple(segment_types),
            metadata={"raw": raw},
        )

    @classmethod
    def from_event(
        cls,
        event: Any,
        bot_id: str,
        is_command: bool = False,
    ) -> ChatMessage:
        raw = getattr(getattr(event, "message_obj", None), "raw_message", None)
        if isinstance(raw, dict):
            message = cls.from_history(raw, bot_id)
        else:
            message = ChatMessage(
                message_id=str(getattr(event.message_obj, "message_id", "")),
                group_id=str(event.get_group_id()),
                sender_id=str(event.get_sender_id()),
                sender_name=str(event.get_sender_name()),
                text=str(event.message_str or ""),
                timestamp=cls._coerce_timestamp(
                    getattr(event.message_obj, "timestamp", 0)
                ),
                is_bot=str(event.get_sender_id()) == str(bot_id),
            )
        native_direct = bool(getattr(event, "is_at_or_wake_command", False))
        return replace(
            message,
            is_command=is_command,
            mentions_bot=message.mentions_bot or native_direct,
            metadata=dict(message.metadata, native_direct=native_direct),
        )


def parse_decision_response(raw: str, trigger: TriggerKind) -> Decision:
    text = (raw or "").strip()
    fenced = re.search(r"\{.*\}", text, re.DOTALL)
    if fenced:
        text = fenced.group(0)
    try:
        data = json.loads(text)
    except (TypeError, ValueError, json.JSONDecodeError):
        return Decision.ignore("invalid_decision_schema", trigger)
    if not isinstance(data, dict):
        return Decision.ignore("invalid_decision_schema", trigger)
    action = str(data.get("action", "ignore")).lower()
    reason = str(data.get("reason_code", "model_decision"))
    if action != "respond":
        return Decision.ignore(reason, trigger)
    contribution = str(data.get("contribution", "")).strip()
    if not contribution:
        return Decision.ignore("invalid_decision_schema", trigger)
    try:
        urgency = Urgency(str(data.get("urgency", Urgency.NORMAL.value)).lower())
    except ValueError:
        urgency = Urgency.NORMAL
    return Decision.respond(
        contribution=contribution,
        confidence=float(data.get("confidence", 0.0)),
        trigger=trigger,
        reason_code=reason,
        target_message_id=(
            str(data["target_message_id"])
            if data.get("target_message_id") is not None
            else None
        ),
        needs_vision=bool(data.get("needs_vision", False)),
        urgency=urgency,
    )


class NapCatHistoryPort:
    def __init__(self, bot: Any, bot_id: str) -> None:
        self.bot = bot
        self.bot_id = str(bot_id)

    async def fetch_recent(self, group_id: str, count: int) -> Sequence[ChatMessage]:
        response = await self.bot.call_action(
            "get_group_msg_history",
            group_id=int(group_id),
            count=int(count),
            reverseOrder=True,
        )
        rows = response.get("messages", []) if isinstance(response, dict) else response
        return [
            OneBotTranslator.from_history(row, self.bot_id)
            for row in (rows or [])
            if isinstance(row, dict)
        ]


class AstrBotDecisionModel:
    def __init__(self, context: Any, provider_getter: Callable[[str], str]) -> None:
        self.context = context
        self.provider_getter = provider_getter

    async def decide(self, topic: TopicSnapshot, policy: GroupPolicy, memories):
        provider_id = self.provider_getter(topic.group_id)
        if not provider_id:
            return Decision.ignore("decision_provider_missing", TriggerKind.CANDIDATE)
        prompt = self._prompt(topic, policy, memories)
        response = await self.context.llm_generate(
            chat_provider_id=provider_id,
            prompt=prompt,
            system_prompt=(
                "你是群聊发言门卫，只输出 JSON。判断是否值得让群聊伙伴加入。"
                "action 只能是 respond 或 ignore；不要生成最终回复。"
            ),
        )
        return parse_decision_response(
            getattr(response, "completion_text", "") or "", TriggerKind.CANDIDATE
        )

    @staticmethod
    def _prompt(topic, policy, memories) -> str:
        active = select_active_messages(
            topic.messages, topic_created_at=topic.created_at
        )
        lines = [
            "<topic>",
            "\n".join(
                "{}: {}".format(message.sender_name, message.text or "[媒体]")
                for message in active
            ),
            "</topic>",
            "<policy>最多每小时 {} 条自主回复；门槛 {:.2f}</policy>".format(
                policy.spontaneous_hourly_limit, policy.decision_threshold
            ),
        ]
        if memories:
            lines.extend(["<memory>", "\n".join(item.text for item in memories), "</memory>"])
        lines.append(
            "输出字段：action, confidence, reason_code, target_message_id, "
            "contribution, needs_vision, urgency"
        )
        return "\n".join(lines)


class AstrBotGenerationModel:
    def __init__(
        self,
        context: Any,
        provider_getter: Callable[[str], str],
        persona: BundledPersonaProvider,
    ) -> None:
        self.context = context
        self.provider_getter = provider_getter
        self.persona = persona

    async def repair(self, text: str, violations: Sequence[str]) -> str:
        provider_id = self.provider_getter("")
        if not provider_id:
            return text
        codes = "、".join(str(item) for item in violations) or "style"
        prompt = "\n".join(
            [
                "把下面的群聊回复改短、改自然，去掉客服腔、旁白和系统词。",
                "违规项：" + codes,
                "只输出修改后的最终回复，不要解释。",
                "原文：",
                (text or "").strip(),
            ]
        )
        response = await self.context.llm_generate(
            chat_provider_id=provider_id,
            prompt=prompt,
            system_prompt="你在帮群聊伙伴润色一句很短的回复。",
        )
        repaired = getattr(response, "completion_text", "") or ""
        return repaired.strip() or text

    async def generate(self, plan, topic, memories) -> str:
        provider_id = self.provider_getter(topic.group_id)
        if not provider_id:
            raise RuntimeError("generation provider missing")
        prompt = "\n".join(
            [
                self.persona.build_user_context(topic, memories),
                "<reply_task>",
                "你可以补充：" + plan.contribution,
                "只输出最终群聊回复，不要解释过程。",
                "</reply_task>",
            ]
        )
        response = await self.context.llm_generate(
            chat_provider_id=provider_id,
            prompt=prompt,
            system_prompt=plan.persona_prompt,
        )
        return getattr(response, "completion_text", "") or ""


class AstrBotVisionPort:
    def __init__(self, context: Any, provider_getter: Callable[[str], str]) -> None:
        self.context = context
        self.provider_getter = provider_getter

    async def describe(self, image_urls: Sequence[str]) -> str:
        provider_id = self.provider_getter("")
        if not provider_id:
            return ""
        response = await self.context.llm_generate(
            chat_provider_id=provider_id,
            prompt="用一句中文描述图片中与当前群聊有关的内容。",
            image_urls=list(image_urls),
        )
        return getattr(response, "completion_text", "") or ""


class AstrBotPlatformPort:
    def __init__(self, context: Any, umo_getter: Callable[[str], str]) -> None:
        self.context = context
        self.umo_getter = umo_getter

    async def send_text(self, group_id: str, text: str, decision_id: str) -> None:
        del decision_id
        from astrbot.api.event import MessageChain

        chain = MessageChain().message(text)
        umo = self.umo_getter(group_id)
        sent = await self.context.send_message(umo, chain)
        if sent:
            return

        from astrbot.api.star import StarTools

        await StarTools.send_message_by_id(
            "GroupMessage",
            str(group_id),
            chain,
            platform="aiocqhttp",
        )

    async def send_segments(
        self,
        group_id: str,
        segments: Sequence[str],
        decision_id: str,
        quote_message_id: Optional[str] = None,
    ) -> None:
        del quote_message_id
        for segment in segments:
            text = str(segment or "").strip()
            if text:
                await self.send_text(group_id, text, decision_id)


class AstrBotPersonaProvider(BundledPersonaProvider):
    def __init__(
        self,
        context: Any,
        persona_id: str = "",
        override_prompt: str = "",
        relationships: Optional[Sequence] = None,
    ) -> None:
        super().__init__(override_prompt, relationships=relationships)
        self.context = context
        self.persona_id = persona_id.strip()

    async def system_prompt(self, group_id: str) -> str:
        del group_id
        if self.persona_id:
            manager = getattr(self.context, "persona_manager", None)
            resolver = getattr(manager, "get_persona_v3_by_id", None)
            if resolver:
                persona = resolver(self.persona_id)
                if isinstance(persona, dict) and persona.get("prompt"):
                    return str(persona["prompt"])
                if isinstance(persona, dict) and persona.get("system_prompt"):
                    return str(persona["system_prompt"])
        return await super().system_prompt("")


class AstrBotBridge:
    """Connects AstrBot events to the framework-free group runtime."""

    def __init__(self, context: Any, settings: Any, data_dir: Path) -> None:
        self.context = context
        self.settings = settings
        self.data_dir = Path(data_dir)
        self.memory = SQLiteMemoryStore(self.data_dir / "groupmate.db")
        self._umo_by_group: Dict[str, str] = {}
        self._provider_by_group: Dict[str, str] = {}
        self._bootstrapped = set()
        self.paused = False
        self.runtime = GroupRuntimeManager(self._workflow_for, self._policy_for)

    async def handle_event(self, event: Any) -> None:
        actor = await self._prepare_actor(event)
        if actor is None:
            return
        await actor.submit(self._message_from_event(event))

    async def observe_only(self, event: Any) -> None:
        """Ingest the message into the group window without generating a reply."""
        actor = await self._prepare_actor(event)
        if actor is None:
            return
        await actor.preload(self._message_from_event(event))

    async def enrich_request(self, event: Any, req: Any) -> None:
        # Skip only when Groupmate will suppress AstrBot and answer itself.
        if self.should_take_native_wake(event) and not self.should_defer_native_wake_to_astrbot(
            event
        ):
            return
        group_id = str(event.get_group_id())
        if self.paused or not group_id or self._is_command_event(event):
            return
        actor = await self.runtime.actor_for(group_id)
        topic = actor.window.snapshot()
        active = select_active_messages(
            topic.messages, topic_created_at=topic.created_at
        )
        memories = self.memory.search_memories(
            group_id,
            " ".join(message.text for message in active),
            now=max((message.timestamp for message in active), default=0),
            limit=8,
        )
        prompt = BundledPersonaProvider(
            relationships=self._relationships()
        ).build_user_context(topic, memories)
        if self.should_defer_native_wake_to_astrbot(event):
            prompt = (
                "Groupmate 已观察本群上下文；本轮需要联网或外部事实，"
                "由平台 Agent 作答（可使用搜索等工具）。\n" + prompt
            )
        try:
            from astrbot.core.agent.message import TextPart

            req.extra_user_content_parts.append(TextPart(text=prompt).mark_as_temp())
        except Exception:
            return

    def should_take_native_wake(self, event: Any) -> bool:
        """Whether this @ / reply-to-bot is a Groupmate native-wake candidate.

        When True and not deferred, the plugin must set ``event.call_llm = True``
        so AstrBot's ProcessStage skips its default agent (condition is
        ``is_at_or_wake_command and not call_llm``; ``call_llm`` means
        "prohibit default LLM").
        """
        if not bool(self._setting("handle_native_wake", True)):
            return False
        if self.paused:
            return False
        group_id = str(event.get_group_id())
        if not group_id or not self._group_enabled(group_id):
            return False
        message = self._message_from_event(event)
        return (
            TriggerRouter(self._policy_for(group_id)).classify(message).kind
            is TriggerKind.NATIVE_DIRECT
        )

    def should_defer_native_wake_to_astrbot(self, event: Any) -> bool:
        """Hand @ wakes that need web/external facts back to AstrBot's Agent."""
        if not self.should_take_native_wake(event):
            return False
        return needs_external_knowledge(self._message_from_event(event).text)

    async def _prepare_actor(self, event: Any):
        if self.paused:
            return None
        group_id = str(event.get_group_id())
        if not self._group_enabled(group_id):
            return None
        umo = str(event.unified_msg_origin)
        self._umo_by_group[group_id] = umo
        try:
            self._provider_by_group[group_id] = await self.context.get_current_chat_provider_id(
                umo
            )
        except Exception:
            self._provider_by_group.setdefault(
                group_id, self._setting("generation_provider", "")
            )
        actor = await self.runtime.actor_for(group_id)
        if group_id not in self._bootstrapped:
            bot = getattr(event, "bot", None)
            if bot is not None:
                try:
                    history = await NapCatHistoryPort(bot, event.get_self_id()).fetch_recent(
                        group_id, self._policy_for(group_id).history_limit
                    )
                    for message in history:
                        await actor.preload(message)
                    await actor.drain()
                    actor.window.reset_topic()
                except Exception:
                    pass
            self._bootstrapped.add(group_id)
        return actor

    def _message_from_event(self, event: Any) -> ChatMessage:
        return OneBotTranslator.from_event(
            event,
            bot_id=str(event.get_self_id()),
            is_command=self._is_command_event(event),
        )

    async def close(self) -> None:
        await self.runtime.close()
        self.memory.close()

    def status(self) -> Dict[str, Any]:
        groups: Dict[str, Any] = {}
        for group_id, snapshot in self.runtime.snapshots().items():
            enriched = dict(snapshot)
            enriched["recent_ends"] = self.memory.recent_decision_ends(group_id, limit=3)
            groups[group_id] = enriched
        return {
            "paused": self.paused,
            "groups": groups,
            "bootstrapped": sorted(self._bootstrapped),
        }

    def _workflow_for(self, group_id: str):
        persona = AstrBotPersonaProvider(
            self.context,
            persona_id=self._setting("persona_id", ""),
            override_prompt=self._setting("persona_prompt", ""),
            relationships=self._relationships(),
        )
        getter = lambda gid: self._provider_by_group.get(
            gid,
            self._setting("generation_provider", "")
            or self._setting("decision_provider", ""),
        )
        policy = self._policy_for(group_id)
        return CognitiveWorkflow(
            decision_model=AstrBotDecisionModel(
                self.context,
                lambda gid: self._setting("decision_provider", "") or getter(gid),
            ),
            generation_model=AstrBotGenerationModel(self.context, getter, persona),
            vision=AstrBotVisionPort(
                self.context,
                lambda gid: self._setting("vision_provider", "") or getter(gid),
            ),
            platform=AstrBotPlatformPort(self.context, lambda gid: self._umo_by_group[gid]),
            memory=self.memory,
            persona=persona,
            output_guard=AemeathOutputGuard(policy.max_reply_chars),
            rate_limiter=SlidingWindowRateLimiter(
                policy.spontaneous_hourly_limit,
                policy.spontaneous_cooldown_seconds,
            ),
            clock=_SystemClock(),
        )

    def _relationships(self):
        from .relationships import DEFAULT_RELATIONSHIPS, parse_relationships

        raw = self._setting("relationships", None)
        if raw is None or raw == () or raw == []:
            if hasattr(self.settings, "relationships") and self.settings.relationships:
                return self.settings.relationships
            return DEFAULT_RELATIONSHIPS
        if isinstance(raw, tuple) and raw and hasattr(raw[0], "sender_id"):
            return raw
        return parse_relationships(raw)

    def _policy_for(self, group_id: str) -> GroupPolicy:
        aliases = tuple(self._setting("aliases", ("爱弥斯", "小爱", "飞行雪绒")))
        return GroupPolicy(
            aliases=aliases,
            handle_native_wake=bool(self._setting("handle_native_wake", True)),
            history_limit=HISTORY_LIMIT,
            decision_threshold=DECISION_THRESHOLD,
            spontaneous_hourly_limit=int(self._setting("spontaneous_hourly_limit", 6)),
            spontaneous_cooldown_seconds=int(
                self._setting("spontaneous_cooldown_seconds", 600)
            ),
            debounce_min_seconds=DEBOUNCE_MIN_SECONDS,
            debounce_max_seconds=DEBOUNCE_MAX_SECONDS,
            topic_max_seconds=TOPIC_MAX_SECONDS,
            vision_enabled=bool(self._setting("vision_enabled", True)),
            continuation_seconds=int(self._setting("continuation_seconds", 90)),
            humanize_delay_enabled=HUMANIZE_DELAY_ENABLED,
            max_reply_segments=MAX_REPLY_SEGMENTS,
        )

    def _group_enabled(self, group_id: str) -> bool:
        groups = tuple(self._setting("enabled_groups", ()))
        return not groups or group_id in {str(item) for item in groups}

    def _setting(self, name: str, default: Any) -> Any:
        if isinstance(self.settings, dict):
            return self.settings.get(name, default)
        return getattr(self.settings, name, default)

    @staticmethod
    def _is_command_event(event: Any) -> bool:
        for handler in event.get_extra("activated_handlers", []) or []:
            for event_filter in getattr(handler, "event_filters", []):
                if "command" in type(event_filter).__name__.lower():
                    return True
        return False


class _SystemClock:
    def now(self) -> int:
        import time

        return int(time.time())
