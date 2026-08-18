"""Deterministic three-lane attention scheduling for group cognition."""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict, deque
from dataclasses import dataclass, replace

from .contracts import PersonaSnapshot, SocialEventEnvelope
from .world import GroupWorldState


@dataclass(frozen=True)
class AttentionFrame:
    frame_id: str
    group_id: str
    scene_version: int
    trigger_kind: str
    focus_topic_ids: tuple[str, ...]
    focus_event_ids: tuple[str, ...]
    candidate_audiences: tuple[str, ...]
    urgency: str
    deadline: int
    requested_workers: tuple[str, ...]
    persona_state_version: int
    config_version: int


@dataclass(frozen=True)
class _AmbientWindow:
    group_id: str
    scene_version: int
    focus_topic_ids: tuple[str, ...]
    focus_event_ids: tuple[str, ...]
    candidate_audiences: tuple[str, ...]
    deadline: int
    persona_state_version: int
    config_version: int


class AttentionScheduler:
    """Creates attention opportunities without authorizing any action."""

    def __init__(self) -> None:
        self._ambient: dict[str, _AmbientWindow] = {}
        self._recent_message_times: dict[str, deque[int]] = defaultdict(deque)

    def on_event(
        self,
        event: SocialEventEnvelope,
        world: GroupWorldState,
        persona: PersonaSnapshot,
        now: int,
    ) -> tuple[AttentionFrame, ...]:
        if not event.group_id or event.group_id != world.group_id:
            raise ValueError("attention event and world must share a group")
        if world.scene_version < 1:
            raise ValueError("attention requires a projected scene")
        now = int(now)
        self._refresh_pending_scene(event.group_id, world, persona)

        if event.event_type == "temporal.commitment_due":
            due_at = int(event.payload.get("due_at") or now)
            if due_at > now:
                return ()
            return (
                self._frame(
                    event=event,
                    world=world,
                    persona=persona,
                    trigger_kind="TEMPORAL",
                    urgency="normal",
                    deadline=now,
                    requested_workers=("commitment_revalidator",),
                ),
            )

        if self._is_fast(event):
            urgency = (
                "critical" if event.event_type == "safety.boundary" else "high"
            )
            return (
                self._frame(
                    event=event,
                    world=world,
                    persona=persona,
                    trigger_kind="FAST",
                    urgency=urgency,
                    deadline=now,
                    requested_workers=self._fast_workers(event),
                ),
            )

        if event.event_type != "platform.message":
            return ()
        self._record_message_time(event.group_id, now)
        delay = self._ambient_delay(event.group_id)
        topic_id = self._topic_id(world, event)
        current = self._ambient.get(event.group_id)
        if current is None:
            window = _AmbientWindow(
                group_id=event.group_id,
                scene_version=world.scene_version,
                focus_topic_ids=(topic_id,) if topic_id else (),
                focus_event_ids=(event.event_id,),
                candidate_audiences=(event.actor_id,) if event.actor_id else (),
                deadline=now + delay,
                persona_state_version=persona.state_version,
                config_version=persona.config_version,
            )
        else:
            window = replace(
                current,
                scene_version=world.scene_version,
                focus_topic_ids=self._append_unique(
                    current.focus_topic_ids, topic_id
                ),
                focus_event_ids=self._append_unique(
                    current.focus_event_ids, event.event_id
                ),
                candidate_audiences=self._append_unique(
                    current.candidate_audiences, event.actor_id
                ),
                deadline=now + delay,
                persona_state_version=persona.state_version,
                config_version=persona.config_version,
            )
        self._ambient[event.group_id] = window
        return ()

    def flush_due(self, now: int) -> tuple[AttentionFrame, ...]:
        now = int(now)
        due = sorted(
            (
                window
                for window in self._ambient.values()
                if window.deadline <= now
            ),
            key=lambda item: (item.deadline, item.group_id),
        )
        frames = tuple(self._ambient_frame(window) for window in due)
        for window in due:
            self._ambient.pop(window.group_id, None)
        return frames

    def _refresh_pending_scene(
        self,
        group_id: str,
        world: GroupWorldState,
        persona: PersonaSnapshot,
    ) -> None:
        current = self._ambient.get(group_id)
        if current is not None:
            self._ambient[group_id] = replace(
                current,
                scene_version=world.scene_version,
                persona_state_version=persona.state_version,
                config_version=persona.config_version,
            )

    def _frame(
        self,
        *,
        event: SocialEventEnvelope,
        world: GroupWorldState,
        persona: PersonaSnapshot,
        trigger_kind: str,
        urgency: str,
        deadline: int,
        requested_workers: tuple[str, ...],
    ) -> AttentionFrame:
        topic_id = self._topic_id(world, event)
        return self._build_frame(
            group_id=world.group_id,
            scene_version=world.scene_version,
            trigger_kind=trigger_kind,
            focus_topic_ids=(topic_id,) if topic_id else (),
            focus_event_ids=(event.event_id,),
            candidate_audiences=(event.actor_id,) if event.actor_id else (),
            urgency=urgency,
            deadline=deadline,
            requested_workers=requested_workers,
            persona_state_version=persona.state_version,
            config_version=persona.config_version,
        )

    def _ambient_frame(self, window: _AmbientWindow) -> AttentionFrame:
        return self._build_frame(
            group_id=window.group_id,
            scene_version=window.scene_version,
            trigger_kind="AMBIENT",
            focus_topic_ids=window.focus_topic_ids,
            focus_event_ids=window.focus_event_ids,
            candidate_audiences=window.candidate_audiences,
            urgency="normal",
            deadline=window.deadline,
            requested_workers=("scene_interpreter", "participation_assessor"),
            persona_state_version=window.persona_state_version,
            config_version=window.config_version,
        )

    @staticmethod
    def _build_frame(**values) -> AttentionFrame:
        identity = {
            key: values[key]
            for key in (
                "group_id",
                "scene_version",
                "trigger_kind",
                "focus_event_ids",
                "deadline",
                "persona_state_version",
                "config_version",
            )
        }
        digest = hashlib.sha256(
            json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()[:24]
        return AttentionFrame(frame_id=f"attention:{digest}", **values)

    def _record_message_time(self, group_id: str, now: int) -> None:
        times = self._recent_message_times[group_id]
        times.append(now)
        cutoff = now - 60
        while times and times[0] < cutoff:
            times.popleft()

    def _ambient_delay(self, group_id: str) -> int:
        count = len(self._recent_message_times[group_id])
        if count <= 3:
            return 2
        if count <= 12:
            return 3
        return 5

    @staticmethod
    def _is_fast(event: SocialEventEnvelope) -> bool:
        return bool(
            event.event_type
            in {"platform.poke", "safety.boundary", "capability.result"}
            or event.payload.get("mentions_bot")
            or event.payload.get("reply_to_bot")
            or event.payload.get("direct_address")
        )

    @staticmethod
    def _fast_workers(event: SocialEventEnvelope) -> tuple[str, ...]:
        if event.event_type == "safety.boundary":
            return ("safety_guard",)
        if event.event_type == "capability.result":
            return ("capability_interpreter",)
        return ("direct_interaction",)

    @staticmethod
    def _topic_id(world: GroupWorldState, event: SocialEventEnvelope) -> str:
        message_id = event.source_message_id or event.event_id
        try:
            return world.topic_for_message(message_id).topic_id
        except KeyError:
            return ""

    @staticmethod
    def _append_unique(values: tuple[str, ...], value: str | None) -> tuple[str, ...]:
        if not value or value in values:
            return values
        return values + (value,)


__all__ = ("AttentionFrame", "AttentionScheduler")
