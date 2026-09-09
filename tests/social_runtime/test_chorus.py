from groupmate.social_runtime.chorus import (
    ChorusDetector,
    ChorusParticipationRepository,
)
from groupmate.social_runtime.social_context import SceneEventFact


def _fact(
    event_id,
    actor_id,
    text,
    *,
    occurred_at,
    origin_kind="USER_TEXT",
    parts=("TEXT",),
    sticker_sha256=None,
):
    return SceneEventFact(
        event_id=event_id,
        actor_id=actor_id,
        text=text,
        reply_to=None,
        parts=parts,
        occurred_at=occurred_at,
        origin_kind=origin_kind,
        sticker_sha256=sticker_sha256,
    )


def test_chorus_requires_two_distinct_humans_with_the_same_short_text():
    evidence = ChorusDetector(window_seconds=45, max_chars=80).detect(
        events=(
            _fact("m1", "u1", "小林今天请客", occurred_at=100),
            _fact("m2", "u2", "小林今天请客", occurred_at=112),
        ),
        source_event_id="m2",
        group_id="g1",
        persona_actor_id="aemeath",
        joined_chain_ids=(),
    )

    assert evidence is not None
    assert evidence.payload == "小林今天请客"
    assert evidence.participant_ids == ("u1", "u2")
    assert evidence.event_ids == ("m1", "m2")


def test_single_actor_spam_and_persona_output_do_not_form_a_chorus():
    detector = ChorusDetector(window_seconds=45, max_chars=80)
    assert detector.detect(
        events=(
            _fact("m1", "u1", "复读", occurred_at=100),
            _fact("m2", "u1", "复读", occurred_at=105),
            _fact("m3", "aemeath", "复读", occurred_at=108),
        ),
        source_event_id="m2",
        group_id="g1",
        persona_actor_id="aemeath",
        joined_chain_ids=(),
    ) is None


def test_normalization_finds_chain_but_payload_remains_current_verbatim_text():
    evidence = ChorusDetector().detect(
        events=(
            _fact("m1", "u1", "小林  今天请客", occurred_at=100),
            _fact("m2", "u2", "小林   今天请客", occurred_at=105),
        ),
        source_event_id="m2",
        group_id="g1",
        persona_actor_id="aemeath",
        joined_chain_ids=(),
    )

    assert evidence is not None
    assert evidence.normalized_key == "小林 今天请客"
    assert evidence.payload == "小林   今天请客"


def test_non_user_or_structured_text_cannot_become_joinable_chorus():
    detector = ChorusDetector()
    for origin_kind, parts in (
        ("COMMAND_RESULT", ("TEXT",)),
        ("PLUGIN_RESULT", ("TEXT",)),
        ("MEDIA", ("TEXT",)),
        ("USER_TEXT", ("TEXT", "PLATFORM_MENTION")),
    ):
        assert detector.detect(
            events=(
                _fact(
                    "m1",
                    "u1",
                    "不应复读",
                    occurred_at=100,
                    origin_kind=origin_kind,
                    parts=parts,
                ),
                _fact(
                    "m2",
                    "u2",
                    "不应复读",
                    occurred_at=101,
                    origin_kind=origin_kind,
                    parts=parts,
                ),
            ),
            source_event_id="m2",
            group_id="g1",
            persona_actor_id="aemeath",
            joined_chain_ids=(),
        ) is None


def test_url_and_overlong_text_are_not_chorus_candidates():
    detector = ChorusDetector(max_chars=12)
    for text in ("https://example.com", "这句话明显超过十二个字符所以不能作为复读候选"):
        assert detector.detect(
            events=(
                _fact("m1", "u1", text, occurred_at=100),
                _fact("m2", "u2", text, occurred_at=101),
            ),
            source_event_id="m2",
            group_id="g1",
            persona_actor_id="aemeath",
            joined_chain_ids=(),
        ) is None


