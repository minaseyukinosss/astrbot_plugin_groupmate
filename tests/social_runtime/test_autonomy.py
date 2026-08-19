from __future__ import annotations

import asyncio

import pytest

from groupmate.social_runtime.autonomy import (
    AutonomousOpportunity,
    AutonomousOpportunityScheduler,
    OpportunityLimitReached,
    OpportunityRevalidation,
    OpportunityStatus,
)


def _opportunity(**overrides):
    values = {
        "source_event_ids": ("qq:source-1",),
        "group_id": "group-1",
        "audience": ("user-1",),
        "earliest_at": 100,
        "expires_at": 150,
        "max_attempts": 2,
        "kind": "delayed-scene",
    }
    values.update(overrides)
    return AutonomousOpportunity(**values)


def _revalidation(**overrides):
    values = {
        "scene_version": 4,
        "relationship_version": 3,
        "scene_allows": True,
        "relationship_allows": True,
        "boundary_active": False,
        "budget_available": True,
    }
    values.update(overrides)
    return OpportunityRevalidation(**values)


@pytest.mark.parametrize(
    ("overrides", "message"),
    (
        ({"source_event_ids": ()}, "source"),
        ({"audience": ()}, "audience"),
        ({"expires_at": 100}, "expiry"),
        ({"max_attempts": 3}, "attempt"),
        ({"kind": "presence-ping"}, "source kind"),
        ({"source_event_ids": ("autonomy:recursive:1",)}, "recursive"),
    ),
)
def test_invalid_or_recursive_opportunity_is_rejected(overrides, message):
    with pytest.raises(ValueError, match=message):
        _opportunity(**overrides)


def test_revalidation_rejects_non_boolean_policy_decisions():
    with pytest.raises(ValueError, match="boolean"):
        _revalidation(budget_available="false")


def test_quiet_hours_delay_due_event_and_latest_context_is_recorded(tmp_path):
    async def scenario():
        emitted = []

        async def sink(event):
            emitted.append(event)

        scheduler = AutonomousOpportunityScheduler(
            tmp_path / "groupmate-social-runtime-v2.db",
            persona_id="persona-1",
            event_sink=sink,
        )
        scheduled = scheduler.schedule(_opportunity(), now=90)

        delayed = await scheduler.run_due(
            now=100,
            revalidate=lambda opportunity: _revalidation(quiet_until=120),
        )
        before_quiet_ends = tuple(emitted)
        persisted_delay = scheduler.get(scheduled.opportunity_id)

        emitted_records = await scheduler.run_due(
            now=120,
            revalidate=lambda opportunity: _revalidation(
                scene_version=7,
                relationship_version=8,
            ),
        )
        return delayed, before_quiet_ends, persisted_delay, emitted_records, emitted

    delayed, before_quiet_ends, persisted_delay, emitted_records, emitted = asyncio.run(
        scenario()
    )

    assert delayed == ()
    assert before_quiet_ends == ()
    assert persisted_delay.earliest_at == 120
    assert len(emitted_records) == 1
    assert emitted_records[0].status is OpportunityStatus.EMITTED
    assert emitted_records[0].attempts == 1
    assert emitted_records[0].last_scene_version == 7
    assert emitted_records[0].last_relationship_version == 8
    assert len(emitted) == 1
    event = emitted[0]
    assert event.event_type == "temporal.opportunity_due"
    assert event.group_id == "group-1"
    assert event.actor_id is None
    assert event.causation_id == "qq:source-1"
    assert event.payload["source_event_ids"] == ["qq:source-1"]
    assert event.payload["audience"] == ["user-1"]
    assert event.payload["attempt"] == 1
    assert event.payload["scene_version"] == 7
    assert event.payload["relationship_version"] == 8


@pytest.mark.parametrize(
    "blocked_context",
    (
        {"scene_allows": False},
        {"relationship_allows": False},
        {"boundary_active": True},
        {"budget_available": False},
    ),
)
def test_due_opportunity_revalidates_all_social_and_budget_gates(
    tmp_path, blocked_context
):
    async def scenario():
        emitted = []

        async def sink(event):
            emitted.append(event)

        scheduler = AutonomousOpportunityScheduler(
            tmp_path / "groupmate-social-runtime-v2.db",
            persona_id="persona-1",
            event_sink=sink,
        )
        scheduled = scheduler.schedule(_opportunity(), now=90)
        records = await scheduler.run_due(
            now=100,
            revalidate=lambda opportunity: _revalidation(**blocked_context),
        )
        return records, emitted, scheduler.get(scheduled.opportunity_id)

    records, emitted, persisted = asyncio.run(scenario())

    assert records == ()
    assert emitted == []
    assert persisted.status is OpportunityStatus.SCHEDULED
    assert persisted.attempts == 0
    assert persisted.last_scene_version == 4
    assert persisted.last_relationship_version == 3


