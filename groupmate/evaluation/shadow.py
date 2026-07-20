"""Live decision observation that cannot generate, inspect media, or send."""

from __future__ import annotations

import hashlib
import hmac
import os
import time
from pathlib import Path
from typing import Optional
from uuid import uuid4

from ..models import Decision, DecisionAction, GroupPolicy, TriggerKind, WorkflowOutcome
from .models import ShadowRecord


class HmacIdentityHasher:
    def __init__(self, key_path: Path) -> None:
        self.key_path = Path(key_path)
        self.key_path.parent.mkdir(parents=True, exist_ok=True)
        self._key = self._load_or_create()

    def digest(self, value: str) -> str:
        return hmac.new(
            self._key, str(value).encode("utf-8"), hashlib.sha256
        ).hexdigest()

    def _load_or_create(self) -> bytes:
        try:
            descriptor = os.open(
                str(self.key_path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600
            )
        except FileExistsError:
            key = self.key_path.read_bytes()
            if len(key) != 32:
                raise ValueError("影子模式 HMAC 密钥长度无效")
            return key
        key = os.urandom(32)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(key)
        return key


class ShadowWorkflow:
    """Decision-only workflow with no generation, vision, or platform dependency."""

    def __init__(
        self,
        decision_model,
        memory,
        collector,
        hasher: HmacIdentityHasher,
        clock,
        model_id: str = "",
        retention_days: int = 7,
        sample_rate: float = 1.0,
        policy_version: str = "1",
        rate_limiter=None,
    ) -> None:
        self.decision_model = decision_model
        self.memory = memory
        self.collector = collector
        self.hasher = hasher
        self.clock = clock
        self.model_id = str(model_id)
        self.retention_days = max(1, min(30, int(retention_days)))
        self.sample_rate = max(0.0, min(1.0, float(sample_rate)))
        self.policy_version = str(policy_version)
        self.rate_limiter = rate_limiter

    async def evaluate(self, topic, trigger, policy: GroupPolicy) -> WorkflowOutcome:
        return await self._observe(topic, trigger, policy)

    async def observe_bypass(self, topic, trigger, policy: GroupPolicy) -> WorkflowOutcome:
        return await self._observe(topic, trigger, policy)

    async def _observe(self, topic, trigger, policy: GroupPolicy) -> WorkflowOutcome:
        decision_id = uuid4().hex
        if not topic.messages or not self._sampled(topic):
            return WorkflowOutcome(
                decision_id=decision_id, sent=False, reason="shadow_not_sampled"
            )
        started = time.perf_counter_ns()
        now = self.clock.now()
        action = "ignore"
        confidence = 0.0
        reason = "ignored"
        error_code: Optional[str] = None
        would_rate_limit = bool(
            trigger is TriggerKind.CANDIDATE
            and self.rate_limiter is not None
            and not self.rate_limiter.allow(now)
        )

        if trigger is TriggerKind.NATIVE_DIRECT:
            action, reason = "bypass", "native_direct"
        elif trigger is TriggerKind.COMMAND:
            reason = "existing_command"
        elif trigger is TriggerKind.IGNORE:
            reason = "ignored_sender_or_empty"
        elif trigger is TriggerKind.ALIAS_DIRECT:
            action, confidence, reason = "respond", 1.0, "alias_direct"
        elif would_rate_limit:
            reason = "rate_limited"
        else:
            query = " ".join(message.text for message in topic.messages[-8:] if message.text)
            try:
                memories = self.memory.search_memories(
                    topic.group_id, query, now=now, limit=8
                )
            except Exception:
                memories = ()
            try:
                decision = await self.decision_model.decide(topic, policy, memories)
            except Exception:
                decision = Decision.ignore("decision_error", trigger)
                error_code = "decision_error"
            if not isinstance(decision, Decision):
                decision = Decision.ignore("invalid_decision", trigger)
                error_code = "invalid_decision"
            confidence = decision.confidence
            reason = decision.reason_code or decision.action.value
            if decision.action is DecisionAction.RESPOND:
                if decision.confidence >= policy.decision_threshold:
                    action = "respond"
                else:
                    reason = "below_threshold"

        sample = self.collector.collect(topic)
        record = ShadowRecord(
            decision_id=decision_id,
            group_hash=self.hasher.digest(topic.group_id),
            sender_hash=self.hasher.digest(sample.sender_id),
            trigger=trigger.value,
            action=action,
            confidence=confidence,
            reason_code=reason,
            would_rate_limit=would_rate_limit,
            features=sample.features,
            context=sample.context,
            model_id=self.model_id,
            policy_version=self.policy_version,
            latency_ms=(time.perf_counter_ns() - started) / 1_000_000.0,
            error_code=error_code,
            created_at=now,
            expires_at=now + self.retention_days * 24 * 3600,
        )
        try:
            self.memory.purge_expired_shadow(now)
            saved = self.memory.save_shadow_decision(record)
        except Exception:
            return WorkflowOutcome(
                decision_id=decision_id, sent=False, reason="shadow_store_error"
            )
        return WorkflowOutcome(
            decision_id=decision_id,
            sent=False,
            reason="shadow_recorded" if saved else "shadow_duplicate",
        )

    def _sampled(self, topic) -> bool:
        if self.sample_rate <= 0:
            return False
        if self.sample_rate >= 1:
            return True
        latest = topic.latest
        identity = "{}:{}".format(topic.group_id, latest.message_id if latest else "")
        bucket = int(self.hasher.digest(identity)[:8], 16) / float(0xFFFFFFFF)
        return bucket < self.sample_rate
