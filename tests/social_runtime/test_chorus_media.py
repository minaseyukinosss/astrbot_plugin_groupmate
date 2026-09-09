from __future__ import annotations

from tests.factories import social_event_values

from groupmate.social_runtime.chorus_media import (
    ChorusMediaStore,
    attach_sticker_chorus_media,
)
from groupmate.social_runtime.contracts import SocialEventEnvelope
from groupmate.social_runtime.social_context import SceneEventFact

from tests.social_runtime.stickers.test_lexicon import gif_bytes, jpeg_with_size


def _image_event(tmp_path, content, name="one.gif"):
    path = tmp_path / name
    path.write_bytes(content)
    return SocialEventEnvelope.create(
        **social_event_values(
            event_id="qq:sticker-1",
            group_id="g1",
            actor_id="u1",
            payload={
                "bot_id": "bot-1",
                "text": "",
                "media": [{"type": "image", "file": str(path)}],
                "segments": [
                    {"type": "image", "data": {"file": str(path)}},
                ],
            },
        )
    ), path


def test_gif_sticker_is_bound_by_content_hash(tmp_path):
    store = ChorusMediaStore(tmp_path)
    content = gif_bytes()
    event, path = _image_event(tmp_path, content)
    bound = attach_sticker_chorus_media(
        event, files={str(path): content}, store=store
    )
    fact = SceneEventFact.from_event(bound)
    assert fact.sticker_sha256
    assert len(fact.sticker_sha256) == 64
    assert store.resolve(fact.sticker_sha256) is not None
    again = attach_sticker_chorus_media(
        bound, files={str(path): content}, store=store
    )
    assert again.payload["chorus_media"] == bound.payload["chorus_media"]


def test_large_jpeg_photo_is_not_bound_for_chorus(tmp_path):
    store = ChorusMediaStore(tmp_path)
    content = jpeg_with_size(4000, 3000)
    event, path = _image_event(tmp_path, content, name="photo.jpg")
    bound = attach_sticker_chorus_media(
        event, files={str(path): content}, store=store
    )
    assert "chorus_media" not in bound.payload
    assert SceneEventFact.from_event(bound).sticker_sha256 is None


def test_filename_plus_url_sticker_is_bound(tmp_path):
    store = ChorusMediaStore(tmp_path)
    content = gif_bytes()
    url = "http://127.0.0.1:3000/one.gif"
    event = SocialEventEnvelope.create(
        **social_event_values(
            event_id="qq:sticker-url",
            payload={
                "text": "",
                "segments": [
                    {
                        "type": "image",
                        "data": {"file": "one.gif", "url": url},
                    }
                ],
            },
        )
    )
    bound = attach_sticker_chorus_media(
        event, files={"one.gif": content, url: content}, store=store
    )
    assert SceneEventFact.from_event(bound).sticker_sha256
    assert store.resolve(bound.payload["chorus_media"]["sha256"]) is not None


def test_mface_is_not_bound_for_chorus(tmp_path):
    store = ChorusMediaStore(tmp_path)
    content = gif_bytes()
    path = tmp_path / "mall.gif"
    path.write_bytes(content)
    event = SocialEventEnvelope.create(
        **social_event_values(
            event_id="qq:mface-1",
            payload={
                "segments": [
                    {"type": "mface", "data": {"file": str(path)}},
                ],
            },
        )
    )
    bound = attach_sticker_chorus_media(
        event, files={str(path): content}, store=store
    )
    assert "chorus_media" not in bound.payload
