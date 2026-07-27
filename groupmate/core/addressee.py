"""AddresseeResolver：分离 reply_audience / memory_subject / social_target。"""

from __future__ import annotations

import re
from typing import Dict, Mapping, Optional, Sequence, Tuple

from ..models import (
    AddresseeKind,
    AddresseeResolution,
    ChatMessage,
    TargetingDecision,
    TopicSnapshot,
    TriggerKind,
)
from .history_format import focus_speaker, select_active_messages

_HARD_TRIGGERS = frozenset(
    {
        TriggerKind.ALIAS_DIRECT,
        TriggerKind.NATIVE_DIRECT,
        TriggerKind.CONTINUATION,
    }
)

_RECOUNT = re.compile(
    r"(他说|她说|ta说|他们说|听说|跟我说|跟你说).{0,24}|"
    r"(.{1,12})(明天|后天|下周).{0,8}(考试|出差|请假|生日)",
    re.IGNORECASE,
)

_QUESTION_END = re.compile(r"[？?]|吗$|呢$|怎么|什么|谁|哪")


def _resolution(
    kind: AddresseeKind,
    *,
    user_ids: Tuple[str, ...] = (),
    message_id: Optional[str] = None,
    confidence: float = 0.0,
    evidence: Tuple[str, ...] = (),
    reasons: Tuple[str, ...] = (),
) -> AddresseeResolution:
    return AddresseeResolution(
        kind=kind,
        target_user_ids=tuple(uid for uid in user_ids if uid),
        target_message_id=message_id,
        confidence=max(0.0, min(1.0, float(confidence))),
        evidence_message_ids=tuple(evidence),
        reason_codes=tuple(reasons),
    )


def _ambiguous(*reasons: str, evidence: Tuple[str, ...] = ()) -> AddresseeResolution:
    return _resolution(
        AddresseeKind.AMBIGUOUS,
        confidence=0.0,
        evidence=evidence,
        reasons=reasons,
    )


def _user(
    user_id: str,
    *,
    message_id: Optional[str] = None,
    confidence: float,
    evidence: Tuple[str, ...] = (),
    reasons: Tuple[str, ...] = (),
) -> AddresseeResolution:
    return _resolution(
        AddresseeKind.USER,
        user_ids=(user_id,),
        message_id=message_id,
        confidence=confidence,
        evidence=evidence,
        reasons=reasons,
    )


