import json

import pytest

from eval.export_ingest import ExportValidationError, load_export
from tests.shadow_fixtures import message, write_export


def _read_manifest(root):
    return json.loads((root / "manifest.json").read_text(encoding="utf-8"))


def _write_manifest(root, manifest):
    (root / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False), encoding="utf-8"
    )


def _mutate_single_record(tmp_path, mutation):
    record = message("m1", "10001", "valid text", 1000)
    mutation(record)
    return write_export(tmp_path / "export", [record], target_uin="10001")


def test_load_export_normalizes_multichunk_records_and_summary(tmp_path):
    rich = message("m-rich", "10001", "fixture text", 3000)
    rich["seq"] = "9"
    rich["content"] = {
        "text": "must not be retained",
        "elements": [
            {
                "type": "reply",
                "data": {
                    "referencedMessageId": "m-source",
                    "messageId": "m-legacy",
                    "senderUin": "10002",
                    "content": "quoted text must not be retained",
                },
            },
            {"type": "text", "data": {"text": "first"}},
            {
                "type": "image",
                "data": {
                    "filename": "private-name.png",
                    "url": "https://private.invalid/image.png",
                    "text": "[image]",
                },
            },
            {"type": "text", "data": {"text": " second"}},
            {
                "type": "reply",
                "data": {
                    "referencedMessageId": "ignored-reference",
                    "senderUin": "ignored-sender",
                },
            },
        ],
        "resources": [
            {
                "type": "image",
                "filename": "private-resource.png",
                "url": "https://private.invalid/resource.png",
            }
        ],
        "mentions": [
            {"uin": "10003"},
            {"uid": "uid-4"},
            {"uin": "10003"},
        ],
    }
    raw_system = message("m-system", "20002", "system text", 2000)
    raw_system["system"] = True
    recalled = message("m-recalled", "10001", "recalled text", 1000)
    recalled["recalled"] = True
    tied = message("m-tied", "10001", "tie breaker", 3000)
    tied["seq"] = 2
    root = write_export(
        tmp_path / "export",
        [rich, raw_system, recalled, tied],
        target_uin="10001",
        chunk_size=2,
    )

    result = load_export(root, target_uin="10001")

    assert [item.message_id for item in result.events] == [
        "m-recalled",
        "m-system",
        "m-tied",
        "m-rich",
    ]
    assert result.target_uin == "10001"
    assert result.summary.manifest_records == 4
    assert result.summary.observed_records == 4
    assert result.summary.target_records == 3
    assert result.summary.excluded_system == 1
    assert result.summary.excluded_recalled == 1
    assert result.summary.duplicate_records == 0
    assert result.summary.chunk_count == 2

    normalized = result.events[-1]
    assert normalized.text == "first second"
    assert normalized.element_types == (
        "reply",
        "text",
        "image",
        "text",
        "reply",
    )
    assert normalized.reply_to_message_id == "m-source"
    assert normalized.reply_to_sender_uin == "10002"
    assert normalized.mentions == ("10003", "uid-4")
    assert normalized.has_media is True
    assert normalized.system is False
    assert normalized.recalled is False
    assert "must not be retained" not in normalized.text
    assert "quoted text" not in normalized.text
    assert "image" not in normalized.text
    assert "private-name" not in normalized.text
    assert "private.invalid" not in normalized.text


def test_load_export_rejects_manifest_total_mismatch(tmp_path):
    root = write_export(
        tmp_path / "export",
        [message("m1", "10001", "text", 1000)],
        target_uin="10001",
    )
    manifest = _read_manifest(root)
    manifest["statistics"]["totalMessages"] = 2
    _write_manifest(root, manifest)

    with pytest.raises(ExportValidationError, match="manifest record count"):
        load_export(root, target_uin="10001")


def test_load_export_rejects_per_chunk_count_mismatch(tmp_path):
    root = write_export(
        tmp_path / "export",
        [message("m1", "10001", "text", 1000)],
        target_uin="10001",
    )
    manifest = _read_manifest(root)
    manifest["chunked"]["chunks"][0]["count"] = 2
    _write_manifest(root, manifest)

    with pytest.raises(ExportValidationError, match="chunk record count"):
        load_export(root, target_uin="10001")


