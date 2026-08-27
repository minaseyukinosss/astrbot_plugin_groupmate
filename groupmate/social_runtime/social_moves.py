"""Concrete social moves and evidence-carrying realization facts."""

from __future__ import annotations

import hashlib
import json
import unicodedata
from dataclasses import dataclass
from enum import Enum
from typing import Iterable


MAX_MOVE_REFERENCES = 32


class SocialMove(str, Enum):
    DIRECT_ANSWER = "DIRECT_ANSWER"
    ACCEPT = "ACCEPT"
    LIMITED_ACCEPT = "LIMITED_ACCEPT"
    PLAYFUL_RESISTANCE = "PLAYFUL_RESISTANCE"
    REFUSE = "REFUSE"
    FIRM_BOUNDARY = "FIRM_BOUNDARY"
    TEASE_FROM_CONTEXT = "TEASE_FROM_CONTEXT"
    COUNTER = "COUNTER"
    CORRECT_SELF = "CORRECT_SELF"
    REQUEST_NEEDED_EVIDENCE = "REQUEST_NEEDED_EVIDENCE"
    CONCRETE_CARE = "CONCRETE_CARE"
    SAFETY_MINIMUM = "SAFETY_MINIMUM"
    GROUP_RESPONSE = "GROUP_RESPONSE"
    JOIN_CHORUS = "JOIN_CHORUS"
    PROACTIVE_JOIN = "PROACTIVE_JOIN"
    SILENCE = "SILENCE"


class Ending(str, Enum):
    STOP = "STOP"
    QUESTION = "QUESTION"
    COUNTER = "COUNTER"
    OPEN_ACTION = "OPEN_ACTION"


class MediaIntent(str, Enum):
    NONE = "NONE"
    REACT = "REACT"
    SEND_IF_AVAILABLE = "SEND_IF_AVAILABLE"


class RealizationMode(str, Enum):
    GENERATED = "GENERATED"
    EXACT_CHORUS = "EXACT_CHORUS"


def _normalized_text(value: object, name: str) -> str:
    normalized = " ".join(unicodedata.normalize("NFKC", str(value or "")).split())
    if not normalized:
        raise ValueError(f"{name} must not be empty")
    return normalized


def _unique_texts(values: Iterable[object], name: str) -> tuple[str, ...]:
    result = tuple(dict.fromkeys(_normalized_text(value, name) for value in values))
    if len(result) > MAX_MOVE_REFERENCES:
        raise ValueError(f"{name} exceeds {MAX_MOVE_REFERENCES} items")
    return result


def _unique_facts(values: Iterable["DecisionFact"], name: str) -> tuple["DecisionFact", ...]:
    result: list[DecisionFact] = []
    seen: set[str] = set()
    for value in values:
        if not isinstance(value, DecisionFact):
            raise ValueError(f"{name} must contain DecisionFact values")
        if value.fact_id not in seen:
            seen.add(value.fact_id)
            result.append(value)
    if len(result) > MAX_MOVE_REFERENCES:
        raise ValueError(f"{name} exceeds {MAX_MOVE_REFERENCES} items")
    return tuple(result)


