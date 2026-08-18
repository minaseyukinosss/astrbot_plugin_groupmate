from __future__ import annotations

import asyncio

from groupmate.social_runtime.cognition.contracts import CognitiveObservation
from groupmate.social_runtime.contracts import (
    GlobalStateEffect,
    RuntimeMode,
    SocialEventEnvelope,
)
from groupmate.social_runtime.manager import SocialRuntimeManager
from tests.factories import social_event_values


class BlockingWorker:
    name = "direct_interaction"

    def __init__(self) -> None:
        self.entered = asyncio.Event()
        self.release = asyncio.Event()

    async def observe(self, frame, context):
        self.entered.set()
        await self.release.wait()
        return (
            CognitiveObservation.create(
                worker=self.name,
                kind="help_request",
                proposition={"subject_id": "u1", "topic_id": "m1"},
                confidence=1.0,
                evidence_event_ids=("qq:m1",),
                scene_version=context.scene_version,
                expires_at=context.now + 30,
                uncertainty=(),
            ),
        )


def _direct_event():
    return SocialEventEnvelope.create(
        **social_event_values(
            event_id="qq:m1",
            source_message_id="m1",
            actor_id="u1",
            correlation_id="corr:m1",
            payload={"text": "帮我看看", "direct_address": True},
        )
    )


def _ambient_event():
    return SocialEventEnvelope.create(
        **social_event_values(
            event_id="qq:m2",
            source_message_id="m2",
            actor_id="u2",
            occurred_at=101,
            received_at=101,
            correlation_id="corr:m2",
            payload={"text": "新的现场事实"},
        )
    )


def test_config_change_while_worker_runs_discards_stale_act(tmp_path):
    async def scenario():
        worker = BlockingWorker()
        manager = SocialRuntimeManager(
            database_path=tmp_path / "groupmate-social-runtime-v2.db",
            persona_id="aemeath",
            mode=RuntimeMode.SHADOW,
            enabled_groups=("885617919",),
            cognition_workers={worker.name: worker},
            config_version=1,
        )
        await manager.start()
        await manager.ingest(_direct_event())
        draining = asyncio.create_task(manager.drain())
        await worker.entered.wait()
        manager.config_version = 2
        worker.release.set()
        evaluations = await draining
        journal = manager.event_store.journal("corr:m1")
        await manager.close()
        return evaluations, journal

    evaluations, journal = asyncio.run(scenario())

    assert len(evaluations) == 1
    assert evaluations[0].governor_result.outcome == "ACT"
    assert evaluations[0].accepted is False
    assert evaluations[0].status == "stale"
    assert all(item.effect_type != "shadow.governor_evaluated" for item in journal)


def test_scene_change_while_worker_runs_discards_stale_act(tmp_path):
    async def scenario():
        worker = BlockingWorker()
        manager = SocialRuntimeManager(
            database_path=tmp_path / "groupmate-social-runtime-v2.db",
            persona_id="aemeath",
            mode=RuntimeMode.SHADOW,
            enabled_groups=("885617919",),
            cognition_workers={worker.name: worker},
        )
        await manager.start()
        await manager.ingest(_direct_event())
        first_cycle = asyncio.create_task(manager.drain())
        await worker.entered.wait()
        await manager.ingest(_ambient_event())
        assert await manager.drain() == ()
        worker.release.set()
        evaluations = await first_cycle
        await manager.close()
        return evaluations

    evaluations = asyncio.run(scenario())

    assert evaluations[0].governor_result.outcome == "ACT"
    assert evaluations[0].accepted is False
    assert evaluations[0].status == "stale"


def test_persona_change_while_worker_runs_discards_stale_act(tmp_path):
    async def scenario():
        worker = BlockingWorker()
        manager = SocialRuntimeManager(
            database_path=tmp_path / "groupmate-social-runtime-v2.db",
            persona_id="aemeath",
            mode=RuntimeMode.SHADOW,
            enabled_groups=("885617919",),
            cognition_workers={worker.name: worker},
        )
        await manager.start()
        await manager.ingest(_direct_event())
        draining = asyncio.create_task(manager.drain())
        await worker.entered.wait()
        await manager.supervisor.apply_effect(
            GlobalStateEffect(
                effect_id="state:1",
                source_event_id="qq:m1",
                expected_version=0,
                kind="energy_delta",
                amount=-1,
                evidence_event_ids=("qq:m1",),
            )
        )
        worker.release.set()
        evaluations = await draining
        await manager.close()
        return evaluations

    evaluations = asyncio.run(scenario())

    assert evaluations[0].governor_result.outcome == "ACT"
    assert evaluations[0].accepted is False
    assert evaluations[0].status == "stale"