def test_load_export_rejects_missing_invalid_and_nonobject_manifest(tmp_path):
    missing = tmp_path / "missing"
    missing.mkdir()
    with pytest.raises(ExportValidationError, match="manifest.json.*missing"):
        load_export(missing, target_uin="10001")

    invalid = tmp_path / "invalid"
    invalid.mkdir()
    (invalid / "manifest.json").write_text("{invalid", encoding="utf-8")
    with pytest.raises(ExportValidationError, match="manifest.json.*JSON"):
        load_export(invalid, target_uin="10001")

    nonobject = tmp_path / "nonobject"
    nonobject.mkdir()
    (nonobject / "manifest.json").write_text("[]", encoding="utf-8")
    with pytest.raises(ExportValidationError, match="manifest.*mapping"):
        load_export(nonobject, target_uin="10001")


@pytest.mark.parametrize(
    "change, match",
    [
        (lambda manifest: manifest.pop("statistics"), "statistics.*mapping"),
        (
            lambda manifest: manifest.__setitem__("statistics", []),
            "statistics.*mapping",
        ),
        (lambda manifest: manifest.pop("chunked"), "chunked.*mapping"),
        (
            lambda manifest: manifest.__setitem__("chunked", []),
            "chunked.*mapping",
        ),
        (
            lambda manifest: manifest["statistics"].__setitem__(
                "totalMessages", True
            ),
            "totalMessages.*non-negative integer",
        ),
        (
            lambda manifest: manifest["statistics"].__setitem__(
                "totalMessages", -1
            ),
            "totalMessages.*non-negative integer",
        ),
        (
            lambda manifest: manifest["chunked"].__setitem__("format", "json"),
            "format.*jsonl",
        ),
        (
            lambda manifest: manifest["chunked"].__setitem__("chunks", []),
            "chunks.*non-empty list",
        ),
        (
            lambda manifest: manifest["chunked"].__setitem__("chunks", {}),
            "chunks.*non-empty list",
        ),
        (
            lambda manifest: manifest["chunked"]["chunks"][0].__setitem__(
                "relativePath", 1
            ),
            "relativePath.*string",
        ),
        (
            lambda manifest: manifest["chunked"]["chunks"][0].__setitem__(
                "count", True
            ),
            "count.*non-negative integer",
        ),
        (
            lambda manifest: manifest["chunked"]["chunks"][0].__setitem__(
                "count", -1
            ),
            "count.*non-negative integer",
        ),
    ],
)
def test_load_export_rejects_invalid_manifest_structure(
    tmp_path, change, match
):
    root = write_export(
        tmp_path / "export",
        [message("m1", "10001", "text", 1000)],
        target_uin="10001",
    )
    manifest = _read_manifest(root)
    change(manifest)
    _write_manifest(root, manifest)

    with pytest.raises(ExportValidationError, match=match):
        load_export(root, target_uin="10001")


def test_load_export_rejects_missing_chunk_file(tmp_path):
    root = write_export(
        tmp_path / "export",
        [message("m1", "10001", "text", 1000)],
        target_uin="10001",
    )
    manifest = _read_manifest(root)
    manifest["chunked"]["chunks"][0]["relativePath"] = "chunks/missing.jsonl"
    _write_manifest(root, manifest)

    with pytest.raises(ExportValidationError, match="missing.jsonl.*file"):
        load_export(root, target_uin="10001")


def test_load_export_rejects_chunk_path_escape(tmp_path):
    root = write_export(
        tmp_path / "export",
        [message("m1", "10001", "text", 1000)],
        target_uin="10001",
    )
    manifest = _read_manifest(root)
    manifest["chunked"]["chunks"] = [
        {"relativePath": "../outside.jsonl", "count": 1}
    ]
    _write_manifest(root, manifest)

    with pytest.raises(ExportValidationError, match="escapes export root"):
        load_export(root, target_uin="10001")


