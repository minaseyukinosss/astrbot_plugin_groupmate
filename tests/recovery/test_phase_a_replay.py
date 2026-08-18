from __future__ import annotations

import asyncio

import pytest

from groupmate.social_runtime.contracts import RuntimeMode, SocialEventEnvelope
from groupmate.social_runtime.manager import SocialRuntimeManager
from groupmate.social_runtime.persistence.event_store import SQLiteSocialEventStore
from groupmate.social_runtime.persistence.schema import connect_database
from tests.factories import social_event_values


class FailBeforeSeventeenthCommit(SQLiteSocialEventStore):
    """One-shot storage fault at the actual effect/cursor transaction boundary."""

    def __init__(self, path):
        super().__init__(path)
        self.commit_attempt = 0

    def commit(self, actor_key, claimed, effects, work_requests=()):
        self.commit_attempt += 1
        if self.commit_attempt == 17:
            raise RuntimeError("injected crash before effect commit")
        return super().commit(actor_key, claimed, effects, work_requests)


def _event(index):
    return SocialEventEnvelope.create(
        **social_event_values(
            event_id=f"qq:m{index}",
            source_message_id=f"m{index}",
            actor_id=f"u{index % 4}",
            occurred_at=index,
            received_at=index,
            correlation_id=f"corr:m{index}",
            payload={"text": f"消息 {index}"},
        )
    )


def _journal_count(store):
    with connect_database(store.path) as db:
        return db.execute("SELECT COUNT(*) FROM journal").fetchone()[0]


async def _run_clean(path):
    manager = SocialRuntimeManager(
        database_path=path,
        persona_id="aemeath",
        mode=RuntimeMode.SHADOW,
        enabled_groups=("885617919",),
    )
    await manager.start()
    for index in range(1, 31):
        await manager.ingest(_event(index))
    await manager.drain()
    world = await manager.group_snapshot("885617919")
    persona = await manager.supervisor.snapshot(config_version=1)
    cursor = manager.event_store.cursor("group:aemeath:885617919")
    journal_count = _journal_count(manager.event_store)
    execution_calls = manager.execution_port.calls
    await manager.close()
    return world, persona, cursor, journal_count, execution_calls


def test_phase_a_replay_matches_clean_run_after_commit_boundary_crash(tmp_path):
    async def scenario():
        crash_path = tmp_path / "crash" / "groupmate-social-runtime-v2.db"
        fault_store = FailBeforeSeventeenthCommit(crash_path)
        interrupted = SocialRuntimeManager(
            database_path=crash_path,
            persona_id="aemeath",
            mode=RuntimeMode.SHADOW,
            enabled_groups=("885617919",),
            event_store=fault_store,
        )
        await interrupted.start()
        for index in range(1, 31):
            await interrupted.ingest(_event(index))

        with pytest.raises(RuntimeError, match="before effect commit"):
            await interrupted.drain()
        crash_cursor = fault_store.cursor("group:aemeath:885617919")
        crash_journal_count = _journal_count(fault_store)
        crash_execution_calls = interrupted.execution_port.calls
        await interrupted.close()

        recovered = await _run_clean(crash_path)
        baseline = await _run_clean(
            tmp_path / "baseline" / "groupmate-social-runtime-v2.db"
        )
        return (
            crash_cursor,
            crash_journal_count,
            crash_execution_calls,
            recovered,
            baseline,
        )

    crash_cursor, crash_journal_count, crash_calls, recovered, baseline = asyncio.run(
        scenario()
    )

    assert crash_cursor.last_sequence == 16
    assert crash_journal_count == 16
    assert crash_calls == ()
    assert recovered == baseline
    world, persona, cursor, journal_count, execution_calls = recovered
    assert world.scene_version == 30
    assert persona.state_version == 0
    assert cursor.last_sequence == 30
    assert journal_count == 30
    assert execution_calls == ()
