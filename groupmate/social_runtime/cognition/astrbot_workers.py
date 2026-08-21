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
                    "worker": self.name,
                    "task": self._worker_instruction(),
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

    def _worker_instruction(self) -> str:
        instructions = {
            "direct_interaction": (
                "分析明确点名或回复 bot 的消息。只识别 help_request、care_signal、"
                "humor_signal、greeting、boundary_signal；不写回复正文。subject_id、"
                "topic_id 和 evidence_event_ids 必须来自输入事实。"
            ),
            "scene_interpreter": (
                "判断当前话题的社交信号，只可输出 help_request、care_signal、"
                "humor_signal、greeting、boundary_signal；不决定 bot 是否插话，"
                "不写回复正文。"
            ),
            "participation_assessor": (
                "保守判断 bot 是否应插话。输出 participation_assessment，proposition "
                "必须包含 should_participate、decision(speak/silence)、"
                "target_confidence、topic_confidence、disruption_cost、novelty、"
                "repetition_cost。不确定时选择 silence。"
            ),
        }
        return instructions.get(
            self.name,
            "只输出有证据、可过期的结构化观察；不写回复正文或推理过程。",
        )

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
                        "properties": {
                            "kind": {
                                "enum": [
                                    "help_request",
                                    "care_signal",
                                    "humor_signal",
                                    "greeting",
                                    "boundary_signal",
                                    "participation_assessment",
                                ]
                            },
                            "proposition": {"type": "object"},
                            "confidence": {
                                "type": "number",
                                "minimum": 0,
                                "maximum": 1,
                            },
                            "evidence_event_ids": {
                                "type": "array",
                                "items": {"type": "string"},
                                "minItems": 1,
                            },
                            "scene_version": {"type": "integer"},
                            "expires_at": {"type": "integer"},
                            "uncertainty": {
                                "type": "array",
                                "items": {"type": "string"},
                            },
                        },
                    },
                }
            },
            "additionalProperties": False,
        }


__all__ = ("AstrBotStructuredWorker", "StructuredModelPort")