def test_identical_duplicate_is_retained_once_and_counted_from_raw(tmp_path):
    first = message("m1", "10001", "same", 1000)
    root = write_export(
        tmp_path / "export",
        [first, first],
        target_uin="10001",
        chunk_size=1,
    )

    result = load_export(root, target_uin="10001")

    assert [item.message_id for item in result.events] == ["m1"]
    assert result.summary.manifest_records == 2
    assert result.summary.observed_records == 2
    assert result.summary.target_records == 2
    assert result.summary.duplicate_records == 1


def test_presentation_only_duplicate_drift_is_retained_once_and_counted(
    tmp_path,
):
    first = message("m1", "10001", "same behavior", 1000, recalled=True)
    first["system"] = True
    first["sender"]["name"] = "First display name"
    first["content"]["text"] = "first rendered content"
    first["content"]["html"] = "<p>first rendered content</p>"
    first["content"]["mentions"] = [
        {"uin": "10002", "name": "First mention display"}
    ]
    first["content"]["elements"].append(
        {
            "type": "system",
            "data": {
                "displayText": "first recalled display",
                "senderName": "First system display",
            },
        }
    )
    first["content"]["resources"] = [
        {"type": "image", "filename": "first.png", "url": "/first"}
    ]
    drift = json.loads(json.dumps(first))
    drift["sender"]["name"] = "Second display name"
    drift["content"]["text"] = "second rendered content"
    drift["content"]["html"] = "<p>second rendered content</p>"
    drift["content"]["mentions"][0]["name"] = "Second mention display"
    drift["content"]["elements"][-1]["data"] = {
        "displayText": "second recalled display",
        "senderName": "Second system display",
    }
    drift["content"]["resources"][0].update(
        {"filename": "second.png", "url": "/second"}
    )
    root = write_export(
        tmp_path / "export",
        [first, drift],
        target_uin="10001",
        chunk_size=1,
    )

    result = load_export(root, target_uin="10001")

    assert len(result.events) == 1
    assert result.events[0].sender_name == "First display name"
    assert result.summary.target_records == 2
    assert result.summary.duplicate_records == 1


@pytest.mark.parametrize(
    "mutation",
    [
        lambda record: record.__setitem__("seq", "1001"),
        lambda record: record.__setitem__("timestamp", 1001),
        lambda record: record["sender"].__setitem__("uid", "other-uid"),
        lambda record: record["sender"].__setitem__("uin", "10002"),
        lambda record: record.__setitem__("type", "video"),
        lambda record: record["content"]["elements"][1]["data"].__setitem__(
            "text", "different text"
        ),
        lambda record: record["content"]["elements"].append(
            {"type": "notice", "data": {}}
        ),
        lambda record: record["content"]["elements"][0]["data"].__setitem__(
            "referencedMessageId", "other-reference"
        ),
        lambda record: record["content"]["elements"][0]["data"].__setitem__(
            "senderUin", "10003"
        ),
        lambda record: record["content"].__setitem__(
            "mentions", [{"uin": "10003"}]
        ),
        lambda record: record["content"].__setitem__(
            "resources", [{"type": "image"}]
        ),
        lambda record: record.__setitem__("recalled", True),
        lambda record: record.__setitem__("system", True),
    ],
    ids=(
        "seq",
        "timestamp",
        "sender-key",
        "sender-uin",
        "message-type",
        "text",
        "element-types",
        "reply-message-id",
        "reply-sender-uin",
        "mentions",
        "media",
        "recalled",
        "system",
    ),
)
def test_conflicting_duplicate_behavior_is_rejected(tmp_path, mutation):
    first = message(
        "m1",
        "10001",
        "same text",
        1000,
        reply_to="m-source",
        reply_sender_uin="10002",
    )
    conflict = json.loads(json.dumps(first))
    mutation(conflict)
    root = write_export(
        tmp_path / "export",
        [first, conflict],
        target_uin="10001",
        chunk_size=1,
    )

    with pytest.raises(ExportValidationError, match="conflicting duplicate.*m1"):
        load_export(root, target_uin="10001")


def test_malformed_json_reports_chunk_name_and_line(tmp_path):
    root = write_export(tmp_path / "export", [], target_uin="10001")
    chunk = root / "chunks" / "chunk_0001.jsonl"
    chunk.write_text("\n{bad json}\n", encoding="utf-8")
    manifest = _read_manifest(root)
    manifest["chunked"]["chunks"][0]["count"] = 1
    manifest["statistics"]["totalMessages"] = 1
    _write_manifest(root, manifest)

    with pytest.raises(
        ExportValidationError, match=r"chunk_0001\.jsonl:2.*malformed JSON"
    ):
        load_export(root, target_uin="10001")


