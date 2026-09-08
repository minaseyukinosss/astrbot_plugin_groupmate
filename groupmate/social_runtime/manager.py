"""Shadow composition service for actor, cognition, and governance routing."""

from __future__ import annotations

import asyncio
import hashlib
import time
from contextlib import closing
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Callable, Mapping

from .attention import AttentionFrame, ambient_deadline_expired
from .actions.contracts import ActionPlan, DeliveryBundle, PlanValidation
from .actions.coordinator import ExecutionCoordinator
from .cognition.contracts import (
    CognitiveContext,
    CognitiveObservation,
    CognitiveWorker,
    CognitiveWorkerDiagnostic,
)
from .cognition.service import CognitionBudget, CognitionService
from .contracts import (
    PersonaSnapshot,
    RuntimeGovernanceState,
    RuntimeMode,
    SocialEventEnvelope,
)
from .chorus import ChorusDetector, ChorusEvidence, ChorusParticipationRepository
from .event_fabric import SocialEventFabric
from .governor import (
    GovernorContext,
    GovernorResult,
    RejectedIntention,
    SocialGovernor,
)
from .intentions import CandidateIntention, IntentionEngine
from .knowledge.contracts import (
    KnowledgeSnapshot,
    KnowledgeResolverPort,
    TopicUnderstandingFrame,
)
from .knowledge.enrichment import SceneGuard, SceneGuardCheck
from .participation import ParticipationPolicy
from .persistence.event_store import AppendResult, SQLiteSocialEventStore
from .persistence.schema import connect_database
from .persistence.repositories import (
    RelationshipEventIdentityConflict,
    SQLitePersonaStateRepository,
    SQLiteSocietyRepository,
)
from .persona.profile import GroupmatePersonaProfile
from .profile.repository import ProfileRepository
from .profile.retrieval import ProfileRetrieval, ProfileRetriever
from .memory.relationship_memory import RelationshipMemorySelector
from .society.relationship_events import (
    RelationshipEventDecision,
    RelationshipEventProposal,
    RelationshipEventService,
)
from .society.relationships import (
    PublicAffection,
    RelationshipEvidence,
    RelationshipProjection,
)
from .memory.relationship_memory import RelationshipMemory
from .replying import ReplyPlan, ReplyPlanRepository
from .social_context import SceneEventFact
from .delivery.outbox import OutboxService
from .dialogue import DialogueContextReader
from .scene_actor import (
    GroupSceneActor,
    SceneWorkRequest,
    SceneWorkResult,
    safe_cognitive_observation,
)
from .supervisor import PersonaSupervisor
from .tasks.runtime import TaskRuntime


class ShadowSideEffectForbidden(RuntimeError):
    """Raised when a Shadow runtime attempts an external side effect."""


class RuntimeModeUnavailable(RuntimeError):
    """Raised before I/O when a runtime mode has not passed its release gate."""


@dataclass(frozen=True)
class _PersonaProfileSnapshot:
    version: int
    profile: GroupmatePersonaProfile


