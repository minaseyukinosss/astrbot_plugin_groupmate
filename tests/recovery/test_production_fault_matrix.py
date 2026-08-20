from __future__ import annotations

import asyncio
import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from eval.shadow import ShadowReviewRepository
from groupmate.adapters.astrbot_bridge import AstrBotSocialRuntimeBridge
from groupmate.adapters.onebot_delivery import OneBotDeliveryAdapter
from groupmate.settings import SocialRuntimeSettings
from groupmate.social_runtime.actions.contracts import (
    DeliveryBundle,
    DeliveryPart,
    DeliveryPartKind,
    OutboxStatus,
)
from groupmate.social_runtime.cognition.astrbot_workers import AstrBotStructuredWorker
from groupmate.social_runtime.cognition.service import CognitionBudget
from groupmate.social_runtime.contracts import RuntimeMode, SocialEventEnvelope
from groupmate.social_runtime.control.projections import ProjectionConsumer
from groupmate.social_runtime.control.queries import ProjectionQueries
from groupmate.social_runtime.control.stream import ProjectionStream
from groupmate.social_runtime.delivery.dispatcher import DeliveryDispatcher
from groupmate.social_runtime.delivery.outbox import OutboxService
from groupmate.social_runtime.manager import SocialRuntimeManager
from groupmate.social_runtime.persistence import event_store as event_store_module
from groupmate.social_runtime.persistence.schema import connect_database
from groupmate.social_runtime.tasks.contracts import (
    CapabilityDescriptor,
    CapabilityField,
    CapabilityRequest,
    ConfirmationPolicy,
    ProviderEvent,
    ProviderEventKind,
    RiskLevel,
    TaskStatus,
)
from groupmate.social_runtime.tasks.runtime import InvalidTaskTransition, TaskRuntime
from tests.factories import social_event_values


PERSONA = "persona-capacity"
GROUP = "fake-group-capacity"


def _message(
    message_id: str,
    *,
    received_at: int = 100,
    direct: bool = True,
) -> SocialEventEnvelope:
    return SocialEventEnvelope.create(
        **social_event_values(
            event_id=f"qq:{message_id}",
            source_message_id=message_id,
            persona_id=PERSONA,
            group_id=GROUP,
            actor_id="fake-user",
            occurred_at=received_at,
            received_at=received_at,
            correlation_id=f"corr:{message_id}",
            payload={"text": message_id, "direct_address": direct},
        )
    )


def _descriptor() -> CapabilityDescriptor:
    return CapabilityDescriptor.create(
        capability_id="fake.lookup",
        provider_id="fake.provider",
        input_schema=(CapabilityField("query", "string"),),
        output_schema=(CapabilityField("answer", "string"),),
        risk_level=RiskLevel.READ_ONLY,
        required_scopes=("lookup.read",),
        idempotent=True,
        cancellable=True,
        supports_progress=True,
        expected_latency_ms=1_000,
        media_output_kinds=(),
        confirmation_policy=ConfirmationPolicy.NEVER,
    )


def _running_task(runtime: TaskRuntime):
    task = runtime.propose(
        _descriptor(),
        CapabilityRequest.create(
            requester_id="fake-user",
            persona_id=PERSONA,
            group_id=GROUP,
            topic_id="topic-1",
            input_payload={"query": "status"},
            authorization_scopes=("lookup.read",),
            idempotency_key="fault-task",
            correlation_id="corr:fault-task",
            expires_at=200,
        ),
        now=100,
    )
    runtime.start(task.task_id, now=101)
    return runtime.start(task.task_id, now=102)


def _bundle() -> DeliveryBundle:
    part = DeliveryPart.create(
        part_id="fault-part-1",
        kind=DeliveryPartKind.TEXT,
        payload={"text": "fake only"},
        order=0,
        idempotency_key="fault-send-1",
        expires_at=300,
    )
    return DeliveryBundle.create(
        bundle_id="fault-bundle-1",
        correlation_id="corr:fault-delivery",
        persona_id=PERSONA,
        group_id=GROUP,
        topic_id="topic-1",
        parts=(part,),
        created_at=100,
        expires_at=300,
    )


