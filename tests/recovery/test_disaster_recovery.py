from __future__ import annotations

import asyncio

from groupmate.social_runtime.actions.contracts import (
    DeliveryBundle,
    DeliveryPart,
    DeliveryPartKind,
    DeliveryReceipt,
    DeliveryReceiptStatus,
    OutboxStatus,
)
from groupmate.social_runtime.contracts import SocialEventEnvelope
from groupmate.social_runtime.control.commands import (
    CommandContext,
    CommandService,
    PauseRuntime,
)
from groupmate.social_runtime.control.projections import ProjectionConsumer
from groupmate.social_runtime.delivery.dispatcher import DeliveryDispatcher
from groupmate.social_runtime.delivery.outbox import OutboxService
from groupmate.social_runtime.persistence.event_store import SQLiteSocialEventStore
from groupmate.social_runtime.readiness import ReadinessGate
from groupmate.social_runtime.recovery import backup_v2_database
from tests.factories import social_event_values


FAKE_GROUP_ID = "fake-dr-group"


def _bundle() -> DeliveryBundle:
    parts = tuple(
        DeliveryPart.create(
            part_id=f"dr-part-{index}",
            kind=DeliveryPartKind.TEXT,
            payload={"text": f"recovery message {index}"},
            order=index,
            idempotency_key=f"dr-key-{index}",
            expires_at=1_000,
        )
        for index in range(2)
    )
    return DeliveryBundle.create(
        bundle_id="dr-bundle",
        correlation_id="corr:dr-delivery",
        persona_id="aemeath",
        group_id=FAKE_GROUP_ID,
        topic_id="topic:dr",
        parts=parts,
        created_at=100,
        expires_at=1_000,
    )


class _ReceiptTransport:
    def __init__(self, status: DeliveryReceiptStatus) -> None:
        self.status = status
        self.calls: list[str] = []

    async def send(self, part):
        self.calls.append(part.part_id)
        return DeliveryReceipt.create(
            receipt_id=f"receipt:{part.part_id}",
            part_id=part.part_id,
            status=self.status,
            occurred_at=200 + len(self.calls),
            platform_message_id=(
                f"qq:{part.part_id}"
                if self.status is DeliveryReceiptStatus.SUCCESS
                else None
            ),
            error_code=(
                None
                if self.status is DeliveryReceiptStatus.SUCCESS
                else "platform_result_unknown"
            ),
        )


def test_paused_v2_database_restores_without_resending_terminal_outbox(tmp_path):
    source_path = tmp_path / "live" / "groupmate-social-runtime-v2.db"
    backup_path = tmp_path / "backup" / "groupmate-social-runtime-v2.db"
    restored_path = tmp_path / "restore" / "groupmate-social-runtime-v2.db"
    actor_key = f"group:aemeath:{FAKE_GROUP_ID}"

    store = SQLiteSocialEventStore(source_path)
    event = SocialEventEnvelope.create(
        **social_event_values(
            event_id="event:dr:1",
            group_id=FAKE_GROUP_ID,
            source_message_id="message:dr:1",
            correlation_id="corr:dr:scene",
            payload={"text": "灾难恢复演练"},
        )
    )
    store.append(event)
    claimed = store.claim(
        actor_key,
        after_sequence=0,
        limit=1,
        persona_id="aemeath",
        group_id=FAKE_GROUP_ID,
    )[0]
    store.commit(
        actor_key,
        claimed,
        (
            {
                "effect_id": "effect:dr:1",
                "kind": "group_world.updated",
                "scene_version": 1,
                "persona_id": "aemeath",
                "group_id": FAKE_GROUP_ID,
            },
        ),
    )
    store.save_snapshot(actor_key, 1, {"scene_version": 1, "focus": "topic:dr"})

    outbox = OutboxService(source_path)
    outbox.commit_bundle(_bundle())
    sent_transport = _ReceiptTransport(DeliveryReceiptStatus.SUCCESS)
    unknown_transport = _ReceiptTransport(DeliveryReceiptStatus.UNKNOWN)
    asyncio.run(DeliveryDispatcher(outbox, sent_transport).dispatch_next(now=150))
    asyncio.run(DeliveryDispatcher(outbox, unknown_transport).dispatch_next(now=151))

    commands = CommandService(
        source_path,
        persona_id="aemeath",
        group_ids=(FAKE_GROUP_ID,),
        admin_ids=("admin:dr",),
        clock=lambda: 300,
    )
    commands.execute(
        PauseRuntime(paused=True, command_id="dr:pause"),
        CommandContext(
            admin_id="admin:dr",
            persona_id="aemeath",
            group_id=FAKE_GROUP_ID,
            expected_version=0,
            reason="offline disaster recovery rehearsal",
            confirmed=True,
        ),
    )
    assert ProjectionConsumer(source_path, "activity").consume(10).applied == 3

    backup_v2_database(source_path, backup_path)
    restored_path.parent.mkdir(parents=True)
    backup_path.replace(restored_path)

    restored_store = SQLiteSocialEventStore(restored_path)
    snapshot = restored_store.load_snapshot(actor_key)
    assert snapshot is not None
    assert snapshot.version == 1
    assert snapshot.payload == {"scene_version": 1, "focus": "topic:dr"}
    assert [item.event.event_id for item in restored_store.read_events(
        0,
        restored_store.cursor(actor_key).last_sequence,
        persona_id="aemeath",
        group_id=FAKE_GROUP_ID,
    )] == ["event:dr:1"]
    assert [effect.effect_id for effect in restored_store.journal("corr:dr:scene")] == [
        "effect:dr:1"
    ]

    restored_projection = ProjectionConsumer(restored_path, "activity")
    assert restored_projection.consume(10).applied == 0
    assert restored_projection.rebuild("activity") == 3

    restored_outbox = OutboxService(restored_path)
    assert restored_outbox.outbox("dr-part-0").status is OutboxStatus.SENT
    assert restored_outbox.outbox("dr-part-1").status is OutboxStatus.UNKNOWN
    no_resend = _ReceiptTransport(DeliveryReceiptStatus.SUCCESS)
    assert asyncio.run(
        DeliveryDispatcher(restored_outbox, no_resend).dispatch_next(now=400)
    ) is None
    assert no_resend.calls == []

    readiness = ReadinessGate(
        restored_path,
        persona_id="aemeath",
        allowlisted_group_ids=(FAKE_GROUP_ID,),
    )
    assert readiness.evaluate(FAKE_GROUP_ID).passed is False
    resumed = CommandService(
        restored_path,
        persona_id="aemeath",
        group_ids=(FAKE_GROUP_ID,),
        admin_ids=("admin:dr",),
        clock=lambda: 400,
    ).execute(
        PauseRuntime(paused=False, command_id="dr:resume"),
        CommandContext(
            admin_id="admin:dr",
            persona_id="aemeath",
            group_id=FAKE_GROUP_ID,
            expected_version=1,
            reason="resume restored fake allowlisted group in no-send mode",
            confirmed=True,
        ),
    )
    assert resumed.data == {"paused": False}
