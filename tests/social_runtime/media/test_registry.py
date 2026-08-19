from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from groupmate.social_runtime.media.registry import (
    InvalidMediaAsset,
    MediaRegistry,
    UnsafeMediaPath,
)


PNG = b"\x89PNG\r\n\x1a\n" + b"groupmate-png"


def _register(registry: MediaRegistry, **overrides):
    values = {
        "filename": "wave.png",
        "content": PNG,
        "mime_type": "image/png",
        "source": "persona-pack",
        "license_status": "owned",
        "semantic_tags": ("greeting",),
        "emotion_tags": ("warm",),
        "act_tags": ("react",),
        "allowed_modes": ("social",),
        "min_familiarity": 0,
        "max_boundary_pressure": 100,
        "intensity": 1,
        "cooldown_seconds": 60,
        "enabled": True,
        "expected_sha256": hashlib.sha256(PNG).hexdigest(),
    }
    values.update(overrides)
    return registry.register(**values)


def test_register_persists_content_and_manifest_only_under_plugin_data(tmp_path):
    registry = MediaRegistry(tmp_path, max_asset_bytes=1024)

    asset = _register(registry)

    stored = (tmp_path / asset.relative_path).resolve()
    assert stored.is_relative_to(tmp_path.resolve())
    assert stored.read_bytes() == PNG
    assert (tmp_path / "persona_media" / "index.json").is_file()
    restored = MediaRegistry(tmp_path, max_asset_bytes=1024).get(asset.asset_id)
    assert restored == asset


@pytest.mark.parametrize("filename", ("../escape.png", "/tmp/escape.png", "nested/escape.png"))
def test_register_rejects_path_escape(filename, tmp_path):
    registry = MediaRegistry(tmp_path, max_asset_bytes=1024)

    with pytest.raises(UnsafeMediaPath):
        _register(registry, filename=filename)


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"content": b""}, "size"),
        ({"content": PNG * 100}, "size"),
        ({"mime_type": "text/plain"}, "MIME"),
        ({"filename": "wave.jpg"}, "extension"),
        ({"expected_sha256": "0" * 64}, "checksum"),
        ({"license_status": "unlicensed"}, "license"),
    ],
)
def test_register_rejects_invalid_media(overrides, message, tmp_path):
    registry = MediaRegistry(tmp_path, max_asset_bytes=64)

    with pytest.raises(InvalidMediaAsset, match=message):
        _register(registry, **overrides)


def test_register_deduplicates_identical_content_without_rewriting_identity(tmp_path):
    registry = MediaRegistry(tmp_path, max_asset_bytes=1024)

    first = _register(registry)
    second = _register(registry, filename="same-content.png")

    assert second.asset_id == first.asset_id
    assert Path(second.relative_path) == Path(first.relative_path)

