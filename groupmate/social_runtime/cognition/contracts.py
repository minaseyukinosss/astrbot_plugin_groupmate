"""Immutable contracts for stateless cognitive workers."""

from __future__ import annotations

import json
from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping, Protocol

from ..attention import AttentionFrame


def _frozen_mapping(value: Mapping[str, object]) -> Mapping[str, object]:
    normalized = dict(value)
    try:
        json.dumps(normalized, ensure_ascii=False, sort_keys=True)
    except (TypeError, ValueError) as exc:
        raise ValueError("mapping must be JSON serializable") from exc
    return MappingProxyType(normalized)


@dataclass(frozen=True)
class CognitiveContext:
    group_id: str
    scene_version: int
    persona_state_version: int
    config_version: int
    now: int
    focus_events: tuple[Mapping[str, object], ...]
    world_summary: Mapping[str, object]
    constraints: tuple[str, ...]
    token_budget: int

    @classmethod
    def create(cls, **values) -> "CognitiveContext":
        normalized = dict(values)
        if not str(normalized.get("group_id") or "").strip():
            raise ValueError("group_id must not be empty")
        for name in (
            "scene_version",
            "persona_state_version",
            "config_version",
            "now",
            "token_budget",
        ):
            number = int(normalized.get(name, -1))
            if number < 0:
                raise ValueError(f"{name} must not be negative")
            normalized[name] = number
        normalized["focus_events"] = tuple(
            _frozen_mapping(item) for item in normalized.get("focus_events", ())
        )
        normalized["world_summary"] = _frozen_mapping(
            normalized.get("world_summary", {})
        )
        normalized["constraints"] = tuple(normalized.get("constraints", ()))
        return cls(**normalized)


@dataclass(frozen=True)
class CognitiveObservation:
    worker: str
    kind: str
    proposition: Mapping[str, object]
    confidence: float
    evidence_event_ids: tuple[str, ...]
    scene_version: int
    expires_at: int
    uncertainty: tuple[str, ...]

    @classmethod
    def create(cls, **values) -> "CognitiveObservation":
        normalized = dict(values)
        for name in ("worker", "kind"):
            text = str(normalized.get(name) or "").strip()
            if not text:
                raise ValueError(f"{name} must not be empty")
            normalized[name] = text
        confidence = float(normalized.get("confidence", -1))
        if not 0.0 <= confidence <= 1.0:
            raise ValueError("confidence must be between 0 and 1")
        normalized["confidence"] = confidence
        evidence = tuple(
            str(item).strip()
            for item in normalized.get("evidence_event_ids", ())
            if str(item).strip()
        )
        if not evidence:
            raise ValueError("evidence_event_ids must not be empty")
        normalized["evidence_event_ids"] = evidence
        normalized["scene_version"] = int(normalized.get("scene_version", -1))
        normalized["expires_at"] = int(normalized.get("expires_at", -1))
        if normalized["scene_version"] < 0 or normalized["expires_at"] < 0:
            raise ValueError("scene_version and expires_at must not be negative")
        normalized["proposition"] = _frozen_mapping(
            normalized.get("proposition", {})
        )
        normalized["uncertainty"] = tuple(normalized.get("uncertainty", ()))
        return cls(**normalized)


class CognitiveWorker(Protocol):
    name: str

    async def observe(
        self, frame: AttentionFrame, context: CognitiveContext
    ) -> tuple[CognitiveObservation, ...]: ...


__all__ = ("CognitiveContext", "CognitiveObservation", "CognitiveWorker")
