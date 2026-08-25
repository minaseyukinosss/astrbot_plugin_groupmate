"""Compact direct-model worker for AMBIENT group-chat cognition."""

from __future__ import annotations

import math
from typing import Mapping

from ...adapters.deepseek_cognition import DirectCognitionError
from ..attention import AttentionFrame
from ..society.relationship_events import (
    RELATIONSHIP_EVENT_KINDS,
    RELATIONSHIP_SEVERITIES,
)
from .contracts import CognitiveContext, CognitiveObservation, CognitiveWorkerResult


_SIGNALS = {
    "help_request",
    "care_signal",
    "humor_signal",
    "greeting",
    "boundary_signal",
    "none",
}
_REQUIRED_VERDICT_FIELDS = {
    "decision",
    "signal",
    "target_id",
    "evidence_event_ids",
    "confidence",
    "disruption",
    "novelty",
    "reason",
}
_MODEL_RELATIONSHIP_EVENT_KINDS = RELATIONSHIP_EVENT_KINDS - {
    "interaction",
    "reciprocal_action",
}


class _VerdictRejected(ValueError):
    def __init__(self, code: str) -> None:
        self.code = str(code)
        super().__init__(self.code)


class DirectAmbientWorker:
    name = "ambient_social_assessor"

    def __init__(self, client) -> None:
        self._client = client
        self.backend = "direct_deepseek"
        self.model = str(getattr(client, "model", "") or "")

    async def observe(
        self, frame: AttentionFrame, context: CognitiveContext
    ) -> tuple[CognitiveObservation, ...]:
        return (await self.observe_with_result(frame, context)).observations

    def input_bytes(
        self, frame: AttentionFrame, context: CognitiveContext
    ) -> int:
        return max(0, int(self._client.input_bytes(self._facts(frame, context))))

    async def observe_with_result(
        self, frame: AttentionFrame, context: CognitiveContext
    ) -> CognitiveWorkerResult:
        facts = self._facts(frame, context)
        fallback_bytes = max(0, int(self._client.input_bytes(facts)))
        try:
            response = await self._client.classify(facts)
        except DirectCognitionError as exc:
            return CognitiveWorkerResult(
                (),
                exc.code,
                provider_latency_ms=exc.latency_ms,
                input_bytes=exc.request_bytes or fallback_bytes,
                backend=exc.backend or self.backend,
                model=exc.model or self.model,
            )
        try:
            observations = self._observations(
                response.verdict, frame, context
            )
        except _VerdictRejected as exc:
            return CognitiveWorkerResult(
                (),
                exc.code,
                provider_latency_ms=response.latency_ms,
                input_bytes=response.request_bytes or fallback_bytes,
                backend=response.backend or self.backend,
                model=response.model or self.model,
            )
        except (KeyError, TypeError, ValueError):
            return CognitiveWorkerResult(
                (),
                "direct_invalid_output",
                provider_latency_ms=response.latency_ms,
                input_bytes=response.request_bytes or fallback_bytes,
                backend=response.backend or self.backend,
                model=response.model or self.model,
            )
        return CognitiveWorkerResult(
            observations,
            provider_latency_ms=response.latency_ms,
            input_bytes=response.request_bytes or fallback_bytes,
            backend=response.backend or self.backend,
            model=response.model or self.model,
        )

    @classmethod
    def _facts(
        cls, frame: AttentionFrame, context: CognitiveContext
    ) -> dict[str, object]:
        allowed_events = set(frame.focus_event_ids)
        events = [
            cls._event_fact(item)
            for item in context.focus_events
            if str(item.get("event_id") or "") in allowed_events
        ][-12:]
        world = context.world_summary
        topics = [
            cls._topic_fact(item)
            for item in cls._mapping_items(world.get("topics"))
            if str(item.get("topic_id") or "") in set(frame.focus_topic_ids)
        ][-4:]
        audiences = [
            cls._audience_fact(item)
            for item in cls._mapping_items(world.get("audiences"))
            if str(item.get("actor_id") or "")
            in set(frame.candidate_audiences)
        ][-8:]
        activity = world.get("group_activity")
        activity_fact = (
            {
                "event_count": cls._safe_int(activity.get("event_count")),
                "message_count": cls._safe_int(activity.get("message_count")),
                "last_event_at": cls._optional_int(
                    activity.get("last_event_at")
                ),
                "last_bot_event_at": cls._optional_int(
                    world.get("last_bot_event_at")
                ),
            }
            if isinstance(activity, Mapping)
            else {}
        )
        lease = world.get("conversation_lease")
        lease_fact = (
            {
                "target_id": cls._text(lease.get("target_id"), 80) or None,
                "topic_id": cls._text(lease.get("topic_id"), 120) or None,
                "expires_at": cls._optional_int(lease.get("expires_at")),
                "remaining_turns": cls._safe_int(
                    lease.get("remaining_turns")
                ),
            }
            if isinstance(lease, Mapping)
            else None
        )
        relationship_memories = [
            cls._relationship_memory_fact(item)
            for item in cls._mapping_items(
                world.get("relationship_memories")
            )
            if cls._text(item.get("subject_id"), 80)
            in set(frame.candidate_audiences)
        ][-8:]
        profile = world.get("persona_profile")
        persona = {}
        if isinstance(profile, Mapping):
            for section in ("presence", "participation"):
                values = profile.get(section)
                if isinstance(values, Mapping):
                    persona[section] = {
                        cls._text(key, 60): cls._text(value, 240)
                        for key, value in values.items()
                        if cls._text(key, 60) and cls._text(value, 240)
                    }
        return {
            "events": events,
            "topics": topics,
            "audiences": audiences,
            "activity": activity_fact,
            "lease": lease_fact,
            "persona": persona,
            "relationship_memories": relationship_memories,
        }

    @classmethod
    def _event_fact(cls, event: Mapping[str, object]) -> dict[str, object]:
        payload = event.get("payload")
        if not isinstance(payload, Mapping):
            payload = {}
        sender = payload.get("sender")
        actor_name = (
            cls._text(sender.get("name"), 60)
            if isinstance(sender, Mapping)
            else ""
        )
        segments = cls._mapping_items(payload.get("segments"))
        parts: list[str] = []
        for segment in segments:
            kind = cls._text(segment.get("type"), 30).lower()
            if kind and kind != "text" and kind not in parts:
                parts.append(kind)
            if len(parts) >= 8:
                break
        mentions = []
        raw_mentions = payload.get("mentions")
        if isinstance(raw_mentions, (list, tuple)):
            mentions = [
                value
                for value in (cls._text(item, 80) for item in raw_mentions)
                if value
            ][:8]
        return {
            "id": cls._text(event.get("event_id"), 160),
            "actor_id": cls._text(event.get("actor_id"), 80) or None,
            "actor_name": actor_name or None,
            "text": cls._text(payload.get("text"), 36),
            "reply_to": cls._text(payload.get("reply_to"), 120) or None,
            "reply_to_actor_id": cls._text(
                payload.get("reply_to_actor_id"), 80
            )
            or None,
            "mentions": mentions,
            "parts": parts,
        }

    @classmethod
    def _topic_fact(cls, topic: Mapping[str, object]) -> dict[str, object]:
        participant_ids = topic.get("participant_ids")
        participants = (
            [
                value
                for value in (
                    cls._text(item, 80) for item in participant_ids
                )
                if value
            ][:8]
            if isinstance(participant_ids, (list, tuple))
            else []
        )
        message_ids = topic.get("message_ids")
        return {
            "topic_id": cls._text(topic.get("topic_id"), 120),
            "participant_ids": participants,
            "message_count": (
                len(message_ids)
                if isinstance(message_ids, (list, tuple))
                else 0
            ),
            "last_event_at": cls._optional_int(topic.get("last_event_at")),
        }

    @classmethod
    def _audience_fact(cls, audience: Mapping[str, object]) -> dict[str, object]:
        return {
            "actor_id": cls._text(audience.get("actor_id"), 80),
            "message_count": cls._safe_int(audience.get("message_count")),
            "last_seen_at": cls._optional_int(audience.get("last_seen_at")),
        }

    @classmethod
    def _relationship_memory_fact(
        cls, memory: Mapping[str, object]
    ) -> dict[str, object]:
        return {
            "event_id": cls._text(memory.get("event_id"), 160),
            "subject_id": cls._text(memory.get("subject_id"), 80),
            "kind": cls._text(memory.get("kind"), 40),
            "summary": cls._text(memory.get("summary"), 120),
            "occurred_at": cls._optional_int(memory.get("occurred_at")),
        }

    @classmethod
    def _observations(
        cls,
        verdict: Mapping[str, object],
        frame: AttentionFrame,
        context: CognitiveContext,
    ) -> tuple[CognitiveObservation, ...]:
        if not isinstance(verdict, Mapping):
            raise _VerdictRejected("direct_response_shape_invalid")
        if not _REQUIRED_VERDICT_FIELDS <= set(verdict):
            raise _VerdictRejected("direct_missing_field")
        decision = cls._text(verdict["decision"], 16).lower()
        signal = cls._text(verdict["signal"], 40).lower()
        if decision not in {"speak", "silence"}:
            raise _VerdictRejected("direct_invalid_decision")
        if signal not in _SIGNALS:
            raise _VerdictRejected("direct_invalid_signal")
        if decision == "speak" and signal == "none":
            raise _VerdictRejected("direct_speak_without_signal")
        target_value = verdict["target_id"]
        target_id = cls._text(target_value, 80) or None
        if target_id is not None and target_id not in frame.candidate_audiences:
            raise _VerdictRejected("direct_unknown_target")
        evidence_value = verdict["evidence_event_ids"]
        if not isinstance(evidence_value, (list, tuple)):
            raise _VerdictRejected("direct_unknown_evidence")
        evidence = tuple(
            dict.fromkeys(
                value
                for value in (
                    cls._text(item, 160) for item in evidence_value
                )
                if value
            )
        )
        if not evidence:
            if decision == "speak":
                raise _VerdictRejected("direct_empty_speak_evidence")
            if not frame.focus_event_ids:
                raise _VerdictRejected("direct_unknown_evidence")
            evidence = (frame.focus_event_ids[-1],)
        if not set(evidence) <= set(frame.focus_event_ids):
            raise _VerdictRejected("direct_unknown_evidence")
        confidence = cls._unit_number(verdict["confidence"])
        disruption = cls._unit_number(verdict["disruption"])
        novelty = cls._unit_number(verdict["novelty"])
        reason = cls._text(verdict["reason"], 80)
        topic_id = next(iter(frame.focus_topic_ids), None)
        common = {
            "worker": cls.name,
            "confidence": confidence,
            "evidence_event_ids": evidence,
            "scene_version": frame.scene_version,
            "expires_at": context.now + 30,
            "uncertainty": (),
        }
        observations = []
        if signal != "none":
            observations.append(
                CognitiveObservation.create(
                    **common,
                    kind=signal,
                    proposition={
                        "value": signal,
                        "subject_id": target_id,
                        "topic_id": topic_id,
                        "reason": reason,
                    },
                )
            )
        observations.extend(
            cls._relationship_observations(
                verdict.get("relationship_events"), frame, context
            )
        )
        observations.append(
            CognitiveObservation.create(
                **common,
                kind="participation_assessment",
                proposition={
                    "should_participate": decision == "speak",
                    "decision": decision,
                    "target_confidence": confidence if target_id else 0.0,
                    "topic_confidence": confidence if topic_id else 0.0,
                    "disruption_cost": disruption,
                    "novelty": novelty,
                    "repetition_cost": 0.0,
                    "reason": reason,
                    "subject_id": target_id,
                    "topic_id": topic_id,
                },
            )
        )
        return tuple(observations)

    @classmethod
    def _relationship_observations(
        cls,
        value: object,
        frame: AttentionFrame,
        context: CognitiveContext,
    ) -> tuple[CognitiveObservation, ...]:
        if not isinstance(value, (list, tuple)):
            return ()
        observations = []
        forbidden_fields = {"amount", "delta", "score", "public_delta"}
        supplied_memories = cls._mapping_items(
            context.world_summary.get("relationship_memories")
        )
        repair_refs = {
            (
                cls._text(item.get("event_id"), 160),
                cls._text(item.get("subject_id"), 80),
            )
            for item in supplied_memories
            if cls._text(item.get("event_id"), 160)
            and cls._text(item.get("subject_id"), 80)
        }
        for item in value[:4]:
            if not isinstance(item, Mapping) or forbidden_fields & set(item):
                continue
            try:
                kind = cls._text(item.get("kind"), 40).lower()
                subject_id = cls._text(item.get("subject_id"), 80)
                severity = cls._text(item.get("severity"), 24).lower()
                confidence = cls._unit_number(item.get("confidence"))
                summary = cls._text(item.get("summary"), 160)
                sensitivity = cls._text(
                    item.get("sensitivity") or "normal", 24
                ).lower()
                repair_of = cls._text(item.get("repair_of"), 160) or None
                evidence_value = item.get("evidence_event_ids")
                if not isinstance(evidence_value, (list, tuple)):
                    continue
                evidence = tuple(
                    dict.fromkeys(
                        text
                        for text in (
                            cls._text(event_id, 160)
                            for event_id in evidence_value
                        )
                        if text
                    )
                )
            except _VerdictRejected:
                continue
            if (
                kind not in _MODEL_RELATIONSHIP_EVENT_KINDS
                or subject_id not in frame.candidate_audiences
                or severity not in RELATIONSHIP_SEVERITIES
                or not summary
                or sensitivity not in {"normal", "sensitive", "restricted"}
                or not evidence
                or len(evidence) > 8
                or not set(evidence) <= set(frame.focus_event_ids)
                or (kind == "repair_confirmed" and repair_of is None)
                or (
                    kind == "repair_confirmed"
                    and (repair_of, subject_id) not in repair_refs
                )
            ):
                continue
            observations.append(
                CognitiveObservation.create(
                    worker=cls.name,
                    kind="relationship_event",
                    proposition={
                        "kind": kind,
                        "subject_id": subject_id,
                        "severity": severity,
                        "summary": summary,
                        "repair_of": repair_of,
                        "sensitivity": sensitivity,
                    },
                    confidence=confidence,
                    evidence_event_ids=evidence,
                    scene_version=frame.scene_version,
                    expires_at=context.now + 30,
                    uncertainty=(),
                )
            )
        return tuple(observations)

    @staticmethod
    def _mapping_items(value: object) -> tuple[Mapping[str, object], ...]:
        if not isinstance(value, (list, tuple)):
            return ()
        return tuple(item for item in value if isinstance(item, Mapping))

    @staticmethod
    def _text(value: object, limit: int) -> str:
        return str(value or "").strip()[: int(limit)]

    @staticmethod
    def _safe_int(value: object) -> int:
        if isinstance(value, bool):
            return 0
        try:
            return max(0, int(value or 0))
        except (TypeError, ValueError):
            return 0

    @staticmethod
    def _optional_int(value: object) -> int | None:
        if value is None or isinstance(value, bool):
            return None
        try:
            return max(0, int(value))
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _unit_number(value: object) -> float:
        if isinstance(value, bool):
            raise _VerdictRejected("direct_invalid_score")
        try:
            number = float(value)
        except (TypeError, ValueError):
            raise _VerdictRejected("direct_invalid_score") from None
        if not math.isfinite(number) or not 0.0 <= number <= 1.0:
            raise _VerdictRejected("direct_invalid_score")
        return number


__all__ = ("DirectAmbientWorker",)
