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
class ConversationLease:
    target_id: str
    topic_id: str
    source_plan_id: str
    opened_at: int
    expires_at: int
    remaining_turns: int
    last_bot_event_id: str | None = None
    unresolved_intent: str | None = None
    last_activity_at: int | None = None


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
    conversation_lease: ConversationLease | None = None

    def topic_for_message(self, message_id: str) -> TopicState:
        for topic in self.active_topics:
            if message_id in topic.message_ids:
                return topic
        raise KeyError(message_id)


class GroupWorldProjector:
    """Projects platform facts before optional model observations are considered."""

    TOPIC_TTL_SECONDS = 300
    TOPIC_MERGE_SECONDS = 30
    MAX_ACTIVE_TOPICS = 12

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
            conversation_lease=None,
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
        conversation_lease = state.conversation_lease
        if (
            conversation_lease is not None
            and event.occurred_at > conversation_lease.expires_at
        ):
            conversation_lease = None
        if event.event_type in {
            "conversation.lease_opened",
            "conversation.lease_advanced",
        }:
            conversation_lease = self._project_conversation_lease(
                conversation_lease, event
            )

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
            last_bot_event_at=(
                event.occurred_at
                if event.event_type == "delivery.sent"
                or bool(event.payload.get("is_self"))
                else state.recent_presence.last_bot_event_at
            ),
        )
        return replace(
            state,
            scene_version=state.scene_version + 1,
            active_topics=topics,
            participants=participants,
            interaction_edges=edges,
            group_activity=activity,
            recent_presence=presence,
            conversation_lease=conversation_lease,
        )

    @staticmethod
    def _project_conversation_lease(
        current: ConversationLease | None,
        event: SocialEventEnvelope,
    ) -> ConversationLease | None:
        payload = event.payload
        target_id = str(payload.get("target_id") or "").strip()
        topic_id = str(payload.get("topic_id") or "").strip()
        source_plan_id = str(payload.get("source_plan_id") or "").strip()
        try:
            opened_at = int(payload.get("opened_at"))
            expires_at = int(payload.get("expires_at"))
            remaining_turns = int(payload.get("remaining_turns"))
        except (TypeError, ValueError):
            return current
        last_bot_event_id = str(payload.get("last_bot_event_id") or "").strip() or None
        unresolved_intent = str(payload.get("unresolved_intent") or "").strip() or None
        raw_last_activity_at = payload.get("last_activity_at")
        try:
            last_activity_at = (
                int(raw_last_activity_at)
                if raw_last_activity_at is not None
                else None
            )
        except (TypeError, ValueError):
            return current
        if last_activity_at is not None and last_activity_at < 0:
            return current
        if (
            not target_id
            or not topic_id
            or not source_plan_id
            or opened_at < 0
            or expires_at <= opened_at
        ):
            return current
        if event.event_type == "conversation.lease_opened":
            if not 1 <= remaining_turns <= 5:
                return current
            return ConversationLease(
                target_id=target_id,
                topic_id=topic_id,
                source_plan_id=source_plan_id,
                opened_at=opened_at,
                expires_at=expires_at,
                remaining_turns=remaining_turns,
                last_bot_event_id=last_bot_event_id,
                unresolved_intent=unresolved_intent,
                last_activity_at=last_activity_at,
            )
        if (
            current is None
            or target_id != current.target_id
            or topic_id != current.topic_id
            or not 0 <= remaining_turns < current.remaining_turns
        ):
            return current
        if remaining_turns == 0:
            return None
        return ConversationLease(
            target_id=target_id,
            topic_id=topic_id,
            source_plan_id=source_plan_id,
            opened_at=opened_at,
            expires_at=expires_at,
            remaining_turns=remaining_turns,
            last_bot_event_id=last_bot_event_id or current.last_bot_event_id,
            unresolved_intent=unresolved_intent or current.unresolved_intent,
            last_activity_at=(
                last_activity_at
                if last_activity_at is not None
                else current.last_activity_at
            ),
        )

    @staticmethod
    def _project_topic(
        topics: tuple[TopicState, ...],
        event: SocialEventEnvelope,
        message_id: str,
    ) -> tuple[TopicState, ...]:
        topics = tuple(
            topic
            for topic in topics
            if event.occurred_at - topic.last_event_at
            <= GroupWorldProjector.TOPIC_TTL_SECONDS
        )
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
        elif event.actor_id:
            for index in range(len(topics) - 1, -1, -1):
                topic = topics[index]
                if (
                    event.actor_id in topic.participant_ids
                    and event.occurred_at - topic.last_event_at
                    <= GroupWorldProjector.TOPIC_MERGE_SECONDS
                ):
                    target_index = index
                    break

        if target_index is None:
            actor_ids = (event.actor_id,) if event.actor_id else ()
            return (topics + (
                TopicState(
                    topic_id=message_id,
                    root_event_id=message_id,
                    message_ids=(message_id,),
                    participant_ids=actor_ids,
                    last_event_at=event.occurred_at,
                ),
            ))[-GroupWorldProjector.MAX_ACTIVE_TOPICS :]

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
        projected = topics[:target_index] + (updated,) + topics[target_index + 1 :]
        return projected[-GroupWorldProjector.MAX_ACTIVE_TOPICS :]

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
        lease_payload = payload.get("conversation_lease")
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
            conversation_lease=(
                ConversationLease(**lease_payload)
                if isinstance(lease_payload, Mapping)
                else None
            ),
        )


__all__ = (
    "ConversationLease",
    "GroupWorldProjector",
    "GroupWorldState",
    "TopicState",
)