def test_expired_opportunity_is_cancelled_without_emission(tmp_path):
    async def scenario():
        emitted = []

        async def sink(event):
            emitted.append(event)

        scheduler = AutonomousOpportunityScheduler(
            tmp_path / "groupmate-social-runtime-v2.db",
            persona_id="persona-1",
            event_sink=sink,
        )
        scheduled = scheduler.schedule(_opportunity(expires_at=101), now=90)
        records = await scheduler.run_due(
            now=101,
            revalidate=lambda opportunity: _revalidation(),
        )
        return records, emitted, scheduler.get(scheduled.opportunity_id)

    records, emitted, persisted = asyncio.run(scenario())

    assert records == ()
    assert emitted == []
    assert persisted.status is OpportunityStatus.EXPIRED


def test_one_followup_and_two_attempts_are_hard_limits(tmp_path):
    async def scenario():
        emitted = []

        async def sink(event):
            emitted.append(event)

        scheduler = AutonomousOpportunityScheduler(
            tmp_path / "groupmate-social-runtime-v2.db",
            persona_id="persona-1",
            event_sink=sink,
        )
        scheduled = scheduler.schedule(_opportunity(), now=90)
        first = (
            await scheduler.run_due(
                now=100,
                revalidate=lambda opportunity: _revalidation(),
            )
        )[0]
        followup = scheduler.schedule_followup(
            first.opportunity_id,
            earliest_at=110,
            now=101,
        )
        second = (
            await scheduler.run_due(
                now=110,
                revalidate=lambda opportunity: _revalidation(
                    scene_version=5,
                    relationship_version=4,
                ),
            )
        )[0]
        with pytest.raises(OpportunityLimitReached):
            scheduler.schedule_followup(
                second.opportunity_id,
                earliest_at=120,
                now=111,
            )
        restarted = AutonomousOpportunityScheduler(
            tmp_path / "groupmate-social-runtime-v2.db",
            persona_id="persona-1",
            event_sink=sink,
        )
        return scheduled, followup, second, emitted, restarted.get(second.opportunity_id)

    scheduled, followup, second, emitted, recovered = asyncio.run(scenario())

    assert scheduled.attempts == 0
    assert followup.followup_count == 1
    assert second.attempts == 2
    assert second.followup_count == 1
    assert recovered == second
    assert len(emitted) == 2
    assert emitted[0].event_id != emitted[1].event_id


def test_failed_fabric_append_reuses_same_event_identity_after_restart(tmp_path):
    async def scenario():
        attempted_ids = []

        async def failing_sink(event):
            attempted_ids.append(event.event_id)
            raise RuntimeError("fabric unavailable")

        path = tmp_path / "groupmate-social-runtime-v2.db"
        scheduler = AutonomousOpportunityScheduler(
            path,
            persona_id="persona-1",
            event_sink=failing_sink,
        )
        scheduled = scheduler.schedule(_opportunity(), now=90)
        with pytest.raises(RuntimeError, match="fabric unavailable"):
            await scheduler.run_due(
                now=100,
                revalidate=lambda opportunity: _revalidation(),
            )

        recovered_events = []

        async def recovered_sink(event):
            recovered_events.append(event)

        restarted = AutonomousOpportunityScheduler(
            path,
            persona_id="persona-1",
            event_sink=recovered_sink,
        )
        recovered = await restarted.run_due(
            now=101,
            revalidate=lambda opportunity: _revalidation(),
        )
        return attempted_ids, recovered_events, recovered, restarted.get(
            scheduled.opportunity_id
        )

    attempted_ids, recovered_events, recovered, persisted = asyncio.run(scenario())

    assert len(attempted_ids) == 1
    assert [event.event_id for event in recovered_events] == attempted_ids
    assert recovered == (persisted,)
    assert persisted.status is OpportunityStatus.EMITTED
    assert persisted.attempts == 1
