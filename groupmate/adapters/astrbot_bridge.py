"""AstrBot composition boundary for Social Runtime v2."""

from __future__ import annotations

import asyncio
from contextlib import suppress
from dataclasses import replace
import time
from pathlib import Path
from typing import Callable

from ..settings import SOCIAL_RUNTIME_DATABASE_NAME, SocialRuntimeSettings
from ..social_runtime.contracts import RuntimeMode
from ..social_runtime.control.config_versions import ConfigVersionRepository
from ..social_runtime.control.message_traces import MessageTraceRepository
from ..social_runtime.cognition.ambient_worker import DirectAmbientWorker
from ..social_runtime.manager import SocialRuntimeManager
from ..social_runtime.ownership import ExternalTriggerPolicy
from ..social_runtime.replying import ReplyExecutor, ReplyPlanner
from ..social_runtime.delivery.dispatcher import DeliveryDispatcher
from ..social_runtime.actions.contracts import OutboxStatus
from .astrbot_delivery import AstrBotOneBotSender
from .astrbot_events import AstrBotEventTranslator
from .astrbot_models import AstrBotModelPort
from .deepseek_cognition import DeepSeekCognitionClient
from .onebot_delivery import OneBotDeliveryAdapter


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
    ) -> None:
        self.context = context
        self.settings = settings
        self.data_dir = Path(data_dir)
        self.clock = time.time if clock is None else clock
        self._cognition_client_factory = (
            cognition_client_factory or self._new_cognition_client
        )
        self.translator = AstrBotEventTranslator(
            settings.persona_id,
            external_trigger_policy=ExternalTriggerPolicy.from_entries(
                command_prefixes=settings.external_command_prefixes,
                link_domains=settings.external_link_domains,
            ),
            clock=self.clock,
        )
        self._manager: SocialRuntimeManager | None = None
        self._cognition_client: object | None = None
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
        self._attention_changed = asyncio.Event()
        self._attention_task: asyncio.Task[None] | None = None
        self._started = False

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
                persona_profile_loader=lambda group_id: config_repository.snapshot(
                    persona_id=self.settings.persona_id,
                    group_id=group_id,
                ),
                clock=self.clock,
            )
            try:
                await manager.start()
                self._manager = manager
                self._cognition_client = cognition_client
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
                with suppress(Exception):
                    await manager.close()
                close = getattr(cognition_client, "close", None)
                if callable(close):
                    with suppress(Exception):
                        await close()
                raise
        self._started = True
        self._reconcile_shadow_reviews()

    async def handle_event(self, event: object):
        if not self._started:
            await self.start()
        if self._manager is None:
            return None
        translated = self.translator.translate(event)
        self._record_trace(
            self.trace_repository.record_received,
            translated,
            self.settings.runtime_mode,
            int(self.clock()),
        )
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

    async def observe_event(self, event: object) -> None:
        """Record arrival and route facts without entering Social Runtime."""

        if not self.settings.enabled_groups:
            return

        translated = self.translator.translate(event)
        self._record_trace(
            self.trace_repository.record_received,
            translated,
            self.settings.runtime_mode,
            int(self.clock()),
        )

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
                plan = self._reply_planner.plan(
                    evaluation, now=int(self.clock())
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
                        persona_profile={"persona_id": self.settings.persona_id},
                        recent_outputs=(),
                    )
                    evaluation = replace(
                        evaluation,
                        candidate_response=preview.text,
                        reply_diagnostic=preview.diagnostic_code,
                    )
                    self._manager.update_shadow_review_evidence(evaluation)
                self._record_trace(
                    self.trace_repository.record_evaluation,
                    evaluation,
                    int(self.clock()),
                )
                source_event = getattr(evaluation, "source_event", None)
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
                        persona_profile={"persona_id": self.settings.persona_id},
                        recent_outputs=(),
                    )
                    if execution.usable_for_lease:
                        await self._manager.record_usable_reply(
                            plan, now=int(self.clock())
                        )
                    await self._dispatch_ready()
                    self.reply_error = None
                except Exception as exc:
                    self.reply_error = f"{type(exc).__name__}: {exc}"

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
        self._manager = None
        self._cognition_client = None
        try:
            if manager is not None:
                await manager.close()
        finally:
            close = getattr(cognition_client, "close", None)
            if callable(close):
                await close()
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