def test_db_busy_rolls_back_and_same_event_retry_commits_capture_once(
    tmp_path, monkeypatch
):
    async def scenario():
        path = tmp_path / "busy.db"
        manager = SocialRuntimeManager(
            database_path=path,
            persona_id=PERSONA,
            mode=RuntimeMode.SHADOW,
            enabled_groups=(GROUP,),
        )
        await manager.start()
        lock = connect_database(path)
        lock.execute("BEGIN IMMEDIATE")
        original_connect = event_store_module.connect_database

        def connect_without_wait(database_path):
            db = original_connect(database_path)
            db.execute("PRAGMA busy_timeout=0")
            return db

        monkeypatch.setattr(
            event_store_module, "connect_database", connect_without_wait
        )
        event = _message("busy-retry")
        with pytest.raises(sqlite3.OperationalError, match="locked"):
            await manager.ingest(event)
        lock.rollback()
        lock.close()

        appended = await manager.ingest(event)
        evaluations = await manager.drain(now=100)
        captures = manager.pending_shadow_review_evidence()
        result = (
            appended,
            evaluations,
            captures,
            manager.event_store.event_ids(),
            manager.event_store.outbox_count(),
            manager.execution_port.calls,
        )
        await manager.close()
        return result

    appended, evaluations, captures, event_ids, outbox_count, calls = asyncio.run(
        scenario()
    )

    assert appended.inserted is True
    assert len(evaluations) == len(captures) == 1
    assert event_ids == ("qq:busy-retry",)
    assert outbox_count == 0
    assert calls == ()


def test_worker_timeout_and_invalid_json_fail_closed_without_delivery(tmp_path):
    class HangingModel:
        async def complete_json(self, *, schema, payload):
            del schema, payload
            await asyncio.Event().wait()

    class InvalidModel:
        async def complete_json(self, *, schema, payload):
            del schema, payload
            return {"observations": "invalid-json-shape"}

    async def run_case(name, model):
        diagnostics = []
        worker = AstrBotStructuredWorker(
            "direct_interaction", model, diagnostic_sink=diagnostics.append
        )
        manager = SocialRuntimeManager(
            database_path=tmp_path / f"{name}.db",
            persona_id=PERSONA,
            mode=RuntimeMode.SHADOW,
            enabled_groups=(GROUP,),
            cognition_workers={"direct_interaction": worker},
            cognition_budget=CognitionBudget(
                max_worker_calls=1,
                max_cost_units=1,
                worker_timeout_seconds=0.01,
            ),
        )
        await manager.start()
        await manager.ingest(_message(name))
        evaluation = (await manager.drain(now=100))[0]
        result = (
            evaluation,
            diagnostics,
            manager.pending_shadow_review_evidence(),
            manager.event_store.outbox_count(),
            manager.execution_port.calls,
        )
        await manager.close()
        return result

    timed_out = asyncio.run(run_case("timeout", HangingModel()))
    invalid = asyncio.run(run_case("invalid", InvalidModel()))

    assert timed_out[0].governor_result.outcome == "OBSERVE"
    assert invalid[0].governor_result.outcome == "SILENCE"
    assert all(
        evaluation.governor_result.outcome != "ACT"
        for evaluation in (timed_out[0], invalid[0])
    )
    assert timed_out[1] == []
    assert invalid[1] == ["invalid_worker_output"]
    assert len(timed_out[2]) == len(invalid[2]) == 1
    assert timed_out[3:] == invalid[3:] == (0, ())