def test_joined_chain_state_is_group_scoped_and_persistent(tmp_path):
    path = tmp_path / "runtime.db"
    repo = ChorusParticipationRepository(path)
    repo.mark_joined("g1", "chorus:abc", joined_at=120)

    reopened = ChorusParticipationRepository(path)
    assert reopened.recent_joined_chain_ids("g1", since=60) == ("chorus:abc",)
    assert reopened.recent_joined_chain_ids("g2", since=60) == ()


def test_detector_marks_persisted_chain_as_already_joined():
    detector = ChorusDetector()
    first = detector.detect(
        events=(
            _fact("m1", "u1", "小林今天请客", occurred_at=100),
            _fact("m2", "u2", "小林今天请客", occurred_at=101),
        ),
        source_event_id="m2",
        group_id="g1",
        persona_actor_id="aemeath",
        joined_chain_ids=(),
    )
    assert first is not None

    repeated = detector.detect(
        events=(
            _fact("m1", "u1", "小林今天请客", occurred_at=100),
            _fact("m2", "u2", "小林今天请客", occurred_at=101),
        ),
        source_event_id="m2",
        group_id="g1",
        persona_actor_id="aemeath",
        joined_chain_ids=(first.chain_id,),
    )
    assert repeated is not None
    assert repeated.already_joined is True


def test_same_sticker_hash_from_two_humans_forms_a_chorus():
    digest = "a" * 64
    evidence = ChorusDetector().detect(
        events=(
            _fact(
                "m1",
                "u1",
                "",
                occurred_at=100,
                parts=("IMAGE", "MEDIA"),
                sticker_sha256=digest,
            ),
            _fact(
                "m2",
                "u2",
                "",
                occurred_at=105,
                parts=("IMAGE", "MEDIA"),
                sticker_sha256=digest,
            ),
        ),
        source_event_id="m2",
        group_id="g1",
        persona_actor_id="aemeath",
        joined_chain_ids=(),
    )

    assert evidence is not None
    assert evidence.kind == "STICKER"
    assert evidence.payload == f"sticker:{digest}"
    assert evidence.participant_ids == ("u1", "u2")


def test_photos_mall_faces_and_captioned_images_do_not_form_sticker_chorus():
    digest = "b" * 64
    detector = ChorusDetector()
    assert detector.detect(
        events=(
            _fact("m1", "u1", "", occurred_at=100, parts=("IMAGE", "MEDIA")),
            _fact("m2", "u2", "", occurred_at=101, parts=("IMAGE", "MEDIA")),
        ),
        source_event_id="m2",
        group_id="g1",
        persona_actor_id="aemeath",
        joined_chain_ids=(),
    ) is None
    assert detector.detect(
        events=(
            _fact(
                "m1",
                "u1",
                "",
                occurred_at=100,
                parts=("MFACE",),
                sticker_sha256=digest,
            ),
            _fact(
                "m2",
                "u2",
                "",
                occurred_at=101,
                parts=("MFACE",),
                sticker_sha256=digest,
            ),
        ),
        source_event_id="m2",
        group_id="g1",
        persona_actor_id="aemeath",
        joined_chain_ids=(),
    ) is None
    assert detector.detect(
        events=(
            _fact(
                "m1",
                "u1",
                "笑死",
                occurred_at=100,
                parts=("TEXT", "IMAGE"),
                sticker_sha256=digest,
            ),
            _fact(
                "m2",
                "u2",
                "笑死",
                occurred_at=101,
                parts=("TEXT", "IMAGE"),
                sticker_sha256=digest,
            ),
        ),
        source_event_id="m2",
        group_id="g1",
        persona_actor_id="aemeath",
        joined_chain_ids=(),
    ) is None
    different = detector.detect(
        events=(
            _fact(
                "m1",
                "u1",
                "",
                occurred_at=100,
                parts=("IMAGE", "MEDIA"),
                sticker_sha256="c" * 64,
            ),
            _fact(
                "m2",
                "u2",
                "",
                occurred_at=101,
                parts=("IMAGE", "MEDIA"),
                sticker_sha256="d" * 64,
            ),
        ),
        source_event_id="m2",
        group_id="g1",
        persona_actor_id="aemeath",
        joined_chain_ids=(),
    )
    assert different is None
