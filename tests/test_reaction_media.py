"""Scene-conditional reaction media selection."""

from pathlib import Path

from groupmate.core.response_act import ResponseAct
from groupmate.media.reactions import (
    LocalReactionCatalog,
    ReactionAsset,
    ReactionPolicy,
)
from groupmate.models import InteractionScene


def _touch(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"image")


def test_boundary_task_and_ambiguous_targets_never_get_decorative_media():
    policy = ReactionPolicy()

    assert not policy.allowed(
        ResponseAct.BOUNDARY, InteractionScene.SOCIAL_RESPONSE, False
    )
    assert not policy.allowed(
        ResponseAct.RECIPROCATE, InteractionScene.TASK_REQUEST, False
    )
    assert not policy.allowed(
        ResponseAct.RECIPROCATE, InteractionScene.SOCIAL_RESPONSE, True
    )


def test_only_semantic_reaction_acts_are_allowed_in_dialogue_scenes():
    policy = ReactionPolicy()

    assert policy.allowed(
        ResponseAct.RECIPROCATE, InteractionScene.SOCIAL_RESPONSE, False
    )
    assert policy.allowed(
        ResponseAct.PLAYFUL_REPLY, InteractionScene.ACTIVE_CONTINUATION, False
    )
    assert policy.allowed(
        ResponseAct.VISUAL_REACTION, InteractionScene.DIRECT_ADDRESS, False
    )
    assert not policy.allowed(
        ResponseAct.ANSWER, InteractionScene.DIRECT_ADDRESS, False
    )
    assert not policy.allowed(
        ResponseAct.PLAYFUL_REPLY,
        InteractionScene.AMBIENT_CONTRIBUTION,
        False,
    )


def test_catalog_selects_by_tags_and_excludes_recent_ids(tmp_path):
    _touch(tmp_path / "warm.png")
    _touch(tmp_path / "play.png")
    catalog = LocalReactionCatalog.from_items(
        tmp_path,
        (
            ReactionAsset("warm-1", "warm.png", ("warm", "gift"), True),
            ReactionAsset("play-1", "play.png", ("playful",), True),
        ),
    )

    assert catalog.select(("warm",), recent_ids=("warm-1",)) is None
    selected = catalog.select(("playful",), recent_ids=())

    assert selected is not None
    assert selected.locator == str((tmp_path / "play.png").resolve())
    assert selected.source == "local_reaction_catalog"
    assert selected.media_kind == "image"
    assert selected.semantic_label == "playful"
    assert selected.purpose == "decorative_reaction"
    assert selected.safety_label == "catalog_approved"


def test_catalog_selection_is_deterministic_by_media_id(tmp_path):
    _touch(tmp_path / "z.png")
    _touch(tmp_path / "a.png")
    catalog = LocalReactionCatalog.from_items(
        tmp_path,
        (
            ReactionAsset("z-last", "z.png", ("warm",), True),
            ReactionAsset("a-first", "a.png", ("warm",), True),
        ),
    )

    first = catalog.select(("warm",), recent_ids=())
    second = catalog.select(("warm",), recent_ids=())

    assert first is not None
    assert second is not None
    assert first.locator.endswith("a.png")
    assert second == first


def test_catalog_rejects_unsafe_outside_missing_and_mismatched_assets(tmp_path):
    outside = tmp_path.parent / "outside-reaction.png"
    _touch(outside)
    _touch(tmp_path / "unsafe.png")
    catalog = LocalReactionCatalog.from_items(
        tmp_path,
        (
            ReactionAsset("outside", "../outside-reaction.png", ("warm",), True),
            ReactionAsset("unsafe", "unsafe.png", ("warm",), False),
            ReactionAsset("missing", "missing.png", ("warm",), True),
        ),
    )

    assert catalog.select(("warm",), recent_ids=()) is None
    assert catalog.select(("playful",), recent_ids=()) is None


def test_reaction_assets_are_immutable_and_validate_identity():
    asset = ReactionAsset("warm-1", "warm.png", ("warm", "warm"), True)

    assert asset.tags == ("warm",)
    try:
        asset.media_id = "changed"
    except (AttributeError, TypeError):
        pass
    else:
        raise AssertionError("reaction asset unexpectedly mutable")

    try:
        ReactionAsset("../bad", "bad.png", ("warm",), True)
    except ValueError:
        pass
    else:
        raise AssertionError("unsafe media id accepted")
