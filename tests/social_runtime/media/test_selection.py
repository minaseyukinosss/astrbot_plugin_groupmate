from __future__ import annotations

import hashlib

import pytest

from groupmate.social_runtime.media.contracts import (
    MediaAsset,
    MediaSelectionContext,
    MediaUse,
)
from groupmate.social_runtime.media.registry import MediaSelector


CONTENT = b"\x89PNG\r\n\x1a\n" + b"persona-media"


def _asset(tmp_path, asset_id="asset-a", **overrides):
    media_dir = tmp_path / "persona_media" / "files"
    media_dir.mkdir(parents=True, exist_ok=True)
    path = media_dir / f"{asset_id}.png"
    path.write_bytes(CONTENT)
    values = {
        "asset_id": asset_id,
        "source": "persona-pack",
        "license_status": "owned",
        "mime_type": "image/png",
        "size_bytes": len(CONTENT),
        "sha256": hashlib.sha256(CONTENT).hexdigest(),
        "relative_path": str(path.relative_to(tmp_path)),
        "semantic_tags": ("greeting",),
        "emotion_tags": ("warm",),
        "act_tags": ("react",),
        "allowed_modes": ("social",),
        "min_familiarity": 10,
        "max_boundary_pressure": 20,
        "intensity": 1,
        "enabled": True,
        "cooldown_seconds": 60,
    }
    values.update(overrides)
    return MediaAsset(**values)


def _context(**overrides):
    values = {
        "now": 1000,
        "semantic_tags": ("greeting",),
        "emotion_tags": ("warm",),
        "act_tags": ("react",),
        "mode": "social",
        "familiarity": 50,
        "boundary_pressure": 0,
        "culture_tags": (),
        "recent_uses": (),
        "text_sufficient": False,
    }
    values.update(overrides)
    return MediaSelectionContext(**values)


@pytest.mark.parametrize(
    ("asset_overrides", "context_overrides", "reason"),
    [
        ({"license_status": "unlicensed"}, {}, "license_not_allowed"),
        ({"sha256": "0" * 64}, {}, "checksum_mismatch"),
        ({"enabled": False}, {}, "asset_disabled"),
        ({"min_familiarity": 80}, {}, "relationship_restricted"),
        ({"max_boundary_pressure": 5}, {"boundary_pressure": 10}, "relationship_restricted"),
        ({}, {"recent_uses": (MediaUse("asset-a", 970),)}, "asset_cooldown"),
    ],
)
def test_ineligible_media_is_never_selected(
    asset_overrides, context_overrides, reason, tmp_path
):
    selection = MediaSelector(tmp_path).select(
        (_asset(tmp_path, **asset_overrides),), _context(**context_overrides)
    )

    assert selection.selected_asset_id is None
    assert reason in selection.reason_codes


def test_text_that_already_expresses_the_semantics_suppresses_media(tmp_path):
    selection = MediaSelector(tmp_path).select(
        (_asset(tmp_path),), _context(text_sufficient=True)
    )

    assert selection.selected_asset_id is None
    assert selection.reason_codes == ("text_sufficient",)


def test_selection_uses_tag_score_and_deterministic_asset_id_tiebreak(tmp_path):
    asset_b = _asset(tmp_path, "asset-b")
    asset_a = _asset(tmp_path, "asset-a")
    irrelevant = _asset(
        tmp_path,
        "asset-z",
        semantic_tags=("farewell",),
        emotion_tags=("sad",),
        act_tags=("leave",),
    )

    selection = MediaSelector(tmp_path).select(
        (asset_b, irrelevant, asset_a), _context(culture_tags=("cookie-club",))
    )

    assert selection.selected_asset_id == "asset-a"
    assert selection.reason_codes == (
        "semantic_match",
        "emotion_match",
        "act_match",
        "deterministic_tiebreak",
    )


def test_mode_and_culture_participate_in_selection(tmp_path):
    generic = _asset(tmp_path, "asset-generic", semantic_tags=("other",))
    cultural = _asset(
        tmp_path,
        "asset-cultural",
        semantic_tags=("other",),
        emotion_tags=(),
        act_tags=(),
        allowed_modes=("social", "focused_task"),
        culture_tags=("cookie-club",),
    )

    selection = MediaSelector(tmp_path).select(
        (generic, cultural),
        _context(
            semantic_tags=(),
            emotion_tags=(),
            act_tags=(),
            mode="focused_task",
            culture_tags=("cookie-club",),
        ),
    )

    assert selection.selected_asset_id == "asset-cultural"
    assert "culture_match" in selection.reason_codes


def test_zero_tag_match_returns_no_relevant_asset(tmp_path):
    irrelevant = _asset(
        tmp_path,
        semantic_tags=("farewell",),
        emotion_tags=("sad",),
        act_tags=("leave",),
    )

    selection = MediaSelector(tmp_path).select((irrelevant,), _context())

    assert selection.selected_asset_id is None
    assert selection.reason_codes == ("no_relevant_asset",)
