from __future__ import annotations

import random

from groupmate.social_runtime.social_moves import SocialMovePlan
from groupmate.social_runtime.stances import PermissionSnapshot, StanceDecision
from groupmate.social_runtime.stickers.contracts import STICKER_PACK_LIMIT
from groupmate.social_runtime.stickers.request import StickerAsk
from groupmate.social_runtime.stickers.select import StickerAccompanist

from tests.social_runtime.stickers.test_lexicon import lexicon, png_bytes


def _stance(attitude="AMUSED"):
    return StanceDecision.create(
        attitude=attitude,
        willingness="WILLING",
        boundary="NONE",
        concession="NONE",
        effort="NORMAL",
        initiative="ALLOW",
        reason_event_ids=("m1",),
        permission=PermissionSnapshot(True, "social_reply"),
    )


def _ready(store, meaning, **overrides):
    content = png_bytes(width=overrides.pop("width", 16), height=16)
    card = store.ingest(
        content,
        mime_type="image/png",
        origin_kind="admin_import",
        license_status="owned",
        now=1,
        skip_coarse_filter=True,
    ).card
    store.write_cognition(
        card.asset_id,
        meaning=meaning,
        use_when=overrides.get("use_when", ()),
        do_not_use=overrides.get("do_not_use", ()),
        attitudes=overrides.get("attitudes", ("amused",)),
        min_familiarity=overrides.get("min_familiarity", 0),
        now=2,
    )
    return store.confirm(card.asset_id, now=3)


def test_candidate_is_never_selected(tmp_path):
    store = lexicon(tmp_path)
    store.ingest(
        png_bytes(),
        mime_type="image/png",
        origin_kind="admin_import",
        license_status="owned",
        now=1,
        skip_coarse_filter=True,
    )
    decision = StickerAccompanist(store, rng=random.Random(1)).decide(
        text="哈哈好好笑我摊手",
        move=SocialMovePlan.create(primary_move="GROUP_RESPONSE"),
        stance=_stance(),
        media_policy="registered_only",
        now=10,
        affection=0,
        boundary_pressure=0,
    )
    assert decision.asset_id is None
    assert decision.reason == "no_ready"


def test_chorus_and_focused_policy_do_not_accompany(tmp_path):
    store = lexicon(tmp_path)
    _ready(store, "摊手无奈我也没办法")
    accompanist = StickerAccompanist(store, rng=random.Random(1))
    chorus = accompanist.decide(
        text="小林今天请客",
        move=SocialMovePlan.create(
            primary_move="JOIN_CHORUS",
            realization_mode="EXACT_CHORUS",
            verbatim_payload="小林今天请客",
            chorus_chain_id="chorus:1",
        ),
        stance=_stance(),
        media_policy="text_only",
        now=10,
        affection=0,
        boundary_pressure=0,
    )
    assert chorus.reason in {"ineligible_move", "media_forbidden"}
    assert chorus.asset_id is None


def test_do_not_use_and_relationship_gate_exclude_cards(tmp_path):
    store = lexicon(tmp_path)
    blocked = _ready(
        store,
        "认真纠错时不要用的得意脸",
        do_not_use=("纠错",),
        attitudes=("proud",),
        width=18,
    )
    ok = _ready(
        store,
        "摊手无奈我也没办法",
        attitudes=("helpless",),
        width=20,
    )
    accompanist = StickerAccompanist(store, rng=random.Random(2))
    decision = accompanist.decide(
        text="这次纠错我摊手没办法",
        move=SocialMovePlan.create(primary_move="REFUSE"),
        stance=_stance("NEUTRAL"),
        media_policy="registered_only",
        now=10,
        affection=0,
        boundary_pressure=0,
    )
    assert decision.asset_id == ok.asset_id
    assert decision.asset_id != blocked.asset_id


def test_random_pick_among_eligible_cards(tmp_path):
    store = lexicon(tmp_path)
    one = _ready(store, "摊手无奈我也没办法", attitudes=("helpless",), width=22)
    two = _ready(store, "摊手表示真没办法", attitudes=("helpless",), width=24)
    seen = set()
    for seed in range(12):
        decision = StickerAccompanist(store, rng=random.Random(seed)).decide(
            text="我摊手没办法啊",
            move=SocialMovePlan.create(primary_move="REFUSE"),
            stance=_stance("NEUTRAL"),
            media_policy="registered_only",
            now=50,
            affection=0,
            boundary_pressure=0,
        )
        seen.add(decision.asset_id)
    assert seen == {one.asset_id, two.asset_id}


