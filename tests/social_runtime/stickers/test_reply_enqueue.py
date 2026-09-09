from __future__ import annotations

import asyncio
import json
from dataclasses import replace

import pytest

from groupmate.social_runtime.actions.contracts import (
    DeliveryPartKind,
    DeliveryReceipt,
    DeliveryReceiptStatus,
)
from groupmate.social_runtime.delivery.outbox import OutboxService
from groupmate.social_runtime.replying import ReplyExecutor, ReplyPlanRepository, ReplyPlanner
from groupmate.social_runtime.stickers.contracts import (
    AccompanimentDecision,
    STICKER_PACK_LIMIT,
)
from groupmate.social_runtime.stickers.select import StickerAccompanist
from tests.social_runtime.actions.test_replying import (
    _evaluation,
    _persona_profile,
    _social_decisions,
)
from tests.social_runtime.stickers.test_lexicon import lexicon, png_bytes


class _Model:
    def __init__(self, text="这次我真没办法。"):
        self.text = text
        self.system_prompt = ""

    async def complete_text(self, **kwargs):
        self.system_prompt = str(kwargs.get("system_prompt") or "")
        return json.dumps(
            {
                "text": self.text,
                "covered_fact_ids": [],
                "used_knowledge_ids": [],
                "used_memory_ids": [],
                "used_capability_ids": [],
                "source_event_ids": [],
            },
            ensure_ascii=False,
        )


def _ready_plan(tmp_path, text, *, move="GROUP_RESPONSE", name="runtime"):
    scene, stance, move_plan = _social_decisions(move=move)
    evaluation = _evaluation(text=text)
    plan = ReplyPlanner().plan(
        evaluation,
        now=100,
        persona_profile=_persona_profile(),
        scene=scene,
        stance=stance,
        move=move_plan,
    )
    plan = replace(
        plan, style=replace(plan.style, media_policy="registered_only")
    )
    database = tmp_path / f"{name}.db"
    repository = ReplyPlanRepository(database)
    outbox = OutboxService(database, bundle_authorizer=repository.authorizes_bundle)
    return plan, evaluation, repository, outbox


def test_ready_sticker_appends_one_image_after_text(tmp_path):
    store = lexicon(tmp_path)
    card = store.ingest(
        png_bytes(),
        mime_type="image/png",
        origin_kind="admin_import",
        license_status="owned",
        now=1,
        skip_coarse_filter=True,
    ).card
    store.write_cognition(
        card.asset_id,
        meaning="摊手无奈我也没办法",
        attitudes=("helpless",),
        now=2,
    )
    store.confirm(card.asset_id, now=3)
    scene, stance, move = _social_decisions(move="REFUSE")
    plan = ReplyPlanner().plan(
        _evaluation(text="帮我改密码"),
        now=100,
        persona_profile=_persona_profile(),
        scene=scene,
        stance=stance,
        move=move,
    )
    plan = replace(
        plan, style=replace(plan.style, media_policy="registered_only")
    )
    database = tmp_path / "runtime.db"
    repository = ReplyPlanRepository(database)
    outbox = OutboxService(database, bundle_authorizer=repository.authorizes_bundle)
    result = asyncio.run(
        ReplyExecutor(
            repository,
            outbox,
            _Model(),
            sticker_accompanist=StickerAccompanist(store),
        ).execute_with_result(
            plan,
            context_events=_evaluation(text="帮我改密码").context_events,
            persona_profile=_persona_profile(),
            recent_outputs=(),
        )
    )
    assert result.status == "READY"
    part = outbox.outbox(f"reply-part:{plan.plan_id}:0")
    assert part.part.kind is DeliveryPartKind.TEXT
    assert part.part.payload["text"] == "这次我真没办法"
    assert part.part.payload["sticker_id"] == card.asset_id
    assert part.part.payload["media_ref"]
    with pytest.raises(LookupError):
        outbox.outbox(f"reply-part:{plan.plan_id}:1")
    assert store.get(card.asset_id).use_count == 1


