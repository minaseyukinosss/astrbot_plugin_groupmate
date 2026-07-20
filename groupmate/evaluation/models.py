"""Stable values shared by offline evaluation and live shadow mode."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, Optional, Sequence, Tuple

from ..models import ChatMessage, TriggerKind


class EvaluationLabel(str, Enum):
    MUST_RESPOND = "must_respond"
    MAY_RESPOND = "may_respond"
    MUST_SILENCE = "must_silence"
    NATIVE_WAKE = "native_wake"
    COMMAND_BYPASS = "command_bypass"
    INVALID_INPUT = "invalid_input"


@dataclass(frozen=True)
class ExpectedOutcome:
    label: EvaluationLabel
    allowed_triggers: Tuple[TriggerKind, ...] = ()
    allowed_reason_codes: Tuple[str, ...] = ()
    target_message_id: Optional[str] = None


@dataclass(frozen=True)
class EvaluationCase:
    schema_version: int
    case_id: str
    description: str
    messages: Tuple[ChatMessage, ...]
    expected: ExpectedOutcome
    tags: Tuple[str, ...] = ()
    source: str = "handcrafted"


@dataclass(frozen=True)
class EvaluationDataset:
    cases: Tuple[EvaluationCase, ...]
    content_hash: str


@dataclass(frozen=True)
class PredictionRecord:
    case_id: str
    expected_label: EvaluationLabel
    trigger: TriggerKind
    action: str
    confidence: float
    reason_code: str
    target_message_id: Optional[str]
    decision_model_called: bool
    latency_ms: float
    error_code: Optional[str]
    matched: bool


@dataclass(frozen=True)
class ShadowSample:
    features: Dict[str, Any]
    context: Optional[Sequence[Dict[str, Any]]]
    sender_id: str


@dataclass(frozen=True)
class ShadowRecord:
    decision_id: str
    group_hash: str
    sender_hash: str
    trigger: str
    action: str
    confidence: float
    reason_code: str
    would_rate_limit: bool
    features: Dict[str, Any]
    context: Optional[Sequence[Dict[str, Any]]]
    model_id: str
    policy_version: str
    latency_ms: float
    error_code: Optional[str]
    created_at: int
    expires_at: int
