"""Strict JSON-schema adapter for AstrBot-provided cognition models."""

from __future__ import annotations

import json
import time
from dataclasses import asdict
from typing import Callable, Protocol

from ..attention import AttentionFrame
from .contracts import CognitiveContext, CognitiveObservation, CognitiveWorkerResult


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
        return (await self.observe_with_result(frame, context)).observations

    async def observe_with_result(
        self, frame: AttentionFrame, context: CognitiveContext
    ) -> CognitiveWorkerResult:
        schema, payload = self._request(frame, context)
        input_bytes = self.input_bytes(frame, context)
        provider_started = time.monotonic_ns()
        try:
            raw = await self._model.complete_json(
                schema=schema,
                payload=payload,
            )
        except Exception as exc:
            return self._result(
                (),
                f"model_call_failed:{type(exc).__name__}",
                provider_latency_ms=self._elapsed_ms(provider_started),
                input_bytes=input_bytes,
            )
        provider_latency_ms = self._elapsed_ms(provider_started)
        if not isinstance(raw, dict) or not isinstance(raw.get("observations"), list):
            return self._result(
                (), "invalid_worker_output", provider_latency_ms, input_bytes
            )
        if any(not isinstance(item, dict) for item in raw["observations"]):
            return self._result(
                (), "invalid_worker_output", provider_latency_ms, input_bytes
            )
        try:
            observations = tuple(
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
            return self._result(
                (), "invalid_worker_output", provider_latency_ms, input_bytes
            )
        if (
            self.name == "ambient_social_assessor"
            and not self._valid_ambient_observations(observations)
        ):
            return self._result(
                (), "invalid_worker_output", provider_latency_ms, input_bytes
            )
        return CognitiveWorkerResult(
            observations,
            provider_latency_ms=provider_latency_ms,
            input_bytes=input_bytes,
        )

    @staticmethod
    def _valid_ambient_observations(
        observations: tuple[CognitiveObservation, ...],
    ) -> bool:
        signal_kinds = {
            "help_request",
            "care_signal",
            "humor_signal",
            "greeting",
            "boundary_signal",
        }
        assessments = tuple(
            item
            for item in observations
            if item.kind == "participation_assessment"
        )
        if (
            len(assessments) != 1
            or observations[-1].kind != "participation_assessment"
            or any(item.kind not in signal_kinds for item in observations[:-1])
        ):
            return False
        proposition = assessments[0].proposition
        required = {
            "should_participate",
            "decision",
            "target_confidence",
            "topic_confidence",
            "disruption_cost",
            "novelty",
            "repetition_cost",
        }
        return (
            required <= proposition.keys()
            and isinstance(proposition.get("should_participate"), bool)
            and proposition.get("decision") in {"speak", "silence"}
        )

    def _result(
        self,
        observations: tuple[CognitiveObservation, ...],
        diagnostic_code: str,
        provider_latency_ms: int = 0,
        input_bytes: int = 0,
    ) -> CognitiveWorkerResult:
        self._diagnostic_sink(diagnostic_code)
        return CognitiveWorkerResult(
            observations,
            diagnostic_code,
            provider_latency_ms=max(0, int(provider_latency_ms)),
            input_bytes=max(0, int(input_bytes)),
        )

    @staticmethod
    def _elapsed_ms(started: int) -> int:
        return max(0, (time.monotonic_ns() - int(started)) // 1_000_000)

    def input_bytes(
        self, frame: AttentionFrame, context: CognitiveContext
    ) -> int:
        schema, payload = self._request(frame, context)
        return len(
            json.dumps(
                {"schema": schema, "input": payload},
                ensure_ascii=False,
                sort_keys=True,
            ).encode("utf-8")
        )

    def _request(
        self, frame: AttentionFrame, context: CognitiveContext
    ) -> tuple[dict, dict]:
        return self._schema(), {
            "worker": self.name,
            "task": self._worker_instruction(),
            "frame": asdict(frame),
            "context": self._context_payload(context),
        }

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
            "ambient_social_assessor": (
                "联合理解普通群聊场景并保守评估 bot 是否应插话。先输出零到多个"
                "有证据的社交信号（help_request、care_signal、humor_signal、"
                "greeting、boundary_signal），再且仅输出一条 participation_assessment。"
                "其 proposition 必须包含 should_participate、decision(speak/silence)、"
                "target_confidence、topic_confidence、disruption_cost、novelty、"
                "repetition_cost。不确定时选择 silence；不写回复正文。"
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
