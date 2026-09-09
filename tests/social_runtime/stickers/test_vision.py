from __future__ import annotations

import asyncio
import struct
import time
from pathlib import Path

from groupmate.social_runtime.stickers.cognition import meaning_is_sendable
from groupmate.social_runtime.stickers.images import (
    representative_frame,
    sample_gif_frame_indices,
    sniff_mime,
    vision_data_uri,
)
from groupmate.social_runtime.stickers.vision import (
    StickerVisionWorker,
    parse_caption,
    parse_json_object,
    parse_judgment,
)
from tests.social_runtime.stickers.test_lexicon import gif_bytes, lexicon, palette_gif, png_bytes


class _FakeVision:
    def __init__(self, judgment, caption):
        self.judgment = judgment
        self.caption_payload = caption
        self.calls = []

    async def judge(self, *, image_data_uri: str):
        self.calls.append(("judge", image_data_uri[:22]))
        return self.judgment

    async def caption(self, *, image_data_uri: str):
        self.calls.append(("caption", image_data_uri[:22]))
        return self.caption_payload


async def _wait_until(predicate, timeout=1.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("vision worker did not finish in time")


def test_png_passes_through_as_vision_frame():
    content = png_bytes()
    frame, mime = representative_frame(content, "image/png")
    assert mime == "image/png"
    assert frame == content
    assert vision_data_uri(content, "image/png").startswith("data:image/png;base64,")


def test_gif_without_palette_falls_back_to_original():
    content = gif_bytes()
    frame, mime = representative_frame(content, "image/gif")
    assert sniff_mime(frame) in {"image/gif", "image/png"}
    assert mime in {"image/gif", "image/png"}


def _png_extent(content: bytes) -> tuple[int, int]:
    assert content[12:16] == b"IHDR"
    width, height = struct.unpack(">II", content[16:24])
    return width, height


def test_sample_gif_frame_indices_keeps_first_and_last():
    assert sample_gif_frame_indices(1) == (0,)
    assert sample_gif_frame_indices(4) == (0, 1, 2, 3)
    assert sample_gif_frame_indices(8) == (0, 1, 2, 4, 5, 7)
    assert sample_gif_frame_indices(8)[-1] == 7


def test_single_frame_gif_stays_one_cell():
    content = palette_gif([3])
    frame, mime = representative_frame(content, "image/gif")
    assert mime == "image/png"
    assert _png_extent(frame) == (8, 8)


def test_eight_frame_gif_becomes_six_cell_sheet():
    content = palette_gif(list(range(8)))
    frame, mime = representative_frame(content, "image/gif")
    assert mime == "image/png"
    assert _png_extent(frame) == (48, 8)
    assert vision_data_uri(content, "image/gif").startswith("data:image/png;base64,")


def test_two_frame_gif_stitches_both_cells():
    content = palette_gif([0, 7])
    frame, mime = representative_frame(content, "image/gif")
    assert mime == "image/png"
    assert _png_extent(frame) == (16, 8)


def test_lexicon_preview_keeps_original_gif(tmp_path):
    store = lexicon(tmp_path)
    content = palette_gif(list(range(8)))
    card = store.ingest(
        content,
        mime_type="image/gif",
        origin_kind="group_captured",
        license_status="group_captured",
        now=1,
    ).card
    preview = store.preview_payload(card.asset_id)
    assert preview["mime_type"] == "image/gif"
    assert preview["data_uri"].startswith("data:image/gif;base64,")
    assert representative_frame(content, "image/gif")[1] == "image/png"


def test_parse_json_object_strips_fences():
    payload = parse_json_object("```json\n{\"is_sticker\": true, \"reason\": \"摊手\"}\n```")
    judgment = parse_judgment(payload)
    assert judgment.is_sticker is True
    assert judgment.reason == "摊手"


def test_vision_rejects_photos_and_captions_stickers(tmp_path):
    store = lexicon(tmp_path)
    photo = store.ingest(
        png_bytes(18, 18),
        mime_type="image/png",
        origin_kind="group_captured",
        license_status="group_captured",
        now=1,
        skip_coarse_filter=True,
    ).card
    photo_client = _FakeVision({"is_sticker": False, "reason": "普通照片"}, {})
    sticker_client = _FakeVision(
        {"is_sticker": True, "reason": "夸张表情"},
        {
            "meaning": "摊手无奈我也没办法",
            "use_when": ["我也没办法", "做不到"],
            "do_not_use": ["认真技术帮助"],
            "attitudes": ["helpless"],
            "affection_floor": 0,
            "intensity": 30,
        },
    )

    async def scenario():
        rejector = StickerVisionWorker(store, photo_client, clock=lambda: 3)
        await rejector.start()
        await _wait_until(lambda: store.get(photo.asset_id).status == "rejected")
        await rejector.close()

        sticker = store.ingest(
            png_bytes(16, 16),
            mime_type="image/png",
            origin_kind="group_captured",
            license_status="group_captured",
            now=2,
            skip_coarse_filter=True,
        ).card
        writer = StickerVisionWorker(store, sticker_client, clock=lambda: 4)
        await writer.start()
        await _wait_until(lambda: meaning_is_sendable(store.get(sticker.asset_id).meaning))
        await writer.close()
        return sticker.asset_id

    sticker_id = asyncio.run(scenario())
    assert store.get(photo.asset_id).status == "rejected"
    card = store.get(sticker_id)
    assert card.status == "candidate"
    assert card.caption_source == "vision"
    assert card.is_sticker_judgment == "sticker"
    assert card.meaning == "摊手无奈我也没办法"
    assert card.attitudes == ("helpless",)
    assert card.use_when == ("我也没办法", "做不到")
    assert card.min_familiarity == 0


def test_handwritten_meaning_is_not_overwritten_unless_forced(tmp_path):
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
        caption_source="admin",
        now=2,
    )
    client = _FakeVision(
        {"is_sticker": True, "reason": "表情"},
        {"meaning": "得意地比耶赢了", "attitudes": ["proud"], "intensity": 80},
    )

    async def scenario():
        worker = StickerVisionWorker(store, client, clock=lambda: 3)
        await worker.start()
        assert worker.enqueue(card.asset_id) is False
        assert worker.enqueue(card.asset_id, force=True) is True
        await _wait_until(lambda: store.get(card.asset_id).caption_source == "mixed")
        await worker.close()

    asyncio.run(scenario())
    refreshed = store.get(card.asset_id)
    assert refreshed.meaning == "得意地比耶赢了"
    assert refreshed.caption_source == "mixed"


