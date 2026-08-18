from __future__ import annotations

import asyncio
from dataclasses import dataclass

import pytest

from groupmate.social_runtime.cognition.contracts import CognitiveObservation
from groupmate.social_runtime.contracts import RuntimeMode, SocialEventEnvelope
from groupmate.social_runtime.manager import SocialRuntimeManager
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
                    "topic_id": frame.focus_topic_ids[0],
                    "chain_of_thought": "不得持久化的模型推理",
                },
                confidence=0.95,
                evidence_event_ids=(frame.focus_event_ids[0],),
                scene_version=context.scene_version,
                expires_at=context.now + 30,
                uncertainty=(),
            ),
        )


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
        projection = manager.event_store.shadow_evaluations("885617919")
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
