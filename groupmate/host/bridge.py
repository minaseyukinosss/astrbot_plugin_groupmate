"""AstrBot 事件桥接到 Companion Core 运行时。"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import Counter
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
from ..mail import MailService, build_send_qq_mail_descriptor
from ..memory import SQLiteMemoryStore
from ..memory.migrations import SCHEMA_VERSION
from ..models import (
    ChatMessage,
    ContinuityStatus,
    MessageOrigin,
    RelationshipState,
    SelfCommitmentStatus,
    StringEnum,
    TriggerKind,
)
from ..persona import default_persona_registry
from ..persona.aemeath import (
    CHARACTER_NAME,
    AemeathOutputFirewall,
)
from ..policies import BehaviorPolicy
from ..social.commitment_scheduler import CommitmentScheduler
from ..tools import (
    AstrBotToolPersonaRenderer,
    AstrBotToolPlanner,
    GroupmateToolOrchestrator,
    HostToolExecutor,
    ToolPolicyEngine,
    UniversalToolCatalog,
)
from .config import DeploymentSettings
from .llm import (
    AstrBotGenerationModel,
    AstrBotPlatformPort,
    AstrBotVisionPort,
)
from .onebot import NapCatHistoryPort, OneBotTranslator, resolve_member_name


logger = logging.getLogger(__name__)


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
        self._umo_path = self.data_dir / "group_umo.json"
        self._umo_by_group: Dict[str, str] = self._load_group_umos()
        self._provider_by_group: Dict[str, str] = {}
        self._mention_name_cache: Dict[tuple[str, str], str] = {}
        self._capability_runtimes = {}
        self._capability_governors = {}
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
        tool_provider_getter = lambda group_id: self._provider_by_group.get(
            str(group_id),
            self.settings.generation_provider,
        )
        self.mail_service = MailService(
            settings.mail,
            context=context,
            provider_getter=tool_provider_getter,
            persona_system_getter=lambda: self.persona_context.prompt_provider.system_text(),
            character_name_getter=lambda: self.persona_context.display_name,
            member_name_getter=self._mail_member_display_name,
            qq_nickname_getter=self._mail_qq_nickname,
        )
        builtin_tools = ()
        if self.mail_service.available():
            builtin_tools = (build_send_qq_mail_descriptor(self.mail_service),)
        self.tool_orchestrator = GroupmateToolOrchestrator(
            catalog=UniversalToolCatalog(
                context,
                command_bridge_enabled=settings.command_bridge_enabled,
                builtin_tools=builtin_tools,
            ),
            planner=AstrBotToolPlanner(context, tool_provider_getter),
            renderer=AstrBotToolPersonaRenderer(
                context,
                tool_provider_getter,
                self.persona_context.prompt_provider,
            ),
            executor=HostToolExecutor(context),
            policy=ToolPolicyEngine(),
            candidate_limit=settings.tool_candidate_limit,
            enabled=settings.tools_enabled,
        )
        self.runtime = GroupRuntimeManager(
            self._workflow_for,
            lambda group_id: self.persona_context,
            lambda group_id: self.behavior,
        )
        self.commitment_scheduler = CommitmentScheduler(
            context=context,
            memory=self.memory,
            persona_id=self.persona_context.persona_id,
            character_name=CHARACTER_NAME,
            platform_factory=self._platform_for_group,
            capability_governor_factory=self._capability_governor_for,
            paused_getter=lambda: self.paused,
            group_enabled=self._group_enabled,
            provider_getter=tool_provider_getter,
            timezone_name=self._host_timezone(),
        )

    async def start(self) -> None:
        await self.commitment_scheduler.start()

    async def handle_event(self, event: Any) -> None:
        actor = await self._prepare_actor(event)
        if actor is None:
            return
        message = await self._message_from_event_with_mentions(event)
        if not self.paused:
            try:
                handled = await self.tool_orchestrator.try_handle(
                    event,
                    actor,
                    message,
                )
            except Exception:
                logger.exception("Groupmate host tool orchestration failed")
                handled = False
            if handled:
                return
        await actor.submit(message, schedule=not self.paused)

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
        message = await self._resolve_message_mentions(event, message)
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
        await actor.preload(await self._message_from_event_with_mentions(event))

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
        focus_id = active[-1].sender_id if active and not active[-1].is_bot else ""
        continuity_items = (
            self.memory.list_continuity_items(
                self.persona_context.persona_id,
                group_id=group_id,
                subject_ids=self.memory.member_subject_ids(
                    self.persona_context.persona_id, group_id, focus_id
                ),
                statuses=(ContinuityStatus.OPEN,),
                limit=5,
            )
            if focus_id
            else ()
        )
        self_commitments = (
            self.memory.list_self_commitments(
                self.persona_context.persona_id,
                group_id=group_id,
                beneficiary_subject_ids=self.memory.member_subject_ids(
                    self.persona_context.persona_id, group_id, focus_id
                ),
                statuses=(
                    SelfCommitmentStatus.PENDING,
                    SelfCommitmentStatus.IN_PROGRESS,
                    SelfCommitmentStatus.BLOCKED,
                ),
                limit=5,
            )
            if focus_id
            else ()
        )
        prompt = self.persona_context.prompt_provider.build_user_context(
            topic,
            memories,
            continuity_items=continuity_items,
            self_commitments=self_commitments,
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
        message = self._message_from_event(event)
        if needs_external_knowledge(message.text) and not self.tool_orchestrator.has_candidate(
            message.text
        ):
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
        self._remember_group_umo(group_id, umo)
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

    def _mail_member_display_name(self, group_id: str, user_id: str) -> str:
        uid = str(user_id or "").strip()
        gid = str(group_id or "").strip()
        if not uid:
            return ""
        persona_id = self.persona_context.persona_id
        try:
            profile = self.memory.get_profile(persona_id, gid, uid)
        except Exception:
            profile = None
        if isinstance(profile, dict):
            name = str(profile.get("display_name") or "").strip()
            if name and name != uid and not name.isdigit():
                return name[:80]
        try:
            messages = self.memory.recent_messages(persona_id, gid, 40) or ()
        except Exception:
            messages = ()
        for message in reversed(tuple(messages)):
            if str(getattr(message, "sender_id", "") or "") != uid:
                continue
            name = str(getattr(message, "sender_name", "") or "").strip()
            if name and name != uid and not name.isdigit():
                return name[:80]
        return ""

    async def _mail_qq_nickname(self, group_id: str, user_id: str) -> str:
        """Fetch platform QQ nickname (not group card) for mail greetings."""

        uid = str(user_id or "").strip()
        gid = str(group_id or "").strip()
        if not uid:
            return ""
        client = None
        try:
            from .llm import AstrBotPlatformPort

            port = AstrBotPlatformPort(self.context, lambda _gid: "")
            client = port._resolve_aiocqhttp_client()
        except Exception:
            client = None
        call = getattr(client, "call_action", None) if client is not None else None
        if not callable(call):
            return ""
        try:
            user_value = int(uid) if uid.isdigit() else uid
            group_value = int(gid) if gid.isdigit() else gid
            info = await call(
                "get_group_member_info",
                group_id=group_value,
                user_id=user_value,
                no_cache=True,
            )
        except Exception:
            return ""
        if isinstance(info, dict):
            return str(info.get("nickname") or "").strip()
        return str(getattr(info, "nickname", "") or "").strip()

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
                    self._remember_mention_profiles(message)
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

    async def _message_from_event_with_mentions(self, event: Any) -> ChatMessage:
        return await self._resolve_message_mentions(
            event,
            self._message_from_event(event),
        )

    async def _resolve_message_mentions(
        self,
        event: Any,
        message: ChatMessage,
    ) -> ChatMessage:
        if not message.mentioned_user_ids:
            return message
        names = dict(message.metadata.get("mention_names") or {})
        bot = getattr(event, "bot", None)
        for user_id in message.mentioned_user_ids[:8]:
            key = (str(message.group_id), str(user_id))
            name = names.get(str(user_id)) or self._mention_name_cache.get(key, "")
            if not name:
                name = self._mail_member_display_name(*key)
            if not name and bot is not None:
                name = await resolve_member_name(bot, key[0], key[1])
            if name:
                names[str(user_id)] = name
                self._mention_name_cache[key] = name
        enriched = OneBotTranslator.enrich_mentions(message, names)
        self._remember_mention_profiles(enriched)
        return enriched

    def _remember_mention_profiles(self, message: ChatMessage) -> None:
        names = message.metadata.get("mention_names") or {}
        if not isinstance(names, dict):
            return
        persona_id = self.persona_context.persona_id
        for user_id, display_name in names.items():
            name = str(display_name or "").strip()
            if not name:
                continue
            existing = self.memory.get_profile(
                persona_id, message.group_id, str(user_id)
            ) or {}
            self.memory.upsert_profile(
                persona_id,
                message.group_id,
                str(user_id),
                name,
                str(existing.get("relationship") or ""),
                max(1, int(existing.get("authority") or 0)),
                updated_at=int(message.timestamp),
            )

    async def close(self) -> None:
        self.paused = True
        await self.commitment_scheduler.close()
        await self.runtime.close()
        for provider_runtime in tuple(self._capability_runtimes.values()):
            provider_runtime.close()
        self._capability_runtimes.clear()
        self._capability_governors.clear()
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
        tool_counts = {"llm_tool": 0, "command": 0}
        if self.settings.tools_enabled:
            try:
                for item in self.tool_orchestrator.catalog.refresh():
                    source = item.source.value
                    tool_counts[source] = tool_counts.get(source, 0) + 1
            except Exception:
                logger.exception("Groupmate host tool discovery failed")
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
            "tools": (
                "enabled" if self.settings.tools_enabled else "disabled"
            ),
            "command_bridge": (
                "enabled"
                if self.settings.command_bridge_enabled
                else "disabled"
            ),
            "mail": (
                "ready"
                if self.mail_service.available()
                else (
                    "enabled_incomplete"
                    if self.settings.mail.enabled
                    else "disabled"
                )
            ),
            "tool_candidate_limit": self.settings.tool_candidate_limit,
            "tool_count": sum(tool_counts.values()),
            "tool_counts": tool_counts,
            "database_schema": SCHEMA_VERSION,
            "commitment_scheduler": self.commitment_scheduler.mode,
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

    def cognition_snapshot(self) -> Dict[str, Any]:
        """Build the human-facing governance snapshot for the plugin page."""
        persona_id = self.persona_context.persona_id
        now = int(time.time())
        runtime_status = self.status()
        runtime_status.pop("active_persona", None)
        for snapshot in runtime_status.get("groups", {}).values():
            if isinstance(snapshot, dict):
                snapshot.pop("persona_id", None)
        display_names: Dict[tuple[str, str], str] = {}

        def display_name(group_id: str, user_id: str) -> str:
            key = (str(group_id), str(user_id))
            if key not in display_names:
                display_names[key] = self._governance_display_name(*key)
            return display_names[key]

        relationship_evidence = []
        evidence_counts: Dict[tuple[str, str], Counter] = {}
        for event in self.memory.list_relationship_evidence(persona_id, limit=800):
            canonical_user_id = self.memory.resolve_member_subject_id(
                persona_id, event.group_id, event.user_id
            )
            key = (event.group_id, canonical_user_id)
            evidence_counts.setdefault(key, Counter())[event.status.value] += 1
            relationship_evidence.append(
                {
                    "event_id": event.event_id,
                    "group_id": event.group_id,
                    "user_id": canonical_user_id,
                    "source_user_id": event.user_id,
                    "display_name": display_name(event.group_id, canonical_user_id),
                    "kind": event.kind.value,
                    "source_message_id": event.source_message_id,
                    "confidence": event.confidence,
                    "occurred_at": event.occurred_at,
                    "decision_id": event.decision_id,
                    "evidence_text": event.evidence_text,
                    "reason_code": event.reason_code,
                    "extractor_version": event.extractor_version,
                    "status": event.status.value,
                    "reviewed_at": event.reviewed_at,
                    "review_code": event.review_code,
                    "review_reason": event.review_reason,
                }
            )

        relationships = []
        relationship_keys = set()
        for state in self.memory.list_relationship_states(persona_id, limit=300):
            canonical_user_id = self.memory.resolve_member_subject_id(
                persona_id, state.group_id, state.user_id
            )
            key = (state.group_id, canonical_user_id)
            if key in relationship_keys:
                continue
            relationship_keys.add(key)
            aggregate = self.memory.get_member_relationship_state(
                persona_id,
                state.group_id,
                canonical_user_id,
                configured_relationship=state.configured_relationship,
                now=now,
            ) or state
            counts = evidence_counts.get(key, Counter())
            relationships.append(
                {
                    "group_id": aggregate.group_id,
                    "user_id": canonical_user_id,
                    "display_name": display_name(aggregate.group_id, canonical_user_id),
                    "familiarity": aggregate.familiarity,
                    "affinity": aggregate.affinity,
                    "trust": aggregate.trust,
                    "boundary_pressure": aggregate.boundary_pressure,
                    "interaction_count": aggregate.interaction_count,
                    "last_interaction_at": aggregate.last_interaction_at,
                    "configured_relationship": aggregate.configured_relationship or "",
                    "updated_at": aggregate.updated_at,
                    "accepted_evidence_count": int(counts.get("accepted", 0)),
                    "pending_evidence_count": int(counts.get("pending", 0)),
                    "rejected_evidence_count": int(counts.get("rejected", 0)),
                }
            )
        for group_id, user_id in evidence_counts:
            if (group_id, user_id) in relationship_keys:
                continue
            counts = evidence_counts[(group_id, user_id)]
            relationships.append(
                {
                    "group_id": group_id,
                    "user_id": user_id,
                    "display_name": display_name(group_id, user_id),
                    "familiarity": 0,
                    "affinity": 0,
                    "trust": 0,
                    "boundary_pressure": 0,
                    "interaction_count": 0,
                    "last_interaction_at": 0,
                    "configured_relationship": "",
                    "updated_at": 0,
                    "accepted_evidence_count": int(counts.get("accepted", 0)),
                    "pending_evidence_count": int(counts.get("pending", 0)),
                    "rejected_evidence_count": int(counts.get("rejected", 0)),
                }
            )

        memories = []
        for item in self.memory.list_recent_memories(
            persona_id, now=now, limit=300
        ):
            canonical_subject_id = self.memory.resolve_member_subject_id(
                persona_id, item.group_id, item.subject_id
            )
            memories.append(
                {
                    "memory_id": item.memory_id,
                    "group_id": item.group_id,
                    "subject_id": canonical_subject_id,
                    "source_subject_id": item.subject_id,
                    "subject_name": display_name(item.group_id, canonical_subject_id),
                    "kind": item.kind.value,
                    "text": item.text,
                    "created_at": item.created_at,
                    "expires_at": item.expires_at,
                    "confidence": item.confidence,
                    "importance": item.importance,
                    "scope": item.scope.value,
                    "sensitivity": item.sensitivity.value,
                }
            )

        continuity = []
        for item in self.memory.list_continuity_items(
            persona_id,
            statuses=(
                ContinuityStatus.OPEN,
                ContinuityStatus.COMPLETED,
                ContinuityStatus.CANCELLED,
            ),
            limit=500,
        ):
            canonical_subject_id = self.memory.resolve_member_subject_id(
                persona_id, item.group_id, item.subject_id
            )
            continuity.append(
                {
                    "item_id": item.item_id,
                    "group_id": item.group_id,
                    "subject_id": canonical_subject_id,
                    "source_subject_id": item.subject_id,
                    "subject_name": display_name(item.group_id, canonical_subject_id),
                    "kind": item.kind.value,
                    "summary": item.summary,
                    "source_message_id": item.source_message_id,
                    "source_quote": item.source_quote,
                    "created_at": item.created_at,
                    "updated_at": item.updated_at,
                    "due_at": item.due_at,
                    "confidence": item.confidence,
                    "status": item.status.value,
                    "resolution_message_id": item.resolution_message_id,
                    "resolution_quote": item.resolution_quote,
                    "resolved_at": item.resolved_at,
                }
            )

        self_commitments = []
        for item in self.memory.list_self_commitments(
            persona_id,
            statuses=(
                SelfCommitmentStatus.PENDING,
                SelfCommitmentStatus.IN_PROGRESS,
                SelfCommitmentStatus.COMPLETED,
                SelfCommitmentStatus.BLOCKED,
                SelfCommitmentStatus.WITHDRAWN,
            ),
            limit=500,
        ):
            canonical_subject_id = self.memory.resolve_member_subject_id(
                persona_id, item.group_id, item.beneficiary_subject_id
            )
            self_commitments.append(
                {
                    "commitment_id": item.commitment_id,
                    "group_id": item.group_id,
                    "beneficiary_subject_id": canonical_subject_id,
                    "source_beneficiary_subject_id": item.beneficiary_subject_id,
                    "beneficiary_name": display_name(
                        item.group_id, canonical_subject_id
                    ),
                    "summary": item.summary,
                    "source_decision_id": item.source_decision_id,
                    "source_message_id": item.source_message_id,
                    "source_quote": item.source_quote,
                    "created_at": item.created_at,
                    "updated_at": item.updated_at,
                    "status": item.status.value,
                    "required_capability": item.required_capability,
                    "fulfillment_mode": item.fulfillment_mode,
                    "due_at": item.due_at,
                    "confidence": item.confidence,
                    "result_decision_id": item.result_decision_id,
                    "result_quote": item.result_quote,
                    "result_facts": list(item.result_facts),
                    "failure_code": item.failure_code,
                    "resolved_at": item.resolved_at,
                    "next_attempt_at": item.next_attempt_at,
                    "attempt_count": item.attempt_count,
                    "lease_until": item.lease_until,
                    "last_attempt_at": item.last_attempt_at,
                    "last_delivery_at": item.last_delivery_at,
                }
            )

        capabilities = []
        if self.settings.tools_enabled:
            try:
                descriptors = self.tool_orchestrator.catalog.refresh()
            except Exception:
                logger.exception("Groupmate governance tool discovery failed")
                descriptors = ()
            for item in descriptors:
                capabilities.append(
                    {
                        "tool_id": item.tool_id,
                        "name": item.name,
                        "description": item.description,
                        "source": item.source.value,
                        "risk": item.risk.value,
                        "permission": item.permission,
                        "compatible": bool(item.compatible),
                        "compatibility_reason": item.compatibility_reason,
                    }
                )

        decisions = self.memory.recent_decisions(persona_id, limit=200)
        sent_count = sum(1 for item in decisions if item.get("sent"))
        direct_count = sum(
            1
            for item in decisions
            if item.get("participation") == "direct_required"
        )
        reason_counts = Counter(
            str(item.get("end_reason") or "unknown") for item in decisions
        )
        groups = list(self.memory.decision_group_ids(persona_id))
        for group_id in runtime_status.get("bootstrapped", ()):
            if group_id not in groups:
                groups.append(group_id)
        for group_id in self.settings.enabled_groups:
            group_id = str(group_id)
            if group_id not in groups:
                groups.append(group_id)
        for group_id, _user_id in evidence_counts:
            if group_id not in groups:
                groups.append(group_id)
        for item in continuity:
            if item["group_id"] not in groups:
                groups.append(item["group_id"])
        for item in self_commitments:
            if item["group_id"] not in groups:
                groups.append(item["group_id"])
        for group_id in self.settings.relationship_learning_groups:
            group_id = str(group_id)
            if group_id not in groups:
                groups.append(group_id)
        learning_groups = []
        active_learning_groups = set(self.settings.relationship_learning_groups)
        for group_id in groups:
            quality = self.memory.relationship_learning_quality(persona_id, group_id)
            sample_ready = (
                quality["reviewed_count"]
                >= self.settings.relationship_learning_min_reviewed
            )
            quality_ready = (
                quality["error_rate"]
                <= self.settings.relationship_learning_max_error_rate
            )
            configured = group_id in active_learning_groups
            learning_groups.append(
                {
                    "group_id": group_id,
                    **quality,
                    "configured": configured,
                    "eligible": sample_ready and quality_ready,
                    "auto_apply": configured and sample_ready and quality_ready,
                    "min_reviewed_samples": self.settings.relationship_learning_min_reviewed,
                    "max_error_rate": self.settings.relationship_learning_max_error_rate,
                }
            )
        governance = []
        for action in self.memory.list_governance_actions(persona_id, limit=150):
            enriched = dict(action)
            enriched["subject_name"] = display_name(
                action["group_id"], action["subject_id"]
            )
            governance.append(enriched)

        members = self.memory.list_member_profiles(persona_id, limit=800)
        member_names = {
            (item["group_id"], item["subject_id"]): item["address"]
            for item in members
        }
        for item in members:
            canonical_id = item.get("canonical_subject_id") or ""
            if canonical_id:
                item["canonical_name"] = member_names.get(
                    (item["group_id"], canonical_id), item.get("canonical_name") or "成员"
                )
            if item["group_id"] not in groups:
                groups.append(item["group_id"])

        return {
            "identity": {
                "display_name": self.persona_context.display_name,
                "aliases": list(self.persona_context.aliases),
                "principles": [
                    "在群里以爱弥斯自己的口吻相处",
                    "先判断该不该说，再决定说什么",
                    "记住关系与共同经历，也允许被纠正和遗忘",
                    "对亲口答应的事情负责，完成、受阻或撤回都保留真实结果",
                    "身份问题按关系和语境自然回应，不复述系统定义",
                ],
            },
            "runtime": runtime_status,
            "groups": groups,
            "relationships": relationships,
            "members": members,
            "relationship_evidence": relationship_evidence,
            "relationship_learning": {
                "groups": learning_groups,
                "configured_groups": sorted(active_learning_groups),
                "min_reviewed_samples": self.settings.relationship_learning_min_reviewed,
                "max_error_rate": self.settings.relationship_learning_max_error_rate,
            },
            "memories": memories,
            "continuity": continuity,
            "self_commitments": self_commitments,
            "capabilities": capabilities,
            "governance": governance,
            "quality": {
                "decision_count": len(decisions),
                "sent_count": sent_count,
                "silent_count": len(decisions) - sent_count,
                "direct_count": direct_count,
                "sent_rate": (
                    round(sent_count / len(decisions), 4) if decisions else 0
                ),
                "reason_counts": dict(reason_counts.most_common(12)),
                "accepted_relationship_evidence": sum(
                    1
                    for item in relationship_evidence
                    if item["status"] == "accepted"
                ),
                "rejected_relationship_evidence": sum(
                    1
                    for item in relationship_evidence
                    if item["status"] == "rejected"
                ),
            },
            "privacy": {
                "memory_deletion": "available",
                "raw_message_retention": "not_configured",
            },
        }

    def delete_governed_memory(
        self, memory_id: str, reason: str
    ) -> Optional[Dict[str, Any]]:
        return self.memory.delete_memory_with_audit(
            self.persona_context.persona_id,
            str(memory_id),
            reason=str(reason or "plugin_page_deletion"),
            actor="AstrBot 插件管理员",
            now=int(time.time()),
        )

    def correct_relationship(
        self,
        *,
        group_id: str,
        user_id: str,
        familiarity: int,
        affinity: int,
        trust: int,
        boundary_pressure: int,
        reason: str,
    ) -> Dict[str, Any]:
        persona_id = self.persona_context.persona_id
        current = self.memory.get_relationship_state(
            persona_id, group_id, user_id
        ) or RelationshipState(group_id=str(group_id), user_id=str(user_id))
        corrected = RelationshipState(
            group_id=str(group_id),
            user_id=str(user_id),
            familiarity=max(0, min(100, int(familiarity))),
            affinity=max(-100, min(100, int(affinity))),
            trust=max(-100, min(100, int(trust))),
            boundary_pressure=max(0, min(100, int(boundary_pressure))),
            interaction_count=current.interaction_count,
            last_interaction_at=current.last_interaction_at,
            configured_relationship=current.configured_relationship,
            updated_at=int(time.time()),
        )
        action = self.memory.correct_relationship_with_audit(
            persona_id,
            corrected,
            reason=str(reason or "管理员修正关系"),
            actor="AstrBot 插件管理员",
            now=int(time.time()),
        )
        relationship = {
            "group_id": corrected.group_id,
            "user_id": corrected.user_id,
            "display_name": self._governance_display_name(
                corrected.group_id, corrected.user_id
            ),
            "familiarity": corrected.familiarity,
            "affinity": corrected.affinity,
            "trust": corrected.trust,
            "boundary_pressure": corrected.boundary_pressure,
            "interaction_count": corrected.interaction_count,
            "last_interaction_at": corrected.last_interaction_at,
            "configured_relationship": corrected.configured_relationship or "",
            "updated_at": corrected.updated_at,
        }
        return {"relationship": relationship, "action": action}

    def correct_member_profile(
        self,
        *,
        group_id: str,
        subject_id: str,
        preferred_address: str,
        reason: str,
    ) -> Dict[str, Any]:
        return self.memory.correct_member_profile_with_audit(
            self.persona_context.persona_id,
            str(group_id),
            str(subject_id),
            str(preferred_address),
            reason=str(reason or "管理员修正成员称呼"),
            actor="AstrBot 插件管理员",
            now=int(time.time()),
        )

    def correct_continuity_status(
        self,
        *,
        item_id: str,
        status: str,
        reason: str,
    ) -> Optional[Dict[str, Any]]:
        action = self.memory.update_continuity_with_audit(
            self.persona_context.persona_id,
            str(item_id),
            status=ContinuityStatus(str(status)),
            reason=str(reason or "管理员修正未完事项"),
            actor="AstrBot 插件管理员",
            now=int(time.time()),
        )
        item = self.memory.get_continuity_item(
            self.persona_context.persona_id, str(item_id)
        )
        if action is None or item is None:
            return None
        return {
            "item": {
                "item_id": item.item_id,
                "group_id": item.group_id,
                "subject_id": self.memory.resolve_member_subject_id(
                    self.persona_context.persona_id,
                    item.group_id,
                    item.subject_id,
                ),
                "kind": item.kind.value,
                "summary": item.summary,
                "source_quote": item.source_quote,
                "created_at": item.created_at,
                "updated_at": item.updated_at,
                "status": item.status.value,
                "resolved_at": item.resolved_at,
            },
            "action": action,
        }

    def correct_self_commitment_status(
        self,
        *,
        commitment_id: str,
        status: str,
        reason: str,
    ) -> Optional[Dict[str, Any]]:
        action = self.memory.update_self_commitment_with_audit(
            self.persona_context.persona_id,
            str(commitment_id),
            status=SelfCommitmentStatus(str(status)),
            reason=str(reason or "管理员修正自我承诺"),
            actor="AstrBot 插件管理员",
            now=int(time.time()),
        )
        item = self.memory.get_self_commitment(
            self.persona_context.persona_id, str(commitment_id)
        )
        if action is None or item is None:
            return None
        return {
            "commitment": {
                "commitment_id": item.commitment_id,
                "group_id": item.group_id,
                "beneficiary_subject_id": self.memory.resolve_member_subject_id(
                    self.persona_context.persona_id,
                    item.group_id,
                    item.beneficiary_subject_id,
                ),
                "summary": item.summary,
                "status": item.status.value,
                "updated_at": item.updated_at,
            },
            "action": action,
        }

    async def run_self_commitment_now(self, commitment_id: str) -> Dict[str, Any]:
        if self.paused:
            raise ValueError("Groupmate is paused")
        item = self.memory.get_self_commitment(
            self.persona_context.persona_id, str(commitment_id)
        )
        if item is None:
            raise KeyError("self commitment not found")
        if item.status not in {
            SelfCommitmentStatus.PENDING,
            SelfCommitmentStatus.IN_PROGRESS,
        }:
            raise ValueError("self commitment is already closed")
        result = await self.commitment_scheduler.run_due(
            commitment_id=item.commitment_id,
            force=True,
        )
        if int(result.get("processed") or 0) != 1:
            reason = str(result.get("reason") or "not_processed")
            raise ValueError("self commitment was not processed: " + reason)
        updated = self.memory.get_self_commitment(
            self.persona_context.persona_id, item.commitment_id
        )
        return {
            "processed": int(result.get("processed") or 0),
            "mode": result.get("mode"),
            "reason": result.get("reason"),
            "commitment": {
                "commitment_id": updated.commitment_id,
                "status": updated.status.value,
                "failure_code": updated.failure_code,
                "last_attempt_at": updated.last_attempt_at,
                "last_delivery_at": updated.last_delivery_at,
            },
        }

    def link_member_identity(
        self,
        *,
        group_id: str,
        source_subject_id: str,
        canonical_subject_id: str,
        reason: str,
    ) -> Dict[str, Any]:
        return self.memory.link_member_identity_with_audit(
            self.persona_context.persona_id,
            str(group_id),
            str(source_subject_id),
            str(canonical_subject_id),
            reason=str(reason or "管理员关联误识别身份"),
            actor="AstrBot 插件管理员",
            now=int(time.time()),
        )

    def reject_relationship_evidence(
        self, event_id: str, reason: str
    ) -> Optional[Dict[str, Any]]:
        return self.memory.reject_social_event_with_audit(
            self.persona_context.persona_id,
            str(event_id),
            reason=str(reason or "管理员否定关系证据"),
            actor="AstrBot 插件管理员",
            now=int(time.time()),
        )

    def review_relationship_evidence(
        self, event_id: str, outcome: str, reason: str
    ) -> Optional[Dict[str, Any]]:
        return self.memory.review_pending_social_event_with_audit(
            self.persona_context.persona_id,
            str(event_id),
            outcome=str(outcome),
            reason=str(reason or "管理员复核关系证据"),
            actor="AstrBot 插件管理员",
            now=int(time.time()),
        )

    def revert_governance_action(
        self, action_id: str, reason: str
    ) -> Dict[str, Any]:
        return self.memory.revert_governance_action(
            self.persona_context.persona_id,
            str(action_id),
            reason=str(reason or "管理员回滚治理操作"),
            actor="AstrBot 插件管理员",
            now=int(time.time()),
        )

    def _governance_display_name(self, group_id: str, user_id: str) -> str:
        canonical_user_id = self.memory.resolve_member_subject_id(
            self.persona_context.persona_id, group_id, user_id
        )
        profile = self.memory.get_profile(
            self.persona_context.persona_id, group_id, canonical_user_id
        )
        if profile:
            preferred = str(profile.get("preferred_address") or "").strip()
            if preferred:
                return preferred
            stored = str(profile.get("display_name") or "").strip()
            if stored and stored != str(canonical_user_id):
                return stored
        name = self._mail_member_display_name(group_id, canonical_user_id)
        if name:
            return name
        value = str(canonical_user_id or "").strip()
        return "成员 {}".format(value[-4:] or "未知")

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
        trace = self.memory.decision_trace(
            self.persona_context.persona_id,
            decision_id,
        )
        if trace is None:
            return None
        addressee = trace.get("addressee") or {}
        for value in addressee.values():
            if not isinstance(value, dict):
                continue
            name = str(value.get("name") or "").strip()
            value["name"] = name or (
                "群聊" if value.get("kind") == "group" else "未确定"
            )
        return trace

    def _workflow_for(self, group_id: str, persona_context):
        getter = lambda gid: self._provider_by_group.get(
            gid,
            self.settings.generation_provider,
        )
        vision = AstrBotVisionPort(
            self.context,
            lambda gid: self.settings.vision_provider or getter(gid),
        )
        provider_runtime = self._capability_runtime_for(group_id, vision=vision)
        capabilities = provider_runtime.registry
        governor = self._capability_governor_for(group_id)

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
                self._umo_for_group,
                poke_interval_seconds=(
                    self.behavior.interaction.poke_interval_seconds
                ),
            ),
            memory=self.memory,
            persona_context=persona_context,
            behavior=self.behavior,
            vision_enabled=self.settings.vision_enabled,
            poke_back_enabled=self.settings.poke_back_enabled,
            relationship_learning_groups=self.settings.relationship_learning_groups,
            relationship_learning_min_reviewed=(
                self.settings.relationship_learning_min_reviewed
            ),
            relationship_learning_max_error_rate=(
                self.settings.relationship_learning_max_error_rate
            ),
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

    def _umo_for_group(self, group_id: str) -> str:
        group_id = str(group_id)
        cached = str(self._umo_by_group.get(group_id) or "").strip()
        if cached:
            return cached
        platform_id = self._resolve_platform_id()
        return "{}:GroupMessage:{}".format(platform_id, group_id)

    def _remember_group_umo(self, group_id: str, umo: str) -> None:
        group_id = str(group_id or "").strip()
        umo = str(umo or "").strip()
        if not group_id or not umo:
            return
        if self._umo_by_group.get(group_id) == umo:
            return
        self._umo_by_group[group_id] = umo
        try:
            self.data_dir.mkdir(parents=True, exist_ok=True)
            self._umo_path.write_text(
                json.dumps(self._umo_by_group, ensure_ascii=False, sort_keys=True),
                encoding="utf-8",
            )
        except Exception:
            logger.exception("Groupmate failed to persist group UMO map")

    def _load_group_umos(self) -> Dict[str, str]:
        try:
            raw = json.loads(self._umo_path.read_text(encoding="utf-8"))
        except Exception:
            return {}
        if not isinstance(raw, dict):
            return {}
        return {
            str(group_id): str(umo)
            for group_id, umo in raw.items()
            if str(group_id).strip() and str(umo).strip()
        }

    def _resolve_platform_id(self) -> str:
        manager = getattr(self.context, "platform_manager", None)
        insts = list(getattr(manager, "platform_insts", None) or ())
        preferred = ""
        for platform in insts:
            meta_fn = getattr(platform, "meta", None)
            try:
                meta = meta_fn() if callable(meta_fn) else meta_fn
            except Exception:
                meta = None
            platform_id = str(getattr(meta, "id", "") or "").strip()
            name = str(getattr(meta, "name", "") or "").strip().casefold()
            if not platform_id:
                continue
            if name == "aiocqhttp" or "aiocqhttp" in name:
                return platform_id
            if name != "webchat" and not preferred:
                preferred = platform_id
        return preferred or "aiocqhttp"

    def _platform_for_group(self, group_id: str):
        del group_id
        return AstrBotPlatformPort(
            self.context,
            self._umo_for_group,
            poke_interval_seconds=self.behavior.interaction.poke_interval_seconds,
        )

    def _capability_runtime_for(self, group_id: str, vision=None):
        group_id = str(group_id)
        provider_runtime = self._capability_runtimes.get(group_id)
        if provider_runtime is None:
            if vision is None:
                getter = lambda gid: self._provider_by_group.get(
                    gid, self.settings.generation_provider
                )
                vision = AstrBotVisionPort(
                    self.context,
                    lambda gid: self.settings.vision_provider or getter(gid),
                )
            provider_runtime = CapabilityProviderRuntime(
                (
                    VisionProvider(
                        vision if self.settings.vision_enabled else None
                    ),
                )
            )
            self._capability_runtimes[group_id] = provider_runtime
        return provider_runtime

    def _capability_governor_for(self, group_id: str) -> CapabilityGovernor:
        group_id = str(group_id)
        governor = self._capability_governors.get(group_id)
        if governor is None:
            governor = CapabilityGovernor(
                self._capability_runtime_for(group_id).registry
            )
            self._capability_governors[group_id] = governor
        return governor

    def _host_timezone(self) -> str:
        getter = getattr(self.context, "get_config", None)
        if callable(getter):
            try:
                config = getter()
                if isinstance(config, dict):
                    value = str(config.get("timezone") or "").strip()
                    if value:
                        return value
            except Exception:
                pass
        return "Asia/Shanghai"

    def _group_enabled(self, group_id: str) -> bool:
        groups = self.settings.enabled_groups
        return not groups or group_id in {str(item) for item in groups}

class _SystemClock:
    def now(self) -> int:
        import time

        return int(time.time())