def test_no_match_keeps_text_only_bundle(tmp_path):
    class Silent:
        def decide(self, **kwargs):
            return AccompanimentDecision(None, None, "no_ready")

        def mark_used(self, *args, **kwargs):
            raise AssertionError("unused")

    scene, stance, move = _social_decisions(move="GROUP_RESPONSE")
    plan = ReplyPlanner().plan(
        _evaluation(text="哈哈好好笑"),
        now=100,
        persona_profile=_persona_profile(),
        scene=scene,
        stance=stance,
        move=move,
    )
    database = tmp_path / "runtime.db"
    repository = ReplyPlanRepository(database)
    outbox = OutboxService(database, bundle_authorizer=repository.authorizes_bundle)
    result = asyncio.run(
        ReplyExecutor(
            repository, outbox, _Model(), sticker_accompanist=Silent()
        ).execute_with_result(
            plan,
            context_events=_evaluation(text="哈哈好好笑").context_events,
            persona_profile=_persona_profile(),
            recent_outputs=(),
        )
    )
    assert result.status == "READY"
    try:
        outbox.outbox(f"reply-part:{plan.plan_id}:1")
    except LookupError:
        return
    raise AssertionError("no image part should exist")


def test_combined_text_and_sticker_is_one_claimable_part(tmp_path):
    store = lexicon(tmp_path)
    card = store.ingest(
        png_bytes(),
        mime_type="image/png",
        origin_kind="admin_import",
        license_status="owned",
        now=1,
        skip_coarse_filter=True,
    ).card
    store.write_cognition(
        card.asset_id,
        meaning="摊手无奈我也没办法",
        attitudes=("helpless",),
        now=2,
    )
    store.confirm(card.asset_id, now=3)
    scene, stance, move = _social_decisions(move="REFUSE")
    plan = ReplyPlanner().plan(
        _evaluation(text="帮我改密码"),
        now=100,
        persona_profile=_persona_profile(),
        scene=scene,
        stance=stance,
        move=move,
    )
    plan = replace(
        plan, style=replace(plan.style, media_policy="registered_only")
    )
    database = tmp_path / "runtime.db"
    repository = ReplyPlanRepository(database)
    outbox = OutboxService(database, bundle_authorizer=repository.authorizes_bundle)
    asyncio.run(
        ReplyExecutor(
            repository,
            outbox,
            _Model(),
            sticker_accompanist=StickerAccompanist(store),
        ).execute_with_result(
            plan,
            context_events=_evaluation(text="帮我改密码").context_events,
            persona_profile=_persona_profile(),
            recent_outputs=(),
        )
    )
    repository.mark(plan.plan_id, "silent")
    assert outbox.claim_ready(now=101) == ()
    repository.mark(plan.plan_id, "enqueued")
    claimed = outbox.claim_ready(now=102)
    assert len(claimed) == 1
    assert claimed[0].part.kind is DeliveryPartKind.TEXT
    assert claimed[0].part.payload["sticker_id"] == card.asset_id
    assert claimed[0].part.payload["media_ref"]
    outbox.record_receipt(
        DeliveryReceipt.create(
            receipt_id="receipt-text",
            part_id=claimed[0].part_id,
            status=DeliveryReceiptStatus.SUCCESS,
            occurred_at=102,
            platform_message_id="qq-text",
        )
    )
    assert outbox.claim_ready(now=103) == ()


def _ready_card(store, meaning, *, attitudes, width):
    card = store.ingest(
        png_bytes(width=width),
        mime_type="image/png",
        origin_kind="admin_import",
        license_status="owned",
        now=1,
        skip_coarse_filter=True,
    ).card
    store.write_cognition(
        card.asset_id,
        meaning=meaning,
        attitudes=attitudes,
        now=2,
    )
    return store.confirm(card.asset_id, now=3)


