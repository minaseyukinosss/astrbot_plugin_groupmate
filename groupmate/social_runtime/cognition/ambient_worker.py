"""Compact direct-model worker for AMBIENT group-chat cognition."""

from __future__ import annotations

import math
from typing import Mapping

from ...adapters.deepseek_cognition import DirectCognitionError
from ..attention import AttentionFrame
from ..social_context import SceneContextBuilder
from ..society.relationship_events import (
    RELATIONSHIP_EVENT_KINDS,
    RELATIONSHIP_SEVERITIES,
)
from .contracts import CognitiveContext, CognitiveObservation, CognitiveWorkerResult


_OPPORTUNITY_KINDS = {
    "bot_context",
    "open_question",
    "help_request",
    "social_bid",
    "emotional_bid",
    "play_bid",
    "topic_opening",
    "boundary",
    "none",
}
_OPPORTUNITY_OBSERVATIONS = {
    "bot_context": "bot_context",
    "open_question": "open_question",
    "help_request": "help_request",
    "social_bid": "social_bid",
    "emotional_bid": "care_signal",
    "play_bid": "humor_signal",
    "topic_opening": "topic_opening",
    "boundary": "boundary_signal",
}
_REQUIRED_VERDICT_FIELDS = {
    "decision",
    "opportunity_kind",
    "anchor_event_id",
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
        if self._hard_block_reason(frame, context) is not None:
            return 0
        return max(0, int(self._client.input_bytes(self._facts(frame, context))))

    async def observe_with_result(
        self, frame: AttentionFrame, context: CognitiveContext
    ) -> CognitiveWorkerResult:
        hard_block = self._hard_block_reason(frame, context)
        if hard_block is not None:
            return CognitiveWorkerResult(
                (self._local_silence(frame, context, hard_block),),
                backend=self.backend,
                model=self.model,
            )
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
        packed_events = tuple(
            event for event in context.context_events
            if event.get("event_id") in frame.focus_event_ids
        ) or SceneContextBuilder.pack_event_mappings(
            context.focus_events,
            frame.focus_event_ids,
            max_chars=400,
        )
        world = context.world_summary
        bot_actor_id = cls._text(world.get("bot_actor_id"), 80)
        events = [
            cls._event_fact(
                item,
                bot_actor_id=bot_actor_id,
                topic_id=cls._topic_id_for_event(item, world),
            )
            for item in packed_events
        ]
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
        member_context = cls._member_context_fact(
            world.get("member_context"),
            allowed_subjects=set(frame.candidate_audiences),
        )
        profile = world.get("persona_profile")
        persona = {}
        bot_names: list[str] = []
        if isinstance(profile, Mapping):
            identity = profile.get("identity")
            if isinstance(identity, Mapping):
                primary_name = cls._text(identity.get("name"), 48)
                aliases = identity.get("aliases")
                bot_names = [primary_name] if primary_name else []
                if isinstance(aliases, (list, tuple)):
                    bot_names.extend(
                        value
                        for value in (
                            cls._text(item, 48) for item in aliases
                        )
                        if value
                    )
                bot_names = list(dict.fromkeys(bot_names))[:12]
            for section in ("presence", "participation"):
                values = profile.get(section)
                if isinstance(values, Mapping):
                    persona[section] = {
                        cls._text(key, 60): cls._text(value, 240)
                        for key, value in values.items()
                        if cls._text(key, 60) and cls._text(value, 240)
                    }
        candidates = cls._dialogue_candidates(frame, context)
        facts = {
            "events": events,
            "dialogue_candidates": candidates,
            "context_events": [
                cls._event_fact(item, bot_actor_id=bot_actor_id, topic_id=None)
                for item in context.context_events
                if item.get("event_id") not in frame.focus_event_ids
            ],
            "current_event_id": (
                frame.focus_event_ids[-1] if frame.focus_event_ids else None
            ),
            "bot_names": bot_names,
            "direction": cls._direction_fact(
                cls._event_by_id(context.focus_events, frame.focus_event_ids[-1])
                if frame.focus_event_ids
                else None,
                bot_actor_id=bot_actor_id,
            ),
            "topics": topics,
            "audiences": audiences,
            "activity": activity_fact,
            "persona": persona,
            "relationship_memories": relationship_memories,
            "member_context": member_context,
        }
        if not candidates:
            facts["lease"] = lease_fact
        return facts

    @classmethod
    def _event_fact(
        cls,
        event: Mapping[str, object],
        *,
        bot_actor_id: str,
        topic_id: str | None,
    ) -> dict[str, object]:
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
            "topic_id": topic_id,
            "text": cls._text(payload.get("text"), 800),
            "reply_to": cls._text(payload.get("reply_to_event_id") or payload.get("reply_to"), 160) or None,
            "occurred_at": cls._optional_int(event.get("occurred_at")),
            "is_self": bool(payload.get("is_self")),
            "target_id": cls._text(payload.get("target_id"), 80) or None,
            "origin_kind": cls._text(payload.get("origin_kind"), 40) or "USER_TEXT",
            "reply_to_actor_id": cls._text(
                payload.get("reply_to_actor_id"), 80
            )
            or None,
            "mentions": mentions,
            "parts": parts,
            "address_scope": cls._address_scope(
                payload, bot_actor_id=bot_actor_id
            ),
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
    def _member_context_fact(
        cls,
        value: object,
        *,
        allowed_subjects: set[str],
    ) -> dict[str, object]:
        if not isinstance(value, Mapping):
            return {"members": [], "relations": []}
        members = []
        for item in cls._mapping_items(value.get("members")):
            subject_id = cls._text(item.get("subject_id"), 80)
            if subject_id not in allowed_subjects:
                continue
            aliases = item.get("aliases")
            habits = item.get("addressing_habits")
            members.append(
                {
                    "subject_id": subject_id,
                    "aliases": [
                        text
                        for text in (
                            cls._text(raw, 48)
                            for raw in (
                                aliases
                                if isinstance(aliases, (list, tuple))
                                else ()
                            )
                        )
                        if text
                    ][:3],
                    "addressing_habits": [
                        text
                        for text in (
                            cls._text(raw, 100)
                            for raw in (
                                habits
                                if isinstance(habits, (list, tuple))
                                else ()
                            )
                        )
                        if text
                    ][:2],
                }
            )
            if len(members) >= 3:
                break
        relations = []
        for item in cls._mapping_items(value.get("relations")):
            source = cls._text(item.get("source_member_id"), 80)
            target = cls._text(item.get("target_member_id"), 80)
            relation = cls._text(item.get("relation_type"), 40)
            if (
                source in allowed_subjects
                and target in allowed_subjects
                and relation
            ):
                relations.append(
                    {
                        "source_member_id": source,
                        "target_member_id": target,
                        "relation_type": relation,
                    }
                )
            if len(relations) >= 3:
                break
        return {"members": members, "relations": relations}

    @classmethod
    def _dialogue_candidates(
        cls, frame: AttentionFrame, context: CognitiveContext,
    ) -> list[dict[str, object]]:
        """ID associations; same recipient is a candidate, not reply authority."""
        candidates = []
        bot_id = cls._text(context.world_summary.get("bot_actor_id"), 80)
        selected = {event.get("event_id"): event for event in context.context_events}
        for history in sorted(context.context_events,
                              key=lambda event: cls._safe_int(event.get("occurred_at")), reverse=True):
            payload = history.get("payload")
            if (history.get("event_type") != "delivery.sent"
                    or history.get("event_id") in frame.focus_event_ids
                    or not isinstance(payload, Mapping)
                    or any(payload.get(key) is not True for key in
                           ("is_self", "delivery_confirmed", "current_dialogue_session"))):
                continue
            for anchor_id in reversed(frame.focus_event_ids):
                current = selected.get(anchor_id)
                if current is None:
                    continue
                current_payload = current.get("payload")
                if (current.get("event_type") != "platform.message"
                        or not isinstance(current_payload, Mapping)
                        or current_payload.get("social_eligible") is False
                        or current_payload.get("is_self") is True
                        or cls._address_scope(current_payload, bot_actor_id=bot_id) == "OTHER_MEMBER"
                        or not current.get("persona_id")
                        or current.get("persona_id") != history.get("persona_id")
                        or current.get("group_id") != context.group_id
                        or history.get("group_id") != context.group_id
                        or not (0 <= cls._safe_int(history.get("occurred_at"))
                                < cls._safe_int(current.get("occurred_at")))):
                    continue
                target = payload.get("target_id")
                if not target:
                    parent = selected.get(payload.get("reply_to_event_id"))
                    if (parent and parent.get("event_type") == "platform.message"
                            and parent.get("persona_id") == history.get("persona_id")
                            and parent.get("group_id") == context.group_id
                            and cls._safe_int(parent.get("occurred_at")) < cls._safe_int(history.get("occurred_at"))
                            and isinstance(parent.get("payload"), Mapping)
                            and parent["payload"].get("social_eligible") is not False
                            and not parent["payload"].get("is_self")):
                        target = parent.get("actor_id")
                if target and target == current.get("actor_id"):
                    candidates.append({
                        "anchor_event_id": anchor_id,
                        "bot_event_id": history["event_id"],
                        "target_id": target,
                        "bot_is_confirmed_self": True,
                        "same_current_session": True,
                    })
                    if len(candidates) >= 16:
                        return candidates
        return candidates

    @classmethod
    def _dialogue_relation(cls, verdict, frame, context) -> dict[str, object]:
        if "dialogue_relation" not in verdict:
            return {}
        relation = verdict["dialogue_relation"]
        error = "direct_invalid_dialogue_relation"
        if not isinstance(relation, Mapping):
            raise _VerdictRejected(error)
        kind = relation.get("kind")
        anchor, bot = relation.get("anchor_event_id"), relation.get("bot_event_id")
        if not isinstance(kind, str) or kind not in {"answers_bot", "asks_bot", "closes_dialogue", "other_exchange", "none"}:
            raise _VerdictRejected(error)
        try:
            confidence = cls._unit_number(relation.get("confidence"))
        except _VerdictRejected:
            raise _VerdictRejected(error) from None
        if kind == "none":
            valid = anchor is None and bot is None
        elif kind == "other_exchange":
            valid = isinstance(anchor, str) and anchor in frame.focus_event_ids and bot is None
        else:
            valid = any(anchor == pair["anchor_event_id"] and bot == pair["bot_event_id"]
                        for pair in cls._dialogue_candidates(frame, context))
        if not valid:
            raise _VerdictRejected(error)
        return {"dialogue_relation_kind": kind, "dialogue_anchor_event_id": anchor,
                "dialogue_bot_event_id": bot, "dialogue_confidence": confidence,
                "dialogue_relation_applied": kind in {"answers_bot", "asks_bot"} and confidence >= 0.75}

    @classmethod
    def _owned_observations(
        cls,
        verdict: Mapping[str, object],
        frame: AttentionFrame,
        context: CognitiveContext,
    ) -> tuple[CognitiveObservation, ...]:
        if "dialogue_relation" not in verdict:
            raise _VerdictRejected("direct_invalid_dialogue_relation")
        relation = cls._dialogue_relation(verdict, frame, context)
        disruption = (
            cls._unit_number(verdict["disruption"])
            if "disruption" in verdict else 0.2
        )
        novelty = (
            cls._unit_number(verdict["novelty"])
            if "novelty" in verdict else 0.3
        )
        original_decision = (
            cls._text(verdict.get("decision"), 16).lower() or None
            if "decision" in verdict else None
        )
        original_opportunity = (
            cls._text(verdict.get("opportunity_kind"), 40).lower() or None
            if "opportunity_kind" in verdict else None
        )
        original_reason = cls._text(verdict.get("reason"), 80)
        if original_decision and original_decision not in {"speak", "silence"}:
            raise _VerdictRejected("direct_invalid_decision")
        relation.update(
            original_decision=original_decision,
            original_opportunity_kind=original_opportunity,
            original_reason=original_reason,
        )
        context_evidence = verdict.get("context_evidence_event_ids", ())
        allowed_context = {
            item.get("event_id") for item in context.context_events
            if item.get("event_id") not in frame.focus_event_ids
        }
        if (not isinstance(context_evidence, (list, tuple))
                or any(not isinstance(item, str) or item not in allowed_context
                       for item in context_evidence)):
            raise _VerdictRejected("direct_unknown_evidence")
        context_evidence = tuple(dict.fromkeys(context_evidence))
        if relation["dialogue_relation_applied"]:
            anchor_id = relation["dialogue_anchor_event_id"]
            target_id, topic_id = cls._anchor_scope(anchor_id, frame, context)
            decision, opportunity_kind = "speak", "bot_context"
            evidence = (anchor_id,)
            context_evidence = tuple(dict.fromkeys(
                (*context_evidence, relation["dialogue_bot_event_id"])
            ))
            confidence = relation["dialogue_confidence"]
            if "confidence" in verdict:
                confidence = min(cls._unit_number(verdict["confidence"]), confidence)
            reason = "当前成员在回应已确认发送的自身对话"
        else:
            decision, opportunity_kind = "silence", "none"
            anchor_id = frame.focus_event_ids[-1] if frame.focus_event_ids else None
            target_id = topic_id = None
            if not anchor_id:
                raise _VerdictRejected("direct_unknown_evidence")
            evidence = (anchor_id,)
            confidence = relation["dialogue_confidence"]
            if "confidence" in verdict:
                confidence = max(confidence, cls._unit_number(verdict["confidence"]))
            reason = original_reason or "当前句与已确认的自身对话无应答关系"
        return cls._participation_observations(
            frame, context, verdict,
            decision=decision, opportunity_kind=opportunity_kind,
            anchor_id=anchor_id, target_id=target_id, topic_id=topic_id,
            evidence=evidence, context_evidence=context_evidence,
            confidence=confidence, disruption=disruption, novelty=novelty,
            reason=reason, relation=relation,
        )

    @classmethod
    def _participation_observations(
        cls, frame, context, verdict, *, decision, opportunity_kind, anchor_id,
        target_id, topic_id, evidence, context_evidence, confidence, disruption,
        novelty, reason, relation,
    ) -> tuple[CognitiveObservation, ...]:
        common = {
            "worker": cls.name,
            "confidence": confidence,
            "evidence_event_ids": evidence,
            "scene_version": frame.scene_version,
            "expires_at": context.now + 30,
            "uncertainty": (),
        }
        observations = []
        observation_kind = _OPPORTUNITY_OBSERVATIONS.get(opportunity_kind)
        if observation_kind is not None:
            observations.append(
                CognitiveObservation.create(
                    **common,
                    kind=observation_kind,
                    proposition={
                        "value": opportunity_kind,
                        "subject_id": target_id,
                        "topic_id": topic_id,
                        "anchor_event_id": anchor_id,
                        "context_evidence_event_ids": context_evidence,
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
                    "opportunity_kind": opportunity_kind,
                    "anchor_event_id": anchor_id,
                    "context_evidence_event_ids": context_evidence,
                    **relation,
                },
            )
        )
        return tuple(observations)

    @classmethod
    def _observations(
        cls,
        verdict: Mapping[str, object],
        frame: AttentionFrame,
        context: CognitiveContext,
    ) -> tuple[CognitiveObservation, ...]:
        if not isinstance(verdict, Mapping):
            raise _VerdictRejected("direct_response_shape_invalid")
        if cls._dialogue_candidates(frame, context):
            return cls._owned_observations(verdict, frame, context)
        if not _REQUIRED_VERDICT_FIELDS <= set(verdict):
            raise _VerdictRejected("direct_missing_field")
        decision = cls._text(verdict["decision"], 16).lower()
        opportunity_kind = cls._text(
            verdict["opportunity_kind"], 40
        ).lower()
        if decision not in {"speak", "silence"}:
            raise _VerdictRejected("direct_invalid_decision")
        if opportunity_kind not in _OPPORTUNITY_KINDS:
            raise _VerdictRejected("direct_invalid_opportunity")
        if decision == "speak" and opportunity_kind == "none":
            raise _VerdictRejected("direct_speak_without_opportunity")
        anchor_id = cls._text(verdict["anchor_event_id"], 160) or None
        target_id = None
        topic_id = None
        if decision == "speak":
            if anchor_id is None:
                raise _VerdictRejected("direct_unknown_anchor")
            target_id, topic_id = cls._anchor_scope(
                anchor_id, frame, context
            )
        elif anchor_id is not None and anchor_id not in frame.focus_event_ids:
            raise _VerdictRejected("direct_unknown_anchor")
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
        if decision == "speak" and anchor_id not in evidence:
            raise _VerdictRejected("direct_anchor_missing_from_evidence")
        context_evidence = verdict.get("context_evidence_event_ids", ())
        allowed_context = {item.get("event_id") for item in context.context_events
                           if item.get("event_id") not in frame.focus_event_ids}
        if (not isinstance(context_evidence, (list, tuple))
                or any(not isinstance(item, str) or item not in allowed_context for item in context_evidence)):
            raise _VerdictRejected("direct_unknown_evidence")
        context_evidence = tuple(dict.fromkeys(context_evidence))
        confidence = cls._unit_number(verdict["confidence"])
        disruption = cls._unit_number(verdict["disruption"])
        novelty = cls._unit_number(verdict["novelty"])
        reason = cls._text(verdict["reason"], 80)
        relation = cls._dialogue_relation(verdict, frame, context)
        if relation:
            relation.update(original_decision=decision, original_opportunity_kind=opportunity_kind,
                            original_reason=reason)
        return cls._participation_observations(
            frame, context, verdict,
            decision=decision, opportunity_kind=opportunity_kind,
            anchor_id=anchor_id, target_id=target_id, topic_id=topic_id,
            evidence=evidence, context_evidence=context_evidence,
            confidence=confidence, disruption=disruption, novelty=novelty,
            reason=reason, relation=relation,
        )

    @classmethod
    def _anchor_scope(
        cls,
        anchor_id: str,
        frame: AttentionFrame,
        context: CognitiveContext,
    ) -> tuple[str, str]:
        if anchor_id not in frame.focus_event_ids:
            raise _VerdictRejected("direct_unknown_anchor")
        event = cls._event_by_id(context.focus_events, anchor_id)
        if event is None:
            raise _VerdictRejected("direct_unknown_anchor")
        payload = event.get("payload")
        payload = payload if isinstance(payload, Mapping) else {}
        if (
            cls._text(event.get("event_type"), 40)
            not in {"", "platform.message"}
            or payload.get("social_eligible") is False
            or payload.get("is_self") is True
        ):
            raise _VerdictRejected("direct_unknown_anchor")
        target_id = cls._text(event.get("actor_id"), 80)
        if not target_id or target_id not in frame.candidate_audiences:
            raise _VerdictRejected("direct_unknown_target")
        topic_id = cls._topic_id_for_event(event, context.world_summary)
        if not topic_id and len(frame.focus_topic_ids) == 1:
            topic_id = frame.focus_topic_ids[0]
        if not topic_id or topic_id not in frame.focus_topic_ids:
            raise _VerdictRejected("direct_unknown_topic")
        return target_id, topic_id

    @classmethod
    def _topic_id_for_event(
        cls,
        event: Mapping[str, object],
        world: Mapping[str, object],
    ) -> str | None:
        source_message_id = cls._text(event.get("source_message_id"), 160)
        event_id = cls._text(event.get("event_id"), 160)
        identifiers = {value for value in (source_message_id, event_id) if value}
        for topic in cls._mapping_items(world.get("topics")):
            message_ids = topic.get("message_ids")
            if not isinstance(message_ids, (list, tuple)):
                continue
            if identifiers & {
                cls._text(item, 160) for item in message_ids
            }:
                return cls._text(topic.get("topic_id"), 120) or None
        return None

    @classmethod
    def _hard_block_reason(
        cls, frame: AttentionFrame, context: CognitiveContext
    ) -> str | None:
        if not frame.focus_event_ids:
            return None
        event = cls._event_by_id(context.focus_events, frame.focus_event_ids[-1])
        if event is None:
            return None
        payload = event.get("payload")
        if not isinstance(payload, Mapping):
            return None
        bot_actor_id = cls._text(context.world_summary.get("bot_actor_id"), 80)
        reply_actor_id = cls._text(payload.get("reply_to_actor_id"), 80)
        if (
            payload.get("reply_to_bot") is not True
            and reply_actor_id
            and reply_actor_id != bot_actor_id
        ):
            return "addressed_elsewhere"
        mentions = cls._member_mentions(payload, bot_actor_id=bot_actor_id)
        if payload.get("mentions_bot") is not True and mentions:
            return "addressed_elsewhere"
        return None

    @classmethod
    def _local_silence(
        cls,
        frame: AttentionFrame,
        context: CognitiveContext,
        reason: str,
    ) -> CognitiveObservation:
        anchor_id = frame.focus_event_ids[-1]
        return CognitiveObservation.create(
            worker=cls.name,
            kind="participation_assessment",
            proposition={
                "should_participate": False,
                "decision": "silence",
                "target_confidence": 0.0,
                "topic_confidence": 0.0,
                "disruption_cost": 1.0,
                "novelty": 0.0,
                "repetition_cost": 0.0,
                "reason": "消息明确指向其他成员",
                "subject_id": None,
                "topic_id": None,
                "opportunity_kind": "none",
                "anchor_event_id": anchor_id,
                "hard_block_reason": reason,
            },
            confidence=1.0,
            evidence_event_ids=(anchor_id,),
            scene_version=frame.scene_version,
            expires_at=context.now + 30,
            uncertainty=(),
        )

    @classmethod
    def _direction_fact(
        cls,
        event: Mapping[str, object] | None,
        *,
        bot_actor_id: str,
    ) -> dict[str, object]:
        if event is None:
            return {"address_scope": "UNKNOWN"}
        payload = event.get("payload")
        payload = payload if isinstance(payload, Mapping) else {}
        return {
            "address_scope": cls._address_scope(
                payload, bot_actor_id=bot_actor_id
            ),
            "reply_to_actor_id": cls._text(
                payload.get("reply_to_actor_id"), 80
            )
            or None,
            "mentioned_actor_ids": list(
                cls._member_mentions(payload, bot_actor_id=bot_actor_id)
            ),
            "possible_bot_alias": (
                cls._text(payload.get("matched_alias"), 48)
                or cls._text(payload.get("alias_candidate"), 48)
                or None
            ),
            "address_confidence": cls._text(
                payload.get("address_confidence"), 16
            )
            or "NONE",
        }

    @classmethod
    def _address_scope(
        cls, payload: Mapping[str, object], *, bot_actor_id: str
    ) -> str:
        if (
            payload.get("mentions_bot") is True
            or payload.get("reply_to_bot") is True
            or payload.get("direct_address") is True
        ):
            return "BOT"
        reply_actor = cls._text(payload.get("reply_to_actor_id"), 80)
        if reply_actor and reply_actor == bot_actor_id:
            return "BOT"
        if reply_actor and reply_actor != bot_actor_id:
            return "OTHER_MEMBER"
        if cls._member_mentions(payload, bot_actor_id=bot_actor_id):
            return "OTHER_MEMBER"
        return "OPEN"

    @classmethod
    def _member_mentions(
        cls,
        payload: Mapping[str, object],
        *,
        bot_actor_id: str = "",
    ) -> tuple[str, ...]:
        raw = payload.get("mentions")
        if not isinstance(raw, (list, tuple)):
            return ()
        return tuple(
            dict.fromkeys(
                value
                for value in (cls._text(item, 80) for item in raw)
                if value
                and value != bot_actor_id
                and value.casefold() not in {"all", "@all", "0"}
            )
        )

    @staticmethod
    def _event_by_id(
        events: tuple[Mapping[str, object], ...], event_id: str
    ) -> Mapping[str, object] | None:
        return next(
            (
                event
                for event in events
                if str(event.get("event_id") or "").strip() == event_id
            ),
            None,
        )

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
