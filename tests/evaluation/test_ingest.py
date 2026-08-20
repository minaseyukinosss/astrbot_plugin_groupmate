from __future__ import annotations

import json
import stat

import pytest

from eval.build_corpus import (
    _IdentityMapping,
    _scene_context_start,
    _safe_text,
    build_candidate_corpus,
    build_review_queue,
)
from eval.export_ingest import ingest_export
from eval.ownership import ReferenceTriggerPolicy


def _write_export(tmp_path, messages):
    chunks = tmp_path / "chunks"
    chunks.mkdir(parents=True)
    chunk = chunks / "c000001.jsonl"
    chunk.write_text(
        "".join(
            json.dumps(message, ensure_ascii=False) + "\n" for message in messages
        ),
        encoding="utf-8",
    )
    (tmp_path / "manifest.json").write_text(
        json.dumps(
            {
                "chatInfo": {"type": "group", "peerUid": "group-7"},
                "chunked": {
                    "chunks": [
                        {
                            "index": 1,
                            "relativePath": "chunks/c000001.jsonl",
                        }
                    ]
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def _message(
    message_id,
    timestamp,
    sender_uin,
    *,
    elements,
    recalled=False,
    extra=None,
):
    value = {
        "id": message_id,
        "seq": str(timestamp),
        "timestamp": timestamp,
        "sender": {
            "uid": f"uid-{sender_uin}",
            "uin": sender_uin,
            "name": f"name-{sender_uin}",
            "groupCard": f"card-{sender_uin}",
        },
        "type": "reply" if any(item["type"] == "reply" for item in elements) else "text",
        "content": {
            "text": "".join(
                str(item["data"].get("text") or "")
                for item in elements
                if item["type"] == "text"
            ),
            "elements": elements,
            "mentions": [],
            "resources": [],
        },
        "recalled": recalled,
        "system": False,
    }
    if extra is not None:
        value["future_extension"] = extra
    return value


def test_ingest_preserves_qq_facts_and_identifies_target_bot(tmp_path):
    member = _message(
        "m2",
        2_000,
        "42",
        recalled=True,
        elements=[
            {
                "type": "reply",
                "data": {"messageId": "m1", "referencedMessageId": "m1"},
            },
            {
                "type": "at",
                "data": {"uid": "uid-323537051", "uin": "323537051", "name": "bot"},
            },
            {"type": "text", "data": {"text": "看看"}},
            {
                "type": "image",
                "data": {"filename": "image.jpg", "url": "https://example/image.jpg"},
            },
        ],
        extra={"opaque": 1},
    )
    bot = _message(
        "m1",
        1_000,
        "323537051",
        elements=[{"type": "text", "data": {"text": "在"}}],
    )
    _write_export(tmp_path, [member, bot, dict(member)])

    events = list(ingest_export(tmp_path))

    assert [event.event_id for event in events] == ["qq:m1", "qq:m2", "qq:m2"]
    assert [event.occurred_at for event in events] == [1, 2, 2]
    assert events[0].payload["is_self"] is True
    assert events[1].group_id == "group-7"
    assert events[1].actor_id == "42"
    assert events[1].source_message_id == "m2"
    assert events[1].causation_id == "qq:m1"
    assert events[1].payload["text"] == "看看"
    assert events[1].payload["mentions"] == ["323537051"]
    assert events[1].payload["mentions_bot"] is True
    assert events[1].payload["recalled"] is True
    assert events[1].payload["source_timestamp_ms"] == 2_000
    assert events[1].payload["media"] == [
        {
            "type": "image",
            "filename": "image.jpg",
            "url": "https://example/image.jpg",
        }
    ]
    assert len(events[1].payload["raw_evidence_hash"]) == 64


def test_unknown_export_fields_participate_in_raw_evidence_hash(tmp_path):
    first = _message(
        "m1",
        1_000,
        "42",
        elements=[{"type": "text", "data": {"text": "同一条"}}],
        extra={"opaque": 1},
    )
    second = dict(first)
    second["future_extension"] = {"opaque": 2}
    _write_export(tmp_path, [first, second])

    events = list(ingest_export(tmp_path))

    assert events[0].payload["text"] == events[1].payload["text"]
    assert (
        events[0].payload["raw_evidence_hash"]
        != events[1].payload["raw_evidence_hash"]
    )


def test_ingest_rejects_non_group_exports(tmp_path):
    _write_export(tmp_path, [])
    manifest_path = tmp_path / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["chatInfo"]["type"] = "friend"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    try:
        list(ingest_export(tmp_path))
    except ValueError as exc:
        assert str(exc) == "evaluation ingest requires a group export"
    else:
        raise AssertionError("non-group export was accepted")


def test_candidate_corpus_removes_real_identifiers_but_keeps_context(tmp_path):
    export_path = tmp_path / "export"
    first = _message(
        "real-message-1",
        1_000,
        "323537051",
        elements=[{"type": "text", "data": {"text": "在 https://private.example/a"}}],
    )
    second = _message(
        "real-message-2",
        2_000,
        "42",
        elements=[
            {
                "type": "reply",
                "data": {
                    "messageId": "real-message-1",
                    "referencedMessageId": "real-message-1",
                },
            },
            {"type": "at", "data": {"uin": "323537051", "name": "bot"}},
            {"type": "text", "data": {"text": "name-323537051 你看 323537051"}},
        ],
    )
    _write_export(export_path, [first, second])
    events = list(ingest_export(export_path))
    corpus_path = tmp_path / "target_candidates.jsonl"
    mapping_path = tmp_path / "private" / "identity-mapping.json"

    summary = build_candidate_corpus(
        events,
        output_path=corpus_path,
        mapping_path=mapping_path,
        context_size=2,
    )

    corpus_text = corpus_path.read_text(encoding="utf-8")
    records = [json.loads(line) for line in corpus_text.splitlines()]
    mapping_text = mapping_path.read_text(encoding="utf-8")
    assert summary.candidate_count == 2
    assert len(records[1]["context"]) == 2
    assert records[0]["context"][0]["actor_id"] == "bot:target"
    assert records[1]["context"][1]["actor_id"] == "member:001"
    assert records[1]["context"][1]["reply_to"] == "message:000001"
    assert records[1]["context"][1]["mentions"] == ["bot:target"]
    assert records[1]["focus_event_id"] == "message:000002"
    for private_value in (
        "group-7",
        "323537051",
        "real-message-1",
        "real-message-2",
        "name-323537051",
        "https://private.example/a",
    ):
        assert private_value not in corpus_text
    assert "323537051" in mapping_text
    assert stat.S_IMODE(mapping_path.stat().st_mode) == 0o600


def test_ingest_rejects_chunks_outside_the_export_root(tmp_path):
    outside = tmp_path / "outside.jsonl"
    outside.write_text(
        json.dumps(
            _message(
                "m1",
                1_000,
                "42",
                elements=[{"type": "text", "data": {"text": "private"}}],
            )
        )
        + "\n",
        encoding="utf-8",
    )
    export_path = tmp_path / "export"
    export_path.mkdir()
    (export_path / "manifest.json").write_text(
        json.dumps(
            {
                "chatInfo": {"type": "group", "peerUid": "group-7"},
                "chunked": {
                    "chunks": [
                        {"index": 1, "relativePath": "../outside.jsonl"}
                    ]
                },
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="chunk path must stay inside export root"):
        list(ingest_export(export_path))


def test_group_export_directory_supplies_missing_manifest_peer(tmp_path):
    export_path = tmp_path / "group_885617919_20260727_100415_chunked_jsonl"
    _write_export(
        export_path,
        [
            _message(
                "m1",
                1_000,
                "42",
                elements=[{"type": "text", "data": {"text": "早"}}],
            )
        ],
    )
    manifest_path = export_path / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    del manifest["chatInfo"]["peerUid"]
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    event = next(ingest_export(export_path))

    assert event.group_id == "885617919"


def test_unresolved_reply_evidence_does_not_fabricate_causation(tmp_path):
    reply = _message(
        "m2",
        2_000,
        "42",
        elements=[
            {
                "type": "reply",
                "data": {
                    "messageId": "0",
                    "referencedMessageId": None,
                    "senderUin": "7",
                    "timestamp": 1_000,
                    "content": "上一条",
                },
            },
            {"type": "text", "data": {"text": "收到"}},
        ],
    )
    _write_export(tmp_path, [reply])

    event = next(ingest_export(tmp_path))

    assert event.causation_id is None
    assert event.payload["reply_to"] is None
    assert event.payload["reply_evidence"] == {
        "resolved": False,
        "message_id": "0",
        "referenced_message_id": None,
        "sender_id": "7",
        "timestamp": 1_000,
        "content": "上一条",
    }


def test_system_record_is_not_misclassified_as_member_message(tmp_path):
    system = _message(
        "m1",
        1_000,
        "42",
        elements=[{"type": "text", "data": {"text": "群系统事件"}}],
    )
    system["type"] = "system"
    system["system"] = False
    _write_export(tmp_path, [system])

    event = next(ingest_export(tmp_path))

    assert event.event_type == "platform.system"
    assert event.payload["system"] is True


def test_candidate_corpus_keeps_unresolved_reply_preview_without_private_ids(
    tmp_path,
):
    reply = _message(
        "m2",
        2_000,
        "42",
        elements=[
            {
                "type": "reply",
                "data": {
                    "messageId": "0",
                    "referencedMessageId": None,
                    "senderUin": "323537051",
                    "timestamp": 1_000,
                    "content": "323537051 发的上一条",
                },
            },
            {"type": "text", "data": {"text": "收到"}},
        ],
    )
    _write_export(tmp_path, [reply])
    corpus_path = tmp_path / "candidates.jsonl"

    build_candidate_corpus(
        ingest_export(tmp_path),
        output_path=corpus_path,
        mapping_path=tmp_path / "private" / "mapping.json",
    )

    event = json.loads(corpus_path.read_text(encoding="utf-8"))["context"][0]
    assert event["reply_evidence"] == {
        "resolved": False,
        "sender_id": "bot:target",
        "age_ms": 1_000,
        "content": "[bot:target] 发的上一条",
    }
    assert "323537051" not in corpus_path.read_text(encoding="utf-8")


def test_candidate_corpus_uses_stable_distinct_aliases_inside_text(tmp_path):
    first = _message(
        "m1",
        1_000,
        "42",
        elements=[
            {
                "type": "text",
                "data": {
                    "text": (
                        "card-42 说 card-42 会联系 card-43，号码 "
                        "12345678 还是 12345678"
                    )
                },
            }
        ],
    )
    second = _message(
        "m2",
        2_000,
        "43",
        elements=[{"type": "text", "data": {"text": "收到"}}],
    )
    _write_export(tmp_path, [first, second])
    corpus_path = tmp_path / "candidates.jsonl"

    build_candidate_corpus(
        ingest_export(tmp_path),
        output_path=corpus_path,
        mapping_path=tmp_path / "private" / "mapping.json",
        context_size=2,
    )

    records = [json.loads(line) for line in corpus_path.read_text().splitlines()]
    text = records[-1]["context"][0]["text"]
    assert text == (
        "[name:000001] 说 [name:000001] 会联系 [name:000002]，号码 "
        "[number:000001] 还是 [number:000001]"
    )
    assert "[identity]" not in corpus_path.read_text()


def test_text_alias_tokens_survive_large_identity_mappings(tmp_path):
    mapping = _IdentityMapping(tmp_path / "mapping.json")
    replacements = tuple(
        (f"absent-value-{index}", f"[name:{index:06d}]")
        for index in range(10_000)
    ) + (("private-person", "[name:010001]"),)

    result = _safe_text("private-person", replacements, mapping)

    assert result == "[name:010001]"


def _review_messages():
    messages = []
    for index in range(16):
        timestamp = index * 10_000 + 1_000
        messages.append(
            _message(
                f"member-{index}",
                timestamp,
                str(index + 10),
                elements=[
                    {"type": "text", "data": {"text": f"成员消息 {index}"}}
                ],
            )
        )
        if index % 2 == 0:
            messages.append(
                _message(
                    f"bot-{index}",
                    timestamp + 500,
                    "323537051",
                    elements=[
                        {"type": "text", "data": {"text": f"历史回复 {index}"}}
                    ],
                )
            )
    return messages


def test_review_queue_balances_signals_without_calling_them_labels(tmp_path):
    _write_export(tmp_path, _review_messages())
    output = tmp_path / "review.jsonl"

    summary = build_review_queue(
        ingest_export(tmp_path),
        output_path=output,
        mapping_path=tmp_path / "private" / "mapping.json",
        per_split=4,
        context_size=3,
        response_window_ms=2_000,
    )

    records = [json.loads(line) for line in output.read_text().splitlines()]
    assert summary.calibration_count == 4
    assert summary.holdout_count == 4
    assert len({item["scenario_id"] for item in records}) == 8
    assert {
        (item["split"], item["selection_signal"])
        for item in records
    } == {
        ("calibration", "historical_bot_action"),
        ("calibration", "historical_silence"),
        ("holdout", "historical_bot_action"),
        ("holdout", "historical_silence"),
    }
    assert all(item["status"] == "needs_human_review" for item in records)
    assert all(item["label"] is None for item in records)
    assert all(1 <= len(item["context"]) <= 3 for item in records)
    assert all(
        item["context"][-1]["event_id"] == item["focus_event_id"]
        for item in records
    )
    assert all(
        item["scene"] == {
            "boundary": "time_gap_or_reply_chain",
            "history_event_count": len(item["context"]) - 1,
            "max_context_events": 3,
            "max_idle_gap_ms": 300_000,
        }
        for item in records
    )


def test_bootstrap_review_profile_selects_direct_actions_and_ambient_silence(
    tmp_path,
):
    messages = []
    for index in range(8):
        timestamp = index * 10_000 + 1_000
        if index % 2 == 0:
            messages.extend(
                [
                    _message(
                        f"direct-{index}",
                        timestamp,
                        str(index + 10),
                        elements=[
                            {
                                "type": "at",
                                "data": {
                                    "uid": "uid-323537051",
                                    "uin": "323537051",
                                    "name": "bot",
                                },
                            },
                            {"type": "text", "data": {"text": "明确问机器人"}},
                        ],
                    ),
                    _message(
                        f"bot-{index}",
                        timestamp + 500,
                        "323537051",
                        elements=[{"type": "text", "data": {"text": "历史回应"}}],
                    ),
                ]
            )
        else:
            messages.append(
                _message(
                    f"ambient-{index}",
                    timestamp,
                    str(index + 10),
                    elements=[{"type": "text", "data": {"text": "普通群聊"}}],
                )
            )
    _write_export(tmp_path, messages)
    output = tmp_path / "bootstrap.jsonl"

    summary = build_review_queue(
        ingest_export(tmp_path),
        output_path=output,
        mapping_path=tmp_path / "private" / "mapping.json",
        per_split=2,
        context_size=3,
        response_window_ms=2_000,
        selection_profile="bootstrap_clear",
    )

    records = [json.loads(line) for line in output.read_text().splitlines()]
    assert summary.calibration_count == 2
    assert summary.holdout_count == 2
    assert all(item["selection_profile"] == "bootstrap_clear" for item in records)
    for item in records:
        if item["selection_signal"] == "historical_bot_action":
            assert "direct_mention" in item["observable_tags"]
        else:
            assert item["selection_signal"] == "historical_silence"
            assert item["observable_tags"] == ["text"]


def test_reference_trigger_policy_is_explicit_and_does_not_guess_substrings():
    policy = ReferenceTriggerPolicy.create(
        command_prefixes={"xw": "reference:waves", "bq": "reference:sticker"},
        link_domains={"v.douyin.com": "reference:video-parser"},
    )

    assert policy.classify("  xw帮助").capability_hint == "reference:waves"
    assert policy.classify("bq 摸头").capability_hint == "reference:sticker"
    assert (
        policy.classify("看看 https://v.douyin.com/example").capability_hint
        == "reference:video-parser"
    )
    assert policy.classify("我们在讨论 xw帮助") is None
    assert policy.classify("xwindow 怎么配置") is None
    assert ReferenceTriggerPolicy.create().classify("xw帮助") is None

    fixed = ReferenceTriggerPolicy.create(
        command_prefixes={"小维审核$": "reference:moderation"}
    )
    assert fixed.classify("小维审核") is not None
    assert fixed.classify("小维审核一下") is None


def test_bootstrap_excludes_external_feature_focus_but_keeps_annotated_context(
    tmp_path,
):
    def direct(message_id, timestamp, sender, text):
        return _message(
            message_id,
            timestamp,
            sender,
            elements=[
                {
                    "type": "at",
                    "data": {
                        "uid": "uid-323537051",
                        "uin": "323537051",
                        "name": "bot",
                    },
                },
                {"type": "text", "data": {"text": text}},
            ],
        )

    messages = []
    for base, prefix in ((0, "xw"), (100_000, "bq")):
        messages.extend(
            [
                direct(f"external-{base}", base + 1_000, "10", f"{prefix}帮助"),
                _message(
                    f"external-bot-{base}",
                    base + 1_500,
                    "323537051",
                    elements=[{"type": "image", "data": {"filename": "result.png"}}],
                ),
                direct(f"social-{base}", base + 10_000, "11", "早上好"),
                _message(
                    f"social-bot-{base}",
                    base + 10_500,
                    "323537051",
                    elements=[{"type": "text", "data": {"text": "早呀"}}],
                ),
                _message(
                    f"ambient-{base}",
                    base + 20_000,
                    "12",
                    elements=[{"type": "text", "data": {"text": "普通群聊"}}],
                ),
            ]
        )
    _write_export(tmp_path, messages)
    output = tmp_path / "bootstrap.jsonl"
    policy = ReferenceTriggerPolicy.create(
        command_prefixes={"xw": "reference:waves", "bq": "reference:sticker"}
    )

    summary = build_review_queue(
        ingest_export(tmp_path),
        output_path=output,
        mapping_path=tmp_path / "private" / "mapping.json",
        per_split=2,
        context_size=5,
        response_window_ms=2_000,
        selection_profile="bootstrap_clear",
        reference_trigger_policy=policy,
    )

    records = [json.loads(line) for line in output.read_text().splitlines()]
    assert (summary.calibration_count, summary.holdout_count) == (2, 2)
    assert all(
        item["context"][-1]["reference_interaction_origin"] == "UNCLASSIFIED"
        for item in records
    )
    annotated = [
        event
        for item in records
        for event in item["context"]
        if event.get("reference_interaction_origin")
        == "REFERENCE_EXTERNAL_TRIGGER"
    ]
    assert annotated
    assert all(event["social_evaluation_eligible"] is False for event in annotated)
    assert {event["reference_capability_hint"] for event in annotated} <= {
        "reference:waves",
        "reference:sticker",
    }
    assert all(
        "does_not_imply_target_installation" in event["ownership_note"]
        for event in annotated
    )


def test_review_scene_stops_at_idle_gap_instead_of_padding_to_fixed_size(tmp_path):
    _write_export(tmp_path, _review_messages())
    output = tmp_path / "review.jsonl"

    build_review_queue(
        ingest_export(tmp_path),
        output_path=output,
        mapping_path=tmp_path / "private" / "mapping.json",
        per_split=4,
        context_size=6,
        response_window_ms=2_000,
        scene_gap_ms=2_000,
    )

    records = [json.loads(line) for line in output.read_text().splitlines()]
    assert len(records) == 8
    assert all(len(item["context"]) == 1 for item in records)
    assert all(item["scene"]["history_event_count"] == 0 for item in records)
    assert all(item["scene"]["max_idle_gap_ms"] == 2_000 for item in records)


def test_review_scene_keeps_resolved_reply_anchor_across_idle_gap(tmp_path):
    messages = [
        _message(
            "m1",
            1_000,
            "10",
            elements=[{"type": "text", "data": {"text": "先前问题"}}],
        ),
        _message(
            "m2",
            20_000,
            "11",
            elements=[
                {
                    "type": "reply",
                    "data": {"messageId": "m1", "referencedMessageId": "m1"},
                },
                {"type": "text", "data": {"text": "针对这条补充"}},
            ],
        ),
    ]
    _write_export(tmp_path, messages)
    events = tuple(ingest_export(tmp_path))

    context_start = _scene_context_start(
        events,
        focus_index=1,
        split_start=0,
        max_context_events=20,
        max_idle_gap_ms=2_000,
    )

    assert context_start == 0


def test_review_queue_is_deterministic_and_split_contexts_do_not_overlap(tmp_path):
    _write_export(tmp_path, _review_messages())
    mapping_path = tmp_path / "private" / "mapping.json"
    first = tmp_path / "first.jsonl"
    second = tmp_path / "second.jsonl"

    for output in (first, second):
        build_review_queue(
            ingest_export(tmp_path),
            output_path=output,
            mapping_path=mapping_path,
            per_split=2,
            context_size=3,
            response_window_ms=2_000,
        )

    assert first.read_bytes() == second.read_bytes()
    records = [json.loads(line) for line in first.read_text().splitlines()]
    context_ids = {
        split: {
            event["event_id"]
            for item in records
            if item["split"] == split
            for event in item["context"]
        }
        for split in ("calibration", "holdout")
    }
    assert context_ids["calibration"].isdisjoint(context_ids["holdout"])


def test_review_queue_refuses_to_invent_a_missing_signal_class(tmp_path):
    messages = []
    for index in range(4):
        messages.extend(
            [
                _message(
                    f"member-{index}",
                    index * 10_000 + 1_000,
                    str(index + 10),
                    elements=[{"type": "text", "data": {"text": "问题"}}],
                ),
                _message(
                    f"bot-{index}",
                    index * 10_000 + 1_500,
                    "323537051",
                    elements=[{"type": "text", "data": {"text": "回复"}}],
                ),
            ]
        )
    _write_export(tmp_path, messages)

    with pytest.raises(ValueError, match="not enough review candidates"):
        build_review_queue(
            ingest_export(tmp_path),
            output_path=tmp_path / "review.jsonl",
            mapping_path=tmp_path / "private" / "mapping.json",
            per_split=2,
            response_window_ms=2_000,
        )
