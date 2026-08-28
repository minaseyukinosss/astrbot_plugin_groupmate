"""Relevance-budgeted facts for social scene interpretation."""

from __future__ import annotations

from dataclasses import dataclass, replace
from types import MappingProxyType
from typing import TYPE_CHECKING, Iterable, Mapping

from .contracts import SocialEventEnvelope

if TYPE_CHECKING:
    from .chorus import ChorusEvidence
    from .knowledge.contracts import TopicUnderstandingFrame


MAX_CONTEXT_EVENTS = 16
MAX_MEMBER_REFS = 24
MAX_ALIASES_PER_MEMBER = 4


def _text(value: object) -> str:
    return " ".join(str(value or "").split())


def _message_text(value: object) -> str:
    return str(value or "").strip()


def _unique(values: Iterable[object], *, limit: int) -> tuple[str, ...]:
    return tuple(
        dict.fromkeys(_text(value) for value in values if _text(value))
    )[:limit]


@dataclass(frozen=True)
class SceneEventFact:
    event_id: str
    actor_id: str | None
    text: str
    reply_to: str | None
    parts: tuple[str, ...]
    occurred_at: int
    origin_kind: str

    @classmethod
    def from_event(cls, event: SocialEventEnvelope) -> "SceneEventFact":
        payload = event.payload
        reply_to = _text(
            payload.get("reply_to_event_id")
            or payload.get("reply_event_id")
            or payload.get("reply_to")
            or event.causation_id
        ) or None
        return cls(
            event_id=event.event_id,
            actor_id=_text(event.actor_id) or None,
            text=_message_text(
                payload.get("text") or payload.get("literal_subject")
            ),
            reply_to=reply_to,
            parts=cls._parts(payload),
            occurred_at=int(event.occurred_at),
            origin_kind=_text(payload.get("origin_kind")) or "USER_TEXT",
        )

    @staticmethod
    def _parts(payload: Mapping[str, object]) -> tuple[str, ...]:
        parts: list[str] = []
        segments = payload.get("segments")
        if isinstance(segments, (list, tuple)):
            for segment in segments:
                if isinstance(segment, Mapping):
                    kind = _text(segment.get("type") or segment.get("kind"))
                    if kind:
                        parts.append(kind.upper())
        if payload.get("mentions"):
            parts.append("MENTION")
        if payload.get("media") or payload.get("media_refs"):
            parts.append("MEDIA")
        return _unique(parts, limit=8) or ("TEXT",)


@dataclass(frozen=True)
class SceneContext:
    source_event_id: str
    current_text: str
    target_id: str | None
    topic_id: str | None
    events: tuple[SceneEventFact, ...]
    persona_actor_id: str
    persona_aliases: tuple[str, ...]
    member_refs: Mapping[str, tuple[str, ...]]
    profile_fact_ids: tuple[str, ...]
    memory_ids: tuple[str, ...]
    facts: Mapping[str, object]
    chorus: "ChorusEvidence | None" = None

    def with_chorus(self, evidence: "ChorusEvidence | None") -> "SceneContext":
        if evidence is None:
            return self
        known_event_ids = {item.event_id for item in self.events}
        if not set(evidence.event_ids).issubset(known_event_ids):
            raise ValueError("chorus event ids must exist in scene context")
        if self.source_event_id not in evidence.event_ids:
            raise ValueError("chorus evidence must include source event")
        return replace(self, chorus=evidence)

    def to_model_facts(self) -> dict[str, object]:
        chorus = self.chorus
        return {
            "source_event_id": self.source_event_id,
            "current_text": self.current_text,
            "target_id": self.target_id,
            "topic_id": self.topic_id,
            "events": [
                {
                    "event_id": item.event_id,
                    "actor_id": item.actor_id,
                    "text": item.text,
                    "reply_to": item.reply_to,
                    "parts": list(item.parts),
                    "occurred_at": item.occurred_at,
                    "origin_kind": item.origin_kind,
                }
                for item in self.events
            ],
            "persona": {
                "actor_id": self.persona_actor_id,
                "aliases": list(self.persona_aliases),
            },
            "member_refs": {
                member_id: list(aliases)
                for member_id, aliases in self.member_refs.items()
            },
            "profile_fact_ids": list(self.profile_fact_ids),
            "memory_ids": list(self.memory_ids),
            "facts": dict(self.facts),
            "chorus_evidence": (
                None
                if chorus is None
                else {
                    "chain_id": chorus.chain_id,
                    "payload": chorus.payload,
                    "normalized_key": chorus.normalized_key,
                    "event_ids": list(chorus.event_ids),
                    "participant_ids": list(chorus.participant_ids),
                    "already_joined": chorus.already_joined,
                }
            ),
        }


