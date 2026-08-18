from __future__ import annotations

import asyncio

import pytest

from groupmate.social_runtime.contracts import GlobalStateEffect
from groupmate.social_runtime.persistence.repositories import SQLitePersonaStateRepository
from groupmate.social_runtime.supervisor import PersonaSupervisor, StateVersionConflict


def _effect(effect_id, expected_version, kind="energy_delta", amount=-5):
    return GlobalStateEffect(
        effect_id=effect_id,
        source_event_id="evt-1",
        expected_version=expected_version,
        kind=kind,
        amount=amount,
        evidence_event_ids=("evt-1",),
    )


def test_supervisor_applies_effect_once_even_when_retried_concurrently(tmp_path):
    async def scenario():
        repository = SQLitePersonaStateRepository(
            tmp_path / "groupmate-social-runtime-v2.db"
        )
        supervisor = PersonaSupervisor("aemeath", repository)
        await supervisor.start()
        effect = _effect("fx-1", 0)
        results = await asyncio.gather(
            *(supervisor.apply_effect(effect) for _ in range(20))
        )
        await supervisor.close()
        return results

    results = asyncio.run(scenario())

    assert {item.state_version for item in results} == {1}
    assert {item.energy for item in results} == {95}


def test_supervisor_serializes_distinct_concurrent_effects_without_lost_updates(
    tmp_path,
):
    async def scenario():
        repository = SQLitePersonaStateRepository(
            tmp_path / "groupmate-social-runtime-v2.db"
        )
        supervisor = PersonaSupervisor("aemeath", repository)
        await supervisor.start()
        # The mailbox receives effects in creation order. Each caller uses the
        # version it expects after the preceding command has committed.
        results = await asyncio.gather(
            *(
                supervisor.apply_effect(_effect(f"fx-{index}", index))
                for index in range(20)
            )
        )
        final = await supervisor.snapshot(config_version=1)
        await supervisor.close()
        return results, final

    results, final = asyncio.run(scenario())

    assert [item.state_version for item in results] == list(range(1, 21))
    assert final.state_version == 20
    assert final.energy == 0


def test_supervisor_rejects_stale_expected_version(tmp_path):
    async def scenario():
        repository = SQLitePersonaStateRepository(
            tmp_path / "groupmate-social-runtime-v2.db"
        )
        supervisor = PersonaSupervisor("aemeath", repository)
        await supervisor.start()
        await supervisor.apply_effect(_effect("fx-1", 0))
        with pytest.raises(StateVersionConflict, match="expected 0, current 1"):
            await supervisor.apply_effect(_effect("fx-2", 0))
        await supervisor.close()

    asyncio.run(scenario())


def test_state_effects_are_clamped_to_authoritative_ranges(tmp_path):
    async def scenario():
        repository = SQLitePersonaStateRepository(
            tmp_path / "groupmate-social-runtime-v2.db"
        )
        supervisor = PersonaSupervisor("aemeath", repository)
        await supervisor.start()
        energy = await supervisor.apply_effect(_effect("fx-energy", 0, amount=-500))
        irritation = await supervisor.apply_effect(
            _effect("fx-irritation", 1, kind="irritation_delta", amount=500)
        )
        await supervisor.close()
        return energy, irritation

    energy, irritation = asyncio.run(scenario())

    assert energy.energy == 0
    assert irritation.irritation == 100


def test_snapshot_freezes_requested_config_version(tmp_path):
    async def scenario():
        repository = SQLitePersonaStateRepository(
            tmp_path / "groupmate-social-runtime-v2.db"
        )
        supervisor = PersonaSupervisor("aemeath", repository)
        await supervisor.start()
        snapshot = await supervisor.snapshot(config_version=7)
        await supervisor.close()
        return snapshot

    snapshot = asyncio.run(scenario())

    assert snapshot.state_version == 0
    assert snapshot.config_version == 7
    assert snapshot.presence == "awake"
    assert snapshot.mode == "social"