@dataclass(frozen=True)
class ShadowEvaluation:
    persona_id: str
    request_id: str
    runtime_mode: RuntimeMode
    scene_version: int
    config_version: int
    frame: AttentionFrame | None
    governor_result: GovernorResult
    source_event: SocialEventEnvelope
    context_events: tuple[SocialEventEnvelope, ...]
    candidates: tuple[CandidateIntention, ...]
    accepted: bool
    status: str
    topic_understanding: TopicUnderstandingFrame | None = None
    knowledge_diagnostics: tuple[str, ...] = ()
    knowledge_diagnostic: Mapping[str, object] | None = None
    participation_lane: str = "AMBIENT"
    participation_diagnostics: tuple[str, ...] = ()
    chorus_evidence: ChorusEvidence | None = None
    cognitive_observations: tuple[CognitiveObservation, ...] = ()
    cognition_diagnostics: tuple[CognitiveWorkerDiagnostic, ...] = ()
    candidate_response: str | None = None
    reply_diagnostic: str | None = None
    social_scene_summary: Mapping[str, object] | None = None
    scene_diagnostic: Mapping[str, object] | None = None
    social_stance_summary: Mapping[str, object] | None = None
    social_move_summary: Mapping[str, object] | None = None
    social_would_reply: bool | None = None
    relationship_decisions: tuple[RelationshipEventDecision, ...] = ()
    relationship_stage: str | None = None
    knowledge_scene_guard: SceneGuard | None = None
    knowledge_snapshot: KnowledgeSnapshot | None = None
    knowledge_reply_valid: bool | None = None

    def resolve_reply_source(self) -> SocialEventEnvelope | None:
        """Resolve the approved message without changing trace/request identity."""
        selected = next((candidate for candidate in self.candidates
                         if candidate.intention_id in self.governor_result.selected_intention_ids), None)
        if selected is None or self.frame is None:
            return None
        events = {event.event_id: event for event in (self.source_event, *self.context_events)}
        if selected.anchor_event_id is None and (
            self.frame.trigger_kind != "AMBIENT" or selected.kind == "CHORUS_CHECK"
        ):
            return events[self.source_event.event_id]
        ids = ((selected.anchor_event_id,) if selected.anchor_event_id
               else tuple(reversed(selected.evidence_event_ids)))
        for event_id in ids:
            event = events.get(event_id)
            if (event is not None and event_id in self.frame.focus_event_ids
                    and event.event_type == "platform.message"
                    and event.actor_id == selected.target_id
                    and event.payload.get("social_eligible") is not False
                    and not event.payload.get("is_self")):
                return event
        return None

    def to_capture_evidence(self) -> dict[str, object]:
        frame_id = (
            self.frame.frame_id
            if self.frame is not None
            else f"external:{self.request_id}"
        )
        return {
            "capture_id": f"runtime-shadow:{self.persona_id}:{frame_id}",
            "evaluation": {
                "persona_id": self.persona_id,
                "request_id": self.request_id,
                "runtime_mode": self.runtime_mode.value,
                "scene_version": self.scene_version,
                "config_version": self.config_version,
                "frame": asdict(self.frame) if self.frame is not None else None,
                "governor_result": asdict(self.governor_result),
                "source_event": self.source_event.to_dict(),
                "context_events": [
                    event.to_dict() for event in self.context_events
                ],
                "candidates": [asdict(candidate) for candidate in self.candidates],
                "topic_understanding": (
                    asdict(self.topic_understanding)
                    if self.topic_understanding is not None
                    else None
                ),
                "knowledge_diagnostics": list(self.knowledge_diagnostics),
                "knowledge_diagnostic": (
                    dict(self.knowledge_diagnostic)
                    if self.knowledge_diagnostic is not None
                    else None
                ),
                "participation_lane": self.participation_lane,
                "participation_diagnostics": list(
                    self.participation_diagnostics
                ),
                "chorus_evidence": (
                    asdict(self.chorus_evidence)
                    if self.chorus_evidence is not None
                    else None
                ),
                "cognitive_observations": [
                    safe_cognitive_observation(item)
                    for item in self.cognitive_observations
                ],
                "cognition_diagnostics": [
                    asdict(item) for item in self.cognition_diagnostics
                ],
                "candidate_response": self.candidate_response,
                "reply_diagnostic": self.reply_diagnostic,
                "scene_diagnostic": dict(self.scene_diagnostic) if self.scene_diagnostic else None,
                "social_scene_summary": (
                    dict(self.social_scene_summary)
                    if self.social_scene_summary is not None
                    else None
                ),
                "social_stance_summary": (
                    dict(self.social_stance_summary)
                    if self.social_stance_summary is not None
                    else None
                ),
                "social_move_summary": (
                    dict(self.social_move_summary)
                    if self.social_move_summary is not None
                    else None
                ),
                "social_would_reply": self.social_would_reply,
                "relationship_decisions": [
                    asdict(item) for item in self.relationship_decisions
                ],
                "relationship_stage": self.relationship_stage,
                "accepted": self.accepted,
                "status": self.status,
            },
        }

    @classmethod
    def from_capture_evidence(
        cls, evidence: Mapping[str, object]
    ) -> "ShadowEvaluation":
        values = dict(evidence.get("evaluation") or {})
        frame_values = values.get("frame")
        if frame_values is not None:
            frame_values = dict(frame_values)
            for key in (
                "focus_topic_ids",
                "focus_event_ids",
                "candidate_audiences",
                "requested_workers",
            ):
                frame_values[key] = tuple(frame_values.get(key, ()))
        governor_values = dict(values["governor_result"])
        governor_values["selected_intention_ids"] = tuple(
            governor_values.get("selected_intention_ids", ())
        )
        governor_values["rejected"] = tuple(
            RejectedIntention(
                intention_id=str(item["intention_id"]),
                reason_codes=tuple(item.get("reason_codes", ())),
            )
            for item in governor_values.get("rejected", ())
        )
        governor_values["reason_codes"] = tuple(
            governor_values.get("reason_codes", ())
        )
        governor_values["constraints"] = tuple(
            governor_values.get("constraints", ())
        )
        candidates = []
        for item in values.get("candidates", ()):
            candidate = dict(item)
            candidate["evidence_event_ids"] = tuple(
                candidate.get("evidence_event_ids", ())
            )
            candidates.append(CandidateIntention(**candidate))
        cognition_diagnostics = tuple(
            CognitiveWorkerDiagnostic(**dict(item))
            for item in values.get("cognition_diagnostics", ())
        )
        cognitive_observations = tuple(
            CognitiveObservation.create(
                **{
                    **dict(item),
                    "evidence_event_ids": tuple(
                        item.get("evidence_event_ids", ())
                    ),
                    "uncertainty": (),
                }
            )
            for item in values.get("cognitive_observations", ())
        )
        relationship_decisions = []
        for item in values.get("relationship_decisions", ()):
            decision = dict(item)
            decision["reason_codes"] = tuple(
                decision.get("reason_codes", ())
            )
            proposal = dict(decision["proposal"])
            proposal["source_event_ids"] = tuple(
                proposal.get("source_event_ids", ())
            )
            decision["proposal"] = RelationshipEventProposal(**proposal)
            evidence = decision.get("evidence")
            decision["evidence"] = (
                RelationshipEvidence(**dict(evidence))
                if isinstance(evidence, Mapping)
                else None
            )
            relationship_decisions.append(
                RelationshipEventDecision(**decision)
            )
        chorus_values = values.get("chorus_evidence")
        topic_values = values.get("topic_understanding")
        topic_understanding = None
        if isinstance(topic_values, Mapping):
            topic_data = dict(topic_values)
            fallback_confidence = float(topic_data.get("confidence", 0.0))
            for field in ("resolved_entities", "resolved_terms"):
                normalized_items = []
                for item in topic_data.get(field, ()):
                    normalized = dict(item)
                    normalized.setdefault("confidence", fallback_confidence)
                    normalized.setdefault("supporting_knowledge_ids", ())
                    normalized_items.append(normalized)
                topic_data[field] = tuple(normalized_items)
            normalized_referents = []
            for item in topic_data.get("discourse_referents", ()):
                normalized = dict(item)
                normalized.setdefault("confidence", fallback_confidence)
                normalized_referents.append(normalized)
            topic_data["discourse_referents"] = tuple(normalized_referents)
            version_data = topic_data.get("version_reference")
            if isinstance(version_data, Mapping):
                version_data = dict(version_data)
                version_data.setdefault("confidence", fallback_confidence)
                topic_data["version_reference"] = version_data
            topic_data["game_ids"] = tuple(topic_data.get("game_ids", ()))
            topic_data["ambiguity_codes"] = tuple(
                topic_data.get("ambiguity_codes", ())
            )
            topic_data["supporting_knowledge_ids"] = tuple(
                topic_data.get("supporting_knowledge_ids", ())
            )
            topic_understanding = TopicUnderstandingFrame.create(**topic_data)
        return cls(
            persona_id=str(values["persona_id"]),
            request_id=str(values["request_id"]),
            runtime_mode=RuntimeMode(values["runtime_mode"]),
            scene_version=int(values["scene_version"]),
            config_version=int(values["config_version"]),
            frame=None if frame_values is None else AttentionFrame(**frame_values),
            governor_result=GovernorResult(**governor_values),
            source_event=SocialEventEnvelope.from_dict(values["source_event"]),
            context_events=tuple(
                SocialEventEnvelope.from_dict(item)
                for item in values.get("context_events", ())
            ),
            candidates=tuple(candidates),
            accepted=bool(values["accepted"]),
            status=str(values["status"]),
            topic_understanding=topic_understanding,
            knowledge_diagnostics=tuple(
                values.get("knowledge_diagnostics", ())
            ),
            knowledge_diagnostic=(
                dict(values["knowledge_diagnostic"])
                if isinstance(values.get("knowledge_diagnostic"), Mapping)
                else None
            ),
            participation_lane=str(
                values.get("participation_lane") or "AMBIENT"
            ),
            participation_diagnostics=tuple(
                values.get("participation_diagnostics", ())
            ),
            chorus_evidence=(
                ChorusEvidence(
                    **{
                        **dict(chorus_values),
                        "event_ids": tuple(dict(chorus_values).get("event_ids", ())),
                        "participant_ids": tuple(
                            dict(chorus_values).get("participant_ids", ())
                        ),
                    }
                )
                if isinstance(chorus_values, Mapping)
                else None
            ),
            cognitive_observations=cognitive_observations,
            cognition_diagnostics=cognition_diagnostics,
            candidate_response=(
                str(values.get("candidate_response") or "").strip() or None
            ),
            reply_diagnostic=(
                str(values.get("reply_diagnostic") or "").strip() or None
            ),
            social_scene_summary=(
                dict(values["social_scene_summary"])
                if isinstance(values.get("social_scene_summary"), Mapping)
                else None
            ),
            scene_diagnostic=(dict(values["scene_diagnostic"])
                              if isinstance(values.get("scene_diagnostic"), Mapping) else None),
            social_stance_summary=(
                dict(values["social_stance_summary"])
                if isinstance(values.get("social_stance_summary"), Mapping)
                else None
            ),
            social_move_summary=(
                dict(values["social_move_summary"])
                if isinstance(values.get("social_move_summary"), Mapping)
                else None
            ),
            social_would_reply=(
                values.get("social_would_reply")
                if isinstance(values.get("social_would_reply"), bool)
                else None
            ),
            relationship_decisions=tuple(relationship_decisions),
            relationship_stage=(
                str(values.get("relationship_stage") or "").strip() or None
            ),
        )


