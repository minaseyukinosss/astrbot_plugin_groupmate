"""Immutable deterministic projection of one group's observable world."""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from typing import Mapping

from .contracts import SocialEventEnvelope


@dataclass(frozen=True)
class TopicState:
    topic_id: str
    root_event_id: str
    message_ids: tuple[str, ...]
    participant_ids: tuple[str, ...]
    last_event_at: int


@dataclass(frozen=True)
class ParticipantState:
    actor_id: str
    message_count: int
    last_seen_at: int


@dataclass(frozen=True)
class InteractionEdge:
    source_actor_id: str
    target_actor_id: str
    kind: str
    count: int
    last_event_at: int


@dataclass(frozen=True)
class GroupActivity:
    event_count: int
    message_count: int
    last_event_at: int | None


@dataclass(frozen=True)
class SocialAtmosphere:
    label: str
    confidence: float


@dataclass(frozen=True)
class BotTopicRole:
    topic_id: str
    role: str


@dataclass(frozen=True)
class OpportunityRef:
    opportunity_id: str


@dataclass(frozen=True)
class TaskRef:
    task_id: str


@dataclass(frozen=True)
class OpenLoopRef:
    loop_id: str


@dataclass(frozen=True)
class PresenceHistory:
    recent_event_ids: tuple[str, ...]
    last_bot_event_at: int | None


@dataclass(frozen=True)
class GroupWorldState:
    group_id: str
    scene_version: int
    active_topics: tuple[TopicState, ...]
    participants: tuple[ParticipantState, ...]
    interaction_edges: tuple[InteractionEdge, ...]
    group_activity: GroupActivity
    social_atmosphere: SocialAtmosphere
    bot_roles: tuple[BotTopicRole, ...]
    pending_opportunities: tuple[OpportunityRef, ...]
    running_tasks: tuple[TaskRef, ...]
    open_loops: tuple[OpenLoopRef, ...]
    recent_presence: PresenceHistory
    culture_version: int

    def topic_for_message(self, message_id: str) -> TopicState:
        for topic in self.active_topics:
            if message_id in topic.message_ids:
                return topic
        raise KeyError(message_id)