def test_sticker_ask_uses_member_text_and_attaches_to_reply(tmp_path):
    store = lexicon(tmp_path)
    wanted = _ready_card(
        store, "摊手无奈我也没办法", attitudes=("helpless",), width=42
    )
    _ready_card(store, "好的给你拿去用", attitudes=("agree",), width=44)
    plan, evaluation, repository, outbox = _ready_plan(
        tmp_path, "发个无奈的表情", move="GROUP_RESPONSE"
    )
    model = _Model("好的给你。")
    result = asyncio.run(
        ReplyExecutor(
            repository,
            outbox,
            model,
            sticker_accompanist=StickerAccompanist(store),
        ).execute_with_result(
            plan,
            context_events=evaluation.context_events,
            persona_profile=_persona_profile(),
            recent_outputs=(),
        )
    )
    part = outbox.outbox(f"reply-part:{plan.plan_id}:0")
    image = outbox.outbox(f"reply-part:{plan.plan_id}:1")
    assert result.status == "READY"
    assert part.part.kind is DeliveryPartKind.TEXT
    assert part.part.payload["text"] == "好的给你"
    assert "sticker_id" not in part.part.payload
    assert "media_ref" not in part.part.payload
    assert image.part.kind is DeliveryPartKind.IMAGE
    assert image.part.payload["sticker_id"] == wanted.asset_id
    assert "另发一条" in model.system_prompt
    with pytest.raises(LookupError):
        outbox.outbox(f"reply-part:{plan.plan_id}:2")
    assert store.get(wanted.asset_id).use_count == 1
    repository.mark(plan.plan_id, "enqueued")
    first = outbox.claim_ready(now=102)
    assert len(first) == 1
    assert first[0].part.kind is DeliveryPartKind.TEXT
    assert "sticker_id" not in first[0].part.payload
    outbox.record_receipt(
        DeliveryReceipt.create(
            receipt_id="receipt-ask-text",
            part_id=first[0].part_id,
            status=DeliveryReceiptStatus.SUCCESS,
            occurred_at=102,
            platform_message_id="qq-ask-text",
        )
    )
    second = outbox.claim_ready(now=103)
    assert len(second) == 1
    assert second[0].part.kind is DeliveryPartKind.IMAGE
    assert second[0].part.payload["sticker_id"] == wanted.asset_id


def test_sticker_pack_ask_enqueues_one_forward(tmp_path):
    store = lexicon(tmp_path)
    cards = [
        _ready_card(
            store, "闲聊表情图", attitudes=("amused",), width=50 + index
        )
        for index in range(STICKER_PACK_LIMIT + 2)
    ]
    plan, evaluation, repository, outbox = _ready_plan(
        tmp_path, "来一堆表情", move="GROUP_RESPONSE"
    )
    result = asyncio.run(
        ReplyExecutor(
            repository,
            outbox,
            _Model("给你。"),
            sticker_accompanist=StickerAccompanist(store),
        ).execute_with_result(
            plan,
            context_events=evaluation.context_events,
            persona_profile=_persona_profile(),
            recent_outputs=(),
        )
    )
    text = outbox.outbox(f"reply-part:{plan.plan_id}:0")
    forward = outbox.outbox(f"reply-part:{plan.plan_id}:1")
    assert result.status == "READY"
    assert text.part.kind is DeliveryPartKind.TEXT
    assert "sticker_id" not in text.part.payload
    assert forward.part.kind is DeliveryPartKind.FORWARD
    nodes = forward.part.payload["nodes"]
    sticker_ids = forward.part.payload["sticker_ids"]
    assert 2 <= len(nodes) <= STICKER_PACK_LIMIT
    assert len(nodes) == len(sticker_ids)
    assert len(set(sticker_ids)) == len(sticker_ids)
    assert set(sticker_ids).issubset({card.asset_id for card in cards})
    assert all(
        node["data"]["content"][0]["type"] == "image" for node in nodes
    )
    assert all(store.get(asset_id).use_count == 1 for asset_id in sticker_ids)
    with pytest.raises(LookupError):
        outbox.outbox(f"reply-part:{plan.plan_id}:2")


def test_unmatched_sticker_ask_still_replies_without_image(tmp_path):
    store = lexicon(tmp_path)
    _ready_card(store, "哈哈好好笑", attitudes=("amused",), width=46)
    plan, evaluation, repository, outbox = _ready_plan(
        tmp_path, "发个无奈的表情", move="GROUP_RESPONSE"
    )
    model = _Model("这回真给不了。")
    result = asyncio.run(
        ReplyExecutor(
            repository,
            outbox,
            model,
            sticker_accompanist=StickerAccompanist(store),
        ).execute_with_result(
            plan,
            context_events=evaluation.context_events,
            persona_profile=_persona_profile(),
            recent_outputs=(),
        )
    )
    part = outbox.outbox(f"reply-part:{plan.plan_id}:0")
    assert result.status == "READY"
    assert part.part.payload["text"] == "这回真给不了"
    assert "sticker_id" not in part.part.payload
    assert "media_ref" not in part.part.payload
    assert "发不出去" in model.system_prompt
    with pytest.raises(LookupError):
        outbox.outbox(f"reply-part:{plan.plan_id}:1")


