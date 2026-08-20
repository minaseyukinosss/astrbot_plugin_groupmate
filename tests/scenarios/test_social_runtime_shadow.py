from __future__ import annotations

import asyncio
from dataclasses import dataclass

import pytest

from groupmate.social_runtime.cognition.contracts import CognitiveObservation
from groupmate.social_runtime.contracts import RuntimeMode, SocialEventEnvelope
from groupmate.social_runtime.manager import (
    RuntimeGovernanceState,
    SocialRuntimeManager,
)
from tests.factories import social_event_values


@dataclass
class FixedWorker:
    name: str
    observation_kind: str

    async def observe(self, frame, context):
        if self.observation_kind == "none":
            return ()
        return (
            CognitiveObservation.create(
                worker=self.name,
                kind=self.observation_kind,
                proposition={
                    "subject_id": frame.candidate_audiences[0],
                    "topic_id": (
                        frame.focus_topic_ids[0]
                        if frame.focus_topic_ids
                        else None
                    ),
                    "chain_of_thought": "不得持久化的模型推理",
                },
                confidence=0.95,
                evidence_event_ids=(frame.focus_event_ids[0],),
                scene_version=context.scene_version,
                expires_at=context.now + 30,
                uncertainty=(),
            ),
        )


class BlockingFixedWorker(FixedWorker):
    def __init__(self, name, observation_kind, entered, release):
        super().__init__(name, observation_kind)
        self.entered = entered
        self.release = release

    async def observe(self, frame, context):
        self.entered.set()
        await self.release.wait()
        return await super().observe(frame, context)


class CountingFixedWorker(FixedWorker):
    def __init__(self, name, observation_kind):
        super().__init__(name, observation_kind)
        self.calls = 0

    async def observe(self, frame, context):
        self.calls += 1
        return await super().observe(frame, context)


def _event(
    message_id: str,
    *,
    actor_id: str = "u1",
    direct: bool = True,
    occurred_at: int = 100,
    suggested_topic_id: str | None = None,
):
    payload = {"text": message_id, "direct_address": direct}
    if suggested_topic_id is not None:
        payload["suggested_topic_id"] = suggested_topic_id
    return SocialEventEnvelope.create(
        **social_event_values(
            event_id=f"qq:{message_id}",
            source_message_id=message_id,
            actor_id=actor_id,
            occurred_at=occurred_at,
            received_at=occurred_at,
            correlation_id=f"corr:{message_id}",
            payload=payload,
        )
    )


def test_external_owned_event_has_zero_frames_workers_candidates_and_outbox(tmp_path):
    async def scenario():
        worker = CountingFixedWorker("direct_interaction", "help_request")
        manager = SocialRuntimeManager(
            database_path=tmp_path / "groupmate-social-runtime-v2.db",
            persona_id="aemeath",
            mode=RuntimeMode.SHADOW,
            enabled_groups=("885617919",),
            cognition_workers={worker.name: worker},
        )
        await manager.start()
        event = SocialEventEnvelope.create(
            **social_event_values(
                event_id="qq:external-owned",
                source_message_id="external-owned",
                occurred_at=100,
                received_at=100,
                correlation_id="corr:external-owned",
                payload={
                    "text": "structured external trigger",
                    "interaction_owner": "EXTERNAL_PLUGIN",
                    "social_eligible": False,
                    "owner_ref": "astrbot.external",
                },
            )
        )
        await manager.ingest(event)
        evaluations = await manager.drain()
        scene_version = (await manager.group_snapshot("885617919")).scene_version
        outbox_count = manager.event_store.outbox_count()
        calls = manager.execution_port.calls
        await manager.close()
        return worker.calls, evaluations, scene_version, outbox_count, calls

    worker_calls, evaluations, scene_version, outbox_count, calls = asyncio.run(
        scenario()
    )
    assert worker_calls == 0
    assert len(evaluations) == 1
    assert evaluations[0].frame is None
    assert evaluations[0].candidates == ()
    assert evaluations[0].governor_result.outcome == "SILENCE"
    assert scene_version == 1
    assert outbox_count == 0
    assert calls == ()