@dataclass(frozen=True)
class DecisionFact:
    fact_id: str
    category: str
    text: str
    source_event_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "fact_id", _normalized_text(self.fact_id, "fact_id"))
        object.__setattr__(self, "category", _normalized_text(self.category, "category"))
        object.__setattr__(self, "text", _normalized_text(self.text, "text"))
        object.__setattr__(
            self,
            "source_event_ids",
            _unique_texts(self.source_event_ids, "source_event_ids"),
        )
        if not self.source_event_ids:
            raise ValueError("source_event_ids must not be empty")

    @classmethod
    def create(
        cls,
        *,
        category: str,
        text: str,
        source_event_ids: Iterable[str],
    ) -> "DecisionFact":
        normalized_category = _normalized_text(category, "category")
        normalized_text = _normalized_text(text, "text")
        normalized_sources = _unique_texts(source_event_ids, "source_event_ids")
        if not normalized_sources:
            raise ValueError("source_event_ids must not be empty")
        canonical = json.dumps(
            {
                "category": normalized_category,
                "source_event_ids": normalized_sources,
                "text": normalized_text,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:24]
        return cls(
            fact_id=f"decision-fact:{digest}",
            category=normalized_category,
            text=normalized_text,
            source_event_ids=normalized_sources,
        )


@dataclass(frozen=True)
class SocialMovePlan:
    primary_move: SocialMove
    secondary_move: SocialMove | None
    must_say: tuple[DecisionFact, ...]
    may_say: tuple[DecisionFact, ...]
    must_not_say: tuple[str, ...]
    mention_event_ids: tuple[str, ...]
    ask_for: tuple[str, ...]
    ending: Ending
    media_intent: MediaIntent
    realization_mode: RealizationMode = RealizationMode.GENERATED
    verbatim_payload: str | None = None
    chorus_chain_id: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "primary_move", SocialMove(self.primary_move))
        object.__setattr__(
            self,
            "secondary_move",
            None if self.secondary_move is None else SocialMove(self.secondary_move),
        )
        object.__setattr__(self, "must_say", _unique_facts(self.must_say, "must_say"))
        object.__setattr__(self, "may_say", _unique_facts(self.may_say, "may_say"))
        object.__setattr__(
            self, "must_not_say", _unique_texts(self.must_not_say, "must_not_say")
        )
        object.__setattr__(
            self,
            "mention_event_ids",
            _unique_texts(self.mention_event_ids, "mention_event_ids"),
        )
        object.__setattr__(self, "ask_for", _unique_texts(self.ask_for, "ask_for"))
        object.__setattr__(self, "ending", Ending(self.ending))
        object.__setattr__(self, "media_intent", MediaIntent(self.media_intent))
        object.__setattr__(self, "realization_mode", RealizationMode(self.realization_mode))
        payload = None if self.verbatim_payload is None else str(self.verbatim_payload).strip()
        chain_id = None if self.chorus_chain_id is None else str(self.chorus_chain_id).strip()
        object.__setattr__(self, "verbatim_payload", payload or None)
        object.__setattr__(self, "chorus_chain_id", chain_id or None)
        self._validate_realization()

    def _validate_realization(self) -> None:
        if self.ending is Ending.QUESTION and not (
            self.ask_for or self.primary_move is SocialMove.COUNTER
        ):
            raise ValueError("question ending requires a real information gap or counter")

        if self.primary_move is SocialMove.JOIN_CHORUS:
            # 原样复读是冻结通道：不能混入模型生成、解释前缀或其他必须表达内容。
            if self.realization_mode is not RealizationMode.EXACT_CHORUS:
                raise ValueError("JOIN_CHORUS requires EXACT_CHORUS realization")
            if not self.verbatim_payload:
                raise ValueError("JOIN_CHORUS requires verbatim_payload")
            if not self.chorus_chain_id:
                raise ValueError("JOIN_CHORUS requires chorus_chain_id")
            if self.must_say or self.may_say or self.ask_for:
                raise ValueError("JOIN_CHORUS cannot carry generated content")
            if self.ending is not Ending.STOP:
                raise ValueError("JOIN_CHORUS must use STOP ending")
            return

        if self.realization_mode is not RealizationMode.GENERATED:
            raise ValueError("only JOIN_CHORUS may use EXACT_CHORUS")
        if self.verbatim_payload is not None or self.chorus_chain_id is not None:
            raise ValueError("verbatim payload and chain id are only JOIN_CHORUS fields")
        if self.primary_move is SocialMove.SILENCE and (
            self.must_say or self.may_say or self.ask_for
        ):
            raise ValueError("SILENCE cannot carry response content")

    @classmethod
    def create(cls, **values: object) -> "SocialMovePlan":
        normalized = {
            "secondary_move": None,
            "must_say": (),
            "may_say": (),
            "must_not_say": (),
            "mention_event_ids": (),
            "ask_for": (),
            "ending": Ending.STOP,
            "media_intent": MediaIntent.NONE,
            **values,
        }
        return cls(**normalized)


__all__ = (
    "DecisionFact",
    "Ending",
    "MediaIntent",
    "RealizationMode",
    "SocialMove",
    "SocialMovePlan",
)
