"""AstrBot 事件桥接到 Companion Core 运行时。"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Dict, Optional

from ..capabilities import (
    CapabilityGovernor,
    CapabilityProviderRuntime,
    CapabilityRequest,
    VisionProvider,
)
from ..core.projections import StateProjector
from ..core.response_act import TaskResolution, TaskResolutionStatus
from ..engine.external_knowledge import needs_external_knowledge
from ..engine.rate_limit import SlidingWindowRateLimiter
from ..engine.runtime import GroupRuntimeManager
from ..engine.triggers import TriggerRouter
from ..engine.workflow import CognitiveWorkflow
from ..memory import SQLiteMemoryStore
from ..memory.migrations import SCHEMA_VERSION
from ..models import ChatMessage, MessageOrigin, StringEnum, TriggerKind
from ..persona import default_persona_registry
from ..persona.aemeath import (
    CHARACTER_NAME,
    AemeathOutputFirewall,
)
from ..policies import BehaviorPolicy
from .config import DeploymentSettings
from .llm import (
    AstrBotGenerationModel,
    AstrBotPlatformPort,
    AstrBotVisionPort,
)
from .onebot import NapCatHistoryPort, OneBotTranslator


class TurnOwner(StringEnum):
    GROUPMATE = "groupmate"
    ASTRBOT_AGENT = "astrbot_agent"
    OBSERVE_ONLY = "observe_only"


class AstrBotBridge:
    """Connects AstrBot events to the framework-free group runtime."""

    def __init__(
        self,
        context: Any,
        settings: DeploymentSettings,
        data_dir: Path,
    ) -> None:
        self.context = context
        self.settings = settings
        self.data_dir = Path(data_dir)
        self.memory = SQLiteMemoryStore(self.data_dir / "groupmate.db")
        self._umo_by_group: Dict[str, str] = {}
        self._provider_by_group: Dict[str, str] = {}
        self._capability_runtimes = {}
        self._bootstrapped = set()
        self._bootstrap_locks: Dict[tuple, asyncio.Lock] = {}
        self._paused = False
        persona_registry = default_persona_registry()
        persona_id = persona_registry.current_persona_id
        self.persona_context = persona_registry.resolve(
            persona_id,
            aliases=settings.aliases_for(persona_id),
            relationships=settings.relationships_for(persona_id),
        )
        self.behavior = BehaviorPolicy(
            interaction=settings.interaction_policy()
        )
        self.runtime = GroupRuntimeManager(
            self._workflow_for,
            lambda group_id: self.persona_context,
            lambda group_id: self.behavior,
        )

    async def handle_event(self, event: Any) -> None:
        actor = await self._prepare_actor(event)
        if actor is None:
            return
        await actor.submit(
            self._message_from_event(event), schedule=not self.paused
        )

    async def handle_adapted_event(
        self,
        event: Any,
        message: ChatMessage,
    ) -> bool:
        if not isinstance(message, ChatMessage):
            raise TypeError("message must be a ChatMessage")
        actor = await self._prepare_actor(event)
        if actor is None:
            return False
        self._mark_groupmate_owner(event)
        settings = getattr(self, "settings", None)
        if settings is not None and getattr(settings, "poke_exclusive", False):
            stopper = getattr(event, "stop_event", None)
            if callable(stopper):
                stopper()
        await actor.submit(message, schedule=not self.paused)
        return True

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
        if self.paused or not group_id:
            return
        actor = await self.runtime.actor_for(group_id, self.persona_context)
        topic = actor.window.snapshot()
        from ..engine.topics import select_active_messages

        active = select_active_messages(
            topic.messages, topic_created_at=topic.created_at
        )
        memories = self.memory.search_memories(
            self.persona_context.persona_id,
            group_id,
            " ".join(message.text for message in active),
            now=max((message.timestamp for message in active), default=0),
            limit=8,
        )
        prompt = self.persona_context.prompt_provider.build_user_context(
            topic, memories
        )
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
        if self.paused:
            return False
        group_id = str(event.get_group_id())
        if not group_id or not self._group_enabled(group_id):
            return False
        message = self._message_from_event(event)
        return (
            TriggerRouter(self.persona_context.aliases).classify(message).kind
            is TriggerKind.NATIVE_DIRECT
        )

    def should_defer_native_wake_to_astrbot(self, event: Any) -> bool:
        """Hand @ wakes that need web/external facts back to AstrBot's Agent."""
        return self.owner_for_event(event) is TurnOwner.ASTRBOT_AGENT

    def owner_for_event(self, event: Any) -> TurnOwner:
        """Return the single final-response owner for this host event."""
        if not self.should_take_native_wake(event):
            return TurnOwner.OBSERVE_ONLY
        if needs_external_knowledge(self._message_from_event(event).text):
            return TurnOwner.ASTRBOT_AGENT
        return TurnOwner.GROUPMATE

    def apply_owner_to_event(self, event: Any) -> TurnOwner:
        owner = self.owner_for_event(event)
        if owner is TurnOwner.GROUPMATE:
            self._mark_groupmate_owner(event)
        return owner

    @staticmethod
    def _mark_groupmate_owner(event: Any) -> None:
        if hasattr(event, "should_call_llm"):
            event.should_call_llm(True)
        else:
            event.call_llm = True

    async def _prepare_actor(self, event: Any):
        group_id = str(event.get_group_id())
        if not self._group_enabled(group_id):
            return None
        umo = str(event.unified_msg_origin)
        self._umo_by_group[group_id] = umo
        if self.settings.generation_provider:
            self._provider_by_group[group_id] = self.settings.generation_provider
        else:
            try:
                self._provider_by_group[
                    group_id
                ] = await self.context.get_current_chat_provider_id(umo)
            except Exception:
                self._provider_by_group[group_id] = ""
        actor = await self.runtime.actor_for(group_id, self.persona_context)
        runtime_key = (self.persona_context.persona_id, group_id)
        if runtime_key not in self._bootstrapped:
            lock = self._bootstrap_locks.get(runtime_key)
            if lock is None:
                lock = asyncio.Lock()
                self._bootstrap_locks[runtime_key] = lock
            async with lock:
                if runtime_key not in self._bootstrapped:
                    await self._bootstrap_group(actor, event)
                    self._bootstrapped.add(runtime_key)
        return actor

    async def _bootstrap_group(self, actor, event: Any) -> None:
        group_id = actor.group_id
        conversation = self.behavior.conversation
        was_dispatch = actor._dispatch_enabled and not self.paused
        actor.set_dispatch_enabled(False)
        clock = _SystemClock()
        projector = StateProjector(
            self.memory,
            character_name=CHARACTER_NAME,
        )
        snapshot = projector.rebuild(
            self.persona_context.persona_id,
            group_id,
            now=clock.now(),
            policy=conversation,
        )
        projector.apply(
            snapshot,
            window=actor.window,
            session=actor.workflow.session_for(group_id),
            rate_limiter=actor.workflow.rate_limiter,
            workflow=actor.workflow,
            set_continuation=actor.set_continuation,
        )

        bot = getattr(event, "bot", None)
        if bot is not None:
            try:
                history = await NapCatHistoryPort(
                    bot, event.get_self_id()
                ).fetch_recent(group_id, conversation.history_limit)
                for message in history:
                    stamped = message
                    if message.origin is not MessageOrigin.BOT_DELIVERY:
                        from dataclasses import replace

                        stamped = replace(
                            message,
                            origin=MessageOrigin.PLATFORM_HISTORY,
                            ingested_at=clock.now(),
                        )
                    await actor.preload(stamped)
                await actor.drain()
            except Exception:
                # Keep local ledger projection; do not wipe context.
                pass

        # Start a fresh open epoch after history projection; never send.
        import time
        from uuid import uuid4

        now = int(time.time())
        topic = actor.window.snapshot()
        last_id = topic.latest.message_id if topic.latest else None
        if topic.topic_id:
            await self.memory.close_topic_epoch_async(
                self.persona_context.persona_id,
                group_id,
                topic.topic_id,
                now,
                "RESET",
                last_id,
            )
        new_id = actor.window.reset_topic()
        await self.memory.open_topic_epoch_async(
            self.persona_context.persona_id,
            group_id,
            new_id,
            now,
            last_id,
        )
        actor.set_dispatch_enabled(was_dispatch and not self.paused)

    def _message_from_event(self, event: Any) -> ChatMessage:
        return OneBotTranslator.from_event(
            event,
            bot_id=str(event.get_self_id()),
        )

    async def close(self) -> None:
        self.paused = True
        await self.runtime.close()
        for provider_runtime in tuple(self._capability_runtimes.values()):
            provider_runtime.close()
        self._capability_runtimes.clear()
        await self.memory.mark_sending_unknown_async()
        await self.memory.flush_async()
        self.memory.close()

    @property
    def paused(self) -> bool:
        return self._paused

    @paused.setter
    def paused(self, value: bool) -> None:
        self._paused = bool(value)
        runtime = getattr(self, "runtime", None)
        if runtime is not None:
            runtime.set_dispatch_enabled(not self._paused)

    def status(self) -> Dict[str, Any]:
        groups: Dict[str, Any] = {}
        for group_id, snapshot in self.runtime.snapshots(
            self.persona_context.persona_id
        ).items():
            enriched = dict(snapshot)
            last_outcome = enriched.get("last_outcome")
            if isinstance(last_outcome, dict):
                # Keep reason only; never expose reply text on status surfaces.
                enriched["last_outcome"] = {
                    "sent": bool(last_outcome.get("sent")),
                    "reason": str(last_outcome.get("reason") or ""),
                }
            enriched["recent_ends"] = self.memory.recent_decision_ends(
                self.persona_context.persona_id, group_id, limit=3
            )
            groups[group_id] = enriched
        return {
            "paused": self.paused,
            "active_persona": self.persona_context.persona_id,
            "enabled_scope": (
                list(self.settings.enabled_groups)
                if self.settings.enabled_groups
                else "all"
            ),
            "alias_count": len(self.persona_context.aliases),
            "relationship_seed_count": len(
                self.persona_context.relationship_seeds
            ),
            "generation_provider_mode": (
                "explicit"
                if self.settings.generation_provider
                else "current_group"
            ),
            "vision_status": (
                "disabled"
                if not self.settings.vision_enabled
                else (
                    "explicit"
                    if self.settings.vision_provider
                    else "reuse_text"
                )
            ),
            "poke_adapter": (
                "enabled" if self.settings.poke_enabled else "disabled"
            ),
            "poke_back": (
                "enabled" if self.settings.poke_back_enabled else "disabled"
            ),
            "poke_exclusive": (
                "enabled" if self.settings.poke_exclusive else "disabled"
            ),
            "poke_face": (
                "enabled" if self.settings.poke_face_enabled else "disabled"
            ),
            "database_schema": SCHEMA_VERSION,
            "config_health": (
                "warning"
                if (
                    self.settings.diagnostics.ignored_legacy_keys
                    or self.settings.diagnostics.unknown_keys
                    or self.settings.diagnostics.warnings
                )
                else "ok"
            ),
            "ignored_legacy_keys": list(
                self.settings.diagnostics.ignored_legacy_keys
            ),
            "unknown_keys": list(self.settings.diagnostics.unknown_keys),
            "warnings": list(self.settings.diagnostics.warnings),
            "groups": groups,
            "bootstrapped": sorted(
                group_id
                for persona_id, group_id in self._bootstrapped
                if persona_id == self.persona_context.persona_id
            ),
        }

    def list_decisions(
        self,
        *,
        group_id: Optional[str] = None,
        outcome: Optional[str] = None,
        limit: int = 50,
    ) -> Dict[str, Any]:
        persona_id = self.persona_context.persona_id
        items = self.memory.recent_decisions(
            persona_id,
            group_id=group_id,
            outcome=outcome,
            limit=limit,
        )
        groups = self.memory.decision_group_ids(persona_id)
        bootstrapped = sorted(
            group
            for persona, group in self._bootstrapped
            if persona == persona_id
        )
        # Prefer ledger groups; keep bootstrapped ids that are not yet in ledger.
        for group in bootstrapped:
            if group not in groups:
                groups.append(group)
        return {
            "items": items,
            "groups": groups,
            "active_persona": persona_id,
        }

    def get_decision_trace(self, decision_id: str) -> Optional[Dict[str, Any]]:
        return self.memory.decision_trace(
            self.persona_context.persona_id,
            decision_id,
        )

    def _workflow_for(self, group_id: str, persona_context):
        getter = lambda gid: self._provider_by_group.get(
            gid,
            self.settings.generation_provider,
        )
        vision = AstrBotVisionPort(
            self.context,
            lambda gid: self.settings.vision_provider or getter(gid),
        )
        provider_runtime = self._capability_runtimes.get(group_id)
        if provider_runtime is None:
            provider_runtime = CapabilityProviderRuntime(
                (
                    VisionProvider(
                        vision if self.settings.vision_enabled else None
                    ),
                )
            )
            self._capability_runtimes[group_id] = provider_runtime
        capabilities = provider_runtime.registry
        governor = CapabilityGovernor(capabilities)

        def resolve_task(scene, message):
            del scene
            if not self.settings.vision_enabled or not message.image_urls:
                return TaskResolution(status=TaskResolutionStatus.UNSUPPORTED)
            return capabilities.resolve(
                CapabilityRequest(
                    capability_name="vision",
                    message_text=message.text,
                    media_locators=message.image_urls,
                    group_id=message.group_id,
                    actor_id=message.sender_id,
                    message_id=message.message_id,
                )
            )

        return CognitiveWorkflow(
            generation_model=AstrBotGenerationModel(
                self.context,
                getter,
                persona_context.prompt_provider,
            ),
            vision=vision,
            platform=AstrBotPlatformPort(
                self.context,
                lambda gid: self._umo_by_group[gid],
                poke_interval_seconds=(
                    self.behavior.interaction.poke_interval_seconds
                ),
            ),
            memory=self.memory,
            persona_context=persona_context,
            behavior=self.behavior,
            vision_enabled=self.settings.vision_enabled,
            poke_back_enabled=self.settings.poke_back_enabled,
            output_guard=AemeathOutputFirewall(),
            rate_limiter=SlidingWindowRateLimiter(
                self.behavior.resources.open_send_hourly_limit,
                self.behavior.resources.open_send_cooldown_seconds,
            ),
            clock=_SystemClock(),
            character_name=CHARACTER_NAME,
            task_response_resolver=resolve_task,
            capabilities=capabilities,
            capability_governor=governor,
        )

    def _group_enabled(self, group_id: str) -> bool:
        groups = self.settings.enabled_groups
        return not groups or group_id in {str(item) for item in groups}

class _SystemClock:
    def now(self) -> int:
        import time

        return int(time.time())
