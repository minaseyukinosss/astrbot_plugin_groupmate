"""AstrBot-hosted scheduling for due Aemeath commitments."""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import replace
from datetime import datetime, timedelta
from typing import Optional
from uuid import uuid4
from zoneinfo import ZoneInfo

from ..capabilities.contracts import (
    CapabilityContext,
    CapabilityMediaPolicy,
    CapabilityRequest,
    CapabilityResult,
    CapabilityStatus,
)
from ..capabilities.governor import CapabilityGovernor
from ..engine.delivery import DeliveryService, build_delivery_plan
from .continuity import close_continuity_for_resolved_reminder
from .reminder_infer import (
    is_short_commitment,
    looks_like_reminder_cancel,
    reminder_task_from_summary,
)
from ..models import (
    OutboundKind,
    OutboundSegment,
    SelfCommitment,
    SelfCommitmentStatus,
    Urgency,
)

logger = logging.getLogger(__name__)


class CommitmentScheduler:
    """Wake on AstrBot cron and claim due work through the Groupmate ledger."""

    CRON_EXPRESSION = "* * * * *"
    CRON_NAME = "爱弥斯到点提醒扫描"
    CRON_NAME_ALIASES = ("Groupmate 承诺履约扫描",)

    def __init__(
        self,
        *,
        context,
        memory,
        persona_id: str,
        character_name: str,
        platform_factory,
        capability_governor_factory,
        paused_getter,
        group_enabled,
        provider_getter,
        timezone_name: str = "Asia/Shanghai",
        quiet_start_hour: int = 0,
        quiet_end_hour: int = 7,
    ) -> None:
        self.context = context
        self.memory = memory
        self.persona_id = str(persona_id)
        self.character_name = str(character_name)
        self.platform_factory = platform_factory
        self.capability_governor_factory = capability_governor_factory
        self.paused_getter = paused_getter
        self.group_enabled = group_enabled
        self.provider_getter = provider_getter
        self.timezone_name = str(timezone_name or "Asia/Shanghai")
        self.quiet_start_hour = max(0, min(23, int(quiet_start_hour)))
        self.quiet_end_hour = max(0, min(23, int(quiet_end_hour)))
        self._cron_job_id = ""
        self._fallback_task = None
        self._closed = False
        self._running = False
        self._lease_owner = "groupmate:" + str(uuid4())

    @property
    def mode(self) -> str:
        fallback_alive = (
            self._fallback_task is not None and not self._fallback_task.done()
        )
        if self._cron_job_id and fallback_alive:
            return "astrbot_cron"
        if self._cron_job_id:
            return "astrbot_cron"
        if fallback_alive:
            return "compatibility_loop"
        return "stopped"

    async def start(self) -> str:
        if self._closed:
            return "stopped"
        manager = getattr(self.context, "cron_manager", None)
        add_basic = getattr(manager, "add_basic_job", None)
        if not self._cron_job_id and callable(add_basic):
            try:
                list_jobs = getattr(manager, "list_jobs", None)
                delete_job = getattr(manager, "delete_job", None)
                if callable(list_jobs) and callable(delete_job):
                    known_names = {self.CRON_NAME, *self.CRON_NAME_ALIASES}
                    for old in tuple(await list_jobs("basic") or ()):
                        if str(getattr(old, "name", "") or "") in known_names:
                            await delete_job(str(getattr(old, "job_id", "") or ""))
                job = await add_basic(
                    name=self.CRON_NAME,
                    cron_expression=self.CRON_EXPRESSION,
                    handler=self.run_due,
                    description="到点扫描爱弥斯未完成的提醒/履约承诺，并主动@当事人。",
                    timezone=self.timezone_name,
                    payload={},
                    enabled=True,
                    persistent=False,
                )
                self._cron_job_id = str(getattr(job, "job_id", "") or "")
            except Exception:
                logger.exception("Groupmate failed to register AstrBot cron job")
        # Always keep a local loop: AstrBot hot-reload / missing cron wake must not
        # leave due reminders stranded in the ledger.
        if self._fallback_task is None or self._fallback_task.done():
            self._fallback_task = asyncio.create_task(self._fallback_loop())
        return self.mode

    async def close(self) -> None:
        self._closed = True
        task = self._fallback_task
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        self._fallback_task = None
        if self._cron_job_id:
            manager = getattr(self.context, "cron_manager", None)
            delete_job = getattr(manager, "delete_job", None)
            if callable(delete_job):
                try:
                    await delete_job(self._cron_job_id)
                except Exception:
                    logger.exception("Groupmate failed to remove AstrBot cron job")
            self._cron_job_id = ""

    async def run_due(
        self,
        *,
        commitment_id: Optional[str] = None,
        force: bool = False,
    ) -> dict:
        if self._closed or bool(self.paused_getter()):
            return {"processed": 0, "mode": self.mode, "reason": "paused"}
        if self._running:
            return {"processed": 0, "mode": self.mode, "reason": "already_running"}
        self._running = True
        try:
            now = int(time.time())
            claimed = self.memory.claim_due_self_commitments(
                self.persona_id,
                now=now,
                lease_owner=self._lease_owner,
                commitment_id=commitment_id,
                limit=1 if commitment_id else 10,
            )
            processed = 0
            deferred_quiet = 0
            for item in claimed:
                if not self.group_enabled(item.group_id):
                    self._finish(
                        item,
                        status=SelfCommitmentStatus.BLOCKED,
                        now=now,
                        failure_code="group_not_enabled",
                    )
                    processed += 1
                    continue
                if (
                    not force
                    and self._is_quiet_hour(now)
                    and not is_short_commitment(item, now=now)
                ):
                    self._finish(
                        item,
                        status=SelfCommitmentStatus.PENDING,
                        now=now,
                        failure_code="quiet_hours_deferred",
                        next_attempt_at=self._quiet_end(now),
                    )
                    deferred_quiet += 1
                    processed += 1
                    continue
                if (
                    not force
                    and self._group_is_busy(item.group_id, now)
                    and not is_short_commitment(item, now=now)
                ):
                    self._finish(
                        item,
                        status=SelfCommitmentStatus.PENDING,
                        now=now,
                        failure_code="group_busy_deferred",
                        next_attempt_at=now + 600,
                    )
                    processed += 1
                    continue
                await self._process(item, now=now)
                processed += 1
            reason = "ok"
            if processed == 0:
                reason = "quiet_hours" if deferred_quiet else "ok"
            elif deferred_quiet == processed:
                reason = "quiet_hours"
            return {"processed": processed, "mode": self.mode, "reason": reason}
        finally:
            self._running = False

    async def _fallback_loop(self) -> None:
        # Cold start / hot reload often registers the scheduler before aiocqhttp
        # connects; give the platform a short head start.
        await asyncio.sleep(20)
        while not self._closed:
            try:
                await self.run_due()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Groupmate commitment compatibility loop failed")
            await asyncio.sleep(self._next_wake_delay(int(time.time())))

    async def _process(self, item: SelfCommitment, *, now: int) -> None:
        decision_id = "commitment-" + str(uuid4())
        self._record(decision_id, item.group_id, "OBSERVE", "due_commitment", now)
        result = None
        if item.fulfillment_mode == "capability":
            result = await self._execute_capability(item, decision_id, now)
            if result.status is not CapabilityStatus.SUCCESS:
                text = "这件事我没能按时做成，原因是{}。".format(
                    self._failure_text(result.error_code)
                )
                outcome = await self._deliver(item, decision_id, text, now)
                self._finish(
                    item,
                    status=SelfCommitmentStatus.BLOCKED,
                    now=now,
                    result_decision_id=decision_id,
                    result_quote=text,
                    failure_code=result.error_code or "capability_failed",
                    delivered=outcome.sent,
                )
                return
            facts = tuple(result.facts or ())
            text = result.user_text or (
                "之前答应你的事有结果了：" + "；".join(facts)
            )
            outcome = await self._deliver(item, decision_id, text, now)
            self._finish(
                item,
                status=(
                    SelfCommitmentStatus.COMPLETED
                    if outcome.sent
                    else SelfCommitmentStatus.PENDING
                ),
                now=now,
                result_decision_id=decision_id,
                result_quote=text if outcome.sent else "",
                result_facts=facts if outcome.sent else (),
                failure_code="" if outcome.sent else outcome.reason,
                next_attempt_at=None if outcome.sent else now + 45,
                delivered=outcome.sent,
            )
            return
        if item.fulfillment_mode == "reminder":
            if self._reminder_cancelled_in_chat(item):
                resolved = self.memory.resolve_self_commitment(
                    self.persona_id,
                    item.commitment_id,
                    status=SelfCommitmentStatus.WITHDRAWN,
                    result_decision_id=decision_id,
                    result_quote="cancelled_before_due",
                    failure_code="cancelled_before_due",
                    resolved_at=now,
                )
                close_continuity_for_resolved_reminder(
                    self.memory,
                    self.persona_id,
                    resolved
                    or replace(item, status=SelfCommitmentStatus.WITHDRAWN),
                    now=int(now),
                )
                self._record(
                    decision_id,
                    item.group_id,
                    "COMMITMENT",
                    "cancelled_before_due",
                    now,
                )
                return
            text = self._reminder_text(item)
            outcome = await self._deliver(item, decision_id, text, now)
            finished = self._finish(
                item,
                status=(
                    SelfCommitmentStatus.COMPLETED
                    if outcome.sent
                    else SelfCommitmentStatus.PENDING
                ),
                now=now,
                result_decision_id=decision_id,
                result_quote=text if outcome.sent else "",
                failure_code="" if outcome.sent else outcome.reason,
                next_attempt_at=None if outcome.sent else now + 45,
                delivered=outcome.sent,
            )
            if outcome.sent:
                close_continuity_for_resolved_reminder(
                    self.memory,
                    self.persona_id,
                    finished
                    or replace(
                        item,
                        status=SelfCommitmentStatus.COMPLETED,
                        result_quote=text,
                    ),
                    now=int(now),
                )
            return
        self._finish(
            item,
            status=SelfCommitmentStatus.BLOCKED,
            now=now,
            failure_code="waiting_for_new_information",
        )

    async def _execute_capability(
        self, item: SelfCommitment, decision_id: str, now: int
    ):
        governor: CapabilityGovernor = self.capability_governor_factory(item.group_id)
        source = self.memory.get_message(
            self.persona_id, item.group_id, item.request_message_id
        ) if item.request_message_id else None
        if source is None:
            return CapabilityResult(
                CapabilityStatus.FAILED,
                item.required_capability,
                error_code="source_message_unavailable",
            )
        spec = governor.registry.lookup(item.required_capability)
        permissions = tuple(spec.manifest.permission_profile) if spec is not None else ()
        request = CapabilityRequest(
            capability_name=item.required_capability,
            message_text=source.text,
            media_locators=source.image_urls,
            group_id=item.group_id,
            actor_id=item.beneficiary_subject_id,
            message_id=source.message_id,
        )
        resolution = governor.registry.resolve(request)
        if resolution.required_information:
            return CapabilityResult(
                CapabilityStatus.FAILED,
                item.required_capability,
                error_code="source_information_missing",
            )
        result = await governor.execute(
            request,
            CapabilityContext(
                persona_id=self.persona_id,
                group_id=item.group_id,
                actor_id=item.beneficiary_subject_id,
                message_id=source.message_id,
                trace_id=decision_id,
                deadline_at=now + 30,
                allowed_permissions=permissions,
                media_policy=CapabilityMediaPolicy(
                    capability_media_allowed=True,
                    allowed_media_kinds=("image",),
                    allowed_safety_labels=(
                        "catalog_approved",
                        "provider_approved",
                        "reviewed",
                        "safe",
                    ),
                ),
            ),
            now=now,
        )
        self._record(
            decision_id,
            item.group_id,
            "CAPABILITY",
            "{}:{}".format(item.required_capability, result.status.value),
            now,
        )
        return result

    async def _deliver(
        self, item: SelfCommitment, decision_id: str, text: str, now: int
    ):
        target = self.memory.resolve_member_subject_id(
            self.persona_id, item.group_id, item.beneficiary_subject_id
        )
        platform = self.platform_factory(item.group_id)
        service = DeliveryService(
            platform,
            self.memory,
            _SchedulerClock(),
            persona_id=self.persona_id,
            character_name=self.character_name,
        )
        plan = build_delivery_plan(
            decision_id=decision_id,
            group_id=item.group_id,
            text=text,
            urgency=Urgency.NORMAL,
            now=now,
            ttl_seconds=120,
            max_chars=180,
            max_segments=2,
            humanize_delay=False,
            direct_wake=True,
        )
        plan = plan.__class__(
            decision_id=plan.decision_id,
            group_id=plan.group_id,
            segments=(),
            delay_seconds=0,
            expires_at=plan.expires_at,
            outbound=(
                OutboundSegment(OutboundKind.MENTION, target_user_id=target),
                OutboundSegment(OutboundKind.TEXT, text=text),
            ),
        )
        self._record(decision_id, item.group_id, "SCHEDULE", "due_delivery", now)
        outcome = await service.deliver(
            plan,
            kind="self_commitment",
            sent_reason="commitment_delivered",
        )
        self._record(
            decision_id,
            item.group_id,
            "SEND",
            "sent" if outcome.sent else outcome.reason,
            int(time.time()),
        )
        self._record(
            decision_id,
            item.group_id,
            "END",
            "completed" if outcome.sent else outcome.reason,
            int(time.time()),
        )
        return outcome

    def _finish(self, item: SelfCommitment, **kwargs):
        return self.memory.finish_self_commitment_attempt(
            self.persona_id,
            item.commitment_id,
            lease_owner=self._lease_owner,
            **kwargs,
        )

    def _record(self, decision_id, group_id, state, reason, now) -> None:
        self.memory.record_transition(
            self.persona_id, decision_id, group_id, state, reason, int(now)
        )

    def _group_is_busy(self, group_id: str, now: int) -> bool:
        messages = self.memory.recent_messages(self.persona_id, group_id, 8)
        recent_people = [
            item
            for item in messages
            if not item.is_bot and int(now) - int(item.timestamp) <= 90
        ]
        return len(recent_people) >= 4

    def _zone(self) -> ZoneInfo:
        try:
            return ZoneInfo(self.timezone_name)
        except Exception:
            return ZoneInfo("Asia/Shanghai")

    def _is_quiet_hour(self, now: int) -> bool:
        hour = datetime.fromtimestamp(int(now), self._zone()).hour
        if self.quiet_start_hour < self.quiet_end_hour:
            return self.quiet_start_hour <= hour < self.quiet_end_hour
        return hour >= self.quiet_start_hour or hour < self.quiet_end_hour

    def _quiet_end(self, now: int) -> int:
        current = datetime.fromtimestamp(int(now), self._zone())
        target = current.replace(
            hour=self.quiet_end_hour, minute=0, second=0, microsecond=0
        )
        if target <= current:
            target += timedelta(days=1)
        return int(target.timestamp())

    def _next_wake_delay(self, now: int) -> float:
        peek = getattr(self.memory, "next_self_commitment_attempt_at", None)
        next_at = peek(self.persona_id) if callable(peek) else None
        if next_at is None:
            return 15.0
        return float(max(1, min(15, int(next_at) - int(now))))

    def _reminder_cancelled_in_chat(self, item: SelfCommitment) -> bool:
        recent = getattr(self.memory, "recent_messages", None)
        if not callable(recent):
            return False
        messages = recent(self.persona_id, item.group_id, 40) or ()
        created = int(item.created_at or 0)
        aliases = {str(item.beneficiary_subject_id or "").strip()}
        member_ids = getattr(self.memory, "member_subject_ids", None)
        if callable(member_ids):
            aliases.update(
                str(value)
                for value in (
                    member_ids(
                        self.persona_id,
                        item.group_id,
                        item.beneficiary_subject_id,
                    )
                    or ()
                )
            )
        resolve = getattr(self.memory, "resolve_member_subject_id", None)
        for message in messages:
            if getattr(message, "is_bot", False):
                continue
            if int(getattr(message, "timestamp", 0) or 0) <= created:
                continue
            sender = str(getattr(message, "sender_id", "") or "").strip()
            canonical = sender
            if callable(resolve) and sender:
                canonical = str(
                    resolve(self.persona_id, item.group_id, sender) or sender
                )
            if canonical not in aliases and sender not in aliases:
                continue
            if looks_like_reminder_cancel(
                str(getattr(message, "text", "") or ""),
                has_open_reminder=True,
            ):
                return True
        return False

    @staticmethod
    def _reminder_text(item: SelfCommitment) -> str:
        task = reminder_task_from_summary(item.summary) or item.summary.strip()
        task = task.rstrip("。.")
        if not task:
            return "到点了，提醒你一下"
        return "到点了，{}".format(task)

    @staticmethod
    def _failure_text(code: str) -> str:
        return {
            "source_message_unavailable": "原来的内容已经找不到了",
            "source_information_missing": "原来的内容不足以继续处理",
            "capability_not_registered": "需要的能力现在不可用",
            "capability_unavailable": "需要的能力暂时不可用",
            "permission_denied": "这项操作没有获得权限",
            "deadline_expired": "处理超时了",
        }.get(str(code or ""), "处理没有成功")


class _SchedulerClock:
    @staticmethod
    def now() -> int:
        return int(time.time())
