from dataclasses import replace

from groupmate.social_runtime.actions.contracts import (
    DeliveryBundle, DeliveryPart, DeliveryPartKind, DeliveryReceipt, DeliveryReceiptStatus,
)
from groupmate.social_runtime.contracts import SocialEventEnvelope
from groupmate.social_runtime.delivery.outbox import OutboxService
from tests.factories import social_event_values


def test_dialogue_uses_only_frozen_confirmed_receipts_and_deduplicates_echo(tmp_path):
    from groupmate.social_runtime.dialogue import DialogueContextReader

    path = tmp_path / "runtime.db"
    outbox = OutboxService(path)
    events = []
    for part_id, group_id, status in (
        ("sent", "g1", DeliveryReceiptStatus.SUCCESS),
        ("unknown", "g1", DeliveryReceiptStatus.UNKNOWN),
        ("other", "g2", DeliveryReceiptStatus.SUCCESS),
        ("future", "g1", DeliveryReceiptStatus.SUCCESS),
    ):
        part = DeliveryPart.create(
            part_id=part_id, kind=DeliveryPartKind.TEXT, order=0,
            payload={"text": "你最近玩什么？", "self_id": "bot"},
            idempotency_key=part_id, expires_at=200,
        )
        outbox.commit_bundle(DeliveryBundle.create(
            bundle_id=part_id, correlation_id=part_id, persona_id="p", group_id=group_id,
            topic_id="m0", parts=(part,), created_at=90, expires_at=200,
        ))
        outbox.claim_ready(now=100)
        outbox.record_receipt(DeliveryReceipt.create(
            receipt_id=f"r:{part_id}", part_id=part_id, status=status,
            occurred_at=101, platform_message_id=f"platform:{part_id}" if status is DeliveryReceiptStatus.SUCCESS else None,
            error_code=None if status is DeliveryReceiptStatus.SUCCESS else "send_unknown",
        ))
        if part_id != "future":
            events.append(SocialEventEnvelope.create(**social_event_values(
                event_id=f"feedback:{part_id}", event_type="delivery.sent",
                persona_id="p", group_id="g1", actor_id=None,
                occurred_at=101, received_at=101,
                payload={"part_id": part_id, "receipt_id": f"r:{part_id}"},
            )))
    echo = SocialEventEnvelope.create(**social_event_values(
        event_id="echo", persona_id="p", group_id="g1", actor_id="bot",
        source_message_id="platform:sent", occurred_at=101, received_at=101,
        payload={"text": "你最近玩什么？", "is_self": True},
    ))
    current = SocialEventEnvelope.create(**social_event_values(
        event_id="m1", persona_id="p", group_id="g1", actor_id="u1",
        occurred_at=102, received_at=102,
        payload={"text": "我在玩三角洲", "reply_to_event_id": "platform:sent"},
    ))
    command = replace(current, event_id="command", payload={"text": "private command", "social_eligible": False})
    old = replace(current, event_id="old", source_message_id="old", occurred_at=1,
                  payload={"text": "昨天的话题"})
    current = replace(current, occurred_at=500, received_at=500)
    # Receipt at 101 is explicitly referenced; unrelated old dialogue is not.
    reader = DialogueContextReader(path)
    result = reader.read("p", "g1", (old, *events, echo, command, current), ("m1",))
    assert [event.event_id for event in result] == ["feedback:sent", "m1"]
    assert result[0].actor_id == "bot"
    assert result[0].payload["origin_kind"] == "BOT_TEXT"
    assert result[0].payload["delivery_confirmed"] is True
    assert result[0].payload["current_dialogue_session"] is False
    assert result[1].payload["reply_to_event_id"] == "feedback:sent"
    assert outbox.count() == 4
    assert events[0].payload.get("text") is None  # source snapshot was not mutated

    recent = replace(current, occurred_at=160, received_at=160)
    result = reader.read("p", "g1", (*events, recent), ("m1",))
    assert result[0].payload["current_dialogue_session"] is True

    # Required chorus evidence cannot be displaced by newer background turns.
    chorus_start = replace(current, event_id="chorus-start", source_message_id="chorus-start",
                           actor_id="u2", occurred_at=480, payload={"text": "好耶"})
    background = tuple(replace(current, event_id=f"bg:{i}", source_message_id=f"bg:{i}",
                               actor_id="u3", occurred_at=481 + i, payload={"text": "路过"})
                       for i in range(18))
    result = reader.read("p", "g1", (chorus_start, *background, current), ("m1",),
                         required_context_event_ids=("chorus-start",))
    assert len(result) == 16
    assert {"chorus-start", "m1"} <= {event.event_id for event in result}