@pytest.mark.parametrize(
    ("observation_kind", "expected_outcome"),
    (
        ("help_request", "ACT"),
        ("humor_signal", "ACT"),
        ("care_signal", "ACT"),
        ("boundary_signal", "ACT"),
        ("none", "SILENCE"),
    ),
)
def test_direct_social_scenarios_are_governed_in_shadow(
    tmp_path, observation_kind, expected_outcome
):
    async def scenario():
        worker = FixedWorker("direct_interaction", observation_kind)
        manager = SocialRuntimeManager(
            database_path=tmp_path / "groupmate-social-runtime-v2.db",
            persona_id="aemeath",
            mode=RuntimeMode.SHADOW,
            enabled_groups=("885617919",),
            cognition_workers={worker.name: worker},
        )
        await manager.start()
        await manager.ingest(_event("direct"))
        evaluations = await manager.drain()
        journal = manager.event_store.journal("corr:direct")
        projection = manager.event_store.shadow_evaluations(
            "aemeath", "885617919"
        )
        outbox_count = manager.event_store.outbox_count()
        await manager.close()
        return manager, evaluations, journal, projection, outbox_count

    manager, evaluations, journal, projection, outbox_count = asyncio.run(
        scenario()
    )

    assert len(evaluations) == 1
    assert evaluations[0].accepted is True
    assert evaluations[0].governor_result.outcome == expected_outcome
    assert any(item.effect_type == "shadow.governor_evaluated" for item in journal)
    assert all("chain_of_thought" not in str(item.payload) for item in journal)
    assert projection[0]["governor_result"]["reason_codes"]
    assert "rejected" in projection[0]["governor_result"]
    assert "chain_of_thought" not in str(projection)
    assert manager.execution_port.calls == ()
    assert outbox_count == 0


def test_ambient_window_waits_then_combines_multiple_topics(tmp_path):
    async def scenario():
        worker = FixedWorker("scene_interpreter", "help_request")
        manager = SocialRuntimeManager(
            database_path=tmp_path / "groupmate-social-runtime-v2.db",
            persona_id="aemeath",
            mode=RuntimeMode.SHADOW,
            enabled_groups=("885617919",),
            cognition_workers={worker.name: worker},
        )
        await manager.start()
        await manager.ingest(
            _event("topic-a", direct=False, occurred_at=100)
        )
        await manager.ingest(
            _event("topic-b", actor_id="u2", direct=False, occurred_at=101)
        )
        too_early = await manager.drain(now=102)
        after_quiet = await manager.drain(now=103)
        await manager.close()
        return too_early, after_quiet

    too_early, after_quiet = asyncio.run(scenario())

    assert too_early == ()
    assert len(after_quiet) == 1
    assert after_quiet[0].frame.trigger_kind == "AMBIENT"
    assert set(after_quiet[0].frame.focus_topic_ids) == {"topic-a", "topic-b"}
    assert after_quiet[0].governor_result.outcome == "ACT"


@pytest.mark.parametrize(
    ("governance", "event_type", "expected_reason"),
    (
        (
            RuntimeGovernanceState(privacy_allowed=False),
            "platform.message",
            "privacy_blocked",
        ),
        (RuntimeGovernanceState(paused=True), "platform.message", "runtime_paused"),
        (
            RuntimeGovernanceState(platform_available=False),
            "platform.message",
            "platform_unavailable",
        ),
        (RuntimeGovernanceState(), "safety.boundary", "boundary_active"),
    ),
)
def test_authoritative_runtime_gates_override_high_utility_act(
    tmp_path, governance, event_type, expected_reason
):
    async def scenario():
        worker = FixedWorker(
            "safety_guard" if event_type == "safety.boundary" else "direct_interaction",
            "help_request",
        )
        manager = SocialRuntimeManager(
            database_path=tmp_path / "groupmate-social-runtime-v2.db",
            persona_id="aemeath",
            mode=RuntimeMode.SHADOW,
            enabled_groups=("885617919",),
            cognition_workers={worker.name: worker},
            governance_state=governance,
        )
        await manager.start()
        event = _event("gated")
        if event_type == "safety.boundary":
            values = event.to_dict()
            values["event_type"] = event_type
            event = SocialEventEnvelope.create(**values)
        await manager.ingest(event)
        evaluations = await manager.drain()
        await manager.close()
        return evaluations

    evaluations = asyncio.run(scenario())

    result = evaluations[0].governor_result
    assert result.outcome == "SILENCE"
    assert expected_reason in result.reason_codes
    assert expected_reason in result.rejected[0].reason_codes


def test_shadow_projection_and_context_are_scoped_by_persona_and_group(tmp_path):
    async def scenario():
        path = tmp_path / "groupmate-social-runtime-v2.db"
        worker = FixedWorker("direct_interaction", "help_request")
        managers = []
        for persona_id in ("persona-a", "persona-b"):
            manager = SocialRuntimeManager(
                database_path=path,
                persona_id=persona_id,
                mode=RuntimeMode.SHADOW,
                enabled_groups=("885617919",),
                cognition_workers={worker.name: worker},
            )
            await manager.start()
            values = _event(persona_id).to_dict()
            values.update(
                persona_id=persona_id,
                event_id=f"qq:{persona_id}",
                source_message_id=persona_id,
                correlation_id=f"corr:{persona_id}",
            )
            await manager.ingest(SocialEventEnvelope.create(**values))
            await manager.drain()
            managers.append(manager)

        store = managers[0].event_store
        projection_a = store.shadow_evaluations("persona-a", "885617919")
        projection_b = store.shadow_evaluations("persona-b", "885617919")
        cross_context = store.event_envelopes(
            "persona-a",
            "885617919",
            ("qq:persona-b",),
        )
        for manager in managers:
            await manager.close()
        return projection_a, projection_b, cross_context

    projection_a, projection_b, cross_context = asyncio.run(scenario())

    assert len(projection_a) == len(projection_b) == 1
    assert projection_a[0]["persona_id"] == "persona-a"
    assert projection_b[0]["persona_id"] == "persona-b"
    assert cross_context == ()


