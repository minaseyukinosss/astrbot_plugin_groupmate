"""Stable values shared by offline evaluation and live shadow mode."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional, Tuple

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