def test_captioned_gif_stays_candidate_until_admin_confirms(tmp_path):
    store = lexicon(tmp_path)
    content = palette_gif(list(range(8)))
    card = store.ingest(
        content,
        mime_type="image/gif",
        origin_kind="group_captured",
        license_status="group_captured",
        now=1,
    ).card
    client = _FakeVision(
        {"is_sticker": True, "reason": "动图表情"},
        {
            "meaning": "摊手无奈我也没办法",
            "attitudes": ["helpless"],
            "affection_floor": "至少熟悉",
            "intensity": 20,
        },
    )

    async def scenario():
        worker = StickerVisionWorker(store, client, clock=lambda: 2)
        await worker.start()
        await _wait_until(
            lambda: meaning_is_sendable(store.get(card.asset_id).meaning)
        )
        await worker.close()

    asyncio.run(scenario())
    refreshed = store.get(card.asset_id)
    assert refreshed.status == "candidate"
    assert refreshed.meaning == "摊手无奈我也没办法"
    assert refreshed.min_familiarity == 30
    assert client.calls[0] == ("judge", "data:image/png;base64,")
    assert client.calls[1][0] == "caption"


def test_parse_caption_accepts_affection_stage_labels():
    caption = parse_caption(
        {
            "meaning": "比耶得意这波稳了",
            "use_when": ["稳了", "赢了"],
            "do_not_use": ["道歉"],
            "attitudes": ["proud", "tease"],
            "affection_floor": "至少亲近",
            "intensity": 70,
        }
    )
    assert caption.meaning == "比耶得意这波稳了"
    assert caption.attitudes == ("proud", "tease")
    assert caption.min_familiarity == 55
    assert caption.intensity == 70


def test_vision_prompts_explain_gif_sheet():
    text = (
        Path(__file__).parents[3]
        / "groupmate"
        / "adapters"
        / "astrbot_vision.py"
    ).read_text(encoding="utf-8")
    assert "从左到右排列的多格" in text
    assert "不要当成多张无关图" in text
    assert "动图只根据可见内容写" not in text
    assert "拿不准但看起来就是群里拿来回一句的" in text
    assert "嘴里会带出的那句话" in text
    assert "affection_floor" in text
    assert "和attitudes无关" in text
    assert "发给陌生人会显得太亲昵" in text
    assert "amused好笑" in text
    assert "网络梗" in text
    assert "当前人格" in text
    assert "软萌" in text
    assert "能结合网络梗就结合" in text
    assert "不要硬套梗" in text
    assert "不要只写「这是一个梗」" in text
    assert "猫猫歪头蹭蹭求抱抱" in text


def test_persona_brief_uses_public_voice_not_private_canon():
    from groupmate.adapters.astrbot_vision import persona_brief_for_stickers

    brief = persona_brief_for_stickers(
        {
            "identity": {"name": "爱弥斯", "role": "群里的长期伙伴"},
            "expression": {"tone": "自然真诚", "language_habits": "口语短句"},
            "social": {"stance": "友好但有边界", "culture_adaptation": "会学本群的梗"},
            "canon": {"current_state": "不该出现在视觉提示词里"},
        },
        name="爱弥斯",
    )
    assert "爱弥斯" in brief
    assert "口语短句" in brief
    assert "不该出现" not in brief


def test_caption_prompt_includes_persona_brief():
    from groupmate.adapters.astrbot_vision import AstrBotVisionClient

    seen = {}

    class _Context:
        async def llm_generate(self, **kwargs):
            seen.update(kwargs)
            return (
                '{"meaning":"摊手无奈我也没办法","use_when":["算了"],'
                '"do_not_use":[],"attitudes":["helpless"],'
                '"affection_floor":0,"intensity":30}'
            )

    client = AstrBotVisionClient(
        _Context(),
        "vision",
        persona_brief_loader=lambda: "你是爱弥斯，口语短句。",
    )
    payload = asyncio.run(
        client.caption(image_data_uri="data:image/png;base64,QQ==")
    )
    assert "爱弥斯" in seen["prompt"]
    assert "网络梗" in seen["prompt"]
    assert payload["meaning"] == "摊手无奈我也没办法"