def test_disabled_accompaniment_still_fulfills_ask(tmp_path):
    store = lexicon(tmp_path)
    card = _ready_card(
        store, "摊手无奈我也没办法", attitudes=("helpless",), width=48
    )
    accompanist = StickerAccompanist(store, enabled=False)
    asked, asked_eval, asked_repo, asked_outbox = _ready_plan(
        tmp_path, "来张表情", move="GROUP_RESPONSE", name="ask"
    )
    asked_result = asyncio.run(
        ReplyExecutor(
            asked_repo,
            asked_outbox,
            _Model("给你。"),
            sticker_accompanist=accompanist,
        ).execute_with_result(
            asked,
            context_events=asked_eval.context_events,
            persona_profile=_persona_profile(),
            recent_outputs=(),
        )
    )
    asked_part = asked_outbox.outbox(f"reply-part:{asked.plan_id}:0")
    asked_image = asked_outbox.outbox(f"reply-part:{asked.plan_id}:1")
    chat, chat_eval, chat_repo, chat_outbox = _ready_plan(
        tmp_path, "哈哈好好笑", move="GROUP_RESPONSE", name="chat"
    )
    chat_result = asyncio.run(
        ReplyExecutor(
            chat_repo,
            chat_outbox,
            _Model("好好笑。"),
            sticker_accompanist=accompanist,
        ).execute_with_result(
            chat,
            context_events=chat_eval.context_events,
            persona_profile=_persona_profile(),
            recent_outputs=(),
        )
    )
    chat_part = chat_outbox.outbox(f"reply-part:{chat.plan_id}:0")
    assert asked_result.status == "READY"
    assert asked_part.part.kind is DeliveryPartKind.TEXT
    assert "sticker_id" not in asked_part.part.payload
    assert asked_image.part.kind is DeliveryPartKind.IMAGE
    assert asked_image.part.payload["sticker_id"] == card.asset_id
    assert chat_result.status == "READY"
    assert chat_part.part.kind is DeliveryPartKind.TEXT
    assert chat_part.part.payload.get("sticker_id") is None
    assert "media_ref" not in chat_part.part.payload


def test_alias_prefixed_ask_attaches_unspecified_card(tmp_path):
    store = lexicon(tmp_path)
    card = _ready_card(
        store, "网络热梗动图拿肚皮撞人互动", attitudes=("tease",), width=70
    )
    plan, evaluation, repository, outbox = _ready_plan(
        tmp_path, "小爱来个表情包", move="GROUP_RESPONSE"
    )
    result = asyncio.run(
        ReplyExecutor(
            repository,
            outbox,
            _Model("给你。"),
            sticker_accompanist=StickerAccompanist(store),
        ).execute_with_result(
            plan,
            context_events=evaluation.context_events,
            persona_profile=_persona_profile(),
            recent_outputs=(),
        )
    )
    part = outbox.outbox(f"reply-part:{plan.plan_id}:0")
    image = outbox.outbox(f"reply-part:{plan.plan_id}:1")
    assert result.status == "READY"
    assert part.part.kind is DeliveryPartKind.TEXT
    assert "sticker_id" not in part.part.payload
    assert image.part.kind is DeliveryPartKind.IMAGE
    assert image.part.payload["sticker_id"] == card.asset_id


def test_pack_ask_sends_forward_on_firm_boundary(tmp_path):
    store = lexicon(tmp_path)
    cards = [
        _ready_card(store, "闲聊表情图", attitudes=("amused",), width=80 + index)
        for index in range(3)
    ]
    plan, evaluation, repository, outbox = _ready_plan(
        tmp_path, "多来点表情包", move="FIRM_BOUNDARY"
    )
    plan = replace(plan, style=replace(plan.style, media_policy="text_only"))
    result = asyncio.run(
        ReplyExecutor(
            repository,
            outbox,
            _Model("真发不了别再要了。"),
            sticker_accompanist=StickerAccompanist(store),
        ).execute_with_result(
            plan,
            context_events=evaluation.context_events,
            persona_profile=_persona_profile(),
            recent_outputs=(),
        )
    )
    text = outbox.outbox(f"reply-part:{plan.plan_id}:0")
    forward = outbox.outbox(f"reply-part:{plan.plan_id}:1")
    assert result.status == "READY"
    assert text.part.kind is DeliveryPartKind.TEXT
    assert forward.part.kind is DeliveryPartKind.FORWARD
    assert set(forward.part.payload["sticker_ids"]).issubset(
        {card.asset_id for card in cards}
    )
    assert 2 <= len(forward.part.payload["sticker_ids"]) <= 3
