"""AstrBot composition boundary for Social Runtime v2."""

from __future__ import annotations

import asyncio
from collections import deque
from contextlib import suppress
from dataclasses import asdict, replace
import inspect
import time
from pathlib import Path
from typing import Callable, Mapping

from ..settings import SOCIAL_RUNTIME_DATABASE_NAME, SocialRuntimeSettings
from ..social_runtime.addressing import PersonaAddressResolver
from ..social_runtime.contracts import RuntimeMode, SocialEventEnvelope
from ..social_runtime.control.config_versions import (
    ConfigSnapshot,
    ConfigVersionRepository,
)
from ..social_runtime.control.message_traces import MessageTraceRepository
from ..social_runtime.cognition.ambient_worker import DirectAmbientWorker
from ..social_runtime.manager import SocialRuntimeManager
from ..social_runtime.ownership import ExternalTriggerPolicy
from ..social_runtime.persona.profile import GroupmatePersonaProfile
from ..social_runtime.persona.presets import PERSONA_CANON_PRESETS
from ..social_runtime.replying import ReplyExecutor, ReplyPlanner
from ..social_runtime.delivery.dispatcher import DeliveryDispatcher
from ..social_runtime.actions.contracts import OutboxStatus
from .astrbot_delivery import AstrBotOneBotSender
from .astrbot_events import AstrBotEventTranslator
from .astrbot_models import AstrBotModelPort
from .affection_card import AffectionCardPresenter
from .affection_query import AffectionQuery, is_affection_query
from .deepseek_cognition import DeepSeekCognitionClient
from .deepseek_profile import DeepSeekProfileClient
from .onebot_delivery import OneBotDeliveryAdapter
from ..social_runtime.profile.extractor import ProfileExtractor
from ..social_runtime.profile.policy import ProfileEvidencePolicy
from ..social_runtime.profile.repository import ProfileRepository
from ..social_runtime.profile.service import ProfileService
from ..social_runtime.society.affection_leaderboard import (
    AffectionLeaderboardService,
)


