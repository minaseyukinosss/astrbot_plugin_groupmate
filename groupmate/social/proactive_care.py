"""Relationship-aware, source-grounded proactive care decisions."""

from __future__ import annotations

import re
import time
from datetime import datetime, timedelta
from typing import Optional
from uuid import uuid5, NAMESPACE_URL
from zoneinfo import ZoneInfo

from ..engine.delivery import DeliveryService, build_delivery_plan
from ..models import (
    ContinuityStatus,
    OutboundKind,
    OutboundSegment,
    ProactiveCareDecision,
    ProactiveCareOutcome,
    Urgency,
)
from .affinity import band_for_affinity

_SENSITIVE = re.compile(
    r"(住院|手术|抑郁|自杀|家暴|失恋|流产|死亡|裁员|被辞|欠钱|借钱|"
    r"隐私|秘密|不要告诉|不想说|难过得不行)"
)
_IMPORTANT = re.compile(r"(考试|考研|高考|面试|求职|找工作|搬家|毕业|入职|离职|项目|答辩|比赛|手术|生病|感冒|发烧|旅行|开学)")


class ProactiveCareScheduler:
    """Scan open personal events and decide whether a gentle check-in fits."""

    MIN_AGE_SECONDS = 24 * 3600
    COOLDOWN_SECONDS = 7 * 24 * 3600

    def __init__(
        self,
        *,
        memory,
        platform_factory,
        persona_id: str,
        character_name: str,
        group_enabled,
        paused_getter,
        timezone_name: str = "Asia/Shanghai",
    ) -> None:
        self.memory = memory
        self.platform_factory = platform_factory
        self.persona_id = str(persona_id)
        self.character_name = str(character_name)
        self.group_enabled = group_enabled
        self.paused_getter = paused_getter
        self.timezone_name = str(timezone_name or "Asia/Shanghai")

    async def run_due(self, *, now: Optional[int] = None, limit: int = 20) -> dict:
        if bool(self.paused_getter()):
            return {"processed": 0, "spoken": 0, "silent": 0, "reason": "paused"}
        now = int(now or time.time())
        spoken = silent = processed = 0
        items = self.memory.list_continuity_items(
            self.persona_id, statuses=(ContinuityStatus.OPEN,), limit=max(20, int(limit) * 4)
        )
        for item in items:
            if processed >= int(limit):
                break
            eligible_at = int(item.updated_at or item.created_at or 0) + self.MIN_AGE_SECONDS
            if item.due_at is not None:
                eligible_at = max(eligible_at, int(item.due_at) + 2 * 3600)
            if eligible_at > now:
                continue
            prior = self.memory.list_proactive_care(self.persona_id, item_id=item.item_id, limit=8)
            if any(entry.status.value == "corrected" for entry in prior):
                continue
            if any(
                entry.status.value == "active"
                and entry.outcome.value in {"spoke", "suppressed"}
                for entry in prior
            ):
                continue
            if any(
                entry.status.value == "active"
                and int(entry.next_review_at or entry.decided_at or 0) > now
                for entry in prior
            ):
                continue
            decision = self._decide(item, prior, now)
            if decision is None:
                continue
            saved = self.memory.append_proactive_care(self.persona_id, decision)
            if saved is None:
                continue
            processed += 1
            if decision.outcome is ProactiveCareOutcome.SPOKE:
                result = await self._deliver(decision, now)
                if result:
                    spoken += 1
                else:
                    silent += 1
            else:
                silent += 1
        return {"processed": processed, "spoken": spoken, "silent": silent, "reason": "ok"}

    def _decide(self, item, prior, now: int) -> Optional[ProactiveCareDecision]:
        group_id = str(item.group_id)
        subject_id = str(item.subject_id)
        if not (_IMPORTANT.search(item.summary) or _IMPORTANT.search(item.source_quote)):
            return None
        if not self.group_enabled(group_id):
            return self._decision(item, "group_disabled", "所在群未启用主动关心", "suppressed", now, False, False)
        profile = self.memory.get_profile(self.persona_id, group_id, subject_id) or {}
        subject_name = str(profile.get("preferred_address") or profile.get("display_name") or "成员")
        relationship = self.memory.get_member_relationship_state(
            self.persona_id, group_id, subject_id,
            configured_relationship=profile.get("relationship") or "", now=now
        )
        familiarity = int(getattr(relationship, "familiarity", 0) or 0)
        affinity = int(getattr(relationship, "affinity", 0) or 0)
        trust = int(getattr(relationship, "trust", 0) or 0)
        boundary = int(getattr(relationship, "boundary_pressure", 0) or 0)
        band = band_for_affinity(affinity).value
        sensitive = bool(_SENSITIVE.search(item.summary) or _SENSITIVE.search(item.source_quote))
        messages = self.memory.recent_messages(self.persona_id, group_id, 8)
        recent_people = [m for m in messages if not m.is_bot and now - int(m.timestamp or 0) <= 120]
        busy = len(recent_people) >= 4
        if self._is_quiet_hour(now):
            return self._decision(item, "quiet_hours", "现在是安静时段，等到白天再判断", "silent", now, sensitive, busy, subject_name, band, familiarity, affinity, trust, boundary, next_review_at=self._quiet_end(now))
        if sensitive:
            return self._decision(item, "sensitive_event", "事项可能涉及敏感处境，先保持克制", "suppressed", now, sensitive, busy, subject_name, band, familiarity, affinity, trust, boundary)
        if busy:
            return self._decision(item, "group_busy", "群里正忙，不把关心变成插话", "silent", now, sensitive, busy, subject_name, band, familiarity, affinity, trust, boundary, next_review_at=now + 6 * 3600)
        if boundary >= 25 or affinity < 15 or familiarity < 10:
            return self._decision(item, "relationship_not_close", "关系还不够近，避免越过对方边界", "silent", now, sensitive, busy, subject_name, band, familiarity, affinity, trust, boundary, next_review_at=now + 30 * 24 * 3600)
        if any(entry.outcome.value == "suppressed" and entry.status.value == "active" for entry in prior):
            return None
        reason = "关系较近，重要事项已沉淀超过一天，适合轻轻问一句" if _IMPORTANT.search(item.summary) else "关系较近，事项沉淀了一段时间，可以自然接住"
        return self._decision(item, "relationship_and_elapsed_time", reason, "spoke", now, sensitive, busy, subject_name, band, familiarity, affinity, trust, boundary)

    def _decision(self, item, code, text, outcome, now, sensitive, busy, subject_name="成员", band="neutral", familiarity=0, affinity=0, trust=0, boundary=0, next_review_at=None):
        care_id = str(uuid5(NAMESPACE_URL, f"groupmate:care:{self.persona_id}:{item.item_id}:{now}"))
        message = ""
        if outcome == "spoke":
            message = self._message(item.summary, subject_name, band)
        return ProactiveCareDecision(
            care_id=care_id, item_id=item.item_id, group_id=item.group_id, subject_id=item.subject_id,
            subject_name=subject_name, item_summary=item.summary, trigger_basis=item.source_quote,
            relationship_band=band, familiarity=familiarity, affinity=affinity, trust=trust,
            boundary_pressure=boundary, sensitive=bool(sensitive), group_busy=bool(busy),
            outcome=ProactiveCareOutcome(outcome), reason_code=code, reason_text=text, decided_at=int(now),
            next_review_at=(int(next_review_at) if next_review_at is not None else int(now) + self.COOLDOWN_SECONDS), message_text=message,
        )

    async def _deliver(self, decision: ProactiveCareDecision, now: int) -> bool:
        target = self.memory.resolve_member_subject_id(self.persona_id, decision.group_id, decision.subject_id)
        plan = build_delivery_plan(
            decision_id=decision.care_id, group_id=decision.group_id, text=decision.message_text,
            urgency=Urgency.NORMAL, now=now, ttl_seconds=120, max_chars=180, max_segments=2,
            humanize_delay=False, direct_wake=False,
        )
        plan = plan.__class__(
            decision_id=plan.decision_id, group_id=plan.group_id, segments=(), delay_seconds=0,
            expires_at=plan.expires_at, outbound=(
                OutboundSegment(OutboundKind.MENTION, target_user_id=target),
                OutboundSegment(OutboundKind.TEXT, text=decision.message_text),
            ),
        )
        service = DeliveryService(self.platform_factory(decision.group_id), self.memory, _CareClock(now), persona_id=self.persona_id, character_name=self.character_name)
        outcome = await service.deliver(plan, kind="proactive_care", sent_reason="proactive_care_spoke")
        self.memory.finish_proactive_care_delivery(
            self.persona_id, decision.care_id, sent=bool(outcome.sent),
            reason="发送失败，未实际开口：{}".format(outcome.reason), now=int(time.time()),
        )
        return bool(getattr(outcome, "sent", False))

    @staticmethod
    def _message(summary: str, subject_name: str, relationship_band: str) -> str:
        event = str(summary or "").strip().rstrip("。")
        name = str(subject_name or "").strip()
        if name and event.startswith(name):
            event = event[len(name):].strip()
        event = re.sub(r"^(今天|明天|后天|这周|下周|周末|月底|最近)", "", event)
        close = relationship_band == "close"
        if re.search(r"考试|考研|高考", event):
            return "前两天还想着你那场考试，后来怎么样？" if close else "你之前提过那场考试，后来还顺利吗？"
        if "面试" in event:
            return "你那场面试后来怎么样？" if close else "之前那场面试还顺利吗？"
        if re.search(r"求职|找工作|投简历", event):
            return "找工作的事最近有进展吗？"
        if "搬家" in event:
            return "搬家还顺利吗，最近安顿下来了吗？" if close else "搬家还顺利吗？"
        if re.search(r"生病|感冒|发烧", event):
            return "这两天身体好些了吗？"
        if re.search(r"答辩|比赛", event):
            label = "答辩" if "答辩" in event else "比赛"
            return "之前那场{}，后来怎么样？".format(label)
        if re.search(r"入职|开学", event):
            return "最近适应得怎么样？"
        if "项目" in event:
            return "你之前说的项目，最近进展还顺利吗？"
        event = event[:42] or "那件事"
        return "想起你之前说的{}，最近还好吗？".format(event) if close else "你之前提到的{}，后来还顺利吗？".format(event)

    def _zone(self) -> ZoneInfo:
        try:
            return ZoneInfo(self.timezone_name)
        except Exception:
            return ZoneInfo("Asia/Shanghai")

    def _is_quiet_hour(self, now: int) -> bool:
        return datetime.fromtimestamp(int(now), self._zone()).hour < 8

    def _quiet_end(self, now: int) -> int:
        current = datetime.fromtimestamp(int(now), self._zone())
        target = current.replace(hour=8, minute=0, second=0, microsecond=0)
        if target <= current:
            target += timedelta(days=1)
        return int(target.timestamp())

class _CareClock:
    def __init__(self, value: int) -> None:
        self.value = int(value)

    def now(self) -> int:
        return self.value