def test_nonobject_record_reports_chunk_name_and_line(tmp_path):
    root = write_export(tmp_path / "export", [], target_uin="10001")
    chunk = root / "chunks" / "chunk_0001.jsonl"
    chunk.write_text("[]\n", encoding="utf-8")
    manifest = _read_manifest(root)
    manifest["chunked"]["chunks"][0]["count"] = 1
    manifest["statistics"]["totalMessages"] = 1
    _write_manifest(root, manifest)

    with pytest.raises(
        ExportValidationError, match=r"chunk_0001\.jsonl:1.*mapping"
    ):
        load_export(root, target_uin="10001")


def test_summary_accounts_for_content_ineligible_records(tmp_path):
    empty = message("m-empty", "20002", "", 1000)
    root = write_export(
        tmp_path / "export-empty", (empty,), target_uin="20002"
    )

    result = load_export(root, target_uin="20002")

    assert result.summary.target_records == 1
    assert result.summary.excluded_content_ineligible == 1


def test_load_export_rejects_configured_target_absent(tmp_path):
    root = write_export(
        tmp_path / "export",
        [message("m1", "10002", "text", 1000)],
        target_uin="10001",
    )

    with pytest.raises(ExportValidationError, match="target sender.*absent"):
        load_export(root, target_uin="10001")


@pytest.mark.parametrize(
    "mutation, match",
    [
        (lambda record: record.__setitem__("id", ""), "id.*non-empty string"),
        (lambda record: record.__setitem__("id", 1), "id.*non-empty string"),
        (
            lambda record: record.__setitem__("timestamp", True),
            "timestamp.*non-negative integer",
        ),
        (
            lambda record: record.__setitem__("timestamp", -1),
            "timestamp.*non-negative integer",
        ),
        (lambda record: record.__setitem__("seq", True), "seq.*decimal"),
        (lambda record: record.__setitem__("seq", "1.5"), "seq.*decimal"),
        (lambda record: record.__setitem__("sender", []), "sender.*mapping"),
        (
            lambda record: record["sender"].__setitem__("uid", ""),
            "sender.uid.*non-empty string",
        ),
        (
            lambda record: record["sender"].__setitem__("uin", 10001),
            "sender.uin.*string",
        ),
        (
            lambda record: record["sender"].__setitem__("name", None),
            "sender.name.*string",
        ),
        (
            lambda record: record.__setitem__("type", ""),
            "type.*non-empty string",
        ),
        (lambda record: record.__setitem__("content", []), "content.*mapping"),
        (
            lambda record: record["content"].__setitem__("elements", "text"),
            "elements.*list",
        ),
        (
            lambda record: record.__setitem__("recalled", 0),
            "recalled.*boolean",
        ),
        (
            lambda record: record.__setitem__("system", 1),
            "system.*boolean",
        ),
    ],
)
def test_load_export_rejects_invalid_required_record_fields(
    tmp_path, mutation, match
):
    root = _mutate_single_record(tmp_path, mutation)

    with pytest.raises(ExportValidationError, match=match):
        load_export(root, target_uin="10001")


@pytest.mark.parametrize(
    "field, invalid, match",
    [
        ("elements", ["text"], "element.*mapping"),
        (
            "elements",
            [{"type": "text", "data": "not-a-mapping"}],
            "element data.*mapping",
        ),
        ("resources", "image", "resources.*list"),
        ("resources", ["image"], "resource.*mapping"),
        ("resources", [{"type": 1}], "resource type.*non-empty string"),
        ("mentions", "10002", "mentions.*list"),
        ("mentions", ["10002"], "mention.*mapping"),
    ],
)
def test_load_export_rejects_invalid_nested_collections(
    tmp_path, field, invalid, match
):
    def mutate(record):
        record["content"][field] = invalid

    root = _mutate_single_record(tmp_path, mutate)

    with pytest.raises(ExportValidationError, match=match):
        load_export(root, target_uin="10001")