class AstrBotSocialRuntimeBridge:
    def __init__(
        self,
        context: object,
        settings: SocialRuntimeSettings,
        data_dir: Path,
        *,
        shadow_reviews: object | None = None,
        clock: Callable[[], float] | None = None,
        cognition_client_factory: Callable[[SocialRuntimeSettings], object]
        | None = None,
        profile_client_factory: Callable[[SocialRuntimeSettings], object]
        | None = None,
    ) -> None:
        self.context = context
        self.settings = settings
        self.data_dir = Path(data_dir)
        self.clock = time.time if clock is None else clock
        self._cognition_client_factory = (
            cognition_client_factory or self._new_cognition_client
        )
        self._profile_client_factory = (
            profile_client_factory or self._new_profile_client
        )
        self._external_trigger_policy = ExternalTriggerPolicy.from_entries(
            command_prefixes=settings.external_command_prefixes,
            link_domains=settings.external_link_domains,
        )
        self.translator = AstrBotEventTranslator(
            settings.persona_id,
            external_trigger_policy=self._external_trigger_policy,
            clock=self.clock,
        )
        self._config_repository: ConfigVersionRepository | None = None
        self._manager: SocialRuntimeManager | None = None
        self._cognition_client: object | None = None
        self._profile_client: object | None = None
        self._profile_service: ProfileService | None = None
        self._trace_repository: MessageTraceRepository | None = None
        self.shadow_reviews = shadow_reviews
        self.shadow_review_error: str | None = None
        self.attention_wakeup_error: str | None = None
        self.cognition_diagnostics: list[str] = []
        self.reply_error: str | None = None
        self.trace_error: str | None = None
        self._reply_planner = ReplyPlanner()
        self._reply_executor: ReplyExecutor | None = None
        self._dispatcher: DeliveryDispatcher | None = None
        self._reply_lock = asyncio.Lock()
        self._recent_outputs: dict[str, deque[str]] = {}
        self._attention_changed = asyncio.Event()
        self._attention_task: asyncio.Task[None] | None = None
        self._started = False

    async def prepare_affection_query(
        self, event: object
    ) -> AffectionQuery | None:
        """Build the local query result without entering chat or cognition."""

        if not self._started or self._manager is None:
            return None
        translated = self.translator.translate(event)
        if not is_affection_query(translated.payload.get("text")):
            return None
        group_id = str(translated.group_id or "").strip()
        if not group_id or self._manager.group_mode(group_id) is RuntimeMode.OFF:
            return None

        now = int(self.clock())
        participants = self.trace_repository.participants
        participants.remember(translated)
        bot_id = str(translated.payload.get("bot_id") or "").strip()
        since = now - 30 * 24 * 60 * 60
        recent_ids = participants.recent_actor_ids(
            persona_id=self.settings.persona_id,
            group_id=group_id,
            since=since,
        )
        members = participants.active_members(
            persona_id=self.settings.persona_id,
            group_id=group_id,
            since=since,
            exclude_actor_ids=(bot_id,),
        )
        group_name = str(
            translated.payload.get("group_name") or "当前群聊"
        )
        roster_complete = False
        group_getter = getattr(event, "get_group", None)
        if callable(group_getter):
            try:
                pending = group_getter(group_id)
                group = (
                    await asyncio.wait_for(pending, timeout=5.0)
                    if inspect.isawaitable(pending)
                    else pending
                )
                platform_members = self._normalize_group_members(
                    group,
                    bot_id=bot_id,
                    now=now,
                )
                if platform_members:
                    members = platform_members
                    roster_complete = True
                    platform_name = (
                        group.get("group_name")
                        if isinstance(group, Mapping)
                        else getattr(group, "group_name", "")
                    )
                    group_name = str(platform_name or group_name)
            except Exception:
                roster_complete = False
        leaderboard = AffectionLeaderboardService(self._manager.society).build(
            persona_id=self.settings.persona_id,
            group_id=group_id,
            group_name=group_name,
            requester_id=translated.actor_id,
            members=members,
            updated_at=now,
            recent_active_count=len(recent_ids - {bot_id}),
            roster_complete=roster_complete,
        )
        self._record_trace(
            self.trace_repository.record_affection_query,
            translated.event_id,
            now,
        )
        return AffectionQuery(
            leaderboard=leaderboard,
            pages=AffectionCardPresenter().pages(leaderboard),
        )

    @staticmethod
    def _normalize_group_members(
        group: object,
        *,
        bot_id: str,
        now: int,
    ) -> tuple[dict[str, object], ...]:
        value = (
            group.get("members")
            if isinstance(group, Mapping)
            else getattr(group, "members", None)
        )
        if not isinstance(value, (list, tuple)):
            return ()
        normalized: list[dict[str, object]] = []
        seen: set[str] = set()
        for member in value:
            if isinstance(member, Mapping):
                actor_id = str(
                    member.get("user_id") or member.get("actor_id") or ""
                ).strip()
                display_name = str(
                    member.get("card")
                    or member.get("nickname")
                    or member.get("display_name")
                    or "群成员"
                )
            else:
                actor_id = str(
                    getattr(member, "user_id", "")
                    or getattr(member, "actor_id", "")
                ).strip()
                display_name = str(
                    getattr(member, "card", "")
                    or getattr(member, "nickname", "")
                    or getattr(member, "display_name", "")
                    or "群成员"
                )
            if not actor_id or actor_id == bot_id or actor_id in seen:
                continue
            seen.add(actor_id)
            normalized.append(
                {
                    "actor_id": actor_id,
                    "display_name": " ".join(display_name.split())[:48]
                    or "群成员",
                    "updated_at": int(now),
                }
            )
        return tuple(normalized)

    @property
    def trace_repository(self) -> MessageTraceRepository:
        """Create trace storage only when the configured plugin actually needs it."""
        if self._trace_repository is None:
            self._trace_repository = MessageTraceRepository(
                self.data_dir / SOCIAL_RUNTIME_DATABASE_NAME
            )
        return self._trace_repository

    @property
    def manager(self) -> SocialRuntimeManager:
        if self._manager is None:
            raise RuntimeError("Social Runtime is disabled")
        return self._manager

    @property
    def profile_service(self) -> ProfileService:
        if self._profile_service is None:
            raise RuntimeError("member profiling is disabled")
        return self._profile_service

    async def start(self) -> None:
        if self._started:
            return
        mode = RuntimeMode(self.settings.runtime_mode)
        if (
            mode in {RuntimeMode.SHADOW, RuntimeMode.SOCIAL_RUNTIME}
            and self.settings.enabled_groups
        ):
            config_repository = ConfigVersionRepository(
                self.data_dir / SOCIAL_RUNTIME_DATABASE_NAME
            )
            self._config_repository = config_repository
            reply_model = AstrBotModelPort(
                self.context, self.settings.generation_provider
            )
            cognition_client = self._cognition_client_factory(self.settings)
            if cognition_client is None:
                raise RuntimeError("direct cognition client is unavailable")
            manager = SocialRuntimeManager(
                database_path=self.data_dir / SOCIAL_RUNTIME_DATABASE_NAME,
                persona_id=self.settings.persona_id,
                mode=mode,
                enabled_groups=self.settings.enabled_groups,
                social_runtime_test_groups=self.settings.social_runtime_test_groups,
                cognition_workers={
                    "ambient_social_assessor": DirectAmbientWorker(
                        cognition_client
                    )
                },
                worker_concurrency_limit=self.settings.worker_concurrency_limit,
                worker_timeout_seconds=self.settings.cognition_timeout_seconds,
                persona_profile_loader=self._persona_config_snapshot,
                clock=self.clock,
            )
            profile_client = None
            profile_service = None
            if self.settings.profile_enabled:
                profile_client = self._profile_client_factory(self.settings)
                if profile_client is None:
                    raise RuntimeError("direct profile client is unavailable")
                profile_service = ProfileService(
                    repository=ProfileRepository(
                        self.data_dir / SOCIAL_RUNTIME_DATABASE_NAME
                    ),
                    extractor=ProfileExtractor(
                        profile_client, ProfileEvidencePolicy()
                    ),
                    persona_id=self.settings.persona_id,
                    group_ids=self.settings.enabled_groups,
                    batch_size=self.settings.profile_batch_messages,
                    interval_seconds=(
                        self.settings.profile_batch_interval_seconds
                    ),
                    clock=self.clock,
                )
            try:
                await manager.start()
                if profile_service is not None:
                    await profile_service.start()
                self._manager = manager
                self._cognition_client = cognition_client
                self._profile_client = profile_client
                self._profile_service = profile_service
                self._reply_executor = ReplyExecutor(
                    manager.reply_plans,
                    manager.outbox,
                    reply_model,
                )
                self._dispatcher = DeliveryDispatcher(
                    manager.outbox,
                    OneBotDeliveryAdapter(
                        AstrBotOneBotSender(self.context), clock=self.clock
                    ),
                    receipt_handler=manager.coordinator.apply_delivery_receipt,
                )
                self._attention_task = asyncio.create_task(
                    self._attention_wakeup_loop(manager),
                    name=f"groupmate-attention:{self.settings.persona_id}",
                )
            except BaseException:
                self._manager = None
                self._cognition_client = None
                self._profile_client = None
                self._profile_service = None
                if profile_service is not None:
                    with suppress(Exception):
                        await profile_service.close()
                with suppress(Exception):
                    await manager.close()
                close = getattr(cognition_client, "close", None)
                if callable(close):
                    with suppress(Exception):
                        await close()
                profile_close = getattr(profile_client, "close", None)
                if callable(profile_close):
                    with suppress(Exception):
                        await profile_close()
                raise
        self._started = True
        self._reconcile_shadow_reviews()

    async def handle_event(self, event: object):
        if not self._started:
            await self.start()
        if self._manager is None:
            return None
        translated = self._resolve_interaction(self.translator.translate(event))
        if self._owns_host_response(translated):
            stop_event = getattr(event, "stop_event", None)
            if callable(stop_event):
                stop_event()
        self._record_trace(
            self.trace_repository.record_received,
            translated,
            self.settings.runtime_mode,
            int(self.clock()),
        )
        await self._observe_profile(translated)
        if translated.payload.get("social_eligible") is not False:
            self._record_trace(
                self.trace_repository.mark_entered,
                translated.event_id,
                int(self.clock()),
            )
        result = await self._manager.ingest(translated)
        if result is not None and result.inserted:
            evaluations = await self._manager.drain()
            await self._handle_evaluations(evaluations)
            self._attention_changed.set()
        self._reconcile_shadow_reviews()
        return result

    def _owns_host_response(self, event: SocialEventEnvelope) -> bool:
        if self._manager is None:
            return False
        payload = event.payload
        return bool(
            self._manager.group_mode(str(event.group_id or ""))
            is RuntimeMode.SOCIAL_RUNTIME
            and payload.get("direct_address")
            and payload.get("social_eligible") is not False
            and payload.get("interaction_owner") != "EXTERNAL_PLUGIN"
        )

    async def observe_event(self, event: object) -> None:
        """Record arrival and route facts without entering Social Runtime."""

        if not self.settings.enabled_groups:
            return

        translated = self._resolve_interaction(self.translator.translate(event))
        self._record_trace(
            self.trace_repository.record_received,
            translated,
            self.settings.runtime_mode,
            int(self.clock()),
        )
        await self._observe_profile(translated)

    async def _observe_profile(self, event: SocialEventEnvelope) -> None:
        service = self._profile_service
        if service is not None:
            await service.observe(event)

    def _resolve_interaction(
        self, event: SocialEventEnvelope
    ) -> SocialEventEnvelope:
        profile = self._profile_snapshot(str(event.group_id or ""))
        identity = profile["identity"]
        resolver = PersonaAddressResolver(
            str(identity["name"]),
            tuple(str(value) for value in identity.get("aliases", ())),
        )
        resolution = resolver.resolve(
            text=str(event.payload.get("text") or ""),
            mentions_bot=bool(event.payload.get("mentions_bot")),
            reply_to_bot=bool(event.payload.get("reply_to_bot")),
        )
        ownership = self._external_trigger_policy.classify(
            resolution.address_remainder
        )
        payload = dict(event.payload)
        payload.update(asdict(resolution))
        payload["direct_address"] = resolution.addressed_to_bot
        if ownership is not None:
            ownership_source = ownership.source
            if resolution.matched_alias is not None:
                ownership_source = f"alias_stripped_{ownership_source}"
            payload.update(
                {
                    "interaction_owner": ownership.owner.value,
                    "social_eligible": ownership.social_eligible,
                    "owner_ref": ownership.owner_ref,
                    "ownership_source": ownership_source,
                    "external_trigger_kind": ownership.trigger_kind,
                    "external_trigger_value": ownership.trigger_value,
                }
            )
        values = event.to_dict()
        values["payload"] = payload
        return SocialEventEnvelope.create(**values)

    def _profile_snapshot(self, group_id: str) -> dict[str, object]:
        snapshot = self._persona_config_snapshot(group_id)
        configured = snapshot.config["persona_profile"]
        return GroupmatePersonaProfile.from_mapping(configured).to_mapping()

    def _persona_config_snapshot(self, group_id: str) -> ConfigSnapshot:
        repository = self._config_repository
        if repository is None:
            repository = ConfigVersionRepository(
                self.data_dir / SOCIAL_RUNTIME_DATABASE_NAME
            )
            self._config_repository = repository
        snapshot = repository.snapshot(
            persona_id=self.settings.persona_id,
            group_id=group_id or None,
        )
        configured = snapshot.config.get("persona_profile")
        if isinstance(configured, Mapping):
            values = dict(configured)
            if "canon" not in values:
                values["canon"] = PERSONA_CANON_PRESETS[
                    self.settings.persona_preset
                ].to_mapping()
            profile = GroupmatePersonaProfile.from_mapping(values).to_mapping()
        else:
            profile = GroupmatePersonaProfile.default().to_mapping()
            profile["identity"]["name"] = self.settings.persona_name
            profile["identity"]["aliases"] = list(self.settings.persona_aliases)
            profile["canon"] = PERSONA_CANON_PRESETS[
                self.settings.persona_preset
            ].to_mapping()
            profile = GroupmatePersonaProfile.from_mapping(profile).to_mapping()
        config = dict(snapshot.config)
        config["persona_profile"] = profile
        return ConfigSnapshot(snapshot.version, config)

    async def _attention_wakeup_loop(
        self, manager: SocialRuntimeManager
    ) -> None:
        while self._manager is manager:
            self._attention_changed.clear()
            try:
                deadline = await manager.next_attention_deadline()
                if deadline is None:
                    await self._attention_changed.wait()
                    continue
                delay = max(0.0, float(deadline) - float(self.clock()))
                try:
                    await asyncio.wait_for(
                        self._attention_changed.wait(), timeout=delay
                    )
                except TimeoutError:
                    evaluations = await manager.drain(now=deadline)
                    await self._handle_evaluations(evaluations)
                    self._reconcile_shadow_reviews()
                self.attention_wakeup_error = None
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.attention_wakeup_error = f"{type(exc).__name__}: {exc}"
                await self._attention_changed.wait()

    async def _handle_evaluations(self, evaluations: tuple[object, ...]) -> None:
        if self._manager is None:
            return
        async with self._reply_lock:
            handled_groups: set[str] = set()
            ordered = sorted(
                evaluations,
                key=lambda item: (
                    0
                    if getattr(getattr(item, "frame", None), "trigger_kind", "")
                    == "FAST"
                    else 1
                ),
            )
            for evaluation in ordered:
                group_id = str(
                    getattr(getattr(evaluation, "source_event", None), "group_id", "")
                    or ""
                )
                if not group_id or group_id in handled_groups:
                    self._record_trace(
                        self.trace_repository.record_evaluation,
                        evaluation,
                        int(self.clock()),
                    )
                    continue
                persona_profile = self._manager.persona_profile_mapping(
                    group_id,
                    int(getattr(evaluation, "config_version", 0)),
                )
                source_event = getattr(evaluation, "source_event", None)
                subject_id = str(getattr(source_event, "actor_id", "") or "")
                relationship = self._manager.relationship_affection(
                    group_id, subject_id
                )
                try:
                    relationship_memory_cues = (
                        self._manager.relationship_memory_cues(
                            group_id,
                            subject_id,
                            text=str(source_event.payload.get("text") or "")
                            if source_event is not None
                            else "",
                            now=int(self.clock()),
                        )
                        if subject_id
                        else ()
                    )
                except Exception:
                    # Relationship memory enriches expression but must never
                    # block an already approved social reply.
                    relationship_memory_cues = ()
                recent_outputs = tuple(
                    self._recent_outputs.get(group_id, ())
                )
                plan = self._reply_planner.plan(
                    evaluation,
                    now=int(self.clock()),
                    persona_profile=persona_profile,
                    relationship=relationship,
                    recent_outputs=recent_outputs,
                    relationship_memory_cues=relationship_memory_cues,
                )
                if plan is None:
                    self._record_trace(
                        self.trace_repository.record_evaluation,
                        evaluation,
                        int(self.clock()),
                    )
                    continue
                mode = self._manager.group_mode(group_id)
                if mode is RuntimeMode.SHADOW and self._reply_executor is not None:
                    preview = await self._reply_executor.preview(
                        plan,
                        context_events=tuple(
                            getattr(evaluation, "context_events", ())
                        ),
                        persona_profile=persona_profile,
                        recent_outputs=(),
                    )
                    evaluation = replace(
                        evaluation,
                        candidate_response=preview.text,
                        reply_diagnostic=preview.diagnostic_code,
                    )
                    self._manager.update_shadow_review_evidence(evaluation)
                    if preview.status == "READY" and preview.text:
                        self._remember_output(group_id, preview.text)
                self._record_trace(
                    self.trace_repository.record_evaluation,
                    evaluation,
                    int(self.clock()),
                )
                if source_event is not None:
                    self._record_trace(
                        self.trace_repository.record_plan,
                        str(getattr(source_event, "event_id", "")),
                        plan,
                        int(self.clock()),
                    )
                handled_groups.add(group_id)
                try:
                    if mode is RuntimeMode.SHADOW:
                        self._manager.reply_plans.save(plan)
                        continue
                    if self._reply_executor is None:
                        raise RuntimeError("reply executor is unavailable")
                    execution = await self._reply_executor.execute_with_result(
                        plan,
                        context_events=tuple(
                            getattr(evaluation, "context_events", ())
                        ),
                        persona_profile=persona_profile,
                        recent_outputs=(),
                    )
                    if execution.usable_for_lease:
                        text = str(
                            execution.part.part.payload.get("text") or ""
                        ).strip()
                        if text:
                            self._remember_output(group_id, text)
                        await self._manager.record_usable_reply(
                            plan, now=int(self.clock())
                        )
                    await self._dispatch_ready()
                    self.reply_error = None
                except Exception as exc:
                    self.reply_error = f"{type(exc).__name__}: {exc}"

    def _remember_output(self, group_id: str, text: str) -> None:
        history = self._recent_outputs.setdefault(str(group_id), deque(maxlen=8))
        history.append(str(text).strip())

    async def _dispatch_ready(self) -> None:
        if self._manager is None or self._dispatcher is None:
            return
        while True:
            part = await self._dispatcher.dispatch_next(now=int(self.clock()))
            if part is None:
                return
            try:
                plan = self._manager.reply_plans.by_correlation(
                    part.correlation_id
                )
                status = "sent" if part.status is OutboxStatus.SENT else part.status.value
                self._manager.reply_plans.mark(plan.plan_id, status)
            except LookupError:
                pass
            if part.receipt is not None:
                delivery_status = {
                    OutboxStatus.SENT: "SENT",
                    OutboxStatus.FAILED: "FAILED",
                    OutboxStatus.UNKNOWN: "UNKNOWN",
                }.get(part.status, "UNKNOWN")
                self._record_trace(
                    self.trace_repository.record_delivery,
                    part.correlation_id,
                    delivery_status,
                    part.receipt.platform_message_id,
                    part.receipt.error_code,
                    int(self.clock()),
                )
            await self._manager.drain()

    def _record_trace(self, operation, *args) -> None:
        try:
            operation(*args)
            self.trace_error = None
        except Exception as exc:
            self.trace_error = f"{type(exc).__name__}: {exc}"

    def _reconcile_shadow_reviews(self) -> None:
        if self._manager is None or self.shadow_reviews is None:
            return
        capture = getattr(self.shadow_reviews, "capture_runtime", None)
        if not callable(capture):
            self.shadow_review_error = "shadow review recorder contract is invalid"
            return
        try:
            pending_evidence = self._manager.pending_shadow_review_evidence()
        except Exception as exc:
            self.shadow_review_error = f"{type(exc).__name__}: {exc}"
            return
        for pending in pending_evidence:
            try:
                capture(pending.evaluation)
                if not self._manager.complete_shadow_review_evidence(
                    pending.capture_id
                ):
                    raise RuntimeError("shadow review evidence acknowledgement failed")
                self.shadow_review_error = None
            except Exception as exc:
                self.shadow_review_error = f"{type(exc).__name__}: {exc}"

    async def close(self) -> None:
        task = self._attention_task
        self._attention_task = None
        if task is not None:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
        manager = self._manager
        cognition_client = self._cognition_client
        profile_service = self._profile_service
        profile_client = self._profile_client
        self._manager = None
        self._cognition_client = None
        self._profile_service = None
        self._profile_client = None
        try:
            if profile_service is not None:
                await profile_service.close()
            if manager is not None:
                await manager.close()
        finally:
            close = getattr(cognition_client, "close", None)
            if callable(close):
                await close()
            profile_close = getattr(profile_client, "close", None)
            if callable(profile_close):
                await profile_close()
            self._reply_executor = None
            self._dispatcher = None
            self._started = False

    @staticmethod
    def _new_cognition_client(
        settings: SocialRuntimeSettings,
    ) -> DeepSeekCognitionClient:
        return DeepSeekCognitionClient(
            api_key=settings.cognition_api_key,
            api_base=settings.cognition_api_base,
            model=settings.cognition_model,
        )

    @staticmethod
    def _new_profile_client(
        settings: SocialRuntimeSettings,
    ) -> DeepSeekProfileClient:
        return DeepSeekProfileClient(
            api_key=settings.cognition_api_key,
            api_base=settings.cognition_api_base,
            model=settings.cognition_model,
            timeout_seconds=settings.profile_timeout_seconds,
        )
