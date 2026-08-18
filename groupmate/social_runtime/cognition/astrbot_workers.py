"""Strict JSON-schema adapter for AstrBot-provided cognition models."""

from __future__ import annotations

from dataclasses import asdict
from typing import Callable, Protocol

from ..attention import AttentionFrame
from .contracts import CognitiveContext, CognitiveObservation


class StructuredModelPort(Protocol):
    async def complete_json(self, *, schema: dict, payload: dict) -> object: ...


class AstrBotStructuredWorker:
    def __init__(
        self,
        name: str,
        model: StructuredModelPort,
        *,
        diagnostic_sink: Callable[[str], None] | None = None,
    ) -> None:
        self.name = name
        self._model = model
        self._diagnostic_sink = diagnostic_sink or (lambda code: None)

    async def observe(
        self, frame: AttentionFrame, context: CognitiveContext
    ) -> tuple[CognitiveObservation, ...]:
        try:
            raw = await self._model.complete_json(
                schema=self._schema(),
                payload={
                    "frame": asdict(frame),
                    "context": self._context_payload(context),
                },
            )
        except Exception:
            self._diagnostic_sink("model_call_failed")
            return ()
        if not isinstance(raw, dict) or not isinstance(raw.get("observations"), list):
            self._diagnostic_sink("invalid_worker_output")
            return ()
        if any(not isinstance(item, dict) for item in raw["observations"]):
            self._diagnostic_sink("invalid_worker_output")
            return ()
        try:
            return tuple(
                CognitiveObservation.create(
                    worker=self.name,
                    kind=item["kind"],
                    proposition=item["proposition"],
                    confidence=item["confidence"],
                    evidence_event_ids=tuple(item["evidence_event_ids"]),
                    scene_version=item["scene_version"],
                    expires_at=item["expires_at"],
                    uncertainty=tuple(item.get("uncertainty", ())),
                )
                for item in raw["observations"]
            )
        except (KeyError, TypeError, ValueError):
            self._diagnostic_sink("invalid_worker_output")
            return ()

    @staticmethod
    def _context_payload(context: CognitiveContext) -> dict[str, object]:
        return {
            "group_id": context.group_id,
            "scene_version": context.scene_version,
            "persona_state_version": context.persona_state_version,
            "config_version": context.config_version,
            "now": context.now,
            "focus_events": [dict(item) for item in context.focus_events],
            "world_summary": dict(context.world_summary),
            "constraints": list(context.constraints),
            "token_budget": context.token_budget,
        }

    @staticmethod
    def _schema() -> dict[str, object]:
        return {
            "type": "object",
            "required": ["observations"],
            "properties": {
                "observations": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "required": [
                            "kind",
                            "proposition",
                            "confidence",
                            "evidence_event_ids",
                            "scene_version",
                            "expires_at",
                        ],
                    },
                }
            },
            "additionalProperties": False,
        }


__all__ = ("AstrBotStructuredWorker", "StructuredModelPort")