def test_mentions_keep_valid_string_ids_and_ignore_invalid_identifier_values(
    tmp_path,
):
    record = message("m1", "10001", "mentions", 1000)
    record["content"]["mentions"] = [
        {"uin": "10002", "uid": None, "name": "display"},
        {"uin": 10003, "uid": "uid-3"},
        {"uin": None},
        {"name": "display only"},
        {"uin": " "},
        {"uid": "uid-3"},
    ]
    root = write_export(tmp_path / "export", [record], target_uin="10001")

    result = load_export(root, target_uin="10001")

    assert result.events[0].mentions == ("10002", "uid-3")


def test_null_referenced_message_id_falls_back_to_message_id(tmp_path):
    record = message(
        "m1",
        "10001",
        "reply",
        1000,
        reply_to="m-source",
        reply_sender_uin="00100",
    )
    record["content"]["elements"][0]["data"]["referencedMessageId"] = None
    root = write_export(tmp_path / "export", [record], target_uin="10001")

    result = load_export(root, target_uin="10001")

    assert result.events[0].reply_to_message_id == "m-source"
    assert result.events[0].reply_to_sender_uin == "00100"


def test_nonnull_nonstring_reply_reference_is_rejected(tmp_path):
    record = message(
        "m1", "10001", "reply", 1000, reply_to="m-source"
    )
    record["content"]["elements"][0]["data"]["referencedMessageId"] = 1
    root = write_export(tmp_path / "export", [record], target_uin="10001")

    with pytest.raises(
        ExportValidationError, match="referencedMessageId.*string"
    ):
        load_export(root, target_uin="10001")


def test_reply_reference_zero_and_blank_use_fallback_or_normalize_absent(
    tmp_path,
):
    zero = message(
        "m-zero",
        "10001",
        "zero",
        1000,
        reply_to="0",
        reply_sender_uin="00100",
    )
    zero["content"]["elements"][0]["data"]["messageId"] = "legacy-id"
    blank = message(
        "m-blank",
        "10001",
        "blank",
        2000,
        reply_to=" ",
        reply_sender_uin="00200",
    )
    blank["content"]["elements"].insert(
        0,
        {
            "type": "reply",
            "data": {
                "referencedMessageId": " ",
                "messageId": "",
                "senderUin": "00200",
            },
        },
    )
    root = write_export(
        tmp_path / "export", [zero, blank], target_uin="10001"
    )

    result = load_export(root, target_uin="10001")

    assert result.events[0].reply_to_message_id == "legacy-id"
    assert result.events[0].reply_to_sender_uin == "00100"
    assert result.events[1].reply_to_message_id == ""
    assert result.events[1].reply_to_sender_uin == "00200"


@pytest.mark.parametrize("message_type", ["video", "audio", "file", "forward"])
def test_message_media_types_are_marked_as_media(tmp_path, message_type):
    record = message(
        "m1", "10001", "text", 1000, message_type=message_type
    )
    root = write_export(tmp_path / "export", [record], target_uin="10001")

    result = load_export(root, target_uin="10001")

    assert result.events[0].has_media is True


def test_image_resource_is_marked_as_media_without_retaining_locator(tmp_path):
    record = message("m1", "10001", "text", 1000)
    record["content"]["resources"] = [
        {
            "type": "image",
            "filename": "private.png",
            "url": "https://private.invalid/private.png",
        }
    ]
    root = write_export(tmp_path / "export", [record], target_uin="10001")

    result = load_export(root, target_uin="10001")

    assert result.events[0].has_media is True
    assert not hasattr(result.events[0], "resources")


def test_system_message_type_and_element_are_excluded(tmp_path):
    by_type = message(
        "m-type", "10001", "type system", 1000, message_type="system"
    )
    by_element = message("m-element", "10001", "element system", 2000)
    by_element["content"]["elements"].append(
        {"type": "system", "data": {}}
    )
    root = write_export(
        tmp_path / "export", [by_type, by_element], target_uin="10001"
    )

    result = load_export(root, target_uin="10001")

    assert [item.system for item in result.events] == [True, True]
    assert result.summary.excluded_system == 2
