# Export Shadow Alignment Phase 3 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a privacy-safe offline tool that ingests the real QQChatExporter dataset, extracts conservative target behavior examples, projects current Groupmate mechanics over the same evidence, and reports conditional mismatches without generation or runtime side effects.

**Architecture:** Keep raw exporter data in memory behind a strict ingest boundary. Extract response runs and high-confidence opportunities, label reference behavior with rules independent from production classifiers, then project production trigger/scene/act/media mechanics and compare both sides in deterministic local reports. Raw excerpts are restricted to ignored review artifacts; shareable reports contain only anonymous IDs and aggregates.

**Tech Stack:** Python 3.7 standard library (`argparse`, `dataclasses`, `enum`, `hashlib`, `hmac`, `json`, `os`, `pathlib`, `secrets`), existing Groupmate pure domain modules, pytest, QQChatExporter V5 chunked JSONL.

---

## File Map

- `eval/shadow_models.py`: immutable in-memory and shareable Phase 3 contracts.
- `eval/export_ingest.py`: strict manifest/chunk parsing and integrity validation.
- `eval/shadow_extract.py`: local ID hashing, response-run grouping, opportunity association, alias normalization.
- `eval/reference_labeler.py`: independent conservative reference labels and local overrides.
- `eval/shadow_projector.py`: read-only projection through current production mechanics.
- `eval/behavior_diff.py`: confusion matrices, conditional diagnostics, privacy checks, JSON/Markdown writers.
- `eval/shadow_export.py`: end-to-end command-line orchestration.
- `eval/README.md`: local execution, privacy, and review workflow.
- `tests/shadow_fixtures.py`: synthetic QQChatExporter fixture builder shared by Phase 3 tests.
- `tests/test_export_ingest.py`: parser and integrity boundary tests.
- `tests/test_shadow_extract.py`: grouping, association, hashing, and ambiguity tests.
- `tests/test_reference_labeler.py`: independent label and override tests.
- `tests/test_shadow_projector.py`: current mechanics projection tests.
- `tests/test_behavior_diff.py`: aggregation, determinism, and privacy tests.
- `tests/test_shadow_export.py`: CLI success/failure and side-effect tests.

### Task 1: Define Phase 3 Data Contracts And Synthetic Export Fixtures

**Files:**
- Create: `eval/shadow_models.py`
- Create: `tests/shadow_fixtures.py`
- Create: `tests/test_shadow_models.py`

- [ ] **Step 1: Write failing contract tests**

Create `tests/test_shadow_models.py` with immutable-contract and validation tests:

```python
import pytest

from eval.shadow_models import (
    AssociationConfidence,
    BehaviorExample,
    ExportEvent,
    ExportSummary,
    IngestResult,
    ReferenceLabel,
    ResponseRun,
    ShadowProjection,
)
from groupmate.core.response_act import ResponseAct
from groupmate.models import InteractionScene


def event(**overrides):
    values = {
        "message_id": "m1",
        "seq": 1,
        "timestamp_ms": 1000,
        "sender_key": "sender-a",
        "sender_uin": "10001",
        "sender_name": "甲",
        "message_type": "text",
        "text": "测试消息",
        "element_types": ("text",),
    }
    values.update(overrides)
    return ExportEvent(**values)


def test_export_event_is_immutable_and_validates_identity():
    item = event()
    assert item.content_eligible is True
    with pytest.raises((AttributeError, TypeError)):
        item.text = "changed"
    with pytest.raises(ValueError):
        event(message_id="")


def test_response_run_exposes_only_derived_mechanics():
    run = ResponseRun(
        run_id="run-a",
        events=(event(sender_uin="20002"),),
        anchor_message_id="m-source",
        confidence=AssociationConfidence.HIGH,
        reason_codes=("explicit_reply",),
    )
    assert run.message_count == 1
    assert run.reply_chars == 4
    assert run.has_media is False


def test_reference_and_projection_require_domain_enums():
    label = ReferenceLabel(
        scene=InteractionScene.DIRECT_ADDRESS,
        act=ResponseAct.ACKNOWLEDGE,
        confidence=AssociationConfidence.HIGH,
        reason_codes=("bare_alias",),
    )
    projection = ShadowProjection(
        sample_id="sample-a",
        owner="groupmate",
        would_reply=True,
        trigger="alias_direct",
        scene=InteractionScene.DIRECT_ADDRESS,
        act=ResponseAct.ACKNOWLEDGE,
        quote_allowed=True,
        decorative_media_allowed=False,
        capability_media_allowed=False,
        ambiguous_target=False,
        owner_count=1,
        completion_claim_allowed=False,
        reason_codes=("hard_trigger",),
    )
    assert label.act is ResponseAct.ACKNOWLEDGE
    assert projection.owner == "groupmate"


def test_behavior_example_carries_in_memory_context_without_serializing_it():
    source = event()
    example = BehaviorExample(
        sample_id="sample-a",
        source=source,
        context=(source,),
        response_run=None,
        observed_replied=False,
        covered_context=False,
        review_reason="",
    )
    assert example.source is source
    assert example.observed_replied is False
```

- [ ] **Step 2: Run the tests and verify the module is missing**

Run: `python3.7 -m pytest tests/test_shadow_models.py -q`

Expected: collection fails with `ModuleNotFoundError: No module named 'eval.shadow_models'`.

- [ ] **Step 3: Implement immutable contracts**

Create `eval/shadow_models.py` with these exact public contracts:

```python
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional, Tuple

from groupmate.core.response_act import ResponseAct
from groupmate.models import InteractionScene


class AssociationConfidence(str, Enum):
    HIGH = "high"
    REVIEW = "review"


@dataclass(frozen=True)
class ExportEvent:
    message_id: str
    seq: int
    timestamp_ms: int
    sender_key: str
    sender_uin: str
    sender_name: str
    message_type: str
    text: str
    element_types: Tuple[str, ...]
    reply_to_message_id: str = ""
    reply_to_sender_uin: str = ""
    mentions: Tuple[str, ...] = ()
    has_media: bool = False
    recalled: bool = False
    system: bool = False

    def __post_init__(self) -> None:
        for name in ("message_id", "sender_key", "message_type"):
            value = str(getattr(self, name) or "").strip()
            if not value:
                raise ValueError("{} is required".format(name))
            object.__setattr__(self, name, value)
        object.__setattr__(self, "seq", int(self.seq))
        object.__setattr__(self, "timestamp_ms", int(self.timestamp_ms))
        if self.timestamp_ms < 0:
            raise ValueError("timestamp_ms must be non-negative")
        object.__setattr__(self, "sender_uin", str(self.sender_uin or "").strip())
        object.__setattr__(self, "sender_name", str(self.sender_name or "").strip())
        object.__setattr__(self, "text", str(self.text or "").strip())
        object.__setattr__(self, "element_types", tuple(self.element_types or ()))
        object.__setattr__(self, "mentions", tuple(self.mentions or ()))

    @property
    def content_eligible(self) -> bool:
        return not self.system and not self.recalled and bool(
            self.text or self.has_media or self.element_types
        )


@dataclass(frozen=True)
class ExportSummary:
    manifest_records: int
    observed_records: int
    target_records: int
    excluded_system: int
    excluded_recalled: int
    duplicate_records: int
    chunk_count: int


@dataclass(frozen=True)
class IngestResult:
    events: Tuple[ExportEvent, ...]
    summary: ExportSummary
    target_uin: str


@dataclass(frozen=True)
class ResponseRun:
    run_id: str
    events: Tuple[ExportEvent, ...]
    anchor_message_id: str
    confidence: AssociationConfidence
    reason_codes: Tuple[str, ...]
    review_reason: str = ""

    @property
    def message_count(self) -> int:
        return len(self.events)

    @property
    def reply_chars(self) -> int:
        return sum(len(item.text) for item in self.events)

    @property
    def has_media(self) -> bool:
        return any(item.has_media for item in self.events)

    @property
    def quoted(self) -> bool:
        return any(bool(item.reply_to_message_id) for item in self.events)


@dataclass(frozen=True)
class BehaviorExample:
    sample_id: str
    source: ExportEvent
    context: Tuple[ExportEvent, ...]
    response_run: Optional[ResponseRun]
    observed_replied: bool
    covered_context: bool
    review_reason: str


@dataclass(frozen=True)
class LocalReviewItem:
    sample_id: str
    reason: str
    source_events: Tuple[ExportEvent, ...]
    response_events: Tuple[ExportEvent, ...]


@dataclass(frozen=True)
class ReferenceLabel:
    scene: InteractionScene
    act: Optional[ResponseAct]
    confidence: AssociationConfidence
    reason_codes: Tuple[str, ...]


@dataclass(frozen=True)
class ShadowProjection:
    sample_id: str
    owner: str
    would_reply: bool
    trigger: str
    scene: InteractionScene
    act: Optional[ResponseAct]
    quote_allowed: bool
    decorative_media_allowed: bool
    capability_media_allowed: bool
    ambiguous_target: bool
    owner_count: int
    completion_claim_allowed: bool
    reason_codes: Tuple[str, ...]
```