def _turn(event_id, actor_id, text, occurred_at, **payload):
    return SocialEventEnvelope.create(**social_event_values(
        event_id=event_id, actor_id=actor_id, occurred_at=occurred_at,
        received_at=occurred_at, source_message_id=event_id, correlation_id=event_id,
        payload={"text": text, **payload},
    ))


def test_continue_from_uses_latest_self_turn_to_current_target_not_later_other():
    from groupmate.social_runtime.dialogue import continue_from_event_id

    owned = _turn("bot:owned", "bot", "我说的是截图里发帖的人", 100,
                  is_self=True, origin_kind="BOT_TEXT", target_id="u1")
    later_other = _turn("bot:other", "bot", "阿初怎么啦", 110,
                        is_self=True, origin_kind="BOT_TEXT", target_id="u2")
    current = _turn("user:now", "u1", "是我理解错了", 120)

    assert continue_from_event_id(
        (owned, later_other, current), target_id="u1", anchor_event_id="user:now",
    ) == "bot:owned"
    assert continue_from_event_id(
        (owned, later_other, current), target_id="u1", anchor_event_id="user:now",
        planned_id="bot:owned",
    ) == "bot:owned"
    assert continue_from_event_id(
        (later_other, current), target_id="u1", anchor_event_id="user:now",
    ) is None
    assert continue_from_event_id(
        (), target_id="u1", anchor_event_id="user:now", planned_id="bot:owned",
    ) == "bot:owned"


def test_dialogue_resolves_indexed_reply_part_to_plan(tmp_path):
    from groupmate.social_runtime.dialogue import DialogueContextReader
    from groupmate.social_runtime.replying import ReplyPlanRepository, ReplyPlanner
    from tests.social_runtime.actions.test_replying import (
        _evaluation,
        _persona_profile,
    )

    path = tmp_path / "runtime.db"
    plan = ReplyPlanner().plan(
        _evaluation(), now=100, persona_profile=_persona_profile(),
    )
    ReplyPlanRepository(path).save(plan)
    outbox = OutboxService(path)
    part = DeliveryPart.create(
        part_id=f"reply-part:{plan.plan_id}:0",
        kind=DeliveryPartKind.TEXT,
        order=0,
        payload={"text": "先这样。", "self_id": "bot"},
        idempotency_key=f"reply-send:{plan.plan_id}:0",
        expires_at=200,
    )
    outbox.commit_bundle(DeliveryBundle.create(
        bundle_id=f"reply-bundle:{plan.plan_id}",
        correlation_id=plan.correlation_id,
        persona_id=plan.persona_id,
        group_id=plan.group_id,
        topic_id=plan.topic_id,
        parts=(part,),
        created_at=90,
        expires_at=200,
    ))
    outbox.claim_ready(now=100)
    outbox.record_receipt(DeliveryReceipt.create(
        receipt_id="r:bubble0",
        part_id=part.part_id,
        status=DeliveryReceiptStatus.SUCCESS,
        occurred_at=101,
        platform_message_id="platform:bubble0",
    ))
    feedback = SocialEventEnvelope.create(**social_event_values(
        event_id="feedback:bubble0",
        event_type="delivery.sent",
        persona_id=plan.persona_id,
        group_id=plan.group_id,
        actor_id=None,
        occurred_at=101,
        received_at=101,
        payload={"part_id": part.part_id, "receipt_id": "r:bubble0"},
    ))
    current = SocialEventEnvelope.create(**social_event_values(
        event_id="m1",
        persona_id=plan.persona_id,
        group_id=plan.group_id,
        actor_id="u1",
        occurred_at=102,
        received_at=102,
        payload={"text": "嗯嗯"},
    ))
    result = DialogueContextReader(path).read(
        plan.persona_id, plan.group_id, (feedback, current), ("m1",),
    )
    assert result[0].payload["target_id"] == plan.target_id
    assert result[0].payload["text"] == "先这样。"