class SceneContextBuilder:
    """Keep causal evidence before nearby but unrelated chat history."""

    def __init__(self, *, max_chars: int = 2400) -> None:
        if int(max_chars) < 128:
            raise ValueError("max_chars must be at least 128")
        self.max_chars = int(max_chars)

    def build(
        self,
        *,
        source_event: SocialEventEnvelope,
        context_events: Iterable[SocialEventEnvelope],
        focus_event_ids: Iterable[str],
        target_id: str | None,
        topic_id: str | None,
        persona_actor_id: str,
        persona_aliases: Iterable[str],
        member_refs: Mapping[str, Iterable[str]],
        profile: object | None,
        relationship_memories: Iterable[object],
        topic_understanding: "TopicUnderstandingFrame | None" = None,
    ) -> SceneContext:
        events = self._deduplicate((*tuple(context_events), source_event))
        source_fact = SceneEventFact.from_event(source_event)
        focus = set(_unique(focus_event_ids, limit=MAX_CONTEXT_EVENTS))
        ranked = sorted(
            (SceneEventFact.from_event(event) for event in events),
            key=lambda item: self._priority(
                item,
                source=source_fact,
                focus_event_ids=focus,
                target_id=_text(target_id) or None,
                topic_id=_text(topic_id) or None,
            ),
        )
        packed = self._pack(ranked, source_event_id=source_event.event_id)
        normalized_refs = self._member_refs(member_refs)
        profile_fact_ids, profile_facts = self._profile_facts(profile)
        memory_ids, memory_facts = self._memory_facts(relationship_memories)
        facts = MappingProxyType(
            {
                "profile": profile_facts,
                "relationship_memories": memory_facts,
                "topic_understanding": (
                    None
                    if topic_understanding is None
                    else topic_understanding.to_prompt_facts()
                ),
            }
        )
        return SceneContext(
            source_event_id=source_event.event_id,
            current_text=_message_text(
                source_event.payload.get("text")
                or source_event.payload.get("literal_subject")
            ),
            target_id=_text(target_id) or None,
            topic_id=_text(topic_id) or None,
            events=packed,
            persona_actor_id=_text(persona_actor_id),
            persona_aliases=_unique(persona_aliases, limit=8),
            member_refs=MappingProxyType(normalized_refs),
            profile_fact_ids=profile_fact_ids,
            memory_ids=memory_ids,
            facts=facts,
        )

    @staticmethod
    def pack_event_mappings(
        events: Iterable[Mapping[str, object]],
        focus_event_ids: Iterable[str],
        *,
        max_chars: int = 2400,
    ) -> tuple[dict[str, object], ...]:
        """Allocate one shared text budget while preserving causal chat order."""

        budget = max(128, int(max_chars))
        focus = tuple(dict.fromkeys(_text(value) for value in focus_event_ids if _text(value)))
        by_id = {
            _text(event.get("event_id")): event
            for event in events
            if _text(event.get("event_id")) in set(focus)
        }
        if not focus or not by_id:
            return ()
        current_id = next((event_id for event_id in reversed(focus) if event_id in by_id), "")
        current_payload = by_id[current_id].get("payload") if current_id else None
        current_payload = current_payload if isinstance(current_payload, Mapping) else {}
        parent_id = _text(
            current_payload.get("reply_to_event_id")
            or current_payload.get("reply_event_id")
            or current_payload.get("reply_to")
        )
        priority = tuple(
            dict.fromkeys(
                (
                    current_id,
                    parent_id if parent_id in by_id else "",
                    *reversed(focus),
                )
            )
        )
        chosen_ids = tuple(
            event_id
            for event_id in priority[:MAX_CONTEXT_EVENTS]
            if event_id in by_id
        )
        base = min(16, budget // max(1, len(chosen_ids)))
        allocations = {event_id: base for event_id in chosen_ids}
        remaining = max(0, budget - base * len(chosen_ids))
        for event_id in chosen_ids:
            desired = (
                min(200, budget // 2)
                if event_id == current_id
                else min(120, budget // 3)
                if event_id == parent_id
                else 32
            )
            extra = min(max(0, desired - allocations[event_id]), remaining)
            allocations[event_id] += extra
            remaining -= extra
        selected: dict[str, dict[str, object]] = {}
        for event_id in chosen_ids:
            event = by_id.get(event_id)
            if event is None:
                continue
            payload = event.get("payload")
            payload = payload if isinstance(payload, Mapping) else {}
            raw_text = _message_text(payload.get("text"))
            text = raw_text[: allocations[event_id]]
            copied = dict(event)
            copied["payload"] = {**dict(payload), "text": text}
            selected[event_id] = copied
        # 先按相关性分配预算，交给模型时恢复原对话顺序，避免把因果顺序倒置。
        return tuple(selected[event_id] for event_id in focus if event_id in selected)

    @staticmethod
    def _deduplicate(events: Iterable[SocialEventEnvelope]) -> tuple[SocialEventEnvelope, ...]:
        result: dict[str, SocialEventEnvelope] = {}
        for event in events:
            result[event.event_id] = event
        return tuple(result.values())

    @staticmethod
    def _priority(
        event: SceneEventFact,
        *,
        source: SceneEventFact,
        focus_event_ids: set[str],
        target_id: str | None,
        topic_id: str | None,
    ) -> tuple[int, int, str]:
        # 回复父链先于当前消息，让“这个方案不行”仍能看到它具体否定了什么。
        if source.reply_to and event.event_id == source.reply_to:
            rank = 0
        elif event.event_id == source.event_id:
            rank = 1
        elif event.event_id in focus_event_ids:
            rank = 2
        elif target_id and event.actor_id == target_id:
            rank = 3
        elif topic_id and event.event_id == topic_id:
            rank = 4
        else:
            rank = 5
        return rank, -event.occurred_at, event.event_id

    def _pack(
        self,
        ranked: Iterable[SceneEventFact],
        *,
        source_event_id: str,
    ) -> tuple[SceneEventFact, ...]:
        selected: list[SceneEventFact] = []
        remaining = self.max_chars
        for event in tuple(ranked)[:MAX_CONTEXT_EVENTS]:
            if not event.text and event.event_id != source_event_id:
                continue
            overhead = 32 + len(event.event_id) + len(event.actor_id or "")
            available = remaining - overhead
            if available <= 0:
                break
            # 当前消息和因果前情拿到完整预算；普通背景不会为了凑数量只留碎片。
            if len(event.text) > available:
                if available < 48 and event.event_id != source_event_id:
                    continue
                text = event.text[:available]
            else:
                text = event.text
            selected.append(
                SceneEventFact(
                    event_id=event.event_id,
                    actor_id=event.actor_id,
                    text=text,
                    reply_to=event.reply_to,
                    parts=event.parts,
                    occurred_at=event.occurred_at,
                    origin_kind=event.origin_kind,
                )
            )
            remaining -= overhead + len(text)
        return tuple(selected)

    @staticmethod
    def _member_refs(
        values: Mapping[str, Iterable[str]],
    ) -> dict[str, tuple[str, ...]]:
        result: dict[str, tuple[str, ...]] = {}
        for member_id, aliases in values.items():
            normalized_id = _text(member_id)
            normalized_aliases = _unique(aliases, limit=MAX_ALIASES_PER_MEMBER)
            if normalized_id and normalized_aliases:
                result[normalized_id] = normalized_aliases
            if len(result) >= MAX_MEMBER_REFS:
                break
        return result

    @staticmethod
    def _profile_facts(profile: object | None) -> tuple[tuple[str, ...], list[dict[str, str]]]:
        facts = tuple(getattr(profile, "facts", ()) or ())
        selected = []
        ids = []
        for fact in facts[:8]:
            fact_id = _text(getattr(fact, "fact_id", ""))
            summary = _text(getattr(fact, "summary", ""))
            if fact_id and summary:
                ids.append(fact_id)
                selected.append(
                    {
                        "fact_id": fact_id,
                        "category": _text(getattr(fact, "category", "")),
                        "summary": summary,
                    }
                )
        return tuple(ids), selected

    @staticmethod
    def _memory_facts(memories: Iterable[object]) -> tuple[tuple[str, ...], list[dict[str, str]]]:
        ids: list[str] = []
        selected: list[dict[str, str]] = []
        for memory in tuple(memories)[:8]:
            memory_id = _text(getattr(memory, "memory_id", ""))
            summary = _text(getattr(memory, "summary", ""))
            if memory_id and summary:
                ids.append(memory_id)
                selected.append(
                    {
                        "memory_id": memory_id,
                        "kind": _text(getattr(memory, "kind", "")),
                        "summary": summary,
                    }
                )
        return tuple(ids), selected


__all__ = ("SceneContext", "SceneContextBuilder", "SceneEventFact")
