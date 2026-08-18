from __future__ import annotations

import asyncio

from groupmate.social_runtime.contracts import GlobalStateEffect
from groupmate.social_runtime.persistence.repositories import SQLitePersonaStateRepository
from groupmate.social_runtime.supervisor import PersonaSupervisor


def test_supervisor_recovers_the_same_state_after_restart(tmp_path):
    async def scenario():
        path = tmp_path / "groupmate-social-runtime-v2.db"
        first = PersonaSupervisor("aemeath", SQLitePersonaStateRepository(path))
        await first.start()
        await first.apply_effect(
            GlobalStateEffect(
                effect_id="fx-recovery",
                source_event_id="evt-recovery",
                expected_version=0,
                kind="cognitive_load_delta",
                amount=35,
                evidence_event_ids=("evt-recovery",),
            )
        )
        before = await first.snapshot(config_version=4)
        await first.close()

        recovered = PersonaSupervisor("aemeath", SQLitePersonaStateRepository(path))
        await recovered.start()
        after = await recovered.snapshot(config_version=4)
        await recovered.close()
        return before, after

    before, after = asyncio.run(scenario())

    assert after == before
    assert after.cognitive_load == 35