def test_provider_duplicate_out_of_order_and_clock_jump_preserve_monotonic_state(
    tmp_path,
):
    runtime = TaskRuntime(tmp_path / "provider-order.db")
    running = _running_task(runtime)
    progress = ProviderEvent.create(
        event_id="provider:progress:60",
        task_id=running.task_id,
        kind=ProviderEventKind.PROGRESS,
        occurred_at=110,
        progress=60,
    )

    first = runtime.apply_event(progress)
    duplicate = runtime.apply_event(progress)
    with pytest.raises(InvalidTaskTransition, match="occurred_at"):
        runtime.apply_event(
            ProviderEvent.create(
                event_id="provider:progress:out-of-order",
                task_id=running.task_id,
                kind=ProviderEventKind.PROGRESS,
                occurred_at=105,
                progress=80,
            )
        )
    completed = runtime.apply_event(
        ProviderEvent.create(
            event_id="provider:success:clock-forward",
            task_id=running.task_id,
            kind=ProviderEventKind.SUCCEEDED,
            occurred_at=250,
            result={"answer": "late but accurate"},
        )
    )

    assert duplicate == first
    assert first.updated_at == 110
    assert completed.status is TaskStatus.SUCCEEDED
    assert completed.updated_at == 250
    assert completed.delivery_relevant is False
    assert runtime.event_count(running.task_id) == 5


def test_onebot_timeout_restart_never_replays_unknown_delivery(tmp_path):
    async def scenario():
        outbox_path = tmp_path / "onebot-unknown.db"
        outbox = OutboxService(outbox_path)
        bundle = _bundle()
        outbox.commit_bundle(bundle)
        fake_calls = []

        async def fake_onebot(**request):
            fake_calls.append(request)
            raise TimeoutError("fake transport lost after call")

        dispatcher = DeliveryDispatcher(
            outbox, OneBotDeliveryAdapter(fake_onebot, clock=lambda: 110)
        )
        unknown = await dispatcher.dispatch_next(now=101)
        restarted_outbox = OutboxService(outbox_path)
        recovered = restarted_outbox.recover_inflight(now=120)
        replay = restarted_outbox.claim_ready(now=121)

        return bundle, unknown, recovered, replay, fake_calls

    bundle, unknown, recovered, replay, fake_calls = asyncio.run(scenario())

    assert unknown.status is OutboxStatus.UNKNOWN
    assert recovered == replay == ()
    assert [call["idempotency_key"] for call in fake_calls] == [
        bundle.parts[0].idempotency_key
    ]


