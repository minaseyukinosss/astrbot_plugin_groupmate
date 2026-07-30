"""异步记忆生命周期写入（不覆盖 SQLiteWriteWorker）。"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Callable, List, Optional, Sequence, Tuple
from uuid import uuid4

from ..models import (
    AddresseeKind,
    CandidateStatus,
    MemoryCandidate,
    MemoryKind,
    MemoryScope,
    Sensitivity,
    TargetingDecision,
    TopicSnapshot,
)
from .arbiter import MemoryArbiter
from .privacy import PrivacyClassifier, claim_hash

logger = logging.getLogger(__name__)

EXTRACTOR_VERSION = "rules-v1"

# authority：显式记住 > 纠错场景由 arbiter 比较；偏好 / 计划 / Bot 承诺
AUTHORITY_EXPLICIT = 8
AUTHORITY_PREFERENCE = 6
AUTHORITY_PLAN = 5
AUTHORITY_BOT_PROMISE = 4


class MemoryWriter:
    def __init__(
        self,
        store,
        *,
        persona_id: str,
        privacy: Optional[PrivacyClassifier] = None,
        arbiter: Optional[MemoryArbiter] = None,
        bot_id: str = "",
        on_error: Optional[Callable[[BaseException], None]] = None,
    ) -> None:
        self.store = store
        self.persona_id = str(persona_id or "").strip()
        if not self.persona_id:
            raise ValueError("persona_id is required")
        self.privacy = privacy or PrivacyClassifier()
        self.arbiter = arbiter or MemoryArbiter()
        self.bot_id = str(bot_id or "")
        self.on_error = on_error

    def schedule_after_send(
        self,
        topic: TopicSnapshot,
        targeting: TargetingDecision,
        *,
        decision_id: str,
        now: int,
        reply_text: str = "",
    ) -> None:
        def _safe() -> None:
            try:
                self.process(
                    topic,
                    targeting,
                    decision_id=decision_id,
                    now=now,
                    reply_text=reply_text,
                )
            except Exception as exc:  # noqa: BLE001 — 绝不影响主回复
                logger.exception("MemoryWriter failed: %s", exc)
                if self.on_error is not None:
                    try:
                        self.on_error(exc)
                    except Exception:  # noqa: BLE001
                        pass

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            _safe()
            return
        task = loop.create_task(asyncio.to_thread(_safe))
        task.add_done_callback(lambda _: None)

    def process(
        self,
        topic: TopicSnapshot,
        targeting: TargetingDecision,
        *,
        decision_id: str,
        now: int,
        reply_text: str = "",
    ) -> List[MemoryCandidate]:
        del decision_id
        candidates, authorities = self.extract_candidates(
            topic, targeting, now=now, reply_text=reply_text
        )
        decided: List[MemoryCandidate] = []
        for candidate in candidates:
            stored = self._persist_candidate(candidate)
            if stored is None:
                continue
            if stored.status is not CandidateStatus.PENDING:
                decided.append(stored)
                continue
            authority = authorities.get(
                stored.candidate_id,
                authorities.get(candidate.candidate_id, AUTHORITY_PREFERENCE),
            )
            result = self._arbitrate(stored, now=now, authority=authority)
            decided.append(result)
        return decided

    def extract_candidates(
        self,
        topic: TopicSnapshot,
        targeting: TargetingDecision,
        *,
        now: int,
        reply_text: str = "",
    ) -> Tuple[List[MemoryCandidate], dict]:
        from ..engine.topics import select_active_messages

        active = select_active_messages(
            topic.messages, topic_created_at=topic.created_at
        )
        memory_subject = targeting.memory_subject
        allow_personal = (
            memory_subject.kind is AddresseeKind.USER
            and bool(memory_subject.target_user_ids)
            and "no_personal_memory" not in memory_subject.reason_codes
        )
        subject_id = memory_subject.target_user_ids[0] if allow_personal else ""
        source_message = None
        for message in reversed(active):
            if message.is_bot:
                continue
            if allow_personal and message.sender_id != subject_id:
                continue
            source_message = message
            break
        if source_message is None and active:
            source_message = active[-1]

        candidates: List[MemoryCandidate] = []
        authorities: dict = {}
        text = (source_message.text if source_message else "") or ""
        source_ids = (
            (source_message.message_id,)
            if source_message and source_message.message_id
            else ()
        )

        if not allow_personal:
            if text.strip() and (
                memory_subject.kind is AddresseeKind.AMBIGUOUS
                or "no_personal_memory" in memory_subject.reason_codes
            ):
                sensitivity, _ = self.privacy.gate(text)
                subject = subject_id or (
                    memory_subject.target_user_ids[0]
                    if memory_subject.target_user_ids
                    else (
                        source_message.sender_id if source_message else "unknown"
                    )
                )
                candidate = self._candidate(
                    group_id=topic.group_id,
                    scope=MemoryScope.USER_IN_GROUP,
                    subject_id=subject,
                    kind=MemoryKind.EPISODIC,
                    claim=text.strip()[:200],
                    source_ids=source_ids,
                    confidence=0.2,
                    sensitivity=sensitivity
                    if sensitivity is not Sensitivity.NONE
                    else Sensitivity.THIRD_PARTY,
                    expires_at=None,
                    now=now,
                    status=CandidateStatus.REJECTED,
                    decision_reason="ambiguous_or_no_personal_memory",
                )
                candidates.append(candidate)
        else:
            message_sensitivity, message_ok = self.privacy.gate(text)
            if not message_ok:
                candidates.append(
                    self._candidate(
                        group_id=topic.group_id,
                        scope=MemoryScope.USER_IN_GROUP,
                        subject_id=subject_id,
                        kind=MemoryKind.EPISODIC,
                        claim=text.strip()[:240],
                        source_ids=source_ids,
                        confidence=0.3,
                        sensitivity=message_sensitivity,
                        expires_at=None,
                        now=now,
                        status=CandidateStatus.REJECTED,
                        decision_reason="sensitivity:" + message_sensitivity.value,
                    )
                )
            else:
                for draft in self._rule_extract_user(text, now=now):
                    sensitivity, ok = self.privacy.gate(draft["claim"])
                    status = (
                        CandidateStatus.PENDING if ok else CandidateStatus.REJECTED
                    )
                    reason = "" if ok else "sensitivity:" + sensitivity.value
                    candidate = self._candidate(
                        group_id=topic.group_id,
                        scope=MemoryScope.USER_IN_GROUP,
                        subject_id=subject_id,
                        kind=draft["kind"],
                        claim=draft["claim"],
                        source_ids=source_ids,
                        confidence=draft["confidence"],
                        sensitivity=sensitivity,
                        expires_at=draft.get("expires_at"),
                        now=now,
                        status=status,
                        decision_reason=reason,
                    )
                    candidates.append(candidate)
                    authorities[candidate.candidate_id] = int(
                        draft.get("authority", AUTHORITY_PREFERENCE)
                    )

        promise = self._rule_extract_bot_promise(reply_text)
        if promise:
            sensitivity, ok = self.privacy.gate(promise)
            status = CandidateStatus.PENDING if ok else CandidateStatus.REJECTED
            candidate = self._candidate(
                group_id=topic.group_id,
                scope=MemoryScope.SELF,
                subject_id=self.bot_id or "self",
                kind=MemoryKind.EPISODIC,
                claim=promise,
                source_ids=source_ids,
                confidence=0.7,
                sensitivity=sensitivity,
                expires_at=None,
                now=now,
                status=status,
                decision_reason="" if ok else "sensitivity:" + sensitivity.value,
            )
            candidates.append(candidate)
            authorities[candidate.candidate_id] = AUTHORITY_BOT_PROMISE
        return candidates, authorities

    def _arbitrate(
        self, candidate: MemoryCandidate, *, now: int, authority: int
    ) -> MemoryCandidate:
        hashed = candidate.claim_hash or claim_hash(candidate.claim)
        blocked = bool(
            self.store.has_tombstone(
                self.persona_id,
                candidate.group_id,
                candidate.subject_id,
                hashed,
            )
        )
        existing = list(
            self.store.list_memories(
                self.persona_id,
                candidate.group_id,
                now=now,
                limit=50,
                subject_id=candidate.subject_id,
                status_accepted_only=True,
            )
        )
        decision = self.arbiter.decide(
            candidate,
            existing=existing,
            has_tombstone=blocked,
            now=now,
            authority=int(authority),
        )
        if decision.status is CandidateStatus.ACCEPTED and decision.memory is not None:
            self.store.accept_candidate_memory(
                self.persona_id,
                candidate.candidate_id,
                decision.memory,
                reason=decision.reason,
                decided_at=now,
                superseded_memory_id=decision.superseded_memory_id,
            )
            return MemoryCandidate(
                candidate_id=candidate.candidate_id,
                group_id=candidate.group_id,
                scope=candidate.scope,
                subject_id=candidate.subject_id,
                kind=candidate.kind,
                claim=candidate.claim,
                source_message_ids=candidate.source_message_ids,
                confidence=candidate.confidence,
                sensitivity=candidate.sensitivity,
                proposed_expires_at=candidate.proposed_expires_at,
                extractor_version=candidate.extractor_version,
                status=CandidateStatus.ACCEPTED,
                created_at=candidate.created_at,
                decided_at=now,
                decision_reason=decision.reason,
                claim_hash=hashed,
            )
        self.store.decide_candidate(
            self.persona_id,
            candidate.candidate_id,
            decision.status,
            reason=decision.reason,
            decided_at=now,
        )
        return MemoryCandidate(
            candidate_id=candidate.candidate_id,
            group_id=candidate.group_id,
            scope=candidate.scope,
            subject_id=candidate.subject_id,
            kind=candidate.kind,
            claim=candidate.claim,
            source_message_ids=candidate.source_message_ids,
            confidence=candidate.confidence,
            sensitivity=candidate.sensitivity,
            proposed_expires_at=candidate.proposed_expires_at,
            extractor_version=candidate.extractor_version,
            status=decision.status,
            created_at=candidate.created_at,
            decided_at=now,
            decision_reason=decision.reason,
            claim_hash=hashed,
        )

    def _persist_candidate(
        self, candidate: MemoryCandidate
    ) -> Optional[MemoryCandidate]:
        # 预标 rejected 的也入库便于审计
        return self.store.append_memory_candidate(self.persona_id, candidate)

    def _candidate(
        self,
        *,
        group_id: str,
        scope: MemoryScope,
        subject_id: str,
        kind: MemoryKind,
        claim: str,
        source_ids: Sequence[str],
        confidence: float,
        sensitivity: Sensitivity,
        expires_at: Optional[int],
        now: int,
        status: CandidateStatus,
        decision_reason: str,
    ) -> MemoryCandidate:
        text = claim.strip()
        return MemoryCandidate(
            candidate_id=str(uuid4()),
            group_id=str(group_id),
            scope=scope,
            subject_id=str(subject_id),
            kind=kind,
            claim=text,
            source_message_ids=tuple(str(item) for item in source_ids if str(item)),
            confidence=float(confidence),
            sensitivity=sensitivity,
            proposed_expires_at=expires_at,
            extractor_version=EXTRACTOR_VERSION,
            status=status,
            created_at=int(now),
            decided_at=int(now) if status is not CandidateStatus.PENDING else None,
            decision_reason=decision_reason,
            claim_hash=claim_hash(text),
        )

    def _rule_extract_user(self, text: str, *, now: int) -> List[dict]:
        cleaned = (text or "").strip()
        if not cleaned:
            return []
        drafts: List[dict] = []

        remember = re.search(
            r"(?:请记住|记住|帮我记(?:住)?|记一下)[:：\s]*(.+)$",
            cleaned,
        )
        if remember:
            claim = remember.group(1).strip()
            if claim:
                drafts.append(
                    {
                        "claim": claim[:240],
                        "kind": MemoryKind.PROFILE,
                        "confidence": 0.92,
                        "authority": AUTHORITY_EXPLICIT,
                        "expires_at": None,
                    }
                )

        plan = re.search(
            r"(?:我)?(?:明天|后天|下周|这周|今晚|今天).{0,12}?(考试|出差|请假|起飞|出发)",
            cleaned,
        )
        if plan:
            drafts.append(
                {
                    "claim": cleaned[:240],
                    "kind": MemoryKind.EPISODIC,
                    "confidence": 0.8,
                    "authority": AUTHORITY_PLAN,
                    "expires_at": int(now) + 14 * 24 * 3600,
                }
            )

        pref = re.search(
            r"我(?:不喜欢|不爱|讨厌|喜欢|爱).+?(?:[。！？!?]|$)",
            cleaned,
        )
        if pref:
            drafts.append(
                {
                    "claim": pref.group(0).strip()[:240],
                    "kind": MemoryKind.PROFILE,
                    "confidence": 0.75,
                    "authority": AUTHORITY_PREFERENCE,
                    "expires_at": None,
                }
            )
        return drafts

    @staticmethod
    def _rule_extract_bot_promise(reply_text: str) -> str:
        cleaned = (reply_text or "").strip()
        if not cleaned:
            return ""
        match = re.search(
            r"(我会(?:帮你|记住|盯着|提醒).{0,40}|帮你记(?:住)?.{0,40})",
            cleaned,
        )
        if not match:
            return ""
        return match.group(0).strip()[:200]