class AddresseeResolver:
    """按路线图 §9.1 优先级解析，并拆出三种目标。"""

    def resolve(
        self,
        topic: TopicSnapshot,
        trigger: TriggerKind,
        *,
        aliases: Sequence[str] = (),
        bot_id: str = "",
        relationships: Optional[Mapping[str, Tuple[str, str]]] = None,
        name_index: Optional[Mapping[str, str]] = None,
    ) -> TargetingDecision:
        active = select_active_messages(
            topic.messages, topic_created_at=topic.created_at
        )
        if not active:
            empty = _ambiguous("empty_topic")
            return TargetingDecision(empty, empty, empty)

        by_id: Dict[str, ChatMessage] = {
            message.message_id: message for message in topic.messages
        }
        latest = active[-1]
        evidence = (latest.message_id,)
        participants = {
            message.sender_id: message.sender_name
            for message in active
            if message.sender_id and not message.is_bot
        }
        index = dict(name_index or {})
        for sender_id, name in participants.items():
            if name:
                index.setdefault(name.strip().lower(), sender_id)
        for sender_id, pair in (relationships or {}).items():
            address = (pair[1] or "").strip()
            if address:
                index.setdefault(address.lower(), str(sender_id))

        primary = self._resolve_primary(
            active=active,
            latest=latest,
            by_id=by_id,
            bot_id=str(bot_id or ""),
            aliases=tuple(aliases or ()),
            name_index=index,
            participants=participants,
            evidence=evidence,
        )
        sender_id, _ = focus_speaker(active)
        if not sender_id and latest.sender_id and not latest.is_bot:
            sender_id = latest.sender_id

        reply = self._reply_audience(trigger, primary, sender_id, latest, evidence)
        social = self._social_target(primary, sender_id, latest, evidence)
        memory = self._memory_subject(
            primary, social, sender_id, latest, participants, evidence
        )
        return TargetingDecision(
            reply_audience=reply,
            memory_subject=memory,
            social_target=social,
        )

    def _resolve_primary(
        self,
        *,
        active: Sequence[ChatMessage],
        latest: ChatMessage,
        by_id: Mapping[str, ChatMessage],
        bot_id: str,
        aliases: Tuple[str, ...],
        name_index: Mapping[str, str],
        participants: Mapping[str, str],
        evidence: Tuple[str, ...],
    ) -> AddresseeResolution:
        # 1. reply/quote 链
        reply_id = latest.reply_to_message_id
        if reply_id:
            if latest.reply_to_bot:
                return _resolution(
                    AddresseeKind.BOT,
                    confidence=0.99,
                    evidence=evidence + (reply_id,),
                    reasons=("reply_to_bot",),
                    message_id=reply_id,
                )
            target = by_id.get(reply_id)
            if target is not None and not target.is_bot and target.sender_id:
                return _user(
                    target.sender_id,
                    message_id=reply_id,
                    confidence=0.98,
                    evidence=evidence + (reply_id,),
                    reasons=("reply_chain",),
                )

        # 2. 平台真实 mention（非 bot）
        mentioned = tuple(
            uid
            for uid in latest.mentioned_user_ids
            if uid and uid != bot_id and uid != latest.sender_id
        )
        if len(mentioned) == 1:
            return _user(
                mentioned[0],
                message_id=latest.message_id,
                confidence=0.97,
                evidence=evidence,
                reasons=("platform_mention",),
            )
        if len(mentioned) > 1:
            return _ambiguous(
                "multi_mention",
                evidence=evidence,
            )

        # 3. 句首显式称呼
        addressed = self._leading_address(latest.text, name_index, aliases)
        if addressed:
            return _user(
                addressed,
                message_id=latest.message_id,
                confidence=0.9,
                evidence=evidence,
                reasons=("leading_address",),
            )

        # 4. 已知别名与当前参与者（正文点名一人）
        named = self._body_single_name(latest.text, name_index, participants)
        if named is not None:
            if named == "":
                return _ambiguous("multi_name_call", evidence=evidence)
            return _user(
                named,
                message_id=latest.message_id,
                confidence=0.82,
                evidence=evidence,
                reasons=("participant_alias",),
            )

        # 5. 邻接问答对
        adjacent = self._adjacent_answer(active, latest)
        if adjacent:
            return _user(
                adjacent,
                message_id=latest.message_id,
                confidence=0.75,
                evidence=evidence,
                reasons=("adjacent_qa",),
            )

        # 6. topic addressee：当前无物化字段，跳过

        # 7. 最新发言人
        if latest.sender_id and not latest.is_bot:
            return _user(
                latest.sender_id,
                message_id=latest.message_id,
                confidence=0.55,
                evidence=evidence,
                reasons=("latest_speaker",),
            )
        speaker_id, _ = focus_speaker(active)
        if speaker_id:
            return _user(
                speaker_id,
                message_id=latest.message_id,
                confidence=0.5,
                evidence=evidence,
                reasons=("focus_speaker_fallback",),
            )

        # 8. AMBIGUOUS
        return _ambiguous("unresolved", evidence=evidence)

    def _reply_audience(
        self,
        trigger: TriggerKind,
        primary: AddresseeResolution,
        sender_id: str,
        latest: ChatMessage,
        evidence: Tuple[str, ...],
    ) -> AddresseeResolution:
        if primary.kind is AddresseeKind.AMBIGUOUS:
            if sender_id and trigger in _HARD_TRIGGERS:
                return _user(
                    sender_id,
                    message_id=latest.message_id,
                    confidence=0.7,
                    evidence=evidence,
                    reasons=("hard_trigger_sender", "primary_ambiguous"),
                )
            return _resolution(
                AddresseeKind.GROUP,
                confidence=0.4,
                evidence=evidence,
                reasons=("ambiguous_group_reply",) + primary.reason_codes,
            )
        if primary.kind is AddresseeKind.BOT and sender_id:
            return _user(
                sender_id,
                message_id=latest.message_id,
                confidence=0.95,
                evidence=evidence,
                reasons=("reply_to_bot_audience_sender",),
            )
        if trigger in _HARD_TRIGGERS and sender_id:
            return _user(
                sender_id,
                message_id=latest.message_id,
                confidence=0.95,
                evidence=evidence,
                reasons=("hard_trigger_sender",),
            )
        if primary.kind is AddresseeKind.USER:
            return primary
        if sender_id:
            return _user(
                sender_id,
                message_id=latest.message_id,
                confidence=0.6,
                evidence=evidence,
                reasons=("sender_fallback",),
            )
        return _resolution(
            AddresseeKind.GROUP,
            confidence=0.3,
            evidence=evidence,
            reasons=("group_fallback",),
        )

    def _social_target(
        self,
        primary: AddresseeResolution,
        sender_id: str,
        latest: ChatMessage,
        evidence: Tuple[str, ...],
    ) -> AddresseeResolution:
        # 社会互动对象默认是发送者；多点名歧义禁止个人写入
        if "multi_mention" in primary.reason_codes or "multi_name_call" in primary.reason_codes:
            return _ambiguous(*(primary.reason_codes), evidence=evidence)
        if sender_id:
            return _user(
                sender_id,
                message_id=latest.message_id,
                confidence=0.9 if primary.kind is not AddresseeKind.AMBIGUOUS else 0.55,
                evidence=evidence,
                reasons=("interaction_partner",),
            )
        if primary.kind is AddresseeKind.USER and primary.target_user_ids:
            return primary
        return _ambiguous("no_social_target", evidence=evidence)

    def _memory_subject(
        self,
        primary: AddresseeResolution,
        social: AddresseeResolution,
        sender_id: str,
        latest: ChatMessage,
        participants: Mapping[str, str],
        evidence: Tuple[str, ...],
    ) -> AddresseeResolution:
        # 转述第三人：可识别主体，但未确认前标 AMBIGUOUS，禁止个人长期记忆写入
        third = self._recounted_subject(latest.text, participants, sender_id, primary)
        if third:
            return _resolution(
                AddresseeKind.AMBIGUOUS,
                user_ids=(third,),
                message_id=latest.message_id,
                confidence=0.4,
                evidence=evidence,
                reasons=("recount_unconfirmed", "no_personal_memory"),
            )
        if social.kind is AddresseeKind.USER:
            return social
        if primary.kind is AddresseeKind.USER:
            return primary
        return _ambiguous("memory_unresolved", evidence=evidence)

    @staticmethod
    def _leading_address(
        text: str,
        name_index: Mapping[str, str],
        aliases: Sequence[str],
    ) -> Optional[str]:
        cleaned = (text or "").strip()
        if not cleaned:
            return None
        # 跳过 bot 别名句首
        lower = cleaned.lower()
        for alias in aliases:
            alias = (alias or "").strip()
            if not alias:
                continue
            if lower.startswith(alias.lower()):
                return None
        # 取句首称呼 token（逗号/空格/顿号前）
        token = re.split(r"[\s,，、:：]", cleaned, maxsplit=1)[0].strip()
        token = token.lstrip("@").strip()
        if len(token) < 1 or len(token) > 24:
            return None
        return name_index.get(token.lower())

    @staticmethod
    def _body_single_name(
        text: str,
        name_index: Mapping[str, str],
        participants: Mapping[str, str],
    ) -> Optional[str]:
        hits = []
        lowered = (text or "").lower()
        seen = set()
        for name, user_id in name_index.items():
            if not name or user_id in seen:
                continue
            if name in lowered:
                hits.append(user_id)
                seen.add(user_id)
        for user_id, name in participants.items():
            key = (name or "").strip().lower()
            if key and key in lowered and user_id not in seen:
                hits.append(user_id)
                seen.add(user_id)
        if len(hits) == 1:
            return hits[0]
        if len(hits) > 1:
            return ""
        return None

    @staticmethod
    def _adjacent_answer(
        active: Sequence[ChatMessage],
        latest: ChatMessage,
    ) -> Optional[str]:
        if latest.is_bot or len(active) < 2:
            return None
        prev = active[-2]
        if prev.is_bot or prev.sender_id == latest.sender_id:
            return None
        if not _QUESTION_END.search(prev.text or ""):
            return None
        # 紧邻回答：当前句回应上一人的问句 → 对上一人说话
        return prev.sender_id or None

    @staticmethod
    def _recounted_subject(
        text: str,
        participants: Mapping[str, str],
        sender_id: str,
        primary: AddresseeResolution,
    ) -> Optional[str]:
        if not _RECOUNT.search(text or ""):
            return None
        if primary.kind is AddresseeKind.USER and primary.target_user_ids:
            candidate = primary.target_user_ids[0]
            if candidate and candidate != sender_id:
                return candidate
        for user_id, name in participants.items():
            if user_id == sender_id:
                continue
            if name and name in (text or ""):
                return user_id
        return None
