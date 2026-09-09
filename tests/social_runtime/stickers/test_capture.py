from __future__ import annotations

from tests.factories import social_event_values

from groupmate.social_runtime.contracts import SocialEventEnvelope
from groupmate.social_runtime.stickers.capture import StickerCapture
from groupmate.social_runtime.stickers.contracts import CaptureOffer

from tests.social_runtime.stickers.test_lexicon import gif_bytes, jpeg_with_size, lexicon, png_bytes


def test_capture_ignores_mface_and_self(tmp_path):
    store = lexicon(tmp_path)
    capture = StickerCapture(store, enabled=True)
    skipped = capture.consider_bytes(
        CaptureOffer(
            content=gif_bytes(),
            mime_type="image/gif",
            group_id="g1",
            event_id="evt-1",
            actor_id="bot-1",
            bot_id="bot-1",
            segment_kind="image",
            now=1,
        )
    )
    assert skipped.reason == "self_message"
    mface = capture.consider_bytes(
        CaptureOffer(
            content=gif_bytes(),
            mime_type="image/gif",
            group_id="g1",
            event_id="evt-2",
            actor_id="u1",
            bot_id="bot-1",
            segment_kind="mface",
            now=1,
        )
    )
    assert mface.reason == "segment_kind"


def test_large_jpeg_photo_is_skipped_and_gif_enters_inbox(tmp_path):
    store = lexicon(tmp_path)
    capture = StickerCapture(store, enabled=True)
    photo = capture.consider_bytes(
        CaptureOffer(
            content=jpeg_with_size(4000, 3000),
            mime_type="image/jpeg",
            group_id="g1",
            event_id="evt-photo",
            actor_id="u1",
            bot_id="bot-1",
            segment_kind="image",
            now=1,
        )
    )
    gif = capture.consider_bytes(
        CaptureOffer(
            content=gif_bytes(),
            mime_type="image/gif",
            group_id="g1",
            event_id="evt-gif",
            actor_id="u1",
            bot_id="bot-1",
            segment_kind="image",
            now=2,
        )
    )
    assert photo.created is False
    assert photo.reason == "coarse_filter"
    assert gif.created is True
    assert gif.card is not None
    assert gif.card.status == "candidate"
    assert gif.card.origin_kind == "group_captured"
    assert gif.card.license_status == "group_captured"


def test_same_hash_from_event_does_not_create_a_second_card(tmp_path):
    store = lexicon(tmp_path)
    capture = StickerCapture(store, enabled=True)
    content = png_bytes()
    event = SocialEventEnvelope.create(
        **social_event_values(
            event_id="qq:sticker-1",
            group_id="g1",
            actor_id="u1",
            payload={
                "bot_id": "bot-1",
                "media": [{"type": "image", "file": "/tmp/one.png"}],
                "segments": [
                    {"type": "image", "data": {"file": "/tmp/one.png"}},
                ],
            },
        )
    )
    first = capture.consider_event(event, now=1, files={"/tmp/one.png": content})
    second = capture.consider_event(event, now=2, files={"/tmp/one.png": content})
    assert first[0].created is True
    assert second[0].created is False
    assert second[0].reason == "seen_before"
    assert store.inbox()[0].sighting_count == 2


def test_url_keyed_bytes_enter_inbox(tmp_path):
    store = lexicon(tmp_path)
    capture = StickerCapture(store, enabled=True)
    url = "https://gchat.qpic.cn/custom.gif"
    event = SocialEventEnvelope.create(
        **social_event_values(
            event_id="qq:sticker-url",
            group_id="g1",
            actor_id="u1",
            payload={
                "bot_id": "bot-1",
                "media": [{"type": "image", "file": "custom.gif", "url": url}],
                "segments": [
                    {"type": "image", "data": {"file": "custom.gif", "url": url}},
                ],
            },
        )
    )
    outcomes = capture.consider_event(event, now=1, files={url: gif_bytes()})
    assert outcomes[0].created is True
    assert store.inbox()[0].origin_kind == "group_captured"

