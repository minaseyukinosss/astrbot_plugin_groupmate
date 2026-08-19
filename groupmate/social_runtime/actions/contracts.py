"""Immutable contracts for deterministic, finite social action plans."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from enum import Enum
from types import MappingProxyType
from typing import Mapping, Optional, Tuple


MAX_ACTION_PLAN_NODES = 24
MAX_ACTION_PLAN_DURATION = 24 * 60 * 60
MAX_ACTION_NODE_RETRIES = 2
MAX_AUTONOMOUS_FOLLOWUPS = 1


@dataclass(frozen=True)
class ActionNode:
    node_id: str
    kind: str
    owner_id: str
    retry_limit: int
    deadline_at: Optional[int]
    permission: str
    visible: bool = False
    autonomous_followup: bool = False


@dataclass(frozen=True)
class ActionEdge:
    source_node_id: str
    target_node_id: str


@dataclass(frozen=True)
class ActionPlan:
    plan_id: str
    correlation_id: str
    group_id: str
    persona_id: str
    scene_version: int
    config_version: int
    persona_version: int
    constitution_version: int
    relationship_version: int
    state_version: int
    intention_ids: Tuple[str, ...]
    audience: Tuple[str, ...]
    topic_id: Optional[str]
    origin: str
    nodes: Tuple[ActionNode, ...]
    edges: Tuple[ActionEdge, ...]
    constraints: Tuple[str, ...]
    constitution_approved: bool
    relationship_approved: bool
    state_approved: bool
    risk_score: int
    media_references: Tuple[str, ...]
    budget_cost: int
    concurrency: int
    confirmation_ids: Tuple[str, ...]
    expires_at: int

    def node_kinds(self) -> Tuple[str, ...]:
        return tuple(node.kind for node in self.nodes)


@dataclass(frozen=True)
class PlanContext:
    """Frozen inputs against which a plan is both built and validated."""

    now: int
    group_id: str
    persona_id: str
    scene_version: int
    config_version: int
    persona_version: int
    constitution_version: int
    relationship_version: int
    state_version: int
    requester_permissions: Tuple[str, ...]
    supported_node_kinds: Tuple[str, ...]
    allowed_audience_ids: Tuple[str, ...]
    allowed_owner_ids: Tuple[str, ...]
    max_nodes: int
    max_plan_duration: int
    max_retries: int
    max_autonomous_followups: int
    constitution_allowed: bool
    relationship_allowed: bool
    state_allowed: bool
    max_risk_score: int
    allowed_media_references: Tuple[str, ...]
    max_budget_cost: int
    max_concurrency: int
    confirmed_ids: Tuple[str, ...]


_INVALID_DISPOSITIONS = frozenset(
    {"REDUCE", "REPLAN", "DEFER", "CLARIFY", "ABANDON"}
)


@dataclass(frozen=True)
class PlanValidation:
    accepted: bool
    errors: Tuple[str, ...]
    reduced_plan: Optional[ActionPlan]
    disposition: Optional[str] = None
    plan_id: Optional[str] = None
    plan_digest: Optional[str] = None

    def __post_init__(self) -> None:
        if self.accepted:
            if self.errors or self.reduced_plan is not None or self.disposition is not None:
                raise ValueError("accepted validation cannot carry a disposition or reduction")
            if not self.plan_id or not self.plan_digest:
                raise ValueError("accepted validation must bind one action plan")
            return
        if self.disposition not in _INVALID_DISPOSITIONS:
            raise ValueError("invalid plan requires a governance disposition")
        if self.plan_id is not None or self.plan_digest is not None:
            raise ValueError("rejected validation cannot bind an action plan")


def action_plan_digest(plan: ActionPlan) -> str:
    if not isinstance(plan, ActionPlan):
        raise ValueError("plan must be an ActionPlan")
    encoded = json.dumps(
        asdict(plan),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


class DeliveryPartKind(str, Enum):
    TEXT = "text"
    MENTION = "mention"
    FACE = "face"
    IMAGE = "image"
    AUDIO = "audio"
    VIDEO = "video"
    FILE = "file"
    FORWARD = "forward"
    POKE = "poke"


class OutboxStatus(str, Enum):
    PLANNED = "planned"
    READY = "ready"
    SENDING = "sending"
    SENT = "sent"
    FAILED = "failed"
    UNKNOWN = "unknown"
    EXPIRED = "expired"
    SUPPRESSED = "suppressed"


class DeliveryReceiptStatus(str, Enum):
    SUCCESS = "success"
    RETRYABLE_FAILURE = "retryable_failure"
    PERMANENT_FAILURE = "permanent_failure"
    UNKNOWN = "unknown"


def _delivery_text(value: object, name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{name} must not be empty")
    return text


def _delivery_payload(value: Mapping[str, object]) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError("delivery payload must be a mapping")
    try:
        encoded = json.dumps(dict(value), ensure_ascii=False, sort_keys=True, allow_nan=False)
        normalized = json.loads(encoded)
    except (TypeError, ValueError) as exc:
        raise ValueError("delivery payload must be JSON serializable") from exc
    return MappingProxyType(normalized)


@dataclass(frozen=True)
class DeliveryPart:
    part_id: str
    kind: DeliveryPartKind
    payload: Mapping[str, object]
    order: int
    idempotency_key: str
    expires_at: int
    decorative: bool = False
    task_id: str | None = None
    role: str = "content"

    @classmethod
    def create(cls, **values: object) -> "DeliveryPart":
        normalized = dict(values)
        normalized["part_id"] = _delivery_text(normalized.get("part_id"), "part_id")
        normalized["kind"] = DeliveryPartKind(normalized.get("kind"))
        normalized["payload"] = _delivery_payload(normalized.get("payload", {}))
        order = int(normalized.get("order", -1))
        if order < 0:
            raise ValueError("delivery part order must not be negative")
        normalized["order"] = order
        normalized["idempotency_key"] = _delivery_text(
            normalized.get("idempotency_key"), "idempotency_key"
        )
        expires_at = int(normalized.get("expires_at", -1))
        if expires_at < 0:
            raise ValueError("delivery part expires_at must not be negative")
        normalized["expires_at"] = expires_at
        decorative = normalized.get("decorative", False)
        if not isinstance(decorative, bool):
            raise ValueError("decorative must be a boolean")
        normalized["decorative"] = decorative
        task_id = normalized.get("task_id")
        normalized["task_id"] = (
            None if task_id is None else _delivery_text(task_id, "task_id")
        )
        role = str(normalized.get("role", "content")).strip().lower()
        if role not in {"content", "progress", "result"}:
            raise ValueError("delivery role must be content, progress, or result")
        if role != "content" and normalized["task_id"] is None:
            raise ValueError("progress and result parts require task_id")
        normalized["role"] = role
        cls._validate_payload(normalized["kind"], normalized["payload"])
        return cls(**normalized)

    @staticmethod
    def _validate_payload(kind: DeliveryPartKind, payload: Mapping[str, object]) -> None:
        required = {
            DeliveryPartKind.TEXT: "text",
            DeliveryPartKind.MENTION: "target_id",
            DeliveryPartKind.FACE: "face_id",
            DeliveryPartKind.IMAGE: "media_ref",
            DeliveryPartKind.AUDIO: "media_ref",
            DeliveryPartKind.VIDEO: "media_ref",
            DeliveryPartKind.FILE: "media_ref",
            DeliveryPartKind.FORWARD: "nodes",
            DeliveryPartKind.POKE: "target_id",
        }[kind]
        if required not in payload:
            raise ValueError(f"{kind.value} delivery payload requires {required}")
        value = payload[required]
        if kind is DeliveryPartKind.FORWARD:
            if not isinstance(value, list) or not value:
                raise ValueError("forward nodes must be a non-empty array")
        else:
            _delivery_text(value, required)


@dataclass(frozen=True)
class DeliveryBundle:
    bundle_id: str
    correlation_id: str
    persona_id: str
    group_id: str
    topic_id: str | None
    parts: tuple[DeliveryPart, ...]
    created_at: int
    expires_at: int

    @classmethod
    def create(cls, **values: object) -> "DeliveryBundle":
        normalized = dict(values)
        for name in ("bundle_id", "correlation_id", "persona_id", "group_id"):
            normalized[name] = _delivery_text(normalized.get(name), name)
        topic_id = normalized.get("topic_id")
        normalized["topic_id"] = (
            None if topic_id is None else _delivery_text(topic_id, "topic_id")
        )
        parts = tuple(normalized.get("parts", ()))
        if not parts or not all(isinstance(item, DeliveryPart) for item in parts):
            raise ValueError("delivery bundle requires DeliveryPart values")
        part_ids = tuple(item.part_id for item in parts)
        keys = tuple(item.idempotency_key for item in parts)
        orders = tuple(item.order for item in parts)
        if len(part_ids) != len(set(part_ids)) or len(keys) != len(set(keys)):
            raise ValueError("delivery part ids and idempotency keys must be unique")
        if set(orders) != set(range(len(parts))):
            raise ValueError("delivery part order must be contiguous from zero")
        normalized["parts"] = tuple(sorted(parts, key=lambda item: item.order))
        created_at = int(normalized.get("created_at", -1))
        expires_at = int(normalized.get("expires_at", -1))
        if created_at < 0 or expires_at <= created_at:
            raise ValueError("delivery bundle must expire after creation")
        if any(item.expires_at > expires_at or item.expires_at <= created_at for item in parts):
            raise ValueError("delivery part expiry must be within bundle lifetime")
        normalized["created_at"] = created_at
        normalized["expires_at"] = expires_at
        return cls(**normalized)


@dataclass(frozen=True)
class DeliveryReceipt:
    receipt_id: str
    part_id: str
    status: DeliveryReceiptStatus
    occurred_at: int
    platform_message_id: str | None = None
    error_code: str | None = None

    @classmethod
    def create(cls, **values: object) -> "DeliveryReceipt":
        normalized = dict(values)
        normalized["receipt_id"] = _delivery_text(
            normalized.get("receipt_id"), "receipt_id"
        )
        normalized["part_id"] = _delivery_text(normalized.get("part_id"), "part_id")
        normalized["status"] = DeliveryReceiptStatus(normalized.get("status"))
        occurred_at = int(normalized.get("occurred_at", -1))
        if occurred_at < 0:
            raise ValueError("receipt occurred_at must not be negative")
        normalized["occurred_at"] = occurred_at
        message_id = normalized.get("platform_message_id")
        normalized["platform_message_id"] = (
            None
            if message_id is None
            else _delivery_text(message_id, "platform_message_id")
        )
        error = normalized.get("error_code")
        normalized["error_code"] = (
            None if error is None else _delivery_text(error, "error_code")
        )
        if (
            normalized["status"] is DeliveryReceiptStatus.SUCCESS
            and normalized["platform_message_id"] is None
        ):
            raise ValueError("successful receipt requires platform_message_id")
        if (
            normalized["status"] is not DeliveryReceiptStatus.SUCCESS
            and normalized["error_code"] is None
        ):
            raise ValueError("failed or unknown receipt requires error_code")
        return cls(**normalized)


@dataclass(frozen=True)
class OutboxPart:
    bundle_id: str
    correlation_id: str
    persona_id: str
    group_id: str
    topic_id: str | None
    part: DeliveryPart
    status: OutboxStatus
    receipt: DeliveryReceipt | None = None

    @property
    def part_id(self) -> str:
        return self.part.part_id

    @property
    def idempotency_key(self) -> str:
        return self.part.idempotency_key


@dataclass(frozen=True)
class BotLedgerEntry:
    part_id: str
    bundle_id: str
    correlation_id: str
    persona_id: str
    group_id: str
    platform_message_id: str
    sent_at: int


__all__ = (
    "ActionEdge",
    "ActionNode",
    "ActionPlan",
    "BotLedgerEntry",
    "DeliveryBundle",
    "DeliveryPart",
    "DeliveryPartKind",
    "DeliveryReceipt",
    "DeliveryReceiptStatus",
    "MAX_ACTION_NODE_RETRIES",
    "MAX_ACTION_PLAN_DURATION",
    "MAX_ACTION_PLAN_NODES",
    "MAX_AUTONOMOUS_FOLLOWUPS",
    "PlanContext",
    "PlanValidation",
    "action_plan_digest",
    "OutboxPart",
    "OutboxStatus",
)