class GroupWorldProjector:
    """Projects platform facts before optional model observations are considered."""

    def empty(self, group_id: str) -> GroupWorldState:
        if not group_id.strip():
            raise ValueError("group_id must not be empty")
        return GroupWorldState(
            group_id=group_id,
            scene_version=0,
            active_topics=(),
            participants=(),
            interaction_edges=(),
            group_activity=GroupActivity(0, 0, None),
            social_atmosphere=SocialAtmosphere("neutral", 0.0),
            bot_roles=(),
            pending_opportunities=(),
            running_tasks=(),
            open_loops=(),
            recent_presence=PresenceHistory((), None),
            culture_version=0,
        )

    def apply(
        self, state: GroupWorldState, event: SocialEventEnvelope
    ) -> GroupWorldState:
        if event.group_id != state.group_id:
            raise ValueError("event group does not match world state")

        is_message = event.event_type == "platform.message"
        topics = state.active_topics
        participants = state.participants
        edges = state.interaction_edges
        message_id = event.source_message_id or event.event_id

        if is_message:
            topics = self._project_topic(topics, event, message_id)
            if event.actor_id:
                participants = self._project_participant(participants, event)
            edges = self._project_reply_edge(edges, topics, event)

        activity = replace(
            state.group_activity,
            event_count=state.group_activity.event_count + 1,
            message_count=state.group_activity.message_count + int(is_message),
            last_event_at=event.occurred_at,
        )
        presence = PresenceHistory(
            recent_event_ids=(state.recent_presence.recent_event_ids + (event.event_id,))[
                -50:
            ],
            last_bot_event_at=state.recent_presence.last_bot_event_at,
        )
        return replace(
            state,
            scene_version=state.scene_version + 1,
            active_topics=topics,
            participants=participants,
            interaction_edges=edges,
            group_activity=activity,
            recent_presence=presence,
        )

    @staticmethod
    def _project_topic(
        topics: tuple[TopicState, ...],
        event: SocialEventEnvelope,
        message_id: str,
    ) -> tuple[TopicState, ...]:
        reply_to = str(event.payload.get("reply_to") or "").strip()
        suggested = str(event.payload.get("suggested_topic_id") or "").strip()
        target_index = None

        # A platform reply chain is authoritative and model advice cannot override it.
        if reply_to:
            for index, topic in enumerate(topics):
                if reply_to in topic.message_ids:
                    target_index = index
                    break
        elif suggested:
            for index, topic in enumerate(topics):
                if suggested == topic.topic_id:
                    target_index = index
                    break

        if target_index is None:
            actor_ids = (event.actor_id,) if event.actor_id else ()
            return topics + (
                TopicState(
                    topic_id=message_id,
                    root_event_id=message_id,
                    message_ids=(message_id,),
                    participant_ids=actor_ids,
                    last_event_at=event.occurred_at,
                ),
            )

        topic = topics[target_index]
        participant_ids = topic.participant_ids
        if event.actor_id and event.actor_id not in participant_ids:
            participant_ids += (event.actor_id,)
        updated = replace(
            topic,
            message_ids=topic.message_ids + (message_id,),
            participant_ids=participant_ids,
            last_event_at=event.occurred_at,
        )
        return topics[:target_index] + (updated,) + topics[target_index + 1 :]

    @staticmethod
    def _project_participant(
        participants: tuple[ParticipantState, ...], event: SocialEventEnvelope
    ) -> tuple[ParticipantState, ...]:
        for index, participant in enumerate(participants):
            if participant.actor_id == event.actor_id:
                updated = replace(
                    participant,
                    message_count=participant.message_count + 1,
                    last_seen_at=event.occurred_at,
                )
                return participants[:index] + (updated,) + participants[index + 1 :]
        return participants + (
            ParticipantState(str(event.actor_id), 1, event.occurred_at),
        )

    @staticmethod
    def _project_reply_edge(
        edges: tuple[InteractionEdge, ...],
        topics: tuple[TopicState, ...],
        event: SocialEventEnvelope,
    ) -> tuple[InteractionEdge, ...]:
        del topics
        reply_actor = str(event.payload.get("reply_to_actor_id") or "").strip()
        if not event.actor_id or not reply_actor:
            return edges
        for index, edge in enumerate(edges):
            if (
                edge.source_actor_id == event.actor_id
                and edge.target_actor_id == reply_actor
                and edge.kind == "reply"
            ):
                updated = replace(
                    edge, count=edge.count + 1, last_event_at=event.occurred_at
                )
                return edges[:index] + (updated,) + edges[index + 1 :]
        return edges + (
            InteractionEdge(
                event.actor_id, reply_actor, "reply", 1, event.occurred_at
            ),
        )

    @staticmethod
    def to_dict(state: GroupWorldState) -> dict[str, object]:
        return asdict(state)

    @staticmethod
    def from_dict(payload: Mapping[str, object]) -> GroupWorldState:
        return GroupWorldState(
            group_id=str(payload["group_id"]),
            scene_version=int(payload["scene_version"]),
            active_topics=tuple(
                TopicState(
                    topic_id=str(item["topic_id"]),
                    root_event_id=str(item["root_event_id"]),
                    message_ids=tuple(item["message_ids"]),
                    participant_ids=tuple(item["participant_ids"]),
                    last_event_at=int(item["last_event_at"]),
                )
                for item in payload["active_topics"]
            ),
            participants=tuple(
                ParticipantState(**item) for item in payload["participants"]
            ),
            interaction_edges=tuple(
                InteractionEdge(**item) for item in payload["interaction_edges"]
            ),
            group_activity=GroupActivity(**payload["group_activity"]),
            social_atmosphere=SocialAtmosphere(**payload["social_atmosphere"]),
            bot_roles=tuple(BotTopicRole(**item) for item in payload["bot_roles"]),
            pending_opportunities=tuple(
                OpportunityRef(**item) for item in payload["pending_opportunities"]
            ),
            running_tasks=tuple(TaskRef(**item) for item in payload["running_tasks"]),
            open_loops=tuple(OpenLoopRef(**item) for item in payload["open_loops"]),
            recent_presence=PresenceHistory(
                recent_event_ids=tuple(
                    payload["recent_presence"]["recent_event_ids"]
                ),
                last_bot_event_at=payload["recent_presence"]["last_bot_event_at"],
            ),
            culture_version=int(payload["culture_version"]),
        )


__all__ = ("GroupWorldProjector", "GroupWorldState", "TopicState")
