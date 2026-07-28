import os

import pytest

from eval.export_ingest import load_export
from eval.shadow_extract import (
    LocalIdHasher,
    extract_behavior_examples,
    load_or_create_salt,
    normalize_alias,
)
from tests.shadow_fixtures import message, write_export


def _extract(tmp_path, records):
    root = write_export(tmp_path / "export", records, target_uin="20002")
    return extract_behavior_examples(
        load_export(root, "20002"),
        LocalIdHasher(b"a" * 32),
        target_alias="小维",
    )


def _by_id(examples, message_id):
    return next(item for item in examples if item.source.message_id == message_id)


def test_explicit_reply_and_following_target_text_form_one_run(tmp_path):
    examples, reviews = _extract(
        tmp_path,
        [
            message("m1", "10001", "小维，在吗", 1000),
            message(
                "m2", "20002", "在", 2000, message_type="reply",
                reply_to="m1", reply_sender_uin="10001",
            ),
            message("m3", "20002", "刚看到", 5000),
        ],
    )

    linked = _by_id(examples, "m1")
    assert linked.observed_replied is True
    assert linked.response_run.message_count == 2
    assert linked.response_run.anchor_message_id == "m1"
    assert reviews == ()


def test_adjacent_reply_requires_one_uninterrupted_candidate(tmp_path):
    examples, reviews = _extract(
        tmp_path,
        [
            message("m1", "10001", "小维，早", 1000),
            message("m2", "20002", "早呀", 5000),
            message("m3", "10001", "第一问", 10000),
            message("m4", "10002", "第二问", 11000),
            message("m5", "20002", "我看看", 12000),
        ],
    )

    assert _by_id(examples, "m1").observed_replied is True
    assert any(item.reason == "multiple_source_candidates" for item in reviews)
    assert _by_id(examples, "m3").observed_replied is False
    assert _by_id(examples, "m3").covered_context is True
    assert _by_id(examples, "m4").covered_context is True


def test_different_explicit_anchors_split_target_runs(tmp_path):
    examples, _ = _extract(
        tmp_path,
        [
            message("m1", "10001", "问题一", 1000),
            message("m2", "10002", "问题二", 1100),
            message(
                "m3", "20002", "回答一", 2000, message_type="reply",
                reply_to="m1", reply_sender_uin="10001",
            ),
            message(
                "m4", "20002", "回答二", 2500, message_type="reply",
                reply_to="m2", reply_sender_uin="10002",
            ),
        ],
    )

    assert _by_id(examples, "m1").response_run.message_count == 1
    assert _by_id(examples, "m2").response_run.message_count == 1


def test_human_interleaving_splits_explicit_target_runs(tmp_path):
    examples, _ = _extract(
        tmp_path,
        [
            message("m1", "10001", "问题一", 1000),
            message(
                "m2", "20002", "回答一", 2000, message_type="reply",
                reply_to="m1", reply_sender_uin="10001",
            ),
            message("m3", "10002", "插话", 2500),
            message(
                "m4", "20002", "回答二", 3000, message_type="reply",
                reply_to="m3", reply_sender_uin="10002",
            ),
            message("m5", "10003", "问题三", 4000),
            message(
                "m6", "20002", "回答三", 21001, message_type="reply",
                reply_to="m5", reply_sender_uin="10003",
            ),
        ],
    )

    assert _by_id(examples, "m1").response_run.message_count == 1
    assert _by_id(examples, "m3").response_run.message_count == 1
    assert _by_id(examples, "m5").response_run.message_count == 1


def test_run_gap_splits_target_messages_even_with_same_anchor(tmp_path):
    examples, reviews = _extract(
        tmp_path,
        [
            message("m1", "10001", "问题", 1000),
            message(
                "m2", "20002", "第一段", 2000, message_type="reply",
                reply_to="m1", reply_sender_uin="10001",
            ),
            message(
                "m3", "20002", "太迟的第二段", 17001,
                message_type="reply", reply_to="m1",
                reply_sender_uin="10001",
            ),
        ],
    )

    assert _by_id(examples, "m1").response_run.message_count == 1
    assert any(item.reason == "multiple_response_runs" for item in reviews)


def test_unique_directed_candidate_can_anchor_within_sixty_seconds(tmp_path):
    examples, reviews = _extract(
        tmp_path,
        [
            message("m1", "10001", "普通消息", 1000),
            message("m2", "10002", "小维，你怎么看", 10000),
            message("m3", "20002", "我觉得可以", 50000),
        ],
    )

    assert _by_id(examples, "m2").observed_replied is True
    assert _by_id(examples, "m1").observed_replied is False
    assert reviews == ()


def test_previous_completed_exchange_does_not_compete_with_next_adjacent_source(tmp_path):
    examples, reviews = _extract(
        tmp_path,
        [
            message("m1", "10001", "第一轮", 1000),
            message("m2", "20002", "第一答", 2000),
            message("m3", "10002", "第二轮", 3000),
            message("m4", "20002", "第二答", 4000),
        ],
    )

    assert _by_id(examples, "m1").observed_replied is True
    assert _by_id(examples, "m3").observed_replied is True
    assert reviews == ()


@pytest.mark.parametrize("reference_time", [3000, 1000])
def test_missing_or_future_explicit_reference_enters_review(tmp_path, reference_time):
    records = [
        message("m1", "10001", "现有消息", reference_time),
        message(
            "m2", "20002", "回复", 2000, message_type="reply",
            reply_to=("missing" if reference_time == 1000 else "m1"),
            reply_sender_uin="10001",
        ),
    ]
    examples, reviews = _extract(tmp_path, records)

    assert all(not item.observed_replied for item in examples)
    assert len(reviews) == 1
    assert reviews[0].reason in ("missing_reply_reference", "timestamp_inversion")
    if reviews[0].reason == "missing_reply_reference":
        assert reviews[0].source_events == (
            _by_id(examples, "m1").source,
        )


def test_context_is_bounded_to_six_events(tmp_path):
    records = [message("m{}".format(i), "10001", "消息", i * 1000) for i in range(1, 9)]
    records.append(message("target", "20002", "收到", 9000))
    examples, _ = _extract(tmp_path, records)
    assert len(_by_id(examples, "m8").context) == 6


def test_local_salt_is_reused_private_and_alias_copy_is_non_mutating(tmp_path):
    salt_path = tmp_path / "results" / ".shadow-id-salt"
    first = load_or_create_salt(salt_path)
    second = load_or_create_salt(salt_path)
    assert first == second
    assert len(first) == 32
    assert os.stat(str(salt_path)).st_mode & 0o777 == 0o600
    hasher = LocalIdHasher(first)
    assert hasher.sample_id("m1") == hasher.sample_id("m1")
    assert hasher.sample_id("m1") != hasher.sample_id("m2")
    assert hasher.sender_id("u1") != hasher.sample_id("u1")
    source = "小维，在吗"
    assert normalize_alias(source, "小维", "爱弥斯") == "爱弥斯，在吗"
    assert source == "小维，在吗"


@pytest.mark.parametrize("salt", [b"short", "a" * 32, bytearray(b"a" * 32)])
def test_hasher_rejects_invalid_salt(salt):
    with pytest.raises(ValueError):
        LocalIdHasher(salt)


def test_existing_invalid_salt_fails_closed(tmp_path):
    path = tmp_path / ".salt"
    path.write_bytes(b"short")
    with pytest.raises(ValueError, match="32 bytes"):
        load_or_create_salt(path)
