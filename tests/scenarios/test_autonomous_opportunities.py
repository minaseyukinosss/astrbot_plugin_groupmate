from __future__ import annotations

import asyncio

from groupmate.social_runtime.autonomy import (
    AutonomousOpportunity,
    AutonomousOpportunityScheduler,
    OpportunityRevalidation,
)
from groupmate.social_runtime.cognition.contracts import CognitiveObservation
from groupmate.social_runtime.contracts import RuntimeMode, SocialEventEnvelope
from groupmate.social_runtime.manager import SocialRuntimeManager
from tests.factories import social_event_values


class AutonomousGreetingWorker:
    name = "autonomy_revalidator"

    async def observe(self, frame, context):
        return (
            CognitiveObservation.create(
                worker=self.name,
                kind="greeting",
                proposition={"subject_id": frame.candidate_audiences[0]},
                confidence=1.0,
                evidence_event_ids=(frame.focus_event_ids[0],),
                scene_version=frame.scene_version,
                expires_at=context.now + 20,
                uncertainty=(),
            ),
        )


def _source_message():
    return SocialEventEnvelope.create(
        **social_event_values(
            event_id="qq:source-1",
            source_message_id="source-1",
            persona_id="persona-1",
            group_id="group-1",
            actor_id="user-1",
            occurred_at=100,
            received_at=100,
            correlation_id="corr:source-1",
            payload={"text": "明天可以再聊聊", "direct_address": True},
        )
    )


def test_autonomous_opportunity_reenters_attention_and_governor_without_delivery(
    tmp_path,
):
    async def scenario():
        path = tmp_path / "groupmate-social-runtime-v2.db"
        manager = SocialRuntimeManager(
            database_path=path,
            persona_id="persona-1",
            mode=RuntimeMode.SHADOW,
            enabled_groups=("group-1",),
            cognition_workers={
                "autonomy_revalidator": AutonomousGreetingWorker(),
            },
        )
        await manager.start()
        await manager.ingest(_source_message())
        await manager.drain(now=100)

        scheduler = AutonomousOpportunityScheduler(
            path,
            persona_id="persona-1",
            event_sink=manager.fabric.publish,
        )
        opportunity = scheduler.schedule(
            AutonomousOpportunity(
                source_event_ids=("qq:source-1",),
                group_id="group-1",
                audience=("user-1",),
                earliest_at=110,
                expires_at=130,
                max_attempts=2,
                kind="delayed-scene",
            ),
            now=101,
        )
        emitted = await scheduler.run_due(
            now=110,
            revalidate=lambda candidate: OpportunityRevalidation(
                scene_version=2,
                relationship_version=1,
                scene_allows=True,
                relationship_allows=True,
                boundary_active=False,
                budget_available=True,
            ),
        )
        evaluations = await manager.drain(now=110)
        event_ids = manager.event_store.event_ids()
        outbox_count = manager.outbox.count()
        execution_calls = manager.execution_port.calls
        await manager.close()
        return opportunity, emitted, evaluations, event_ids, outbox_count, execution_calls

    opportunity, emitted, evaluations, event_ids, outbox_count, execution_calls = (
        asyncio.run(scenario())
    )

    assert len(emitted) == 1
    assert len(evaluations) == 1
    evaluation = evaluations[0]
    assert evaluation.frame.trigger_kind == "TEMPORAL"
    assert evaluation.frame.requested_workers == ("autonomy_revalidator",)
    assert evaluation.frame.candidate_audiences == ("user-1",)
    assert evaluation.frame.focus_event_ids == (
        "qq:source-1",
        emitted[0].last_event_id,
    )
    assert evaluation.governor_result.outcome == "ACT"
    assert emitted[0].last_event_id in event_ids
    assert opportunity.opportunity_id in emitted[0].last_event_id
    assert outbox_count == 0
    assert execution_calls == ()