@dataclass(frozen=True)
class PendingShadowReviewEvidence:
    capture_id: str
    evaluation: ShadowEvaluation


class NoSideEffectExecutionPort:
    """Fail-closed execution port installed for Shadow operation."""

    def __init__(self) -> None:
        self._calls: list[object] = []

    @property
    def calls(self) -> tuple[object, ...]:
        return tuple(self._calls)

    async def execute(self, action: object) -> None:
        self._calls.append(action)
        raise ShadowSideEffectForbidden("external side effects are disabled")


class SocialRuntimeManager:
    def __init__(
        self,
        *,
        database_path: Path,
        persona_id: str,
        mode: RuntimeMode,
        enabled_groups: tuple[str, ...],
        social_runtime_test_groups: tuple[str, ...] = (),
        config_version: int = 1,
        event_store: SQLiteSocialEventStore | None = None,
        cognition_workers: Mapping[str, CognitiveWorker] | None = None,
        cognition_budget: CognitionBudget | None = None,
        worker_concurrency_limit: int = 12,
        worker_timeout_seconds: float = 8.0,
        governance_state: RuntimeGovernanceState | None = None,
        persona_profile_loader: Callable[[str], object] | None = None,
        knowledge_resolver: KnowledgeResolverPort | None = None,
        clock: Callable[[], float] | None = None,
    ) -> None:
        resolved_mode = RuntimeMode(mode)
        enabled = frozenset(
            str(group_id).strip()
            for group_id in enabled_groups
            if str(group_id).strip()
        )
        test_groups = frozenset(
            str(group_id).strip()
            for group_id in social_runtime_test_groups
            if str(group_id).strip()
        )
        if resolved_mode is RuntimeMode.OFF:
            raise RuntimeModeUnavailable(
                "current release gate only supports SHADOW or allowlisted SOCIAL_RUNTIME"
            )
        if resolved_mode is RuntimeMode.SOCIAL_RUNTIME and (
            not test_groups or not test_groups.issubset(enabled)
        ):
            raise RuntimeModeUnavailable(
                "current release gate only supports SHADOW unless Gate C has a "
                "test group allowlist contained in enabled_groups"
            )
        self.persona_id = persona_id
        self.mode = resolved_mode
        self.enabled_groups = enabled
        self.social_runtime_test_groups = test_groups
        self.config_version = config_version
        self._persona_profile_loader = persona_profile_loader
        self.knowledge_resolver = knowledge_resolver
        self._persona_profiles: dict[
            tuple[str, int], GroupmatePersonaProfile
        ] = {}
        resolved_clock = time.time if clock is None else clock
        if not callable(resolved_clock):
            raise ValueError("runtime clock must be callable")
        self._clock = resolved_clock
        self.event_store = event_store or SQLiteSocialEventStore(database_path)
        self.supervisor = PersonaSupervisor(
            persona_id, SQLitePersonaStateRepository(database_path)
        )
        self.society = SQLiteSocietyRepository(database_path)
        self.profile_retriever = ProfileRetriever(
            ProfileRepository(database_path)
        )
        self.relationship_events = RelationshipEventService(self.society)
        self._relationship_memory_selector = RelationshipMemorySelector()
        self.execution_port = NoSideEffectExecutionPort()
        self.cognition = CognitionService(
            workers=cognition_workers or {},
            budget=cognition_budget
            or CognitionBudget(
                8,
                12,
                worker_timeout_seconds=float(worker_timeout_seconds),
                max_worker_concurrency=int(worker_concurrency_limit),
            ),
        )
        self.intentions = IntentionEngine()
        self.participation = ParticipationPolicy(self.intentions)
        self.chorus_detector = ChorusDetector()
        self.chorus_participation = ChorusParticipationRepository(database_path)
        self.governor = SocialGovernor()
        self._governance_state = governance_state or RuntimeGovernanceState()
        self.fabric = SocialEventFabric(self._new_actor, self.event_store)
        self.task_runtime = TaskRuntime(database_path)
        self.reply_plans = ReplyPlanRepository(database_path)
        self.outbox = OutboxService(
            database_path,
            group_authorizer=lambda group_id: self.group_mode(group_id)
            is RuntimeMode.SOCIAL_RUNTIME,
            bundle_authorizer=self._bundle_has_matching_plan,
        )
        self.coordinator = ExecutionCoordinator(
            database_path,
            task_runtime=self.task_runtime,
            outbox=self.outbox,
            event_sink=self._publish_execution_event,
            plan_authorizer=lambda plan: self.require_social_runtime_group(
                plan.group_id
            ),
        )
        self._started = False
        self._closing = False
        self._active_drains = 0
        self._lifecycle_lock = asyncio.Lock()
        self._drains_idle = asyncio.Event()
        self._drains_idle.set()
        self._closed = asyncio.Event()
        self._closed.set()
        self._startup_requests = ()
        self._expired_attention_count = 0

    async def start(self) -> None:
        while True:
            initialize_feedback = False
            async with self._lifecycle_lock:
                if self._closing:
                    closed = self._closed
                else:
                    if self._started:
                        return
                    await self.fabric.open()
                    await self.supervisor.start()
                    for group_id in self.event_store.pending_groups(self.persona_id):
                        if group_id in self.enabled_groups:
                            await self.fabric.notify(self.persona_id, group_id)
                    self._started = True
                    self._closed.set()
                    initialize_feedback = True
            if initialize_feedback:
                try:
                    await self.coordinator.recover_feedback()
                    startup_requests = await self.fabric.drain()
                except BaseException:
                    await self.close()
                    raise
                async with self._lifecycle_lock:
                    self._startup_requests = startup_requests
                return
            await closed.wait()

    async def ingest(self, envelope: SocialEventEnvelope) -> AppendResult | None:
        async with self._lifecycle_lock:
            self._ensure_available()
            if envelope.persona_id != self.persona_id:
                raise ValueError("event persona does not match manager")
            if not envelope.group_id or envelope.group_id not in self.enabled_groups:
                return None
            envelope = self._resolve_platform_reply(envelope)
            return await self.fabric.publish(envelope)

    def _resolve_platform_reply(
        self, envelope: SocialEventEnvelope
    ) -> SocialEventEnvelope:
        if envelope.event_type != "platform.message" or not envelope.group_id:
            return envelope
        reply_to = str(envelope.payload.get("reply_to") or "").strip()
        if not reply_to:
            return envelope
        platform = str(envelope.payload.get("platform") or "qq").strip()
        replied = self.event_store.event_by_source_message(
            envelope.persona_id,
            envelope.group_id,
            platform,
            reply_to,
        )
        if replied is None or not replied.actor_id:
            return envelope
        values = envelope.to_dict()
        payload = dict(envelope.payload)
        reply_actor_id = str(replied.actor_id)
        bot_id = str(payload.get("bot_id") or "").strip()
        payload["reply_to_actor_id"] = reply_actor_id
        payload["reply_to_bot"] = bool(bot_id and reply_actor_id == bot_id)
        values["payload"] = payload
        return SocialEventEnvelope.create(**values)

    async def _publish_execution_event(
        self, envelope: SocialEventEnvelope
    ) -> AppendResult:
        async with self._lifecycle_lock:
            self._ensure_available()
            if envelope.persona_id != self.persona_id:
                raise ValueError("execution feedback persona does not match manager")
            if not envelope.group_id or envelope.group_id not in self.enabled_groups:
                raise ValueError("execution feedback requires an enabled group")
            return await self.fabric.publish(envelope)

    async def drain(self, *, now: int | None = None) -> tuple[ShadowEvaluation, ...]:
        await self._begin_drain()
        try:
            recovered = self._startup_requests
            self._startup_requests = ()
            requests = recovered + await self.fabric.drain()
            flushed = await self.fabric.flush_attention(self._resolve_now(now))
            requests += flushed
            evaluations = []
            for request in requests:
                if self._is_external_compatibility_request(request):
                    evaluations.append(
                        await self._external_compatibility_cycle(request)
                    )
                    continue
                for frame in request.attention_frames:
                    if frame.frame_id in request.evaluated_frame_ids:
                        continue
                    cycle_now = self._resolve_now(now)
                    if ambient_deadline_expired(frame, cycle_now):
                        actor = await self.fabric.notify(
                            request.persona_id, request.group_id
                        )
                        discarded = await actor.discard_work(
                            request.request_id,
                            "attention_deadline_expired",
                        )
                        self._expired_attention_count += int(discarded)
                        continue
                    evaluations.append(
                        await self._evaluate_cycle(
                            request,
                            frame,
                            now=cycle_now,
                            explicit_now=now is not None,
                        )
                    )
            return tuple(evaluations)
        finally:
            await self._end_drain()

    async def group_snapshot(self, group_id: str):
        await self._begin_drain()
        try:
            actor = await self.fabric.notify(self.persona_id, group_id)
            return await actor.snapshot()
        finally:
            await self._end_drain()

    async def freeze_scene_guard(
        self, group_id: str, evaluation: object, *, now: int
    ) -> SceneGuard:
        """Freeze only the reply authority that must survive enrichment."""

        normalized_group = str(group_id).strip()
        if normalized_group not in self.enabled_groups:
            raise ValueError("scene guard requires an enabled group")
        result = getattr(evaluation, "governor_result", None)
        if str(getattr(result, "outcome", "")).upper() != "ACT":
            raise ValueError("scene guard requires an ACT evaluation")
        selected_ids = tuple(
            getattr(result, "selected_intention_ids", ()) or ()
        )
        candidates = tuple(getattr(evaluation, "candidates", ()) or ())
        selected = next(
            (
                item
                for item in candidates
                if getattr(item, "intention_id", None) in selected_ids
            ),
            None,
        )
        if selected is None:
            raise ValueError("selected intention is unavailable")
        source_event = getattr(evaluation, "source_event", None)
        target_id = str(
            getattr(selected, "target_id", None)
            or getattr(source_event, "actor_id", None)
            or ""
        ).strip()
        if not target_id:
            raise ValueError("scene guard requires a reply target")
        world = await self.group_snapshot(normalized_group)
        lane = str(
            getattr(evaluation, "participation_lane", "AMBIENT") or "AMBIENT"
        ).upper()
        lease = world.conversation_lease if lane == "CONTINUATION" else None
        if lane == "CONTINUATION" and (
            lease is None
            or lease.target_id != target_id
            or lease.expires_at <= int(now)
            or lease.remaining_turns <= 0
        ):
            raise ValueError("continuation scene guard requires a live lease")
        return SceneGuard.create(
            group_id=normalized_group,
            scene_version=int(getattr(evaluation, "scene_version", -1)),
            target_id=target_id,
            lease_id=None if lease is None else lease.source_plan_id,
            lease_expires_at=None if lease is None else lease.expires_at,
            intention_id=str(getattr(selected, "intention_id", "")),
            intention_expires_at=int(getattr(selected, "expires_at", now)),
        )

    async def current_scene_guard(
        self, group_id: str, evaluation: object
    ) -> SceneGuardCheck:
        """Compare a frozen guard with the latest group world and intention."""

        normalized_group = str(group_id).strip()
        guard = getattr(evaluation, "knowledge_scene_guard", None)
        if not isinstance(guard, SceneGuard) or guard.group_id != normalized_group:
            return SceneGuardCheck.invalid("scene_guard_missing")
        now = int(self._clock())
        current = await self.validate_scene_guard(guard, now)
        if not current.is_valid:
            return current
        result = getattr(evaluation, "governor_result", None)
        selected_ids = tuple(
            getattr(result, "selected_intention_ids", ()) or ()
        )
        selected = next(
            (
                item
                for item in tuple(getattr(evaluation, "candidates", ()) or ())
                if getattr(item, "intention_id", None) in selected_ids
            ),
            None,
        )
        if selected is None or str(getattr(selected, "intention_id", "")) != (
            guard.intention_id
        ):
            return SceneGuardCheck.invalid("intention_changed")
        source_event = getattr(evaluation, "source_event", None)
        current_target = str(
            getattr(selected, "target_id", None)
            or getattr(source_event, "actor_id", None)
            or ""
        ).strip()
        if current_target != guard.target_id:
            return SceneGuardCheck.invalid("reply_target_changed")
        if int(getattr(selected, "expires_at", -1)) != int(
            guard.intention_expires_at
        ):
            return SceneGuardCheck.invalid("intention_changed")
        return SceneGuardCheck.valid()

    async def validate_scene_guard(
        self, guard: SceneGuard, now: int
    ) -> SceneGuardCheck:
        """Validate the durable world dimensions available to enrichment."""

        if not isinstance(guard, SceneGuard):
            return SceneGuardCheck.invalid("scene_guard_missing")
        timestamp = int(now)
        if guard.intention_expires_at <= timestamp:
            return SceneGuardCheck.invalid("intention_expired")
        world = await self.group_snapshot(guard.group_id)
        if world.scene_version != guard.scene_version:
            return SceneGuardCheck.invalid("scene_version_changed")
        if guard.lease_id is not None:
            lease = world.conversation_lease
            if (
                lease is None
                or lease.source_plan_id != guard.lease_id
                or lease.target_id != guard.target_id
            ):
                return SceneGuardCheck.invalid("continuation_lease_changed")
            if lease.expires_at <= timestamp or lease.remaining_turns <= 0:
                return SceneGuardCheck.invalid("continuation_lease_expired")
        return SceneGuardCheck.valid()

    def persona_profile_mapping(
        self, group_id: str, config_version: int
    ) -> dict[str, object]:
        """Return only the Persona frozen into the matching evaluation."""

        profile = self._persona_profiles.get((str(group_id), int(config_version)))
        if profile is None:
            loaded = self._load_persona_profile(str(group_id))
            if loaded.version != int(config_version):
                raise RuntimeError("persona profile changed after frozen evaluation")
            profile = loaded.profile
        return profile.to_mapping()

    def relationship_affection(
        self, group_id: str, subject_id: str
    ) -> PublicAffection:
        normalized_group = str(group_id).strip()
        normalized_subject = str(subject_id).strip()
        if normalized_group not in self.enabled_groups or not normalized_subject:
            raise ValueError("relationship lookup requires an enabled group and subject")
        _, affection = self.society.relationship_snapshot(
            self.persona_id,
            normalized_group,
            normalized_subject,
        )
        return affection

    def relationship_projection(
        self, group_id: str, subject_id: str
    ) -> RelationshipProjection:
        normalized_group = str(group_id).strip()
        normalized_subject = str(subject_id).strip()
        if normalized_group not in self.enabled_groups or not normalized_subject:
            raise ValueError("relationship lookup requires an enabled group and subject")
        state, _ = self.society.relationship_snapshot(
            self.persona_id,
            normalized_group,
            normalized_subject,
        )
        return state

    def member_profile_retrieval(
        self, event: SocialEventEnvelope, *, max_chars: int = 1200
    ) -> ProfileRetrieval:
        try:
            return self.profile_retriever.for_message(event, max_chars=max_chars)
        except Exception:
            return ProfileRetrieval((), (), (), (), "", {"members": [], "relations": []})

    def relationship_memory_records(
        self, group_id: str, subject_id: str
    ) -> tuple[RelationshipMemory, ...]:
        normalized_group = str(group_id).strip()
        normalized_subject = str(subject_id).strip()
        if normalized_group not in self.enabled_groups or not normalized_subject:
            raise ValueError("relationship memory lookup requires an enabled scope")
        return self.society.relationship_memories(
            self.persona_id,
            normalized_group,
            normalized_subject,
        )

    async def persona_snapshot(
        self, group_id: str, config_version: int
    ) -> PersonaSnapshot:
        profile = self._load_persona_profile(str(group_id))
        if profile.version != int(config_version):
            raise RuntimeError("persona profile changed after frozen evaluation")
        return await self.supervisor.snapshot(int(config_version))

    def group_member_refs(self, group_id: str) -> Mapping[str, tuple[str, ...]]:
        normalized_group = str(group_id).strip()
        if normalized_group not in self.enabled_groups:
            raise ValueError("member directory requires an enabled group")
        return self.profile_retriever.group_member_refs(
            self.persona_id, normalized_group
        )

    def relationship_memory_cues(
        self,
        group_id: str,
        subject_id: str,
        *,
        text: str,
        now: int,
    ) -> tuple[str, ...]:
        """Select bounded, expressible memories inside one relationship scope."""

        normalized_group = str(group_id).strip()
        normalized_subject = str(subject_id).strip()
        if normalized_group not in self.enabled_groups or not normalized_subject:
            raise ValueError("relationship memory lookup requires an enabled scope")
        records = self.society.relationship_memories(
            self.persona_id,
            normalized_group,
            normalized_subject,
        )
        affection = self.relationship_affection(
            normalized_group, normalized_subject
        )
        return self._relationship_memory_selector.select(
            records,
            text=str(text or ""),
            stage=affection.stage,
            now=int(now),
        )

    def member_profile_context(
        self,
        event: SocialEventEnvelope,
        *,
        max_chars: int = 1200,
    ) -> str:
        try:
            return self.member_profile_retrieval(
                event, max_chars=max_chars
            ).prompt_text
        except Exception:
            return ""

    async def record_usable_reply(self, plan: ReplyPlan, *, now: int) -> bool:
        """Project a bounded dialogue lease after usable text exists."""

        if (
            plan.persona_id != self.persona_id
            or plan.group_id not in self.enabled_groups
            or not plan.target_id
            or not plan.topic_id
        ):
            return False
        now = int(now)
        world = await self.group_snapshot(plan.group_id)
        lease = world.conversation_lease
        if plan.participation_lane == "CONTINUATION":
            if (
                lease is None
                or lease.target_id != plan.target_id
                or lease.expires_at < now
                or lease.remaining_turns <= 0
            ):
                return False
            event_type = "conversation.lease_advanced"
            opened_at = lease.opened_at
            remaining_turns = lease.remaining_turns - 1
            topic_id = lease.topic_id
            unresolved_intent = lease.unresolved_intent or plan.act
        else:
            event_type = "conversation.lease_opened"
            opened_at = now
            remaining_turns = 5
            topic_id = plan.topic_id
            unresolved_intent = plan.act
        event = SocialEventEnvelope.create(
            event_id=f"conversation-lease:{plan.plan_id}",
            event_type=event_type,
            occurred_at=now,
            received_at=now,
            persona_id=self.persona_id,
            group_id=plan.group_id,
            actor_id=None,
            source_message_id=None,
            correlation_id=f"{plan.correlation_id}:conversation-lease",
            causation_id=(
                plan.evidence_event_ids[0]
                if plan.evidence_event_ids
                else None
            ),
            payload={
                "target_id": plan.target_id,
                "topic_id": topic_id,
                "source_plan_id": plan.plan_id,
                "opened_at": opened_at,
                "expires_at": now + 180,
                "remaining_turns": remaining_turns,
                "last_bot_event_id": plan.plan_id,
                "unresolved_intent": unresolved_intent,
                "last_activity_at": now,
            },
        )
        appended = await self.ingest(event)
        if appended is None:
            return False
        await self.fabric.drain()
        return appended.inserted

    async def next_attention_deadline(self) -> int | None:
        async with self._lifecycle_lock:
            self._ensure_available()
            return await self.fabric.next_attention_deadline()

    @property
    def governance_state(self) -> RuntimeGovernanceState:
        return self._governance_state

    @property
    def expired_attention_count(self) -> int:
        return self._expired_attention_count

    def group_mode(self, group_id: str) -> RuntimeMode:
        normalized = str(group_id).strip()
        if normalized not in self.enabled_groups:
            return RuntimeMode.OFF
        if (
            self.mode is RuntimeMode.SOCIAL_RUNTIME
            and normalized in self.social_runtime_test_groups
        ):
            return RuntimeMode.SOCIAL_RUNTIME
        return RuntimeMode.SHADOW

    def require_social_runtime_group(self, group_id: str) -> None:
        if self.group_mode(group_id) is not RuntimeMode.SOCIAL_RUNTIME:
            raise RuntimeModeUnavailable(
                "Gate C external actions require an explicit test group allowlist"
            )

    def pending_shadow_review_evidence(
        self,
    ) -> tuple[PendingShadowReviewEvidence, ...]:
        return tuple(
            PendingShadowReviewEvidence(
                capture_id=item.capture_id,
                evaluation=ShadowEvaluation.from_capture_evidence(item.payload),
            )
            for item in self.event_store.pending_shadow_captures(
                self.persona_id, tuple(sorted(self.enabled_groups))
            )
        )

    def complete_shadow_review_evidence(self, capture_id: str) -> bool:
        return self.event_store.complete_shadow_capture(capture_id)

    def update_shadow_review_evidence(self, evaluation: ShadowEvaluation) -> bool:
        evidence = evaluation.to_capture_evidence()
        return self.event_store.update_shadow_capture(evidence)

    def submit_plan(
        self,
        plan: ActionPlan,
        validation: PlanValidation,
        *,
        now: int,
    ):
        self.require_social_runtime_group(plan.group_id)
        return self.coordinator.submit(plan, validation, now=now)

    def _bundle_has_matching_plan(self, bundle: DeliveryBundle) -> bool:
        if self.reply_plans.authorizes_bundle(bundle):
            return True
        if not hasattr(self, "coordinator"):
            return False
        with closing(connect_database(self.outbox.path)) as db:
            rows = db.execute(
                "SELECT plan_id FROM action_plans WHERE correlation_id=? "
                "AND persona_id=? AND group_id=? "
                "AND status IN ('running','completed')",
                (bundle.correlation_id, bundle.persona_id, bundle.group_id),
            ).fetchall()
        for row in rows:
            plan = self.coordinator.load(str(row["plan_id"])).plan
            if (
                bundle.topic_id == plan.topic_id
                and bundle.expires_at <= plan.expires_at
                and bundle.created_at < bundle.expires_at
            ):
                return True
        return False

    def update_governance_state(
        self,
        state: RuntimeGovernanceState,
        *,
        config_version: int,
    ) -> None:
        if config_version <= self.config_version:
            raise ValueError("governance config version must advance")
        self._governance_state = state
        self.config_version = int(config_version)

    async def close(self) -> None:
        async with self._lifecycle_lock:
            if not self._started:
                return
            if self._closing:
                closed = self._closed
                wait_for_other = True
            else:
                self._closing = True
                self._closed.clear()
                closed = self._closed
                wait_for_other = False
        if wait_for_other:
            await closed.wait()
            return
        await self._drains_idle.wait()
        async with self._lifecycle_lock:
            try:
                await self.fabric.close()
            finally:
                try:
                    await self.supervisor.close()
                finally:
                    self._started = False
                    self._closing = False
                    self._closed.set()

    def _new_actor(self, persona_id: str, group_id: str) -> GroupSceneActor:
        async def snapshot_provider():
            profile = self._load_persona_profile(group_id)
            return await self.supervisor.snapshot(profile.version)

        return GroupSceneActor(
            persona_id,
            group_id,
            self.event_store,
            snapshot_provider,
            lambda: self._governance_state,
        )

    async def _evaluate_cycle(
        self,
        request: SceneWorkRequest,
        frame: AttentionFrame,
        *,
        now: int,
        explicit_now: bool,
    ) -> ShadowEvaluation:
        focus_events = self.event_store.event_envelopes(
            request.persona_id,
            request.group_id,
            frame.focus_event_ids,
        )
        context_events = self.event_store.event_envelopes(
            request.persona_id,
            request.group_id,
            request.world_snapshot.recent_presence.recent_event_ids,
        )
        chorus_evidence = self._detect_chorus(
            request=request,
            context_events=context_events[-20:],
            now=now,
        )
        context_events = DialogueContextReader(self.outbox.path).read(
            request.persona_id, request.group_id,
            (*context_events, *focus_events), frame.focus_event_ids,
            required_context_event_ids=chorus_evidence.event_ids if chorus_evidence else (),
        )
        profile = self._persona_profiles.get(
            (request.group_id, frame.config_version)
        )
        if profile is None:
            loaded = self._load_persona_profile(request.group_id)
            if loaded.version != frame.config_version:
                raise RuntimeError("persona profile changed during frozen cognition")
            profile = loaded.profile
        topic_understanding = None
        knowledge_diagnostics: tuple[str, ...] = ()
        if self.knowledge_resolver is not None:
            try:
                topic_understanding = self.knowledge_resolver.resolve(
                    request.event,
                    context_events,
                    request.group_id,
                    now,
                )
            except Exception:
                topic_understanding = TopicUnderstandingFrame.create(
                    frame_id="knowledge-frame:error:"
                    + hashlib.sha256(
                        request.event.event_id.encode("utf-8")
                    ).hexdigest()[:20],
                    game_ids=(),
                    resolved_entities=(),
                    resolved_terms=(),
                    discourse_referents=(),
                    version_reference=None,
                    conversation_intent_hint=None,
                    ambiguity_codes=(
                        "knowledge_local_resolution_failed",
                    ),
                    confidence=0.0,
                    supporting_knowledge_ids=(),
                )
                knowledge_diagnostics = (
                    "knowledge_local_resolution_failed",
                )
        world_summary = self._cognitive_world_view(request, frame, profile)
        if topic_understanding is not None:
            world_summary["topic_understanding"] = (
                topic_understanding.to_prompt_facts()
            )
        context = CognitiveContext.create(
            group_id=request.group_id,
            scene_version=frame.scene_version,
            persona_state_version=frame.persona_state_version,
            config_version=frame.config_version,
            now=now,
            focus_events=tuple(event.to_dict() for event in focus_events),
            context_events=tuple(event.to_dict() for event in context_events),
            world_summary=world_summary,
            constraints=("no_side_effects", "evidence_required"),
            token_budget=1024,
        )
        blackboard = await self.cognition.evaluate(frame, context)
        decision_now = now if explicit_now else self._resolve_now(None)
        proposal = self.participation.propose(
            frame,
            blackboard,
            decision_now,
            chorus_evidence=chorus_evidence,
        )
        candidates = proposal.candidates
        governor_result = self.governor.decide(
            candidates,
            GovernorContext(
                now=decision_now,
                scene_version=frame.scene_version,
                allowed_target_ids=frame.candidate_audiences,
                allowed_topic_ids=frame.focus_topic_ids,
                privacy_allowed=request.governance_snapshot.privacy_allowed,
                boundary_active=request.event.event_type == "safety.boundary",
                paused=(
                    request.governance_snapshot.paused
                    or request.persona_snapshot.presence in {"paused", "offline"}
                    or request.persona_snapshot.mode == "paused"
                ),
                platform_available=request.governance_snapshot.platform_available,
                capability_allowed=request.governance_snapshot.capability_allowed,
                force_observe=(
                    (blackboard.degraded and not proposal.allow_degraded)
                    or (
                        chorus_evidence is None
                        and not self._participation_allows(frame, blackboard)
                    )
                ),
                rate_limited_until=request.governance_snapshot.rate_limited_until,
                minimum_utility=request.governance_snapshot.minimum_utility,
            ),
        )
        evaluation = ShadowEvaluation(
            persona_id=request.persona_id,
            request_id=request.request_id,
            runtime_mode=self.group_mode(request.group_id),
            scene_version=frame.scene_version,
            config_version=frame.config_version,
            frame=frame,
            governor_result=governor_result,
            source_event=request.event,
            context_events=context_events,
            candidates=candidates,
            accepted=True,
            status="accepted",
            topic_understanding=topic_understanding,
            knowledge_diagnostics=knowledge_diagnostics,
            participation_lane=proposal.lane.value,
            participation_diagnostics=proposal.diagnostics,
            chorus_evidence=chorus_evidence,
            cognitive_observations=tuple(
                entry.observation for entry in blackboard.entries
            ),
            cognition_diagnostics=blackboard.worker_diagnostics,
        )
        reply_source = evaluation.resolve_reply_source()
        if (self.knowledge_resolver is not None and reply_source is not None
                and reply_source.event_id != request.event.event_id):
            # The local resolver initially saw the window's trigger. Once the
            # governor selects an earlier anchor, do not send B's knowledge to A.
            try:
                topic_understanding = self.knowledge_resolver.resolve(
                    reply_source, context_events, request.group_id, now,
                )
            except Exception:
                topic_understanding = None
                knowledge_diagnostics = (*knowledge_diagnostics, "knowledge_anchor_resolution_failed")
            evaluation = replace(evaluation, topic_understanding=topic_understanding,
                                 knowledge_diagnostics=knowledge_diagnostics)
        result = SceneWorkResult(
            request_id=request.request_id,
            group_id=request.group_id,
            scene_version=frame.scene_version,
            config_version=frame.config_version,
            persona_state_version=frame.persona_state_version,
            frame_id=frame.frame_id,
            governor_result=governor_result,
            participation_lane=proposal.lane.value,
            participation_diagnostics=proposal.diagnostics,
            cognitive_observations=tuple(
                entry.observation for entry in blackboard.entries
            ),
            candidates=candidates,
            cognition_diagnostics=blackboard.worker_diagnostics,
            capture_evidence=evaluation.to_capture_evidence(),
        )
        actor = await self.fabric.notify(request.persona_id, request.group_id)
        if ambient_deadline_expired(frame, decision_now):
            discarded = await actor.discard_work(
                request.request_id,
                "attention_deadline_expired_after_cognition",
            )
            self._expired_attention_count += int(discarded)
            return replace(evaluation, accepted=False, status="stale")
        accepted = await actor.accept_result(result)
        evaluation = replace(
            evaluation,
            accepted=accepted,
            status="accepted" if accepted else "stale",
        )
        if not accepted:
            return evaluation
        decisions = self._process_relationship_events(
            request,
            frame,
            blackboard,
            proposal.lane.value,
            focus_events,
            decision_now,
        )
        relationship_stage = None
        if decisions:
            affection = self.relationship_affection(
                request.group_id, decisions[-1].proposal.subject_id
            )
            relationship_stage = affection.stage.value
            evaluation = replace(
                evaluation,
                relationship_decisions=decisions,
                relationship_stage=relationship_stage,
            )
            if evaluation.runtime_mode is RuntimeMode.SHADOW:
                self.update_shadow_review_evidence(evaluation)
        return evaluation

    def _detect_chorus(
        self,
        *,
        request: SceneWorkRequest,
        context_events: tuple[SocialEventEnvelope, ...],
        now: int,
    ) -> ChorusEvidence | None:
        if request.event.payload.get("social_eligible") is False:
            return None
        events_by_id = {event.event_id: event for event in context_events}
        events_by_id[request.event.event_id] = request.event
        joined = self.chorus_participation.recent_joined_chain_ids(
            request.group_id,
            since=max(0, int(now) - 3600),
        )
        return self.chorus_detector.detect(
            events=tuple(
                SceneEventFact.from_event(event) for event in events_by_id.values()
            ),
            source_event_id=request.event.event_id,
            group_id=request.group_id,
            persona_actor_id=request.persona_id,
            joined_chain_ids=joined,
        )

    def _process_relationship_events(
        self,
        request: SceneWorkRequest,
        frame: AttentionFrame,
        blackboard: object,
        lane: str,
        focus_events: tuple[SocialEventEnvelope, ...],
        now: int,
    ) -> tuple[RelationshipEventDecision, ...]:
        proposals = self._relationship_proposals(
            request, frame, blackboard, lane, focus_events, now
        )
        decisions = []
        mode = self.group_mode(request.group_id).value
        for proposal in proposals:
            try:
                decisions.append(
                    self.relationship_events.process(proposal, mode=mode)
                )
            except RelationshipEventIdentityConflict:
                existing = self.relationship_events.decisions(
                    proposal.persona_id,
                    proposal.group_id,
                    proposal.subject_id,
                )
                previous = next(
                    (
                        item
                        for item in reversed(existing)
                        if item.proposal.event_id == proposal.event_id
                    ),
                    None,
                )
                if previous is not None:
                    decisions.append(
                        RelationshipEventDecision(
                            "DUPLICATE",
                            ("event_id_already_processed",),
                            previous.proposal,
                            previous.evidence,
                            0.0,
                        )
                    )
            except Exception:
                # Relationship projection is supplementary and must never
                # invalidate an already accepted participation decision.
                continue
        return tuple(decisions)

    def _relationship_proposals(
        self,
        request: SceneWorkRequest,
        frame: AttentionFrame,
        blackboard: object,
        lane: str,
        focus_events: tuple[SocialEventEnvelope, ...],
        now: int,
    ) -> tuple[RelationshipEventProposal, ...]:
        occurred_at_by_id = {
            event.event_id: int(event.occurred_at) for event in focus_events
        }
        proposals = []
        for entry in getattr(blackboard, "entries", ()):
            observation = entry.observation
            if entry.conflict or observation.kind != "relationship_event":
                continue
            proposition = observation.proposition
            subject_id = str(proposition.get("subject_id") or "").strip()
            source_ids = tuple(observation.evidence_event_ids)
            occurred_at = max(
                (occurred_at_by_id.get(item, int(now)) for item in source_ids),
                default=int(now),
            )
            try:
                proposals.append(
                    RelationshipEventProposal(
                        event_id=self._relationship_event_id(
                            request.persona_id,
                            request.group_id,
                            subject_id,
                            str(proposition.get("kind") or ""),
                            source_ids,
                        ),
                        persona_id=request.persona_id,
                        group_id=request.group_id,
                        subject_id=subject_id,
                        kind=str(proposition.get("kind") or ""),
                        confidence=observation.confidence,
                        severity=str(proposition.get("severity") or ""),
                        summary=str(proposition.get("summary") or ""),
                        source_event_ids=source_ids,
                        occurred_at=occurred_at,
                        repair_of=(
                            str(proposition.get("repair_of") or "").strip()
                            or None
                        ),
                        sensitivity=str(
                            proposition.get("sensitivity") or "normal"
                        ),
                    )
                )
            except ValueError:
                continue
        if proposals or lane not in {"DIRECT_FAST", "CONTINUATION"}:
            return tuple(proposals)
        subject_id = str(request.event.actor_id or "").strip()
        if not subject_id:
            return ()
        kind = "interaction" if lane == "DIRECT_FAST" else "reciprocal_action"
        source_ids = (request.event.event_id,)
        return (
            RelationshipEventProposal(
                event_id=self._relationship_event_id(
                    request.persona_id,
                    request.group_id,
                    subject_id,
                    kind,
                    source_ids,
                ),
                persona_id=request.persona_id,
                group_id=request.group_id,
                subject_id=subject_id,
                kind=kind,
                confidence=1.0,
                severity="minor",
                summary=(
                    "成员延续了与爱弥斯的对话"
                    if lane == "CONTINUATION"
                    else "成员直接与爱弥斯互动"
                ),
                source_event_ids=source_ids,
                occurred_at=int(request.event.occurred_at),
            ),
        )

    @staticmethod
    def _relationship_event_id(
        persona_id: str,
        group_id: str,
        subject_id: str,
        kind: str,
        source_event_ids: tuple[str, ...],
    ) -> str:
        identity = "\x1f".join(
            (
                str(persona_id),
                str(group_id),
                str(subject_id),
                str(kind),
                *tuple(str(item) for item in source_event_ids),
            )
        )
        digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:32]
        return f"relationship:{digest}"

    def _cognitive_world_view(
        self,
        request: SceneWorkRequest,
        frame: AttentionFrame,
        profile: GroupmatePersonaProfile,
    ) -> dict[str, object]:
        world = request.world_snapshot
        topic_ids = set(frame.focus_topic_ids)
        topics = tuple(
            asdict(topic)
            for topic in world.active_topics
            if topic.topic_id in topic_ids
        )
        audience_ids = set(frame.candidate_audiences)
        for topic in world.active_topics:
            if topic.topic_id in topic_ids:
                audience_ids.update(topic.participant_ids)
        audiences = tuple(
            asdict(participant)
            for participant in world.participants
            if participant.actor_id in audience_ids
        )
        try:
            relationship_records = self.society.relationship_memories_for_subjects(
                request.persona_id,
                request.group_id,
                tuple(sorted(audience_ids)),
            )
            relationship_memories = tuple(
                {
                    "event_id": item.relationship_event_id,
                    "subject_id": item.subject_id,
                    "kind": item.kind,
                    "summary": item.summary,
                    "occurred_at": item.occurred_at,
                }
                for item in relationship_records
                if item.resolved_at is None
                and item.sensitivity == "normal"
                and item.kind in {"boundary_pressure", "repair_attempt"}
            )[-8:]
        except Exception:
            relationship_memories = ()
        try:
            member_context = self.profile_retriever.for_message(
                request.event, max_chars=800
            ).ambient_context
        except Exception:
            member_context = {"members": [], "relations": []}
        return {
            "topics": topics,
            "audiences": audiences,
            "group_activity": asdict(world.group_activity),
            "last_bot_event_at": world.recent_presence.last_bot_event_at,
            "bot_actor_id": str(request.event.payload.get("bot_id") or "")[:80],
            "conversation_lease": (
                asdict(world.conversation_lease)
                if world.conversation_lease is not None
                else None
            ),
            "persona_profile": profile.to_mapping(),
            "relationship_memories": relationship_memories,
            "member_context": member_context,
        }

    @staticmethod
    def _participation_allows(frame: AttentionFrame, blackboard: object) -> bool:
        if frame.trigger_kind != "AMBIENT":
            return True
        for entry in getattr(blackboard, "entries", ()):
            observation = entry.observation
            if (
                observation.kind == "participation_assessment"
                and not entry.conflict
                and observation.confidence >= 0.75
                and str(observation.proposition.get("decision") or "").lower()
                == "speak"
            ):
                return True
        return False

    def _load_persona_profile(self, group_id: str) -> _PersonaProfileSnapshot:
        if self._persona_profile_loader is None:
            snapshot = _PersonaProfileSnapshot(
                self.config_version,
                GroupmatePersonaProfile.default(),
            )
        else:
            raw = self._persona_profile_loader(group_id)
            version = int(getattr(raw, "version", -1))
            config = getattr(raw, "config", None)
            if version < 0 or not isinstance(config, Mapping):
                raise ValueError("persona profile loader returned an invalid snapshot")
            snapshot = _PersonaProfileSnapshot(
                version,
                (
                    GroupmatePersonaProfile.from_behavior_config(config)
                ),
            )
        self._persona_profiles[(str(group_id), snapshot.version)] = snapshot.profile
        return snapshot

    def _resolve_now(self, explicit: int | None) -> int:
        return int(self._clock()) if explicit is None else int(explicit)

    @staticmethod
    def _is_external_compatibility_request(request: SceneWorkRequest) -> bool:
        return bool(
            request.event.payload.get("interaction_owner") == "EXTERNAL_PLUGIN"
            and request.event.payload.get("social_eligible") is False
            and not request.attention_frames
        )

    async def _external_compatibility_cycle(
        self, request: SceneWorkRequest
    ) -> ShadowEvaluation:
        actor = await self.fabric.notify(request.persona_id, request.group_id)
        context_events = self.event_store.event_envelopes(
            request.persona_id,
            request.group_id,
            request.world_snapshot.recent_presence.recent_event_ids[-20:],
        )
        evaluation = ShadowEvaluation(
            persona_id=request.persona_id,
            request_id=request.request_id,
            runtime_mode=self.group_mode(request.group_id),
            scene_version=request.scene_version,
            config_version=request.persona_snapshot.config_version,
            frame=None,
            governor_result=GovernorResult(
                outcome="SILENCE",
                selected_intention_ids=(),
                rejected=(),
                reason_codes=("external_plugin_owned",),
                reconsider_at=None,
                constraints=("external_plugin_owns_response", "no_side_effects"),
            ),
            source_event=request.event,
            context_events=context_events,
            candidates=(),
            accepted=True,
            status="accepted",
            cognition_diagnostics=(),
        )
        accepted = await actor.discard_work(
            request.request_id,
            "external_plugin_owned",
            capture_evidence=evaluation.to_capture_evidence(),
        )
        return replace(
            evaluation,
            accepted=accepted,
            status="accepted" if accepted else "stale",
        )

    async def _begin_drain(self) -> None:
        async with self._lifecycle_lock:
            self._ensure_available()
            self._active_drains += 1
            self._drains_idle.clear()

    async def _end_drain(self) -> None:
        async with self._lifecycle_lock:
            self._active_drains -= 1
            if self._active_drains == 0:
                self._drains_idle.set()

    def _ensure_available(self) -> None:
        if not self._started or self._closing:
            raise RuntimeError("social runtime manager is not accepting work")


__all__ = (
    "NoSideEffectExecutionPort",
    "PendingShadowReviewEvidence",
    "RuntimeGovernanceState",
    "RuntimeModeUnavailable",
    "ShadowEvaluation",
    "ShadowSideEffectForbidden",
    "SocialRuntimeManager",
)
