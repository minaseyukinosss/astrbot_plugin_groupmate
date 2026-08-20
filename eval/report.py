"""Stable, serializable reports produced by the offline evaluation runner."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Mapping

from .safety import SafetyReport


@dataclass(frozen=True)
class LaneReport:
    lane: str
    effect_count: int
    group_confusions: Mapping[str, Mapping[str, object]]
    scene_confusions: Mapping[str, Mapping[str, object]]
    metrics: Mapping[str, object]
    label_source: str = "human_fixture"

    def to_dict(self) -> dict[str, object]:
        return {
            "lane": self.lane,
            "effect_count": self.effect_count,
            "group_confusions": {
                key: dict(value) for key, value in sorted(self.group_confusions.items())
            },
            "scene_confusions": {
                key: dict(value) for key, value in sorted(self.scene_confusions.items())
            },
            "metrics": dict(self.metrics),
            "label_source": self.label_source,
        }


@dataclass(frozen=True)
class EvaluationReport:
    lanes: Mapping[str, LaneReport]
    excluded_unknown_count: int
    latency_ms: Mapping[str, float | int]
    cost: Mapping[str, float | int]
    safety: SafetyReport
    model_facts: tuple[Mapping[str, object], ...]
    kind: str
    production_readiness_eligible: bool
    readiness_reason: str

    def to_dict(self) -> dict[str, object]:
        return {
            "lanes": {key: self.lanes[key].to_dict() for key in sorted(self.lanes)},
            "excluded_unknown_count": self.excluded_unknown_count,
            "latency_ms": dict(self.latency_ms),
            "cost": dict(self.cost),
            "safety": self.safety.to_dict(),
            "model_facts": [dict(value) for value in self.model_facts],
            "kind": self.kind,
            "production_readiness_eligible": self.production_readiness_eligible,
            "readiness_reason": self.readiness_reason,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":"))


__all__ = ("EvaluationReport", "LaneReport")