Create `tests/shadow_fixtures.py` with deterministic synthetic V5 records:

```python
import json
from pathlib import Path


def message(
    message_id, sender_uin, text, timestamp_ms, *, message_type="text",
    reply_to="", reply_sender_uin="", image=False, recalled=False,
):
    elements = []
    if reply_to:
        elements.append({
            "type": "reply",
            "data": {
                "messageId": reply_to,
                "referencedMessageId": reply_to,
                "senderUin": reply_sender_uin,
                "senderName": "被回复用户",
                "content": "合成引用",
                "timestamp": max(0, int(timestamp_ms / 1000) - 1),
                "previewElements": [],
            },
        })
    if text:
        elements.append({"type": "text", "data": {"text": text}})
    if image:
        elements.append({
            "type": "image",
            "data": {
                "filename": "fixture.png",
                "url": "/download?fixture=image",
            },
        })
    return {
        "id": str(message_id),
        "seq": str(timestamp_ms),
        "timestamp": int(timestamp_ms),
        "sender": {
            "uid": "uid-{}".format(sender_uin),
            "uin": str(sender_uin),
            "name": "用户{}".format(sender_uin),
        },
        "type": message_type,
        "recalled": bool(recalled),
        "system": message_type == "system",
        "content": {
            "text": text,
            "elements": elements,
            "resources": [],
            "mentions": [],
        },
    }


def write_export(root, records, target_uin="20002", chunk_size=3):
    root = Path(root)
    chunk_dir = root / "chunks"
    chunk_dir.mkdir(parents=True)
    chunks = []
    batches = [records[index:index + chunk_size]
               for index in range(0, len(records), chunk_size)] or [[]]
    for index, batch in enumerate(batches, 1):
        relative = "chunks/chunk_{:04d}.jsonl".format(index)
        path = root / relative
        path.write_text(
            "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in batch),
            encoding="utf-8",
        )
        chunks.append({"relativePath": relative, "count": len(batch)})
    manifest = {
        "exporter": {"name": "QQChatExporter", "version": "5-test"},
        "statistics": {"totalMessages": len(records)},
        "target": {"uin": str(target_uin)},
        "chunked": {"format": "jsonl", "chunks": chunks},
    }
    (root / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False), encoding="utf-8"
    )
    return root
```

Keep all fixture names, locators, and text synthetic.

- [ ] **Step 4: Run contract tests**

Run: `python3.7 -m pytest tests/test_shadow_models.py -q`

Expected: all tests pass.

- [ ] **Step 5: Commit contracts and fixtures**

```bash
git add eval/shadow_models.py tests/shadow_fixtures.py tests/test_shadow_models.py
git commit -m "test: define export shadow contracts"
```

### Task 2: Strict QQChatExporter Ingestion

**Files:**
- Create: `eval/export_ingest.py`
- Create: `tests/test_export_ingest.py`

- [ ] **Step 1: Write parser integrity tests**

Create `tests/test_export_ingest.py` with these cases:

```python
import json

import pytest

from eval.export_ingest import ExportValidationError, load_export
from tests.shadow_fixtures import message, write_export


def test_load_export_validates_counts_and_extracts_reply_media(tmp_path):
    records = [
        message("m1", "10001", "小维，看看这个", 1000),
        message(
            "m2",
            "20002",
            "看到了",
            2000,
            message_type="reply",
            reply_to="m1",
            reply_sender_uin="10001",
            image=True,
        ),
        message("m3", "20002", "撤回内容", 3000, recalled=True),
    ]
    root = write_export(tmp_path / "export", records, target_uin="20002")

    result = load_export(root, target_uin="20002")

    assert result.summary.manifest_records == 3
    assert result.summary.observed_records == 3
    assert result.summary.target_records == 2
    assert result.summary.excluded_recalled == 1
    target = result.events[1]
    assert target.reply_to_message_id == "m1"
    assert target.reply_to_sender_uin == "10001"
    assert target.has_media is True
    assert target.text == "看到了"


def test_load_export_rejects_manifest_count_mismatch(tmp_path):
    root = write_export(
        tmp_path / "export",
        [message("m1", "10001", "测试", 1000)],
    )
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    manifest["statistics"]["totalMessages"] = 2
    (root / "manifest.json").write_text(
        json.dumps(manifest), encoding="utf-8"
    )

    with pytest.raises(ExportValidationError, match="record count"):
        load_export(root, target_uin="20002")


def test_load_export_rejects_chunk_path_escape(tmp_path):
    root = write_export(tmp_path / "export", [])
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    manifest["chunked"]["chunks"] = [
        {"relativePath": "../outside.jsonl", "count": 0}
    ]
    (root / "manifest.json").write_text(
        json.dumps(manifest), encoding="utf-8"
    )

    with pytest.raises(ExportValidationError, match="escapes export root"):
        load_export(root, target_uin="20002")


def test_behavior_equivalent_duplicate_is_counted_but_conflict_fails(tmp_path):
    first = message("m1", "10001", "synthetic text", 1000)
    drift = json.loads(json.dumps(first))
    drift["sender"]["name"] = "Synthetic display variant"
    drift["content"]["text"] = "synthetic rendered variant"
    root = write_export(tmp_path / "same", [first, drift], chunk_size=1)
    result = load_export(root, target_uin="10001")
    assert len(result.events) == 1
    assert result.summary.duplicate_records == 1

    conflict = message("m1", "10001", "different normalized text", 1000)
    root = write_export(tmp_path / "conflict", [first, conflict], chunk_size=1)
    with pytest.raises(ExportValidationError, match="conflicting duplicate"):
        load_export(root, target_uin="10001")


def test_malformed_json_reports_chunk_and_line(tmp_path):
    root = write_export(tmp_path / "export", [])
    chunk = root / "chunks" / "chunk_0001.jsonl"
    chunk.write_text("{bad json}\n", encoding="utf-8")
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    manifest["chunked"]["chunks"][0]["count"] = 1
    manifest["statistics"]["totalMessages"] = 1
    (root / "manifest.json").write_text(
        json.dumps(manifest), encoding="utf-8"
    )
    with pytest.raises(ExportValidationError, match="chunk_0001.jsonl:1"):
        load_export(root, target_uin="20002")
```

- [ ] **Step 2: Run parser tests and verify failure**

Run: `python3.7 -m pytest tests/test_export_ingest.py -q`

Expected: collection fails because `eval.export_ingest` does not exist.

- [ ] **Step 3: Implement the strict parser**

Create `eval/export_ingest.py`. Implement:

```python
class ExportValidationError(ValueError):
    pass


def _behavior_key(event):
    return (
        event.message_id,
        event.seq,
        event.timestamp_ms,
        event.sender_key,
        event.sender_uin,
        event.message_type,
        event.text,
        event.element_types,
        event.reply_to_message_id,
        event.reply_to_sender_uin,
        event.mentions,
        event.has_media,
        event.recalled,
        event.system,
    )


def load_export(export_dir: Path, target_uin: str) -> IngestResult:
    root = Path(export_dir).expanduser().resolve()
    manifest = _load_manifest(root / "manifest.json")
    chunks = _declared_chunks(root, manifest)
    expected_total = _required_int(manifest["statistics"], "totalMessages")
    event_by_id = {}
    events = []
    observed = 0
    duplicates = 0
    target_records = 0
    for chunk_path, declared_count in chunks:
        chunk_observed = 0
        with chunk_path.open("r", encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, 1):
                if not line.strip():
                    continue
                observed += 1
                chunk_observed += 1
                try:
                    raw = json.loads(line)
                except ValueError as exc:
                    raise ExportValidationError(
                        "{}:{} malformed JSON".format(chunk_path.name, line_number)
                    ) from exc
                event = _parse_event(raw, chunk_path.name, line_number)
                if event.sender_uin == str(target_uin):
                    target_records += 1
                previous = event_by_id.get(event.message_id)
                if previous is not None:
                    if _behavior_key(previous) != _behavior_key(event):
                        raise ExportValidationError(
                            "conflicting duplicate message id {}".format(
                                event.message_id
                            )
                        )
                    duplicates += 1
                    continue
                event_by_id[event.message_id] = event
                events.append(event)
        if chunk_observed != declared_count:
            raise ExportValidationError(
                "chunk record count mismatch: {} expected {} observed {}".format(
                    chunk_path.name, declared_count, chunk_observed
                )
            )
    if observed != expected_total:
        raise ExportValidationError(
            "manifest record count mismatch: expected {} observed {}".format(
                expected_total, observed
            )
        )
    if target_records == 0:
        raise ExportValidationError("configured target sender is absent")
    ordered = tuple(
        sorted(events, key=lambda item: (item.timestamp_ms, item.seq, item.message_id))
    )
    return IngestResult(
        events=ordered,
        summary=ExportSummary(
            manifest_records=expected_total,
            observed_records=observed,
            target_records=target_records,
            excluded_system=sum(item.system for item in ordered),
            excluded_recalled=sum(item.recalled for item in ordered),
            duplicate_records=duplicates,
            chunk_count=len(chunks),
        ),
        target_uin=str(target_uin),
    )
```

The private helpers must enforce these exact boundaries:

- manifest top-level objects `statistics` and `chunked` are mappings;
- `chunked.format == "jsonl"` and `chunked.chunks` is a non-empty list;
- each chunk has string `relativePath` and non-negative integer `count`;
- resolved chunk path remains inside the export root and is a file;
- record `id`, `timestamp`, `sender.uid`, `sender.uin`, `sender.name`, `type`, and
  `content.elements` are type checked;
- `seq` accepts a decimal string or integer;
- text is assembled only from `element.type == "text"` data, so exporter reply
  markers and image placeholders do not enter the dialogue text;
- the first reply element's non-empty, non-`"0"`
  `data.referencedMessageId` supplies the reference; null, blank, and `"0"`
  fall back to a valid string `data.messageId`, while a non-null non-string
  referenced value fails validation;
- `data.senderUin` supplies the referenced sender UIN, preserving its string
  representation;
- media is true for image elements/resources or message types `video`, `audio`,
  `file`, and `forward`;
- mentions retain only non-empty string `uin`/`uid` values from mapping entries,
  ignoring missing or non-string identifier values while rejecting scalar
  mention collections and entries;
- duplicate IDs retain the first event and count behavior-equivalent occurrences;
  sender display names and other exporter rendering metadata may drift, but any
  conflict in the normalized behavior key fails validation;
- system exclusion is true when the top-level flag is true, the message type is
  `system`, or a system element is present.

- [ ] **Step 4: Run parser and contract tests**

Run: `python3.7 -m pytest tests/test_shadow_models.py tests/test_export_ingest.py -q`

Expected: all tests pass.

- [ ] **Step 5: Commit ingestion**

```bash
git add eval/export_ingest.py tests/test_export_ingest.py
git commit -m "feat: ingest qq exporter data safely"
```

### Task 3: Extract Opportunities, Response Runs, And Anonymous IDs

**Files:**
- Create: `eval/shadow_extract.py`
- Create: `tests/test_shadow_extract.py`

- [ ] **Step 1: Write failing extraction tests**

Create `tests/test_shadow_extract.py` covering exact anchors, conservative adjacency,
conflicts, covered context, run boundaries, salt persistence, and alias copying:

```python
from eval.export_ingest import load_export
from eval.shadow_extract import (
    LocalIdHasher,
    extract_behavior_examples,
    load_or_create_salt,
    normalize_alias,
)
from tests.shadow_fixtures import message, write_export


def test_explicit_reply_and_following_target_text_form_one_run(tmp_path):
    root = write_export(
        tmp_path / "export",
        [
            message("m1", "10001", "小维，在吗", 1000),
            message(
                "m2", "20002", "在", 2000,
                message_type="reply", reply_to="m1",
                reply_sender_uin="10001",
            ),
            message("m3", "20002", "刚看到", 5000),
        ],
        target_uin="20002",
    )
    ingest = load_export(root, "20002")
    examples, reviews = extract_behavior_examples(
        ingest, LocalIdHasher(b"a" * 32), target_alias="小维"
    )
    linked = next(item for item in examples if item.source.message_id == "m1")
    assert linked.observed_replied is True
    assert linked.response_run.message_count == 2
    assert linked.response_run.anchor_message_id == "m1"
    assert reviews == ()


def test_adjacent_unquoted_reply_is_high_confidence_only_without_interleaving(tmp_path):
    root = write_export(
        tmp_path / "export",
        [
            message("m1", "10001", "小维，早", 1000),
            message("m2", "20002", "早呀", 5000),
            message("m3", "10001", "第一问", 10000),
            message("m4", "10002", "第二问", 11000),
            message("m5", "20002", "我看看", 12000),
        ],
        target_uin="20002",
    )
    examples, reviews = extract_behavior_examples(
        load_export(root, "20002"),
        LocalIdHasher(b"a" * 32),
        target_alias="小维",
    )
    assert next(item for item in examples if item.source.message_id == "m1").observed_replied
    assert any(item.reason == "multiple_source_candidates" for item in reviews)
    assert not next(item for item in examples if item.source.message_id == "m3").observed_replied
    assert next(item for item in examples if item.source.message_id == "m3").covered_context


def test_different_explicit_anchors_split_target_runs(tmp_path):
    root = write_export(
        tmp_path / "export",
        [
            message("m1", "10001", "问题一", 1000),
            message("m2", "10002", "问题二", 1100),
            message("m3", "20002", "回答一", 2000, message_type="reply", reply_to="m1", reply_sender_uin="10001"),
            message("m4", "20002", "回答二", 2500, message_type="reply", reply_to="m2", reply_sender_uin="10002"),
        ],
        target_uin="20002",
    )
    examples, _ = extract_behavior_examples(
        load_export(root, "20002"), LocalIdHasher(b"a" * 32), "小维"
    )
    assert next(item for item in examples if item.source.message_id == "m1").response_run.message_count == 1
    assert next(item for item in examples if item.source.message_id == "m2").response_run.message_count == 1


def test_local_salt_is_reused_and_alias_normalization_is_non_mutating(tmp_path):
    salt_path = tmp_path / ".shadow-id-salt"
    first = load_or_create_salt(salt_path)
    second = load_or_create_salt(salt_path)
    assert first == second
    assert len(first) == 32
    hasher = LocalIdHasher(first)
    assert hasher.sample_id("m1") == hasher.sample_id("m1")
    assert hasher.sample_id("m1") != hasher.sample_id("m2")
    assert normalize_alias("小维，在吗", "小维", "爱弥斯") == "爱弥斯，在吗"
```

- [ ] **Step 2: Run extraction tests and verify failure**

Run: `python3.7 -m pytest tests/test_shadow_extract.py -q`

Expected: collection fails because `eval.shadow_extract` does not exist.

- [ ] **Step 3: Implement hashing and extraction**

Create `eval/shadow_extract.py` with this public surface:

```python
class LocalIdHasher:
    def __init__(self, salt: bytes) -> None:
        if not isinstance(salt, bytes) or len(salt) != 32:
            raise ValueError("local id salt must contain exactly 32 bytes")
        self._salt = salt

    def sample_id(self, message_id: str) -> str:
        digest = hmac.new(
            self._salt,
            str(message_id).encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        return "sample-" + digest[:20]

    def sender_id(self, sender_key: str) -> str:
        digest = hmac.new(
            self._salt,
            ("sender:" + str(sender_key)).encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        return "u-" + digest[:16]


def load_or_create_salt(path: Path) -> bytes:
    target = Path(path)
    if target.exists():
        raw = target.read_bytes()
        if len(raw) != 32:
            raise ValueError("local id salt must contain exactly 32 bytes")
        return raw
    target.parent.mkdir(parents=True, exist_ok=True)
    raw = secrets.token_bytes(32)
    descriptor = os.open(
        str(target), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600
    )
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(raw)
    return raw


def normalize_alias(text: str, target_alias: str, current_alias: str) -> str:
    if not target_alias or not current_alias:
        raise ValueError("both aliases are required")
    return str(text or "").replace(target_alias, current_alias)


def extract_behavior_examples(
    ingest: IngestResult,
    hasher: LocalIdHasher,
    target_alias: str,
    run_gap_ms: int = 15000,
    adjacent_gap_ms: int = 20000,
    directed_gap_ms: int = 60000,
) -> Tuple[Tuple[BehaviorExample, ...], Tuple[LocalReviewItem, ...]]:
    eligible = tuple(item for item in ingest.events if item.content_eligible)
    runs = _build_response_runs(
        eligible, ingest.target_uin, hasher, run_gap_ms=run_gap_ms
    )
    linked, reviews = _associate_runs(
        eligible,
        runs,
        ingest.target_uin,
        target_alias,
        adjacent_gap_ms=adjacent_gap_ms,
        directed_gap_ms=directed_gap_ms,
    )
    covered = _covered_context_message_ids(linked, window_ms=30000)
    examples = []
    for index, source in enumerate(eligible):
        if source.sender_uin == ingest.target_uin:
            continue
        run = linked.get(source.message_id)
        context = tuple(
            item for item in eligible[max(0, index - 5):index + 1]
        )
        examples.append(BehaviorExample(
            sample_id=hasher.sample_id(source.message_id),
            source=source,
            context=context,
            response_run=run,
            observed_replied=run is not None,
            covered_context=source.message_id in covered,
            review_reason=_review_reason(source.message_id, reviews),
        ))
    return tuple(examples), tuple(reviews)
```

Import `LocalReviewItem` from `eval.shadow_models`. The private helpers in the
function above must implement the exact precedence in the approved design:

1. exclude system, recalled, and content-ineligible events;
2. group consecutive target events when gap is at most `run_gap_ms`, no human
   event intervenes, and explicit anchors do not conflict;
3. anchor one-reference runs exactly;
4. send missing/conflicting explicit references to review;
5. otherwise accept an adjacent unique human event within `adjacent_gap_ms`;
6. otherwise accept exactly one target-directed candidate within
   `directed_gap_ms`;
7. send multiple candidates to review;
8. mark other human messages in the preceding 30-second response context as
   `covered_context`;
9. emit all remaining human events as observed silence examples;
10. keep at most six preceding content events in each example context.

Do not serialize events or excerpts in this task.

- [ ] **Step 4: Run extraction and ingest tests**

Run: `python3.7 -m pytest tests/test_shadow_extract.py tests/test_export_ingest.py -q`

Expected: all tests pass.

- [ ] **Step 5: Commit extraction**

```bash
git add eval/shadow_extract.py tests/test_shadow_extract.py
git commit -m "feat: extract conservative shadow examples"
```

### Task 4: Independent Reference Labels And Local Overrides

**Files:**
- Create: `eval/reference_labeler.py`
- Create: `tests/test_reference_labeler.py`

- [ ] **Step 1: Write failing independent-label tests**

Create `tests/test_reference_labeler.py`:

```python
import json

import pytest

from eval.reference_labeler import (
    ReferenceLabeler,
    apply_overrides,
    collect_label_reviews,
    load_overrides,
)
from eval.shadow_models import AssociationConfidence
from groupmate.core.response_act import ResponseAct
from groupmate.models import InteractionScene
from tests.test_shadow_models import event


def example(
    text, *, media=False, replied=True, response_text="收到",
    mentions_target=False,
):
    source = event(
        text=text,
        has_media=media,
        mentions=(("20002",) if mentions_target else ()),
    )
    run = None
    if replied:
        response = event(
            message_id="bot-1", sender_key="target", sender_uin="20002",
            text=response_text, timestamp_ms=2000,
        )
        from eval.shadow_models import ResponseRun, BehaviorExample
        run = ResponseRun(
            "run-1", (response,), source.message_id,
            AssociationConfidence.HIGH, ("explicit_reply",),
        )
    from eval.shadow_models import BehaviorExample
    return BehaviorExample(
        "sample-1", source, (source,), run, replied, False, ""
    )


@pytest.mark.parametrize(
    ("text", "scene", "act"),
    (
        ("小维", InteractionScene.DIRECT_ADDRESS, ResponseAct.ACKNOWLEDGE),
        ("小维，谢谢你", InteractionScene.SOCIAL_RESPONSE, ResponseAct.RECIPROCATE),
        ("小维，来比比", InteractionScene.DIRECT_ADDRESS, ResponseAct.PLAYFUL_REPLY),
        ("小维，叫你老婆行吗", InteractionScene.DIRECT_ADDRESS, ResponseAct.BOUNDARY),
    ),
)
def test_high_confidence_reference_rules(text, scene, act):
    label = ReferenceLabeler("小维", "20002").label(example(text))
    assert label.scene is scene
    assert label.act is act
    assert label.confidence is AssociationConfidence.HIGH


def test_ambiguous_task_status_is_sent_to_review():
    label = ReferenceLabeler("小维", "20002").label(
        example("小维，帮我执行这个操作", response_text="我看看")
    )
    assert label.confidence is AssociationConfidence.REVIEW
    assert "task_status_ambiguous" in label.reason_codes


def test_observed_silence_has_no_reference_response_act():
    label = ReferenceLabeler("小维", "20002").label(
        example("小维，在吗", replied=False)
    )
    assert label.scene is InteractionScene.DIRECT_ADDRESS
    assert label.act is None
    assert label.confidence is AssociationConfidence.HIGH


def test_covered_or_ambiguous_association_is_not_ground_truth():
    item = example("小维，在吗", replied=False)
    item = item.__class__(
        item.sample_id, item.source, item.context, None, False, True,
        "multiple_source_candidates",
    )
    label = ReferenceLabeler("小维", "20002").label(item)
    assert label.confidence is AssociationConfidence.REVIEW


def test_visual_reaction_and_missing_object_are_conservative():
    visual = ReferenceLabeler("小维", "20002").label(
        example("小维，看看这个", media=True)
    )
    missing = ReferenceLabeler("小维", "20002").label(
        example("小维，帮我翻译一下", response_text="要翻译哪一句？")
    )
    assert visual.act is ResponseAct.VISUAL_REACTION
    assert missing.act is ResponseAct.CLARIFY


def test_overrides_validate_ids_enums_and_duplicates(tmp_path):
    path = tmp_path / "overrides.jsonl"
    path.write_text(
        json.dumps({
            "sample_id": "sample-1",
            "scene": "task_request",
            "act": "task_unsupported",
        }) + "\n",
        encoding="utf-8",
    )
    overrides = load_overrides(path)
    applied = apply_overrides(
        {"sample-1": ReferenceLabeler("小维", "20002").label(example("未知"))},
        overrides,
    )
    assert applied["sample-1"].act is ResponseAct.TASK_UNSUPPORTED


def test_review_labels_become_local_review_items():
    item = example("小维，帮我执行这个操作", response_text="我看看")
    labels = {item.sample_id: ReferenceLabeler("小维", "20002").label(item)}
    reviews = collect_label_reviews((item,), labels)
    assert len(reviews) == 1
    assert reviews[0].sample_id == item.sample_id
    assert reviews[0].reason == "task_status_ambiguous"
```

- [ ] **Step 2: Run label tests and verify failure**

Run: `python3.7 -m pytest tests/test_reference_labeler.py -q`

Expected: collection fails because `eval.reference_labeler` does not exist.

- [ ] **Step 3: Implement independent labeling**

Create `eval/reference_labeler.py`. It may import only Phase 3 contracts and the
domain enums `InteractionScene` and `ResponseAct`; it must not import
`groupmate.core.scenes`, `groupmate.engine.triggers`, `ReplyIntentPlanner`, or
`OpportunityArbiter`.

Use ordered regular expressions with safety precedence:

```python
_BOUNDARY = re.compile(r"(?:老婆|老公|亲一下|摸摸|隐私|密码|住址|去死|滚|骚扰)", re.I)
_TASK = re.compile(r"(?:帮我|麻烦你|请你|给我).{0,16}(?:看|查|找|搜|识别|翻译|执行|处理|发送|导出)")
_MISSING_OBJECT = re.compile(r"(?:帮我|麻烦你|请你)(?:翻译|看看|查查|处理)(?:一下)?[。！？?!]*$")
_SOCIAL = re.compile(r"(?:谢谢|感谢|厉害|真棒|喜欢你|给你|送你|牛奶|礼物)")
_PLAYFUL = re.compile(r"(?:比比|逗|捏|哈哈|开玩笑|不服|来战)")
_QUESTION = re.compile(r"[？?]|(?:吗|呢|怎么|什么|谁|哪)$")
_UNSUPPORTED_REPLY = re.compile(r"(?:做不了|不能做|没法|不支持|办不到)")


class ReferenceLabeler:
    def __init__(self, target_alias: str, target_uin: str) -> None:
        self.target_alias = str(target_alias)
        self.target_uin = str(target_uin)

    def label(self, example: BehaviorExample) -> ReferenceLabel:
        if example.covered_context or example.review_reason:
            return self._review(
                InteractionScene.AMBIENT_CONTRIBUTION,
                None,
                "association_ambiguous",
            )
        candidate = self._classify(example)
        if not example.observed_replied:
            return ReferenceLabel(
                scene=candidate.scene,
                act=None,
                confidence=candidate.confidence,
                reason_codes=candidate.reason_codes + ("observed_silence",),
            )
        return candidate

    def _classify(self, example: BehaviorExample) -> ReferenceLabel:
        text = example.source.text
        directed = self._directed(example.source)
        if _BOUNDARY.search(text):
            return self._high(InteractionScene.DIRECT_ADDRESS, ResponseAct.BOUNDARY, "boundary_signal")
        if _TASK.search(text):
            if _MISSING_OBJECT.search(text) and self._response_is_question(example):
                return self._high(InteractionScene.TASK_REQUEST, ResponseAct.CLARIFY, "missing_task_object")
            if example.source.has_media:
                return self._high(InteractionScene.TASK_REQUEST, ResponseAct.TASK_HANDOFF, "visual_task")
            if self._response_matches(example, _UNSUPPORTED_REPLY):
                return self._high(InteractionScene.TASK_REQUEST, ResponseAct.TASK_UNSUPPORTED, "explicit_limitation")
            return self._review(InteractionScene.TASK_REQUEST, ResponseAct.TASK_HANDOFF, "task_status_ambiguous")
        if _SOCIAL.search(text) and directed:
            return self._high(InteractionScene.SOCIAL_RESPONSE, ResponseAct.RECIPROCATE, "social_signal")
        if _PLAYFUL.search(text) and directed:
            return self._high(InteractionScene.DIRECT_ADDRESS, ResponseAct.PLAYFUL_REPLY, "playful_signal")
        if example.source.has_media and directed:
            return self._high(InteractionScene.DIRECT_ADDRESS, ResponseAct.VISUAL_REACTION, "visual_signal")
        if self._bare_alias(text):
            return self._high(InteractionScene.DIRECT_ADDRESS, ResponseAct.ACKNOWLEDGE, "bare_alias")
        if example.source.reply_to_sender_uin == self.target_uin:
            return self._high(InteractionScene.REPLY_TO_BOT, ResponseAct.ANSWER, "reply_to_target")
        if _QUESTION.search(text) and directed:
            return self._high(InteractionScene.DIRECT_ADDRESS, ResponseAct.ANSWER, "direct_question")
        scene = (
            InteractionScene.DIRECT_ADDRESS
            if directed else InteractionScene.AMBIENT_CONTRIBUTION
        )
        return self._review(scene, ResponseAct.ANSWER, "semantic_ambiguity")
```

Implement JSONL overrides as a tuple of immutable `LabelOverride` values. Reject
unknown keys, duplicate sample IDs, invalid `InteractionScene`/`ResponseAct`
values, missing sample IDs, and overrides for samples absent from the current run.
An override may use JSON `null` for `act` only when the example is observed
silence. Applied overrides return `AssociationConfidence.HIGH` with reason code
`human_override`. `collect_label_reviews(examples, labels)` returns one
`LocalReviewItem` for every remaining `REVIEW` label, with the source event and
its response-run events kept in memory for the local queue.

- [ ] **Step 4: Run label and extraction tests**

Run: `python3.7 -m pytest tests/test_reference_labeler.py tests/test_shadow_extract.py -q`

Expected: all tests pass.

- [ ] **Step 5: Commit reference labeling**

```bash
git add eval/reference_labeler.py tests/test_reference_labeler.py
git commit -m "feat: label shadow references conservatively"
```

### Task 5: Project Current Groupmate Mechanics Without Side Effects

**Files:**
- Create: `eval/shadow_projector.py`
- Create: `tests/test_shadow_projector.py`

- [ ] **Step 1: Write failing projector tests**

Create `tests/test_shadow_projector.py` using synthetic `BehaviorExample` values:

```python
import pytest

from eval.shadow_extract import LocalIdHasher
from eval.shadow_projector import ShadowProjector
from groupmate.core.response_act import ResponseAct
from groupmate.models import GroupPolicy, InteractionScene
from tests.test_reference_labeler import example


@pytest.fixture
def projector():
    return ShadowProjector(
        GroupPolicy(
            aliases=("爱弥斯",),
            spontaneous_cooldown_seconds=0,
            humanize_delay_enabled=False,
        ),
        LocalIdHasher(b"a" * 32),
        target_uin="20002",
        target_alias="小维",
        current_alias="爱弥斯",
    )


def test_direct_social_boundary_and_vision_task_projection(projector):
    direct = projector.project(example("小维"))
    social = projector.project(example("小维，谢谢你"))
    boundary = projector.project(example("小维，叫你老婆行吗"))
    visual_task = projector.project(example("小维，帮我看看这张图", media=True))

    assert direct.trigger == "alias_direct"
    assert direct.act is ResponseAct.ACKNOWLEDGE
    assert direct.quote_allowed is True
    assert social.scene is InteractionScene.SOCIAL_RESPONSE
    assert social.decorative_media_allowed is True
    assert boundary.act is ResponseAct.BOUNDARY
    assert boundary.decorative_media_allowed is False
    assert visual_task.act is ResponseAct.TASK_HANDOFF
    assert visual_task.capability_media_allowed is False


def test_external_knowledge_has_exactly_one_agent_owner(projector):
    projected = projector.project(
        example("搜索今天发布的公告", mentions_target=True)
    )
    assert projected.owner == "astrbot_agent"
    assert projected.owner_count == 1
    assert projected.would_reply is True
    assert "external_handoff" in projected.reason_codes


def test_ordinary_ambient_message_can_project_silence(projector):
    projected = projector.project(example("今天天气还行", replied=False))
    assert projected.owner == "observe_only"
    assert projected.trigger == "candidate"
    assert projected.scene is InteractionScene.AMBIENT_CONTRIBUTION


def test_projection_never_calls_generation_delivery_memory_or_capabilities(monkeypatch, projector):
    monkeypatch.setattr(
        "groupmate.engine.workflow.CognitiveWorkflow.evaluate",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("workflow called")),
    )
    projected = projector.project(example("小维，在吗"))
    assert projected.would_reply is True
```

- [ ] **Step 2: Run projector tests and verify failure**

Run: `python3.7 -m pytest tests/test_shadow_projector.py -q`

Expected: collection fails because `eval.shadow_projector` does not exist.

- [ ] **Step 3: Implement the pure projector**

Create `eval/shadow_projector.py`. Construct only the following production pure
components: `TriggerRouter`, `classify_scene`, `policy_for_scene`,
`AddresseeResolver`, `OpportunityArbiter`, `ReplyIntentPlanner`,
`ReactionPolicy`, and `needs_external_knowledge`.

The public class is:

```python
class ShadowProjector:
    def __init__(
        self,
        policy: GroupPolicy,
        hasher: LocalIdHasher,
        *,
        target_uin: str,
        target_alias: str,
        current_alias: str,
    ) -> None:
        self.policy = replace(policy, aliases=(current_alias,))
        self.hasher = hasher
        self.target_uin = str(target_uin)
        self.target_alias = str(target_alias)
        self.current_alias = str(current_alias)
        self.addressee = AddresseeResolver()
        self.arbiter = OpportunityArbiter(
            send_limiter=SlidingWindowRateLimiter(
                hourly_limit=max(100000, policy.spontaneous_hourly_limit),
                cooldown_seconds=0,
            )
        )
        self.planner = ReplyIntentPlanner()
        self.reactions = ReactionPolicy()

    def project(self, example: BehaviorExample) -> ShadowProjection:
        topic = self._topic(example)
        latest = topic.latest
        trigger = TriggerRouter(self.policy).classify(latest)
        if (
            trigger.kind is TriggerKind.NATIVE_DIRECT
            and needs_external_knowledge(latest.text)
        ):
            return ShadowProjection(
                sample_id=example.sample_id,
                owner="astrbot_agent",
                would_reply=True,
                trigger=trigger.kind.value,
                scene=InteractionScene.TASK_REQUEST,
                act=ResponseAct.TASK_HANDOFF,
                quote_allowed=True,
                decorative_media_allowed=False,
                capability_media_allowed=False,
                ambiguous_target=False,
                owner_count=1,
                completion_claim_allowed=False,
                reason_codes=("external_handoff",),
            )
        scene = classify_scene(trigger.kind, latest)
        targeting = self.addressee.resolve(
            topic,
            trigger.kind,
            aliases=self.policy.aliases,
            relationships={},
        )
        opportunity = self.arbiter.evaluate(
            topic,
            trigger.kind,
            self.policy,
            targeting,
            now=latest.timestamp,
            recent_outputs=(),
            favorability=None,
        )
        task_resolution = self._task_resolution(scene, latest)
        intent = self.planner.plan(
            opportunity,
            topic,
            targeting,
            decision_id=example.sample_id,
            scene=scene,
            aliases=self.policy.aliases,
            task_resolution=task_resolution,
        )
        act = intent.response_act.act if intent and intent.response_act else None
        ambiguous = (
            targeting.reply_audience.kind is AddresseeKind.AMBIGUOUS
            or targeting.social_target.kind is AddresseeKind.AMBIGUOUS
        )
        quote_allowed = policy_for_scene(scene).should_quote(
            interleaved=self._interleaved(topic, opportunity.target_message_id)
        )
        decorative = bool(
            act is not None and self.reactions.allowed(act, scene, ambiguous)
        )
        capability_media = False
        owner = (
            "groupmate"
            if opportunity.action is OpportunityAction.SPEAK
            else "observe_only"
        )
        return ShadowProjection(
            sample_id=example.sample_id,
            owner=owner,
            would_reply=opportunity.action is OpportunityAction.SPEAK,
            trigger=trigger.kind.value,
            scene=scene,
            act=act,
            quote_allowed=quote_allowed,
            decorative_media_allowed=decorative,
            capability_media_allowed=capability_media,
            ambiguous_target=ambiguous,
            owner_count=1,
            completion_claim_allowed=False,
            reason_codes=tuple(opportunity.reason_codes),
        )
```

`_topic` must create `ChatMessage` values only in memory. Replace the target alias
with the current alias; hash non-target sender IDs; map prior target events to
`sender_id="__target_bot__"` and `is_bot=True`; map reply/mention evidence to
`reply_to_bot` and `mentions_bot`; use `("shadow://media",)` only as an in-memory
image placeholder; never preserve exporter media URLs. Use integer seconds for
`ChatMessage.timestamp`.

`_task_resolution` returns supported `vision` only for task scenes with media and
enabled vision; all other task scenes are unsupported. The two built-in
capabilities (`vision` and `external_handoff`) do not produce outbound media, so
the offline projector keeps `capability_media_allowed=False` without executing
them. `_interleaved` copies the current workflow's pure interleaving rule without
instantiating the workflow.

- [ ] **Step 4: Run projector, planner, scene, and opportunity tests**

Run: `python3.7 -m pytest tests/test_shadow_projector.py tests/test_triggers.py tests/test_scenes.py tests/test_opportunity.py tests/test_reply_intent.py -q`

Expected: all tests pass.

- [ ] **Step 5: Commit the projector**

```bash
git add eval/shadow_projector.py tests/test_shadow_projector.py
git commit -m "feat: project current mechanics offline"
```

### Task 6: Build Conditional Diff Reports And Privacy Gate

**Files:**
- Create: `eval/behavior_diff.py`
- Create: `tests/test_behavior_diff.py`

- [ ] **Step 1: Write failing diff and privacy tests**

Create `tests/test_behavior_diff.py`:

```python
import json

import pytest

from eval.behavior_diff import (
    PrivacyViolation,
    assert_shareable_report,
    build_diff_report,
    render_markdown,
    write_json_report,
)
from eval.shadow_models import (
    AssociationConfidence,
    ExportSummary,
    ReferenceLabel,
    ShadowProjection,
)
from groupmate.core.response_act import ResponseAct
from groupmate.models import InteractionScene
from tests.test_reference_labeler import example


def label(scene, act):
    return ReferenceLabel(scene, act, AssociationConfidence.HIGH, ("test",))


def projection(sample_id, *, reply=True, scene=InteractionScene.DIRECT_ADDRESS, act=ResponseAct.ACKNOWLEDGE, quote=True, media=False):
    return ShadowProjection(
        sample_id, "groupmate", reply, "alias_direct", scene, act,
        quote, media, False, False, 1, False, ("test",),
    )


def test_report_groups_reply_scene_act_quote_and_media_mismatches():
    first = example("小维")
    second = example("小维，谢谢你")
    first = first.__class__("sample-a", first.source, first.context, first.response_run, True, False, "")
    second = second.__class__("sample-b", second.source, second.context, second.response_run, True, False, "")
    report = build_diff_report(
        ExportSummary(2, 2, 1, 0, 0, 0, 1),
        (first, second),
        {
            "sample-a": label(InteractionScene.DIRECT_ADDRESS, ResponseAct.ACKNOWLEDGE),
            "sample-b": label(InteractionScene.SOCIAL_RESPONSE, ResponseAct.RECIPROCATE),
        },
        {
            "sample-a": projection("sample-a"),
            "sample-b": projection("sample-b", reply=False, scene=InteractionScene.DIRECT_ADDRESS, act=None, quote=False),
        },
        review_count=1,
        configuration={"pipeline_version": "phase3-v1"},
    )
    assert report["reply_confusion"]["target_reply_projected_silence"] == 1
    assert report["scene_confusion"]["social_response"]["direct_address"] == 1
    assert report["mismatches"]["reply"] == ["sample-b"]
    assert "runtime_probability" not in json.dumps(report)


def test_shareable_report_rejects_sensitive_keys_and_values():
    with pytest.raises(PrivacyViolation):
        assert_shareable_report(
            {"target_uin": "900000001"},
            forbidden_identifiers=("900000001",),
            forbidden_texts=(),
        )
    with pytest.raises(PrivacyViolation):
        assert_shareable_report(
            {"summary": "原始聊天长句泄漏"},
            forbidden_identifiers=(),
            forbidden_texts=("原始聊天长句泄漏",),
        )


def test_json_and_markdown_are_deterministic(tmp_path):
    report = {
        "schema_version": 1,
        "configuration": {"pipeline_version": "phase3-v1"},
        "counts": {"examples": 2},
        "reply_confusion": {"target_reply_projected_silence": 1},
        "scene_confusion": {},
        "act_confusion": {},
        "quote": {},
        "media": {},
        "run_diagnostics": {},
        "violations": {},
        "mismatches": {"reply": ["sample-b"]},
    }
    target = tmp_path / "report.json"
    write_json_report(report, target)
    first = target.read_text(encoding="utf-8")
    write_json_report(report, target)
    assert target.read_text(encoding="utf-8") == first
    assert "sample-b" in render_markdown(report)
```

- [ ] **Step 2: Run diff tests and verify failure**

Run: `python3.7 -m pytest tests/test_behavior_diff.py -q`

Expected: collection fails because `eval.behavior_diff` does not exist.

- [ ] **Step 3: Implement report aggregation and privacy checks**

Create `eval/behavior_diff.py` with:

```python
class PrivacyViolation(ValueError):
    pass


_SENSITIVE_KEYS = frozenset({
    "target_uin", "sender_uin", "sender_uid", "sender_name", "group_name",
    "text", "source_text", "response_text", "media_url", "filename", "md5",
})
_MEDIA_VALUE = re.compile(
    r"(?:https?://|download\?|fileid=|\bmd5\b|\.(?:png|jpe?g|gif|webp|mp4|mp3)\b)",
    re.I,
)


def build_diff_report(
    summary: ExportSummary,
    examples: Sequence[BehaviorExample],
    labels: Mapping[str, ReferenceLabel],
    projections: Mapping[str, ShadowProjection],
    *,
    review_count: int,
    configuration: Mapping[str, object],
) -> Dict[str, object]:
    aligned = tuple(
        (item, labels[item.sample_id], projections[item.sample_id])
        for item in examples
        if item.sample_id in labels and item.sample_id in projections
    )
    return {
        "schema_version": 1,
        "configuration": dict(sorted(configuration.items())),
        "counts": _build_counts(summary, examples, aligned, review_count),
        "reply_confusion": _reply_confusion(aligned),
        "scene_confusion": _enum_confusion(aligned, "scene"),
        "act_confusion": _enum_confusion(aligned, "act"),
        "quote": _quote_comparison(aligned),
        "media": _media_comparison(aligned),
        "run_diagnostics": _run_diagnostics(aligned),
        "violations": _boundary_violations(aligned),
        "mismatches": _mismatch_ids(aligned),
    }


def assert_shareable_report(
    report: Mapping[str, object],
    forbidden_identifiers: Sequence[str],
    forbidden_texts: Sequence[str],
) -> None:
    identifiers = frozenset(value for value in forbidden_identifiers if value)
    raw_texts = tuple(value for value in forbidden_texts if len(value) >= 8)

    def walk(value, path):
        if isinstance(value, Mapping):
            for key, child in value.items():
                if key in _SENSITIVE_KEYS:
                    raise PrivacyViolation("sensitive key at {}".format(path + (key,)))
                walk(child, path + (str(key),))
        elif isinstance(value, (list, tuple)):
            for index, child in enumerate(value):
                walk(child, path + (str(index),))
        elif isinstance(value, str):
            if value in identifiers:
                raise PrivacyViolation("export identifier at {}".format(path))
            if _MEDIA_VALUE.search(value):
                raise PrivacyViolation("media locator at {}".format(path))
            if any(raw in value for raw in raw_texts):
                raise PrivacyViolation("raw export text at {}".format(path))

    walk(report, ())


def write_json_report(report: Mapping[str, object], path: Path) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def render_markdown(report: Mapping[str, object]) -> str:
    titles = (
        ("configuration", "Configuration"),
        ("counts", "Counts"),
        ("reply_confusion", "Reply Confusion"),
        ("scene_confusion", "Scene Confusion"),
        ("act_confusion", "Act Confusion"),
        ("quote", "Quote"),
        ("media", "Media"),
        ("run_diagnostics", "Run Diagnostics"),
        ("violations", "Violations"),
        ("mismatches", "Mismatches"),
    )
    lines = ["# Export Shadow Alignment"]
    for key, title in titles:
        lines.extend([
            "",
            "## " + title,
            "",
            "```json",
            json.dumps(report[key], ensure_ascii=False, indent=2, sort_keys=True),
            "```",
        ])
    return "\n".join(lines) + "\n"


def write_markdown_report(report: Mapping[str, object], path: Path) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(render_markdown(report), encoding="utf-8")
```

The report schema must be deterministic and contain only:

- `schema_version`;
- `configuration` with fixed pipeline/mechanics versions and extraction windows;
- `counts` with manifest/observed/target/excluded/duplicates/chunks/examples,
  linked, silence, covered, high-confidence, and review values;
- `reply_confusion` with four explicit target/projected cells;
- nested sorted `scene_confusion` and `act_confusion` mappings;
- `quote` and `media` with conditional match/mismatch counts;
- `run_diagnostics` with run-length, reply-character, and latency buckets;
- `violations` with boundary media, ambiguous media, false completion eligibility,
  and multiple-owner counts. `false_completion_eligibility` counts projections
  whose `completion_claim_allowed` invariant is true; `multiple_owner` counts
  projections whose `owner_count` is not exactly one;
- `mismatches` mapping categories to sorted anonymous sample IDs.

Implement each aggregation helper with sorted keys and sorted mismatch IDs. Quote
and act comparisons apply only when both the target and projection reply; act
comparison additionally requires non-null acts. Scene comparison applies to every
aligned high-confidence opportunity. Media comparisons distinguish observed run
media, projected decorative media, and projected capability media, but report a
mismatch only when both sides reply.
Latency is the first response timestamp minus the source timestamp and uses fixed
buckets `0-2s`, `2-5s`, `5-15s`, `15-60s`, and `60s+`.

Do not include a current timestamp. `_MEDIA_VALUE` rejects strings matching URL,
`download?`, `fileid=`, `md5`, and common media suffix patterns.
`assert_shareable_report` rejects sensitive keys, every exact exporter identifier
regardless of length, and forbidden raw-text substrings of at least eight
characters. Report construction is a closed schema, so arbitrary identifier
fields cannot enter it; enum values, reason-code names, headings, and anonymous
IDs matching `^sample-[0-9a-f]{20}$` are the only string value classes created by
aggregation. Build Markdown only from the shareable report, never from examples.

- [ ] **Step 4: Run diff and metric tests**

Run: `python3.7 -m pytest tests/test_behavior_diff.py tests/test_behavior_metrics.py tests/test_scene_metrics.py -q`

Expected: all tests pass.

- [ ] **Step 5: Commit reporting**

```bash
git add eval/behavior_diff.py tests/test_behavior_diff.py
git commit -m "feat: report conditional behavior differences"
```

### Task 7: End-To-End CLI And Local Review Queue

**Files:**
- Create: `eval/shadow_export.py`
- Create: `tests/test_shadow_export.py`
- Modify: `eval/README.md`

- [ ] **Step 1: Write failing CLI tests**

Create `tests/test_shadow_export.py`:

```python
import json

from eval.shadow_export import main
from tests.shadow_fixtures import message, write_export


def test_cli_writes_shareable_json_markdown_and_local_review(tmp_path):
    root = write_export(
        tmp_path / "export",
        [
            message("m1", "10001", "小维", 1000),
            message("m2", "20002", "在", 2000),
            message("m3", "10002", "普通群聊", 3000),
        ],
        target_uin="20002",
    )
    output = tmp_path / "results" / "report.json"
    markdown = tmp_path / "results" / "report.md"
    review = tmp_path / "results" / "review.jsonl"
    code = main([
        "--export-dir", str(root),
        "--target-uin", "20002",
        "--target-alias", "小维",
        "--current-alias", "爱弥斯",
        "--id-salt-file", str(tmp_path / "results" / ".salt"),
        "--output", str(output),
        "--markdown-output", str(markdown),
        "--review-output", str(review),
    ])
    assert code == 0
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["counts"]["manifest_records"] == 3
    assert "20002" not in output.read_text(encoding="utf-8")
    assert "小维" not in output.read_text(encoding="utf-8")
    assert markdown.is_file()
    assert review.is_file()
    rows = [json.loads(line) for line in review.read_text(encoding="utf-8").splitlines()]
    assert all(item["local_only"] is True for item in rows)


def test_cli_returns_nonzero_for_invalid_export(tmp_path, capsys):
    code = main([
        "--export-dir", str(tmp_path / "missing"),
        "--target-uin", "20002",
        "--target-alias", "小维",
        "--current-alias", "爱弥斯",
        "--id-salt-file", str(tmp_path / ".salt"),
        "--output", str(tmp_path / "report.json"),
    ])
    assert code == 2
    assert "manifest" in capsys.readouterr().err.lower()


def test_cli_and_projector_do_not_reference_effectful_modules():
    import inspect
    import eval.shadow_export as cli_module
    import eval.shadow_projector as projector_module

    source = inspect.getsource(cli_module) + inspect.getsource(projector_module)
    for forbidden in (
        "eval.providers", "CognitiveWorkflow", "capability_executor",
        "delivery_queue", "memory_store",
    ):
        assert forbidden not in source
