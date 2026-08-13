"""Framework-independent domain values used by the Groupmate workflow."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Any, Dict, Optional, Tuple

if TYPE_CHECKING:
    from .core.response_act import ResponseActPlan


class StringEnum(str, Enum):
    def __str__(self) -> str:
        return self.value


class TriggerKind(StringEnum):
    IGNORE = "ignore"
    COMMAND = "command"
    NATIVE_DIRECT = "native_direct"
    ALIAS_DIRECT = "alias_direct"
    COPIED_AT = "copied_at"
    CONTINUATION = "continuation"
    ALIAS_MENTION = "alias_mention"
    CANDIDATE = "candidate"
    HOST_INTERACTION = "host_interaction"


class DecisionAction(StringEnum):
    RESPOND = "respond"
    IGNORE = "ignore"


class Urgency(StringEnum):
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"


class OutboxStatus(StringEnum):
    PENDING = "pending"
    SENDING = "sending"
    SENT = "sent"
    FAILED = "failed"
    EXPIRED = "expired"
    UNKNOWN = "unknown"


class OutboundKind(StringEnum):
    TEXT = "text"
    MENTION = "mention"
    IMAGE = "image"
    POKE = "poke"
    FACE = "face"


@dataclass(frozen=True)
class OutboundSegment:
    kind: OutboundKind
    text: str = ""
    media_id: str = ""
    media_ref: str = ""
    target_user_id: str = ""

    def __post_init__(self) -> None:
        kind = self.kind
        if not isinstance(kind, OutboundKind):
            kind = OutboundKind(str(kind))
        text = str(self.text or "").strip()
        media_id = str(self.media_id or "").strip()
        media_ref = str(self.media_ref or "").strip()
        target_user_id = str(self.target_user_id or "").strip()
        if kind is OutboundKind.TEXT:
            if not text:
                raise ValueError("text outbound segment requires text")
            if media_id or media_ref or target_user_id:
                raise ValueError("text outbound segment cannot contain media")
        elif kind is OutboundKind.MENTION:
            if not target_user_id:
                raise ValueError("mention outbound segment requires target_user_id")
            if text or media_id or media_ref:
                raise ValueError("mention outbound segment cannot contain text or media")
        elif kind is OutboundKind.POKE:
            if not target_user_id:
                raise ValueError("poke outbound segment requires target_user_id")
            if text or media_id or media_ref:
                raise ValueError("poke outbound segment cannot contain text or media")
        elif kind is OutboundKind.FACE:
            if not media_id:
                raise ValueError("face outbound segment requires media_id")
            if text or media_ref or target_user_id:
                raise ValueError("face outbound segment cannot contain text or poke target")
        elif kind is OutboundKind.IMAGE:
            if not media_id or not media_ref:
                raise ValueError("image outbound segment requires media_id and media_ref")
        else:
            raise ValueError("unsupported outbound kind: {}".format(kind))
        object.__setattr__(self, "kind", kind)
        object.__setattr__(self, "text", text)
        object.__setattr__(self, "media_id", media_id)
        object.__setattr__(self, "media_ref", media_ref)
        object.__setattr__(self, "target_user_id", target_user_id)


class SendReceiptKind(StringEnum):
    CONFIRMED = "confirmed"
    FAILED = "failed"
    UNKNOWN = "unknown"


class MemoryKind(StringEnum):
    PROFILE = "profile"
    EPISODIC = "episodic"


class MemoryScope(StringEnum):
    GROUP = "GROUP"
    USER_IN_GROUP = "USER_IN_GROUP"
    SELF = "SELF"


class MemoryStatus(StringEnum):
    ACCEPTED = "accepted"
    SUPERSEDED = "superseded"
    EXPIRED = "expired"
    DELETED = "deleted"


class Sensitivity(StringEnum):
    NONE = "none"
    CREDENTIAL = "credential"
    PII = "pii"
    MEDICAL = "medical"
    POLITICAL = "political"
    SEXUAL = "sexual"
    MINOR = "minor"
    THIRD_PARTY = "third_party"
    JOKE = "joke"
    OTHER = "other"


class CandidateStatus(StringEnum):
    PENDING = "pending"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    CONFLICTED = "conflicted"


class MessageOrigin(StringEnum):
    PLATFORM_REALTIME = "PLATFORM_REALTIME"
    PLATFORM_HISTORY = "PLATFORM_HISTORY"
    BOT_DELIVERY = "BOT_DELIVERY"
    SYSTEM_SYNTHETIC = "SYSTEM_SYNTHETIC"


class AddresseeKind(StringEnum):
    USER = "user"
    BOT = "bot"
    GROUP = "group"
    AMBIGUOUS = "ambiguous"


class SocialEventKind(StringEnum):
    PRAISE = "PRAISE"
    THANKS = "THANKS"
    HELP_REQUEST = "HELP_REQUEST"
    HELPED = "HELPED"
    FRIENDLY_TEASE = "FRIENDLY_TEASE"
    CORRECTION = "CORRECTION"
    BOUNDARY_PUSH = "BOUNDARY_PUSH"
    HARASSMENT = "HARASSMENT"
    APOLOGY = "APOLOGY"
    NEUTRAL = "NEUTRAL"


class SocialEventStatus(StringEnum):
    PENDING = "pending"
    ACCEPTED = "accepted"
    REJECTED = "rejected"


class ContinuityKind(StringEnum):
    PLAN = "plan"
    PROMISE = "promise"
    FOLLOW_UP = "follow_up"


class ContinuityStatus(StringEnum):
    OPEN = "open"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    DELETED = "deleted"


class SelfCommitmentStatus(StringEnum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    BLOCKED = "blocked"
    WITHDRAWN = "withdrawn"
    DELETED = "deleted"


class InteractionScene(StringEnum):
    DIRECT_ADDRESS = "direct_address"
    REPLY_TO_BOT = "reply_to_bot"
    ACTIVE_CONTINUATION = "active_continuation"
    SOCIAL_RESPONSE = "social_response"
    AMBIENT_CONTRIBUTION = "ambient_contribution"
    TASK_REQUEST = "task_request"
    DIRECT_INTERACTION = "direct_interaction"


class QuoteMode(StringEnum):
    ALWAYS = "always"
    WHEN_INTERLEAVED = "when_interleaved"
    NEVER = "never"


class ReplyMode(StringEnum):
    SHORT_SOCIAL = "short_social"
    HELP_DETAIL = "help_detail"
    BOUNDARY = "boundary"
    TASK_RESULT = "task_result"


@dataclass(frozen=True)
class AddresseeResolution:
    kind: AddresseeKind
    target_user_ids: Tuple[str, ...] = ()
    target_message_id: Optional[str] = None
    confidence: float = 0.0
    evidence_message_ids: Tuple[str, ...] = ()
    reason_codes: Tuple[str, ...] = ()


@dataclass(frozen=True)
class TargetingDecision:
    reply_audience: AddresseeResolution
    memory_subject: AddresseeResolution
    social_target: AddresseeResolution


@dataclass(frozen=True)
class SocialEvent:
    event_id: str
    group_id: str
    user_id: str
    kind: SocialEventKind
    source_message_id: str
    confidence: float
    occurred_at: int
    decision_id: Optional[str] = None
    evidence_text: str = ""
    reason_code: str = ""
    extractor_version: str = "context-llm-v1"
    status: SocialEventStatus = SocialEventStatus.ACCEPTED
    reviewed_at: Optional[int] = None
    review_code: str = ""
    review_reason: str = ""

    def __post_init__(self) -> None:
        kind = self.kind
        if not isinstance(kind, SocialEventKind):
            try:
                kind = SocialEventKind(str(kind))
            except ValueError:
                kind = SocialEventKind.NEUTRAL
        status = self.status
        if not isinstance(status, SocialEventStatus):
            try:
                status = SocialEventStatus(str(status))
            except ValueError:
                status = SocialEventStatus.ACCEPTED
        object.__setattr__(self, "kind", kind)
        object.__setattr__(self, "status", status)
        object.__setattr__(self, "evidence_text", str(self.evidence_text or "").strip()[:160])
        object.__setattr__(self, "reason_code", str(self.reason_code or "").strip()[:80])
        object.__setattr__(
            self,
            "extractor_version",
            str(self.extractor_version or "context-llm-v1").strip()[:80],
        )
        object.__setattr__(self, "review_code", str(self.review_code or "").strip()[:40])
        object.__setattr__(self, "review_reason", str(self.review_reason or "").strip()[:160])


@dataclass(frozen=True)
class ContinuityItem:
    item_id: str
    group_id: str
    subject_id: str
    kind: ContinuityKind
    summary: str
    source_message_id: str
    source_quote: str
    created_at: int
    updated_at: int
    status: ContinuityStatus = ContinuityStatus.OPEN
    due_at: Optional[int] = None
    confidence: float = 1.0
    extractor_version: str = "context-llm-v1"
    resolution_message_id: Optional[str] = None
    resolution_quote: str = ""
    resolved_at: Optional[int] = None

    def __post_init__(self) -> None:
        kind = self.kind
        if not isinstance(kind, ContinuityKind):
            kind = ContinuityKind(str(kind))
        status = self.status
        if not isinstance(status, ContinuityStatus):
            status = ContinuityStatus(str(status))
        object.__setattr__(self, "kind", kind)
        object.__setattr__(self, "status", status)
        object.__setattr__(self, "summary", str(self.summary or "").strip()[:240])
        object.__setattr__(
            self, "source_quote", str(self.source_quote or "").strip()[:180]
        )
        object.__setattr__(
            self, "resolution_quote", str(self.resolution_quote or "").strip()[:180]
        )
        object.__setattr__(
            self, "confidence", max(0.0, min(1.0, float(self.confidence)))
        )


@dataclass(frozen=True)
class SelfCommitment:
    commitment_id: str
    group_id: str
    beneficiary_subject_id: str
    summary: str
    source_decision_id: str
    source_message_id: str
    source_quote: str
    created_at: int
    updated_at: int
    request_message_id: str = ""
    status: SelfCommitmentStatus = SelfCommitmentStatus.PENDING
    required_capability: str = ""
    fulfillment_mode: str = "follow_up"
    due_at: Optional[int] = None
    confidence: float = 1.0
    extractor_version: str = "context-llm-v1"
    result_decision_id: Optional[str] = None
    result_quote: str = ""
    result_facts: Tuple[str, ...] = ()
    failure_code: str = ""
    resolved_at: Optional[int] = None
    next_attempt_at: Optional[int] = None
    attempt_count: int = 0
    lease_owner: str = ""
    lease_until: Optional[int] = None
    last_attempt_at: Optional[int] = None
    last_delivery_at: Optional[int] = None

    def __post_init__(self) -> None:
        status = self.status
        if not isinstance(status, SelfCommitmentStatus):
            status = SelfCommitmentStatus(str(status))
        object.__setattr__(self, "status", status)
        object.__setattr__(self, "summary", str(self.summary or "").strip()[:240])
        object.__setattr__(
            self, "source_quote", str(self.source_quote or "").strip()[:180]
        )
        object.__setattr__(
            self, "result_quote", str(self.result_quote or "").strip()[:180]
        )
        object.__setattr__(
            self,
            "result_facts",
            tuple(str(item).strip()[:240] for item in self.result_facts if str(item).strip())[:8],
        )
        object.__setattr__(
            self, "required_capability", str(self.required_capability or "").strip()[:80]
        )
        mode = str(self.fulfillment_mode or "follow_up").strip().lower()
        if mode not in {"reminder", "capability", "follow_up"}:
            mode = "follow_up"
        object.__setattr__(self, "fulfillment_mode", mode)
        object.__setattr__(self, "failure_code", str(self.failure_code or "").strip()[:80])
        object.__setattr__(
            self, "confidence", max(0.0, min(1.0, float(self.confidence)))
        )
        object.__setattr__(self, "attempt_count", max(0, int(self.attempt_count)))
        object.__setattr__(self, "lease_owner", str(self.lease_owner or "")[:120])


@dataclass(frozen=True)
class RelationshipState:
    group_id: str
    user_id: str
    familiarity: int = 0
    affinity: int = 0
    trust: int = 0
    boundary_pressure: int = 0
    interaction_count: int = 0
    last_interaction_at: int = 0
    configured_relationship: Optional[str] = None
    updated_at: int = 0


@dataclass(frozen=True)
class ChatMessage:
    message_id: str
    group_id: str
    sender_id: str
    sender_name: str
    text: str
    timestamp: int
    reply_to_message_id: Optional[str] = None
    reply_to_bot: bool = False
    mentions_bot: bool = False
    is_bot: bool = False
    is_command: bool = False
    image_urls: Tuple[str, ...] = ()
    segment_types: Tuple[str, ...] = ()
    metadata: Dict[str, Any] = field(default_factory=dict, compare=False)
    origin: MessageOrigin = MessageOrigin.PLATFORM_REALTIME
    decision_id: Optional[str] = None
    ingested_at: int = 0
    platform: str = ""
    bot_id: str = ""
    event_version: int = 1
    mentioned_user_ids: Tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "message_id", str(self.message_id))
        object.__setattr__(self, "group_id", str(self.group_id))
        object.__setattr__(self, "sender_id", str(self.sender_id))
        object.__setattr__(self, "sender_name", (self.sender_name or "").strip())
        object.__setattr__(self, "text", (self.text or "").strip())
        object.__setattr__(self, "image_urls", tuple(self.image_urls))
        object.__setattr__(self, "segment_types", tuple(self.segment_types))
        mentions = tuple(
            str(item).strip()
            for item in (self.mentioned_user_ids or ())
            if str(item).strip()
        )
        object.__setattr__(self, "mentioned_user_ids", mentions)
        origin = self.origin
        if not isinstance(origin, MessageOrigin):
            try:
                origin = MessageOrigin(str(origin))
            except ValueError:
                origin = MessageOrigin.PLATFORM_REALTIME
        object.__setattr__(self, "origin", origin)
        decision_id = str(self.decision_id).strip() if self.decision_id else None
        object.__setattr__(self, "decision_id", decision_id or None)
        ingested = int(self.ingested_at or 0)
        if ingested <= 0:
            ingested = int(self.timestamp or 0)
        object.__setattr__(self, "ingested_at", ingested)
        object.__setattr__(self, "platform", str(self.platform or ""))
        object.__setattr__(self, "bot_id", str(self.bot_id or ""))
        object.__setattr__(self, "event_version", max(1, int(self.event_version or 1)))
        meta = dict(self.metadata or {})
        if mentions and "mentioned_user_ids" not in meta:
            meta["mentioned_user_ids"] = list(mentions)
        object.__setattr__(self, "metadata", meta)


    @property
    def identity(self) -> Tuple[str, str]:
        return self.group_id, self.message_id

    @property
    def has_content(self) -> bool:
        return bool(self.text or self.image_urls or self.segment_types)


@dataclass(frozen=True)
class TopicSnapshot:
    topic_id: str
    group_id: str
    messages: Tuple[ChatMessage, ...]
    created_at: int
    updated_at: int

    @property
    def latest(self) -> Optional[ChatMessage]:
        return self.messages[-1] if self.messages else None


@dataclass(frozen=True)
class Decision:
    action: DecisionAction
    confidence: float
    trigger: TriggerKind = TriggerKind.CANDIDATE
    reason_code: str = ""
    target_message_id: Optional[str] = None
    contribution: str = ""
    needs_vision: bool = False
    urgency: Urgency = Urgency.NORMAL

    @classmethod
    def ignore(
        cls,
        reason_code: str,
        trigger: TriggerKind = TriggerKind.CANDIDATE,
    ) -> "Decision":
        return cls(
            action=DecisionAction.IGNORE,
            confidence=0.0,
            trigger=trigger,
            reason_code=reason_code,
        )

    @classmethod
    def respond(
        cls,
        contribution: str,
        confidence: float = 1.0,
        trigger: TriggerKind = TriggerKind.CANDIDATE,
        reason_code: str = "useful_contribution",
        target_message_id: Optional[str] = None,
        needs_vision: bool = False,
        urgency: Urgency = Urgency.NORMAL,
    ) -> "Decision":
        return cls(
            action=DecisionAction.RESPOND,
            confidence=max(0.0, min(1.0, float(confidence))),
            trigger=trigger,
            reason_code=reason_code,
            target_message_id=target_message_id,
            contribution=contribution.strip(),
            needs_vision=needs_vision,
            urgency=urgency,
        )


@dataclass(frozen=True)
class MemoryItem:
    memory_id: str
    group_id: str
    subject_id: str
    kind: MemoryKind
    text: str
    created_at: int
    expires_at: Optional[int] = None
    confidence: float = 1.0
    importance: float = 0.5
    authority: int = 0
    source_message_id: Optional[str] = None
    status: MemoryStatus = MemoryStatus.ACCEPTED
    scope: MemoryScope = MemoryScope.USER_IN_GROUP
    sensitivity: Sensitivity = Sensitivity.NONE
    extractor_version: str = "rules-v1"
    supersedes_memory_id: Optional[str] = None
    source_message_ids: Tuple[str, ...] = ()

    def __post_init__(self) -> None:
        ids = tuple(
            str(item)
            for item in (self.source_message_ids or ())
            if str(item).strip()
        )
        if not ids and self.source_message_id:
            ids = (str(self.source_message_id),)
        object.__setattr__(self, "source_message_ids", ids)
        if self.source_message_id is None and ids:
            object.__setattr__(self, "source_message_id", ids[0])
        status = self.status
        if not isinstance(status, MemoryStatus):
            try:
                status = MemoryStatus(str(status))
            except ValueError:
                status = MemoryStatus.ACCEPTED
        object.__setattr__(self, "status", status)
        scope = self.scope
        if not isinstance(scope, MemoryScope):
            try:
                scope = MemoryScope(str(scope))
            except ValueError:
                scope = MemoryScope.USER_IN_GROUP
        object.__setattr__(self, "scope", scope)
        sensitivity = self.sensitivity
        if not isinstance(sensitivity, Sensitivity):
            try:
                sensitivity = Sensitivity(str(sensitivity))
            except ValueError:
                sensitivity = Sensitivity.NONE
        object.__setattr__(self, "sensitivity", sensitivity)


@dataclass(frozen=True)
class MemoryCandidate:
    candidate_id: str
    group_id: str
    scope: MemoryScope
    subject_id: str
    kind: MemoryKind
    claim: str
    source_message_ids: Tuple[str, ...]
    confidence: float
    sensitivity: Sensitivity
    proposed_expires_at: Optional[int]
    extractor_version: str
    status: CandidateStatus = CandidateStatus.PENDING
    created_at: int = 0
    decided_at: Optional[int] = None
    decision_reason: str = ""
    claim_hash: str = ""


@dataclass(frozen=True)
class MemoryRecord:
    memory_id: str
    status: MemoryStatus
    scope: MemoryScope
    subject_id: str
    kind: MemoryKind
    text: str
    source_message_ids: Tuple[str, ...]
    authority: int
    confidence: float
    created_at: int
    expires_at: Optional[int] = None
    supersedes_memory_id: Optional[str] = None
    group_id: str = ""
    sensitivity: Sensitivity = Sensitivity.NONE
    extractor_version: str = "rules-v1"
    importance: float = 0.5


@dataclass(frozen=True)
class ReplyPlan:
    decision_id: str
    group_id: str
    trigger: TriggerKind
    contribution: str
    target_message_id: Optional[str]
    urgency: Urgency
    persona_prompt: str
    user_prompt: str = ""
    soft_trigger: bool = False
    image_urls: Tuple[str, ...] = ()
    reply_mode: ReplyMode = ReplyMode.SHORT_SOCIAL
    response_act: Optional["ResponseActPlan"] = None
    required_capabilities: Tuple[str, ...] = ()


@dataclass(frozen=True)
class ResponseDraft:
    segments: Tuple[OutboundSegment, ...]
    quote_message_id: Optional[str]
    response_act: "ResponseAct"
    capability_name: str = ""

    def __post_init__(self) -> None:
        segments = tuple(self.segments or ())
        if not all(isinstance(item, OutboundSegment) for item in segments):
            raise TypeError("response draft segments must be OutboundSegment values")
        object.__setattr__(self, "segments", segments)
        object.__setattr__(
            self,
            "quote_message_id",
            str(self.quote_message_id).strip()
            if self.quote_message_id is not None
            else None,
        )
        object.__setattr__(
            self, "capability_name", str(self.capability_name or "").strip()
        )


@dataclass(frozen=True)
class WorkflowOutcome:
    decision_id: str
    sent: bool
    reason: str
    text: str = ""


@dataclass(frozen=True)
class SegmentReceipt:
    index: int
    platform_message_id: Optional[str] = None


@dataclass(frozen=True)
class SendResult:
    kind: SendReceiptKind
    segments: Tuple[SegmentReceipt, ...] = ()
    error_code: str = ""
    error_detail: str = ""

    @classmethod
    def confirmed(cls, count: int = 1) -> "SendResult":
        return cls(
            SendReceiptKind.CONFIRMED,
            tuple(SegmentReceipt(index) for index in range(max(0, int(count)))),
        )

    @classmethod
    def failed(cls, code: str, detail: str = "") -> "SendResult":
        return cls(SendReceiptKind.FAILED, error_code=code, error_detail=detail)

    @classmethod
    def unknown(
        cls,
        code: str = "no_receipt",
        detail: str = "",
        segments: Tuple[SegmentReceipt, ...] = (),
    ) -> "SendResult":
        return cls(
            SendReceiptKind.UNKNOWN,
            segments=segments,
            error_code=code,
            error_detail=detail,
        )