def test_fast_evaluation_does_not_consume_pending_ambient_window(tmp_path):
    async def scenario():
        ambient_worker = FixedWorker("scene_interpreter", "help_request")
        direct_worker = FixedWorker("direct_interaction", "care_signal")
        manager = SocialRuntimeManager(
            database_path=tmp_path / "groupmate-social-runtime-v2.db",
            persona_id="aemeath",
            mode=RuntimeMode.SHADOW,
            enabled_groups=("885617919",),
            cognition_workers={
                ambient_worker.name: ambient_worker,
                direct_worker.name: direct_worker,
            },
            clock=lambda: 101,
        )
        await manager.start()
        await manager.ingest(_event("ambient", direct=False, occurred_at=100))
        await manager.ingest(_event("direct", direct=True, occurred_at=101))
        fast = await manager.drain()
        ambient = await manager.drain(now=102)
        projection = manager.event_store.shadow_evaluations(
            "aemeath", "885617919"
        )
        await manager.close()
        return fast, ambient, projection

    fast, ambient, projection = asyncio.run(scenario())

    assert [item.frame.trigger_kind for item in fast] == ["FAST"]
    assert [item.frame.trigger_kind for item in ambient] == ["AMBIENT"]
    assert all(item.accepted for item in fast + ambient)
    assert len(projection) == 2


def test_single_drain_accepts_fast_and_due_ambient_frames(tmp_path):
    async def scenario():
        ambient_worker = FixedWorker("scene_interpreter", "help_request")
        direct_worker = FixedWorker("direct_interaction", "care_signal")
        manager = SocialRuntimeManager(
            database_path=tmp_path / "groupmate-social-runtime-v2.db",
            persona_id="aemeath",
            mode=RuntimeMode.SHADOW,
            enabled_groups=("885617919",),
            cognition_workers={
                ambient_worker.name: ambient_worker,
                direct_worker.name: direct_worker,
            },
        )
        await manager.start()
        await manager.ingest(_event("ambient", direct=False, occurred_at=100))
        await manager.ingest(_event("direct", direct=True, occurred_at=101))
        evaluations = await manager.drain(now=102)
        projection = manager.event_store.shadow_evaluations(
            "aemeath", "885617919"
        )
        await manager.close()
        return evaluations, projection

    evaluations, projection = asyncio.run(scenario())

    assert [item.frame.trigger_kind for item in evaluations] == ["FAST", "AMBIENT"]
    assert all(item.accepted for item in evaluations)
    assert len(projection) == 2


def test_concurrent_flush_does_not_invalidate_running_fast_frame(tmp_path):
    async def scenario():
        entered = asyncio.Event()
        release = asyncio.Event()
        ambient_worker = FixedWorker("scene_interpreter", "help_request")
        direct_worker = BlockingFixedWorker(
            "direct_interaction",
            "care_signal",
            entered,
            release,
        )
        manager = SocialRuntimeManager(
            database_path=tmp_path / "groupmate-social-runtime-v2.db",
            persona_id="aemeath",
            mode=RuntimeMode.SHADOW,
            enabled_groups=("885617919",),
            cognition_workers={
                ambient_worker.name: ambient_worker,
                direct_worker.name: direct_worker,
            },
            clock=lambda: 101,
        )
        await manager.start()
        await manager.ingest(_event("ambient", direct=False, occurred_at=100))
        await manager.ingest(_event("direct", direct=True, occurred_at=101))
        fast_task = asyncio.create_task(manager.drain())
        await entered.wait()
        ambient = await manager.drain(now=102)
        release.set()
        fast = await fast_task
        projection = manager.event_store.shadow_evaluations(
            "aemeath", "885617919"
        )
        await manager.close()
        return fast, ambient, projection

    fast, ambient, projection = asyncio.run(scenario())

    assert [item.frame.trigger_kind for item in fast] == ["FAST"]
    assert [item.frame.trigger_kind for item in ambient] == ["AMBIENT"]
    assert fast[0].accepted is True
    assert ambient[0].accepted is True
    assert len(projection) == 2
