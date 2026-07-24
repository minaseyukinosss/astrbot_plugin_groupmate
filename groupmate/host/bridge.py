"""AstrBot 事件桥接到 Companion Core 运行时。"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

from ..config import (
    DEBOUNCE_MAX_SECONDS,
    DEBOUNCE_MIN_SECONDS,
    HISTORY_LIMIT,
    HUMANIZE_DELAY_ENABLED,
    MAX_REPLY_SEGMENTS,
    TOPIC_MAX_SECONDS,
)
from ..engine.external_knowledge import needs_external_knowledge
from ..engine.rate_limit import SlidingWindowRateLimiter
from ..engine.runtime import GroupRuntimeManager
from ..engine.triggers import TriggerRouter
from ..engine.workflow import CognitiveWorkflow
from ..memory import SQLiteMemoryStore
from ..models import ChatMessage, GroupPolicy, TriggerKind
from ..persona.aemeath import (
    CHARACTER_NAME,
    AemeathOutputFirewall,
    AemeathPersonaProvider,
    DEFAULT_RELATIONSHIPS,
    parse_relationships,
)
from .llm import (
    AstrBotGenerationModel,
    AstrBotPersonaProvider,
    AstrBotPlatformPort,
    AstrBotVisionPort,
)
from .onebot import NapCatHistoryPort, OneBotTranslator


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
        if self.should_take_native_wake(event) and not self.should_defer_native_wake_to_astrbot(
            event
        ):
            return
        group_id = str(event.get_group_id())
        if self.paused or not group_id or self._is_command_event(event):
            return
        actor = await self.runtime.actor_for(group_id)
        topic = actor.window.snapshot()
        from ..engine.topics import select_active_messages

        active = select_active_messages(
            topic.messages, topic_created_at=topic.created_at
        )
        memories = self.memory.search_memories(
            group_id,
            " ".join(message.text for message in active),
            now=max((message.timestamp for message in active), default=0),
            limit=8,
        )
        prompt = AemeathPersonaProvider(
            relationships=self._relationships(),
            group_brief=str(self._setting("group_brief", "") or ""),
            character_name=str(self._setting("character_name", CHARACTER_NAME) or CHARACTER_NAME),
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
        """Whether this @ / reply-to-bot is a Groupmate native-wake candidate."""
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
        character_name = str(
            self._setting("character_name", CHARACTER_NAME) or CHARACTER_NAME
        ).strip() or CHARACTER_NAME
        persona = AstrBotPersonaProvider(
            self.context,
            persona_id=self._setting("persona_id", ""),
            override_prompt=self._setting("persona_prompt", ""),
            relationships=self._relationships(),
            group_brief=str(self._setting("group_brief", "") or ""),
            character_name=character_name,
        )
        getter = lambda gid: self._provider_by_group.get(
            gid,
            self._setting("generation_provider", ""),
        )
        policy = self._policy_for(group_id)
        return CognitiveWorkflow(
            generation_model=AstrBotGenerationModel(self.context, getter, persona),
            vision=AstrBotVisionPort(
                self.context,
                lambda gid: self._setting("vision_provider", "") or getter(gid),
            ),
            platform=AstrBotPlatformPort(self.context, lambda gid: self._umo_by_group[gid]),
            memory=self.memory,
            persona=persona,
            output_guard=AemeathOutputFirewall(policy.max_reply_chars),
            rate_limiter=SlidingWindowRateLimiter(
                policy.spontaneous_hourly_limit,
                policy.spontaneous_cooldown_seconds,
            ),
            clock=_SystemClock(),
            character_name=character_name,
        )

    def _relationships(self):
        configured = getattr(self.settings, "relationships", None)
        if configured:
            return configured
        raw = self._setting("relationships", None)
        if raw is None or raw == () or raw == []:
            return DEFAULT_RELATIONSHIPS
        if isinstance(raw, tuple) and raw and hasattr(raw[0], "sender_id"):
            return raw
        return parse_relationships(raw)

    def _policy_for(self, group_id: str) -> GroupPolicy:
        del group_id
        aliases = tuple(self._setting("aliases", ("爱弥斯", "小爱", "飞行雪绒")))
        return GroupPolicy(
            aliases=aliases,
            handle_native_wake=bool(self._setting("handle_native_wake", True)),
            history_limit=HISTORY_LIMIT,
            spontaneous_hourly_limit=int(self._setting("spontaneous_hourly_limit", 6)),
            spontaneous_cooldown_seconds=int(
                self._setting("spontaneous_cooldown_seconds", 600)
            ),
            debounce_min_seconds=DEBOUNCE_MIN_SECONDS,
            debounce_max_seconds=DEBOUNCE_MAX_SECONDS,
            topic_max_seconds=TOPIC_MAX_SECONDS,
            max_reply_chars=int(self._setting("max_reply_chars", 60) or 60),
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