def test_abrupt_process_crash_reconciles_committed_shadow_capture_once(tmp_path):
    crash_worker = Path(__file__).parents[1] / "shadow_capture_crash.py"
    crashed = subprocess.run(
        [sys.executable, str(crash_worker), str(tmp_path)],
        cwd=Path(__file__).parents[2],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert crashed.returncode == 23, crashed.stderr

    raw_event = {
        "message_id": "abrupt-shadow-capture",
        "group_id": GROUP,
        "user_id": "fake-user",
        "time": 100,
        "message": [
            {"type": "text", "data": {"text": "@你 crash window"}},
            {"type": "at", "data": {"qq": "323537051"}},
        ],
    }

    async def restart():
        path = tmp_path / "groupmate-social-runtime-v2.db"
        reviews = ShadowReviewRepository(path)
        bridge = AstrBotSocialRuntimeBridge(
            object(),
            SocialRuntimeSettings.from_mapping(
                {"runtime_mode": "SHADOW", "enabled_groups": [GROUP]}
            ),
            tmp_path,
            shadow_reviews=reviews,
        )
        await bridge.start()
        pending_after_reconcile = bridge.manager.pending_shadow_review_evidence()
        duplicate = await bridge.handle_event(raw_event)
        items = reviews.list_items(persona_id="aemeath", group_id=GROUP)
        snapshot = await bridge.manager.group_snapshot(GROUP)
        no_send = (
            bridge.manager.event_store.outbox_count(),
            bridge.manager.execution_port.calls,
        )
        await bridge.close()
        return pending_after_reconcile, duplicate, items, snapshot, no_send

    pending, duplicate, items, snapshot, no_send = asyncio.run(restart())

    assert pending == ()
    assert duplicate.inserted is False
    assert len(items) == 1
    assert snapshot.scene_version == 1
    assert no_send == (0, ())


def test_sse_outage_and_projection_corruption_do_not_block_runtime_and_rebuild(
    tmp_path,
):
    async def scenario():
        path = tmp_path / "projection-outage.db"
        manager = SocialRuntimeManager(
            database_path=path,
            persona_id=PERSONA,
            mode=RuntimeMode.SHADOW,
            enabled_groups=(GROUP,),
        )
        await manager.start()
        await manager.ingest(_message("projection-1", received_at=100))
        await manager.drain(now=100)
        projection = ProjectionConsumer(path, "activity")
        projection.consume(100)
        with connect_database(path) as db:
            db.execute(
                "UPDATE control_projection_items SET summary_json='{' "
                "WHERE projection_name='activity'"
            )
            db.execute(
                "UPDATE control_projection_events SET summary_json='{' "
                "WHERE projection_name='activity'"
            )

        with pytest.raises(json.JSONDecodeError):
            ProjectionQueries(path).activity(persona_id=PERSONA, group_id=GROUP)

        stream = ProjectionStream(path)
        with pytest.raises(json.JSONDecodeError):
            stream.read(last_event_id=None, persona_id=PERSONA, group_id=GROUP)

        task = manager.task_runtime.propose(
            _descriptor(),
            CapabilityRequest.create(
                requester_id="fake-user",
                persona_id=PERSONA,
                group_id=GROUP,
                topic_id="topic-1",
                input_payload={"query": "still-running"},
                authorization_scopes=("lookup.read",),
                idempotency_key="projection-outage-task",
                correlation_id="corr:projection-outage-task",
                expires_at=300,
            ),
            now=110,
        )
        await manager.ingest(_message("projection-2", received_at=110))
        await manager.drain(now=110)
        snapshot = await manager.group_snapshot(GROUP)
        projection.rebuild("activity")
        repaired = ProjectionQueries(path).activity(
            persona_id=PERSONA, group_id=GROUP
        )
        recovered_stream = stream.read(
            last_event_id=None,
            persona_id=PERSONA,
            group_id=GROUP,
        )
        result = (
            snapshot,
            manager.task_runtime.load(task.task_id),
            repaired,
            recovered_stream,
            manager.event_store.outbox_count(),
            manager.execution_port.calls,
        )
        await manager.close()
        return result

    snapshot, task, repaired, recovered_stream, outbox_count, calls = asyncio.run(
        scenario()
    )

    assert snapshot.scene_version == 2
    assert task.status is TaskStatus.PROPOSED
    assert repaired["items"]
    assert recovered_stream.events
    assert outbox_count == 0
    assert calls == ()


def test_expired_ambient_frame_is_durably_discarded_without_cognition_or_capture(
    tmp_path,
):
    async def scenario():
        path = tmp_path / "expired-ambient.db"
        manager = SocialRuntimeManager(
            database_path=path,
            persona_id=PERSONA,
            mode=RuntimeMode.SHADOW,
            enabled_groups=(GROUP,),
        )
        await manager.start()
        await manager.ingest(
            _message("expired-ambient", received_at=100, direct=False)
        )
        evaluations = await manager.drain(now=200)
        actor = await manager.fabric.notify(PERSONA, GROUP)
        request_id = f"scene:{PERSONA}:{GROUP}:qq:expired-ambient:1"
        stored = manager.event_store.scene_work_request(actor.actor_key, request_id)
        result = (
            evaluations,
            manager.expired_attention_count,
            stored,
            manager.pending_shadow_review_evidence(),
            manager.event_store.outbox_count(),
            manager.execution_port.calls,
        )
        await manager.close()
        return result

    evaluations, expired, stored, captures, outbox_count, calls = asyncio.run(
        scenario()
    )

    assert evaluations == ()
    assert expired == 1
    assert stored.status == "stale"
    assert stored.resolution == {
        "kind": "explicit_discard",
        "reason_code": "attention_deadline_expired",
    }
    assert captures == ()
    assert outbox_count == 0
    assert calls == ()
