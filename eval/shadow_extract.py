"""Conservative response-run extraction for offline shadow evaluation."""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
from dataclasses import replace
from pathlib import Path
from typing import Dict, List, Sequence, Tuple

from eval.shadow_models import (
    AssociationConfidence,
    BehaviorExample,
    ExportEvent,
    IngestResult,
    LocalReviewItem,
    ResponseRun,
)


class LocalIdHasher:
    def __init__(self, salt: bytes) -> None:
        if not isinstance(salt, bytes) or len(salt) != 32:
            raise ValueError("local id salt must contain exactly 32 bytes")
        self._salt = salt

    def _digest(self, namespace: str, value: str) -> str:
        return hmac.new(
            self._salt,
            (namespace + str(value)).encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

    def sample_id(self, message_id: str) -> str:
        return "sample-" + self._digest("", message_id)[:20]

    def sender_id(self, sender_key: str) -> str:
        return "u-" + self._digest("sender:", sender_key)[:16]

    def run_id(self, message_id: str) -> str:
        return "run-" + self._digest("run:", message_id)[:20]


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
    target = str(target_alias or "").strip()
    current = str(current_alias or "").strip()
    if not target or not current:
        raise ValueError("both aliases are required")
    return str(text or "").replace(target, current)


def _positive_window(value: int, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError("{} must be a positive integer".format(name))
    return value


def _explicit_anchors(events: Sequence[ExportEvent]) -> Tuple[str, ...]:
    return tuple(dict.fromkeys(
        item.reply_to_message_id for item in events if item.reply_to_message_id
    ))


def _build_response_runs(
    events: Sequence[ExportEvent],
    target_uin: str,
    hasher: LocalIdHasher,
    *,
    run_gap_ms: int,
) -> Tuple[ResponseRun, ...]:
    runs: List[ResponseRun] = []
    current: List[ExportEvent] = []

    def flush() -> None:
        if not current:
            return
        anchors = _explicit_anchors(current)
        runs.append(ResponseRun(
            run_id=hasher.run_id(current[0].message_id),
            events=tuple(current),
            anchor_message_id=(anchors[0] if len(anchors) == 1 else ""),
            confidence=AssociationConfidence.REVIEW,
            reason_codes=("unassociated",),
            review_reason=("conflicting_explicit_anchors" if len(anchors) > 1 else ""),
        ))
        current[:] = []

    for event in events:
        if event.sender_uin != target_uin:
            flush()
            continue
        if current:
            gap = event.timestamp_ms - current[-1].timestamp_ms
            anchors = _explicit_anchors(current)
            incoming = event.reply_to_message_id
            conflict = bool(incoming and anchors and incoming not in anchors)
            if gap > run_gap_ms or conflict:
                flush()
        current.append(event)
    flush()
    return tuple(runs)


def _is_directed(event: ExportEvent, target_uin: str, target_alias: str) -> bool:
    return bool(
        (target_alias and target_alias in event.text)
        or target_uin in event.mentions
        or event.reply_to_sender_uin == target_uin
    )


def _preceding_humans(
    events: Sequence[ExportEvent],
    run: ResponseRun,
    target_uin: str,
    window_ms: int,
    after_ms: int = None,
) -> Tuple[ExportEvent, ...]:
    start = run.events[0].timestamp_ms
    return tuple(
        item for item in events
        if item.sender_uin != target_uin
        and 0 <= start - item.timestamp_ms <= window_ms
        and (after_ms is None or item.timestamp_ms > after_ms)
    )


def _review(
    run: ResponseRun,
    reason: str,
    sources: Sequence[ExportEvent],
) -> LocalReviewItem:
    return LocalReviewItem(
        sample_id=run.run_id,
        reason=reason,
        source_events=tuple(sources),
        response_events=run.events,
    )


def _associate_runs(
    events: Sequence[ExportEvent],
    runs: Sequence[ResponseRun],
    target_uin: str,
    target_alias: str,
    *,
    adjacent_gap_ms: int,
    directed_gap_ms: int,
) -> Tuple[Dict[str, ResponseRun], Tuple[LocalReviewItem, ...]]:
    humans = tuple(item for item in events if item.sender_uin != target_uin)
    by_id = {item.message_id: item for item in humans}
    linked: Dict[str, ResponseRun] = {}
    reviews: List[LocalReviewItem] = []
    previous_target_ms = None

    for run in runs:
        start = run.events[0].timestamp_ms
        candidate_floor = previous_target_ms
        previous_target_ms = run.events[-1].timestamp_ms
        anchors = _explicit_anchors(run.events)
        if run.review_reason or len(anchors) > 1:
            sources = tuple(by_id[item] for item in anchors if item in by_id)
            reviews.append(_review(run, "conflicting_explicit_anchors", sources))
            continue
        if len(anchors) == 1:
            source = by_id.get(anchors[0])
            if source is None:
                reviews.append(_review(run, "missing_reply_reference", ()))
                continue
            if source.timestamp_ms > start:
                reviews.append(_review(run, "timestamp_inversion", (source,)))
                continue
            if source.message_id in linked:
                reviews.append(_review(run, "multiple_response_runs", (source,)))
                continue
            linked[source.message_id] = replace(
                run,
                anchor_message_id=source.message_id,
                confidence=AssociationConfidence.HIGH,
                reason_codes=("explicit_reply",),
                review_reason="",
            )
            continue

        adjacent = _preceding_humans(
            events,
            run,
            target_uin,
            adjacent_gap_ms,
            after_ms=candidate_floor,
        )
        if len(adjacent) == 1:
            source = adjacent[0]
            linked[source.message_id] = replace(
                run,
                anchor_message_id=source.message_id,
                confidence=AssociationConfidence.HIGH,
                reason_codes=("adjacent_unique",),
                review_reason="",
            )
            continue
        if len(adjacent) > 1:
            reviews.append(_review(run, "multiple_source_candidates", adjacent))
            continue

        directed = tuple(
            item for item in _preceding_humans(
                events,
                run,
                target_uin,
                directed_gap_ms,
                after_ms=candidate_floor,
            )
            if _is_directed(item, target_uin, target_alias)
        )
        if len(directed) == 1:
            source = directed[0]
            linked[source.message_id] = replace(
                run,
                anchor_message_id=source.message_id,
                confidence=AssociationConfidence.HIGH,
                reason_codes=("directed_unique",),
                review_reason="",
            )
        elif len(directed) > 1:
            reviews.append(_review(run, "multiple_directed_candidates", directed))
        else:
            reviews.append(_review(run, "no_source_candidate", ()))
    return linked, tuple(reviews)


def _covered_message_ids(
    events: Sequence[ExportEvent],
    linked: Dict[str, ResponseRun],
    reviews: Sequence[LocalReviewItem],
    target_uin: str,
    window_ms: int,
) -> Tuple[str, ...]:
    covered = set()
    for anchor_id, run in linked.items():
        for source in _preceding_humans(events, run, target_uin, window_ms):
            if source.message_id != anchor_id:
                covered.add(source.message_id)
    for review in reviews:
        if not review.response_events:
            continue
        start = review.response_events[0].timestamp_ms
        for source in events:
            if (
                source.sender_uin != target_uin
                and 0 <= start - source.timestamp_ms <= window_ms
            ):
                covered.add(source.message_id)
        covered.update(item.message_id for item in review.source_events)
    return tuple(sorted(covered))


def extract_behavior_examples(
    ingest: IngestResult,
    hasher: LocalIdHasher,
    target_alias: str,
    run_gap_ms: int = 15000,
    adjacent_gap_ms: int = 20000,
    directed_gap_ms: int = 60000,
) -> Tuple[Tuple[BehaviorExample, ...], Tuple[LocalReviewItem, ...]]:
    if not isinstance(ingest, IngestResult):
        raise TypeError("ingest must be an IngestResult")
    if not isinstance(hasher, LocalIdHasher):
        raise TypeError("hasher must be a LocalIdHasher")
    alias = str(target_alias or "").strip()
    if not alias:
        raise ValueError("target_alias is required")
    run_gap_ms = _positive_window(run_gap_ms, "run_gap_ms")
    adjacent_gap_ms = _positive_window(adjacent_gap_ms, "adjacent_gap_ms")
    directed_gap_ms = _positive_window(directed_gap_ms, "directed_gap_ms")

    eligible = tuple(item for item in ingest.events if item.content_eligible)
    runs = _build_response_runs(
        eligible, ingest.target_uin, hasher, run_gap_ms=run_gap_ms
    )
    linked, reviews = _associate_runs(
        eligible,
        runs,
        ingest.target_uin,
        alias,
        adjacent_gap_ms=adjacent_gap_ms,
        directed_gap_ms=directed_gap_ms,
    )
    covered = set(_covered_message_ids(
        eligible, linked, reviews, ingest.target_uin, 30000
    ))
    review_reasons = {}
    for review in reviews:
        for source in review.source_events:
            review_reasons.setdefault(source.message_id, review.reason)

    examples = []
    for index, source in enumerate(eligible):
        if source.sender_uin == ingest.target_uin:
            continue
        run = linked.get(source.message_id)
        examples.append(BehaviorExample(
            sample_id=hasher.sample_id(source.message_id),
            source=source,
            context=tuple(eligible[max(0, index - 5):index + 1]),
            response_run=run,
            observed_replied=run is not None,
            covered_context=source.message_id in covered,
            review_reason=review_reasons.get(source.message_id, ""),
        ))
    return tuple(examples), reviews