def test_affection_floor_follows_public_relationship_stage(tmp_path):
    store = lexicon(tmp_path)
    close_only = _ready(
        store,
        "摊手无奈我也没办法",
        attitudes=("helpless",),
        min_familiarity=55,
        width=26,
    )
    accompanist = StickerAccompanist(store, rng=random.Random(3))
    stranger = accompanist.decide(
        text="我摊手没办法啊",
        move=SocialMovePlan.create(primary_move="REFUSE"),
        stance=_stance("NEUTRAL"),
        media_policy="registered_only",
        now=10,
        affection=0,
        boundary_pressure=0,
    )
    close = accompanist.decide(
        text="我摊手没办法啊",
        move=SocialMovePlan.create(primary_move="REFUSE"),
        stance=_stance("NEUTRAL"),
        media_policy="registered_only",
        now=11,
        affection=60,
        boundary_pressure=0,
    )
    assert stranger.asset_id is None
    assert stranger.reason == "no_match"
    assert close.asset_id == close_only.asset_id


def _fulfill(accompanist, *, query, pack=False, specified=False, move="GROUP_RESPONSE"):
    return accompanist.fulfill(
        ask=StickerAsk(query=query, pack=pack, specified=specified),
        move=SocialMovePlan.create(primary_move=move),
        stance=_stance("NEUTRAL"),
        media_policy="registered_only",
        now=10,
        affection=0,
        boundary_pressure=0,
    )


def test_fulfill_unspecified_picks_a_ready_card(tmp_path):
    store = lexicon(tmp_path)
    one = _ready(store, "摊手无奈我也没办法", attitudes=("helpless",), width=28)
    two = _ready(store, "哈哈好好笑", attitudes=("amused",), width=30)
    seen = set()
    for seed in range(16):
        cards, reason = _fulfill(
            StickerAccompanist(store, rng=random.Random(seed)),
            query="来张表情",
        )
        assert reason == "fulfilled"
        assert len(cards) == 1
        seen.add(cards[0].asset_id)
    assert seen == {one.asset_id, two.asset_id}


def test_fulfill_specified_query_filters_by_meaning(tmp_path):
    store = lexicon(tmp_path)
    helpless = _ready(
        store, "摊手无奈我也没办法", attitudes=("helpless",), width=32
    )
    _ready(store, "哈哈好好笑", attitudes=("amused",), width=34)
    cards, reason = _fulfill(
        StickerAccompanist(store, rng=random.Random(1)),
        query="无奈",
        specified=True,
        move="REFUSE",
    )
    assert reason == "fulfilled"
    assert [card.asset_id for card in cards] == [helpless.asset_id]


def test_fulfill_pack_caps_unique_hashes(tmp_path):
    store = lexicon(tmp_path)
    cards = [
        _ready(store, "闲聊表情图", attitudes=("amused",), width=40 + index)
        for index in range(STICKER_PACK_LIMIT + 1)
    ]
    picked, reason = _fulfill(
        StickerAccompanist(store, rng=random.Random(4)),
        query="来一堆表情",
        pack=True,
    )
    assert reason == "fulfilled"
    assert len(picked) == STICKER_PACK_LIMIT
    assert len({card.sha256 for card in picked}) == STICKER_PACK_LIMIT
    assert {card.asset_id for card in picked}.issubset(
        {card.asset_id for card in cards}
    )


def test_fulfill_specified_ignores_move_attitude_bonus(tmp_path):
    store = lexicon(tmp_path)
    _ready(store, "哈哈好好笑", attitudes=("amused",), width=38)
    cards, reason = _fulfill(
        StickerAccompanist(store, rng=random.Random(1)),
        query="无奈",
        specified=True,
    )
    assert cards == ()
    assert reason == "no_match"


def test_fulfill_ignores_accompaniment_switch(tmp_path):
    store = lexicon(tmp_path)
    card = _ready(store, "摊手无奈我也没办法", attitudes=("helpless",), width=36)
    accompanist = StickerAccompanist(store, enabled=False, rng=random.Random(1))
    picked, reason = _fulfill(accompanist, query="来张表情")
    disabled = accompanist.decide(
        text="我摊手没办法啊",
        move=SocialMovePlan.create(primary_move="REFUSE"),
        stance=_stance("NEUTRAL"),
        media_policy="registered_only",
        now=10,
        affection=0,
        boundary_pressure=0,
    )
    assert reason == "fulfilled"
    assert [item.asset_id for item in picked] == [card.asset_id]
    assert disabled.reason == "disabled"
    assert disabled.asset_id is None


def test_fulfill_pack_survives_firm_boundary_text_only(tmp_path):
    store = lexicon(tmp_path)
    one = _ready(store, "闲聊表情图", attitudes=("amused",), width=60)
    two = _ready(store, "闲聊表情图二", attitudes=("tease",), width=62)
    picked, reason = StickerAccompanist(store, rng=random.Random(1)).fulfill(
        ask=StickerAsk(query="多来点表情包", pack=True, specified=False),
        move=SocialMovePlan.create(primary_move="FIRM_BOUNDARY"),
        stance=_stance("IRRITATED"),
        media_policy="text_only",
        now=10,
        affection=2.4,
        boundary_pressure=0,
    )
    assert reason == "fulfilled"
    assert {card.asset_id for card in picked} == {one.asset_id, two.asset_id}