```

- [ ] **Step 2: Run CLI tests and verify failure**

Run: `python3.7 -m pytest tests/test_shadow_export.py -q`

Expected: collection fails because `eval.shadow_export` does not exist.

- [ ] **Step 3: Implement orchestration**

Create `eval/shadow_export.py` with these exact command-line options:

```python
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Offline export shadow alignment")
    parser.add_argument("--export-dir", type=Path, required=True)
    parser.add_argument("--target-uin", required=True)
    parser.add_argument("--target-alias", required=True)
    parser.add_argument("--current-alias", required=True)
    parser.add_argument("--id-salt-file", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--markdown-output", type=Path)
    parser.add_argument("--review-output", type=Path)
    parser.add_argument("--overrides", type=Path)
    return parser
```

`main(argv=None)` must:

```python
def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        salt = load_or_create_salt(args.id_salt_file)
        hasher = LocalIdHasher(salt)
        ingest = load_export(args.export_dir, args.target_uin)
        examples, reviews = extract_behavior_examples(
            ingest, hasher, args.target_alias
        )
        labeler = ReferenceLabeler(args.target_alias, args.target_uin)
        labels = {item.sample_id: labeler.label(item) for item in examples}
        if args.overrides:
            labels = apply_overrides(labels, load_overrides(args.overrides))
        label_reviews = collect_label_reviews(examples, labels)
        all_reviews = _dedupe_reviews(reviews + label_reviews)
        projector = ShadowProjector(
            GroupPolicy(aliases=(args.current_alias,)),
            hasher,
            target_uin=args.target_uin,
            target_alias=args.target_alias,
            current_alias=args.current_alias,
        )
        projections = {
            item.sample_id: projector.project(item) for item in examples
        }
        high_confidence = {
            key: value
            for key, value in labels.items()
            if value.confidence is AssociationConfidence.HIGH
        }
        if not high_confidence:
            raise ValueError("no high-confidence alignment examples")
        report = build_diff_report(
            ingest.summary,
            examples,
            high_confidence,
            projections,
            review_count=len(all_reviews),
            configuration={
                "pipeline_version": "phase3-v1",
                "mechanics_version": "phase2-scene-act-v1",
                "run_gap_ms": 15000,
                "adjacent_gap_ms": 20000,
                "directed_gap_ms": 60000,
                "context_window_ms": 30000,
            },
        )
        identifiers, raw_texts = _privacy_values(ingest, args)
        assert_shareable_report(report, identifiers, raw_texts)
        write_json_report(report, args.output)
        if args.markdown_output:
            write_markdown_report(report, args.markdown_output)
        if args.review_output:
            write_review_queue(all_reviews, args.review_output)
    except (ExportValidationError, PrivacyViolation, OSError, TypeError, ValueError) as exc:
        print("shadow export failed: {}".format(exc), file=sys.stderr)
        return 2
    print(json.dumps(report["counts"], ensure_ascii=False, sort_keys=True))
    return 0
```

Implement `_dedupe_reviews` by `(sample_id, reason)`, preserving first-seen
order. Implement the local writer without exporter message or sender IDs:

```python
def write_review_queue(reviews, path: Path) -> None:
    rows = []
    for item in reviews:
        rows.append({
            "local_only": True,
            "sample_id": item.sample_id,
            "reason": item.reason,
            "source_excerpts": [event.text[:240] for event in item.source_events],
            "response_excerpts": [
                event.text[:240] for event in item.response_events
            ],
        })
    payload = "".join(
        json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows
    )
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(payload, encoding="utf-8")
```

`_privacy_values` returns two collections: identifiers contain the target UIN,
every non-empty sender UIN/key/name, and target alias; raw texts contain unique
source/response text values. The local
review writer is the only function allowed to serialize excerpts and must include
a top-level warning `local_only: true` on every JSONL item. It must never include
media locators because the ingest layer does not retain them.

- [ ] **Step 4: Document local use and privacy**

Add a `Phase 3 export shadow alignment` section to `eval/README.md` with the exact
real-data command from the spec, a statement that `eval/results/` and the salt are
local-only, the manual override JSONL schema, and an explicit warning that overall
ratios must not become runtime probabilities.

- [ ] **Step 5: Run all Phase 3 tests**

Run: `python3.7 -m pytest tests/test_shadow_models.py tests/test_export_ingest.py tests/test_shadow_extract.py tests/test_reference_labeler.py tests/test_shadow_projector.py tests/test_behavior_diff.py tests/test_shadow_export.py -q`

Expected: all tests pass.

- [ ] **Step 6: Commit CLI and documentation**

```bash
git add eval/shadow_export.py eval/README.md tests/test_shadow_export.py
git commit -m "feat: add offline export shadow cli"
```

### Task 8: Real Export Gate, Privacy Audit, And Full Regression

**Files:**
- Modify only files required by failures found in this task.

- [ ] **Step 1: Run the CLI against the confirmed real export**

Run:

```bash
python3.7 -m eval.shadow_export \
  --export-dir "$SHADOW_EXPORT_DIR" \
  --target-uin "$SHADOW_TARGET_UIN" \
  --target-alias 小维 \
  --current-alias 爱弥斯 \
  --id-salt-file eval/results/.shadow-id-salt \
  --output eval/results/phase3-shadow.json \
  --markdown-output eval/results/phase3-shadow.md \
  --review-output eval/results/phase3-review.jsonl
```

Expected: exit 0; printed counts show `manifest_records: 11304` and
`target_records: 1461`.

- [ ] **Step 2: Verify the shareable report contains no target identifiers or raw fields**

Run:

```bash
jq '{counts, reply_confusion, violations}' eval/results/phase3-shadow.json
rg -n '"target_uin"|"sender_uin"|"sender_uid"|"sender_name"|"group_name"|"text"|download\?|fileid=|md5' eval/results/phase3-shadow.json eval/results/phase3-shadow.md
```

Expected: `jq` shows counts and aggregate diagnostics; `rg` exits 1 with no
matches.

- [ ] **Step 3: Verify deterministic output with the same salt**

Run the command from Step 1 again with output
`eval/results/phase3-shadow-second.json`, then run:

```bash
cmp eval/results/phase3-shadow.json eval/results/phase3-shadow-second.json
```

Expected: exit 0 and no output.

- [ ] **Step 4: Run the full test suite**

Run: `python3.7 -m pytest -q`

Expected: all tests pass with no pending-task warnings.

- [ ] **Step 5: Run both deterministic evaluation gates**

```bash
python3.7 -m eval.runner --mode deterministic --enforce --output /tmp/groupmate-phase3-baseline.json
python3.7 -m eval.runner --mode deterministic --enforce --scenarios eval/scenarios/phase2_behavior.jsonl --output /tmp/groupmate-phase3-behavior.json
```

Expected: both commands exit 0 with `errors: 0` and `pass_rate: 1.0`.

- [ ] **Step 6: Run compatibility and repository checks**

```bash
python3.7 -m compileall -q groupmate eval tests
python3.7 -S -c 'import groupmate.capabilities; import eval.shadow_export'
git diff --check
git status --short
```

Expected: compile/import/diff commands exit 0. `git status --short` shows no
tracked or untracked Phase 3 result artifacts because `eval/results/` is ignored.

- [ ] **Step 7: Commit any verification-only fixes**

If and only if Steps 1-6 required a code correction, commit the minimal correction
with its regression test:

```bash
Stage only the explicit source file and its regression test that were changed by
the failed verification; do not use `git add .`. Then commit them with:

```bash
git commit -m "fix: harden export shadow alignment"
```
```

If no correction was required, do not create an empty commit.

### Task 9: Completion Review

**Files:**
- Modify only files required by review findings.

- [ ] **Step 1: Review the complete Phase 3 diff against the approved spec**

Run:

```bash
git diff --stat 10a35ce..HEAD
git diff --check 10a35ce..HEAD
git log --oneline 10a35ce..HEAD
```

Verify every acceptance criterion maps to a test or real-data gate, no raw target
content entered Git, and no production runtime behavior changed.

- [ ] **Step 2: Re-run verification after any review fix**

Run: `python3.7 -m pytest -q`

Expected: all tests pass.

- [ ] **Step 3: Use the completion workflow**

Invoke `superpowers:verification-before-completion`, then
`superpowers:finishing-a-development-branch`. Report the local report paths,
counts, mismatch categories, review-queue size, test totals, evaluation gates, and
branch integration options. Do not push without explicit user instruction.
