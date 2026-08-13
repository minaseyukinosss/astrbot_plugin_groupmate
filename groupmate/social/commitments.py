"""Source-grounded lifecycle for Aemeath's own commitments."""

from __future__ import annotations

import asyncio
import logging
from typing import Callable, Optional
from uuid import NAMESPACE_URL, uuid5

from ..capabilities.contracts import CapabilityStatus, validate_capability_name
from ..models import (
    AddresseeKind,
    SelfCommitment,
    SelfCommitmentStatus,
    TargetingDecision,
    TopicSnapshot,
)
from .reminder_infer import (
    infer_timed_reminder_from_topic,
    infer_timed_reminder_commitment,
    latest_user_message,
    looks_like_reminder_cancel,
    recover_due_at,
)
from .continuity import close_continuity_for_resolved_reminder

logger = logging.getLogger(__name__)

EXTRACTOR_VERSION = "context-llm-v1"
MIN_CONFIDENCE = 0.92


class SelfCommitmentWriter:
    """Create auditable commitments only from the delivered reply."""

    def __init__(
        self,
        store,
        model,
        *,
        persona_id: str,
        on_error: Optional[Callable[[BaseException], None]] = None,
    ) -> None:
        self.store = store
        self.model = model
        self.persona_id = str(persona_id or "").strip()
        if not self.persona_id:
            raise ValueError("persona_id is required")
        self.on_error = on_error

    def schedule_after_send(
        self,
        topic: TopicSnapshot,
        targeting: TargetingDecision,
        *,
        decision_id: str,
        now: int,
        reply_text: str,
        capability_result=None,
    ) -> None:
        async def _safe() -> None:
            try:
                await self.process(
                    topic,
                    targeting,
                    decision_id=decision_id,
                    now=now,
                    reply_text=reply_text,
                    capability_result=capability_result,
                )
            except Exception as exc:  # noqa: BLE001 - never break a sent reply
                logger.exception("SelfCommitmentWriter failed: %s", exc)
                if self.on_error is not None:
                    try:
                        self.on_error(exc)
                    except Exception:  # noqa: BLE001
                        pass

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            asyncio.run(_safe())
            return
        task = loop.create_task(_safe())
        task.add_done_callback(lambda _: None)

    async def process(
        self,
        topic: TopicSnapshot,
        targeting: TargetingDecision,
        *,
        decision_id: str,
        now: int,
        reply_text: str,
        capability_result=None,
    ) -> Optional[SelfCommitment]:
        delivered = " ".join(str(reply_text or "").split())
        latest = latest_user_message(topic)
        extractor = getattr(self.model, "extract_self_commitment", None)
        beneficiary = targeting.reply_audience
        if (
            latest is None
            or not delivered
            or beneficiary.kind is not AddresseeKind.USER
            or len(beneficiary.target_user_ids) != 1
        ):
            return None
        subject_id = str(beneficiary.target_user_ids[0])
        open_items = self.store.list_self_commitments(
            self.persona_id,
            group_id=topic.group_id,
            beneficiary_subject_ids=self.store.member_subject_ids(
                self.persona_id, topic.group_id, subject_id
            ),
            statuses=(
                SelfCommitmentStatus.PENDING,
                SelfCommitmentStatus.IN_PROGRESS,
                SelfCommitmentStatus.BLOCKED,
            ),
            limit=12,
        )
        cancelled = self._withdraw_if_cancelled(
            latest_text=str(latest.text or ""),
            reply_text=delivered,
            open_items=open_items,
            decision_id=decision_id,
            now=now,
        )
        if cancelled is not None:
            return cancelled
        # Strong cancel on a continuation turn must not re-OPEN from earlier
        # 「N分钟后提醒我」still sitting in the same window.
        if looks_like_reminder_cancel(str(latest.text or "")):
            return self.cancel_open_reminder_for_sender(
                topic,
                decision_id=decision_id,
                now=now,
            )
        reminder_request = infer_timed_reminder_from_topic(topic, now=int(now))
        inferred = infer_timed_reminder_commitment(
            user_text=(
                reminder_request.source_text
                if reminder_request is not None
                else str(latest.text or "")
            ),
            reply_text=delivered,
            now=int(now),
        )
        if inferred is not None:
            item = self._apply_validated(
                inferred,
                topic=topic,
                beneficiary_subject_id=subject_id,
                decision_id=decision_id,
                now=now,
                reply_text=delivered,
                capability_result=capability_result,
                open_items=open_items,
                reminder_request=reminder_request,
            )
            if item is not None:
                return item
        raw = None
        if callable(extractor):
            raw = await extractor(
                topic=topic,
                targeting=targeting,
                open_items=open_items,
                reply_text=delivered,
                capability_result=capability_result,
                now=int(now),
            )
        return self._apply_validated(
            raw,
            topic=topic,
            beneficiary_subject_id=subject_id,
            decision_id=decision_id,
            now=now,
            reply_text=delivered,
            capability_result=capability_result,
            open_items=open_items,
            reminder_request=reminder_request,
        )

    def _apply_validated(
        self,
        raw,
        *,
        topic: TopicSnapshot,
        beneficiary_subject_id: str,
        decision_id: str,
        now: int,
        reply_text: str,
        capability_result,
        open_items,
        reminder_request=None,
    ) -> Optional[SelfCommitment]:
        if not isinstance(raw, dict):
            return None
        action = str(raw.get("action") or "").strip().upper()
        if action in {"", "NONE"}:
            return None
        try:
            confidence = float(raw.get("confidence") or 0)
        except (TypeError, ValueError):
            return None
        quote = " ".join(str(raw.get("evidence_quote") or "").split())[:180]
        summary = " ".join(str(raw.get("summary") or "").split())[:240]
        if confidence < MIN_CONFIDENCE:
            return None
        if len(quote) < 3 or quote not in reply_text:
            return None

        if action in {"COMPLETE", "BLOCK", "WITHDRAW"}:
            item_id = str(raw.get("commitment_id") or "").strip()
            selected = next(
                (item for item in open_items if item.commitment_id == item_id), None
            )
            if selected is None:
                return None
            capability_name = str(
                getattr(capability_result, "capability_name", "") or ""
            ).strip()
            capability_status = getattr(capability_result, "status", None)
            facts = tuple(getattr(capability_result, "facts", ()) or ())
            failure_code = str(getattr(capability_result, "error_code", "") or "")
            if action == "COMPLETE":
                if selected.required_capability and not (
                    capability_name == selected.required_capability
                    and capability_status is CapabilityStatus.SUCCESS
                ):
                    return None
                status = SelfCommitmentStatus.COMPLETED
            elif action == "BLOCK":
                if selected.required_capability and not (
                    capability_name == selected.required_capability
                    and capability_status is not CapabilityStatus.SUCCESS
                ):
                    return None
                status = SelfCommitmentStatus.BLOCKED
            else:
                status = SelfCommitmentStatus.WITHDRAWN
            return self._sync_resolved_reminder(
                self.store.resolve_self_commitment(
                    self.persona_id,
                    selected.commitment_id,
                    status=status,
                    result_decision_id=decision_id,
                    result_quote=quote,
                    result_facts=facts,
                    failure_code=failure_code,
                    resolved_at=int(now),
                ),
                now=now,
            )

        if action != "OPEN" or len(summary) < 4:
            return None

        required_capability = str(raw.get("required_capability") or "").strip()[:80]
        if required_capability:
            try:
                required_capability = validate_capability_name(required_capability)
            except (TypeError, ValueError):
                return None
        fulfillment_mode = str(raw.get("fulfillment_mode") or "").strip().lower()
        if fulfillment_mode not in {"reminder", "capability", "follow_up"}:
            fulfillment_mode = "capability" if required_capability else "follow_up"
        if required_capability:
            fulfillment_mode = "capability"
        due_at = self._optional_timestamp(raw.get("due_at"))
        latest_text = str(getattr(topic.latest, "text", "") or "")
        if reminder_request is not None and not required_capability:
            fulfillment_mode = "reminder"
            due_at = reminder_request.due_at(int(now))
            summary = reminder_request.ledger_summary()
        elif fulfillment_mode == "reminder" and due_at is None:
            due_at = recover_due_at(
                user_text=latest_text,
                reply_text=reply_text,
                now=int(now),
            )
        if fulfillment_mode == "reminder" and due_at is None:
            return None
        capability_name = str(
            getattr(capability_result, "capability_name", "") or ""
        ).strip()
        capability_status = getattr(capability_result, "status", None)
        result_facts = tuple(getattr(capability_result, "facts", ()) or ())
        failure_code = str(getattr(capability_result, "error_code", "") or "")
        status = SelfCommitmentStatus.PENDING
        resolved_at = None
        result_decision_id = None
        result_quote = ""
        extractor_version = str(
            raw.get("extractor_version") or EXTRACTOR_VERSION
        ).strip() or EXTRACTOR_VERSION

        if required_capability:
            if not capability_name or capability_name != required_capability:
                status = SelfCommitmentStatus.BLOCKED
                failure_code = "capability_not_executed"
                resolved_at = int(now)
            elif capability_status is CapabilityStatus.SUCCESS:
                status = SelfCommitmentStatus.COMPLETED
                result_decision_id = decision_id
                result_quote = quote
                resolved_at = int(now)
            else:
                status = SelfCommitmentStatus.BLOCKED
                failure_code = failure_code or str(
                    getattr(capability_status, "value", capability_status) or "capability_failed"
                )
                resolved_at = int(now)

        commitment_id = str(
            uuid5(
                NAMESPACE_URL,
                "groupmate:{}:{}:{}:{}".format(
                    self.persona_id,
                    topic.group_id,
                    decision_id,
                    quote,
                ),
            )
        )
        return self.store.append_self_commitment(
            self.persona_id,
            SelfCommitment(
                commitment_id=commitment_id,
                group_id=topic.group_id,
                beneficiary_subject_id=beneficiary_subject_id,
                summary=summary,
                source_decision_id=decision_id,
                source_message_id="bot-" + str(decision_id),
                request_message_id=(topic.latest.message_id if topic.latest else ""),
                source_quote=quote,
                created_at=int(now),
                updated_at=int(now),
                status=status,
                required_capability=required_capability,
                fulfillment_mode=fulfillment_mode,
                due_at=due_at,
                next_attempt_at=due_at,
                confidence=confidence,
                extractor_version=extractor_version,
                result_decision_id=result_decision_id,
                result_quote=result_quote,
                result_facts=result_facts,
                failure_code=failure_code,
                resolved_at=resolved_at,
            ),
        )

    @staticmethod
    def _optional_timestamp(value) -> Optional[int]:
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            return None
        return parsed if parsed > 0 else None

    def cancel_open_reminder_for_sender(
        self,
        topic: TopicSnapshot,
        *,
        decision_id: str,
        now: int,
    ) -> Optional[SelfCommitment]:
        latest = latest_user_message(topic)
        if latest is None:
            return None
        subject_id = str(getattr(latest, "sender_id", "") or "").strip()
        if not subject_id:
            return None
        open_items = self.store.list_self_commitments(
            self.persona_id,
            group_id=topic.group_id,
            beneficiary_subject_ids=self.store.member_subject_ids(
                self.persona_id, topic.group_id, subject_id
            ),
            statuses=(
                SelfCommitmentStatus.PENDING,
                SelfCommitmentStatus.IN_PROGRESS,
                SelfCommitmentStatus.BLOCKED,
            ),
            limit=12,
        )
        reminders = [
            item
            for item in tuple(open_items or ())
            if str(getattr(item, "fulfillment_mode", "") or "") == "reminder"
        ]
        if not reminders:
            group_open = self.store.list_self_commitments(
                self.persona_id,
                group_id=topic.group_id,
                statuses=(
                    SelfCommitmentStatus.PENDING,
                    SelfCommitmentStatus.IN_PROGRESS,
                    SelfCommitmentStatus.BLOCKED,
                ),
                limit=12,
            )
            group_reminders = [
                item
                for item in tuple(group_open or ())
                if str(getattr(item, "fulfillment_mode", "") or "") == "reminder"
            ]
            aliases = {
                str(item)
                for item in self.store.member_subject_ids(
                    self.persona_id, topic.group_id, subject_id
                )
            }
            aliases.add(subject_id)
            matching = [
                item
                for item in group_reminders
                if str(getattr(item, "beneficiary_subject_id", "") or "") in aliases
            ]
            reminders = matching or (
                list(group_reminders[:1]) if len(group_reminders) == 1 else []
            )
        if not looks_like_reminder_cancel(
            str(latest.text or ""),
            has_open_reminder=bool(reminders),
        ):
            return None
        if not reminders:
            return None
        selected = reminders[0]
        quote = " ".join(str(latest.text or "").split())[:180] or "不用提醒了"
        return self._sync_resolved_reminder(
            self.store.resolve_self_commitment(
                self.persona_id,
                selected.commitment_id,
                status=SelfCommitmentStatus.WITHDRAWN,
                result_decision_id=decision_id,
                result_quote=quote,
                resolved_at=int(now),
            ),
            now=now,
        )

    def _withdraw_if_cancelled(
        self,
        *,
        latest_text: str,
        reply_text: str,
        open_items,
        decision_id: str,
        now: int,
    ) -> Optional[SelfCommitment]:
        if not looks_like_reminder_cancel(
            latest_text, has_open_reminder=True
        ):
            return None
        reminders = [
            item
            for item in tuple(open_items or ())
            if str(getattr(item, "fulfillment_mode", "") or "") == "reminder"
        ]
        if not reminders:
            return None
        selected = reminders[0]
        quote = " ".join(str(reply_text or "").split())[:180]
        if len(quote) < 3:
            quote = " ".join(str(latest_text or "").split())[:180]
        if len(quote) < 2:
            quote = "不用提醒了"
        return self._sync_resolved_reminder(
            self.store.resolve_self_commitment(
                self.persona_id,
                selected.commitment_id,
                status=SelfCommitmentStatus.WITHDRAWN,
                result_decision_id=decision_id,
                result_quote=quote,
                resolved_at=int(now),
            ),
            now=now,
        )

    def _sync_resolved_reminder(self, commitment, *, now: int):
        if commitment is None:
            return None
        close_continuity_for_resolved_reminder(
            self.store, self.persona_id, commitment, now=int(now)
        )
        return commitment
