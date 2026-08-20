"""Build de-identified candidate windows for manual evaluation labeling."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping

from groupmate.social_runtime.contracts import SocialEventEnvelope

from .export_ingest import TARGET_BOT_QQ
from .ownership import ReferenceTriggerMatch, ReferenceTriggerPolicy


_URL = re.compile(r"https?://\S+", re.IGNORECASE)
_QQ_LIKE_NUMBER = re.compile(r"(?<!\d)\d{5,12}(?!\d)")


@dataclass(frozen=True)
class CorpusBuildSummary:
    candidate_count: int
    output_path: Path
    mapping_path: Path


@dataclass(frozen=True)
class ReviewQueueSummary:
    calibration_count: int
    holdout_count: int
    output_path: Path
    mapping_path: Path


class _IdentityMapping:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.values: dict[str, dict[str, str]] = {
            "groups": {},
            "actors": {},
            "messages": {},
            "evidence": {},
            "names": {},
            "numbers": {},
        }
        if path.exists():
            raw = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(raw, Mapping) or raw.get("version") != 1:
                raise ValueError("identity mapping version is unsupported")
            for kind in self.values:
                entries = raw.get(kind, {})
                if not isinstance(entries, Mapping):
                    raise ValueError(f"identity mapping {kind} must be an object")
                self.values[kind] = {
                    str(source): str(alias) for source, alias in entries.items()
                }

    def alias(self, kind: str, source: object, *, prefix: str) -> str | None:
        value = str(source or "").strip()
        if not value:
            return None
        entries = self.values[kind]
        existing = entries.get(value)
        if existing is not None:
            return existing
        alias = f"{prefix}:{len(entries) + 1:06d}"
        entries[value] = alias
        return alias

    def actor(self, source: object, *, is_self: bool = False) -> str | None:
        value = str(source or "").strip()
        if not value:
            return None
        entries = self.values["actors"]
        existing = entries.get(value)
        if existing is not None:
            return existing
        if is_self or value == TARGET_BOT_QQ:
            entries[value] = "bot:target"
            return "bot:target"
        alias = f"member:{len([item for item in entries.values() if item.startswith('member:')]) + 1:03d}"
        entries[value] = alias
        return alias

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"version": 1, **self.values}
        descriptor = os.open(
            self.path,
            os.O_WRONLY | os.O_CREAT | os.O_TRUNC,
            0o600,
        )
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
                handle.write("\n")
        finally:
            os.chmod(self.path, 0o600)


def _private_replacements(
    events: tuple[SocialEventEnvelope, ...], mapping: _IdentityMapping
) -> tuple[tuple[str, str], ...]:
    replacements: dict[str, str] = {}

    def register(source: object, alias: str | None) -> None:
        value = str(source or "").strip()
        if value and alias:
            replacements.setdefault(value, f"[{alias}]")

    register(TARGET_BOT_QQ, mapping.actor(TARGET_BOT_QQ, is_self=True))
    for event in events:
        is_self = bool(event.payload.get("is_self"))
        register(
            event.group_id,
            mapping.alias("groups", event.group_id, prefix="group"),
        )
        register(event.actor_id, mapping.actor(event.actor_id, is_self=is_self))
        register(
            event.source_message_id,
            mapping.alias("messages", event.source_message_id, prefix="message"),
        )
        sender = event.payload.get("sender")
        if isinstance(sender, Mapping):
            name = str(sender.get("name") or "").strip()
            if len(name) >= 2:
                register(name, mapping.alias("names", name, prefix="name"))
        for mention in event.payload.get("mentions", ()):
            register(
                mention,
                mapping.actor(
                    mention,
                    is_self=str(mention or "") == TARGET_BOT_QQ,
                ),
            )
    return tuple(
        sorted(
            replacements.items(),
            key=lambda item: (-len(item[0]), item[0]),
        )
    )


def _safe_text(
    text: object,
    replacements: tuple[tuple[str, str], ...],
    mapping: _IdentityMapping,
) -> str:
    value = _URL.sub("[url]", str(text or ""))
    protected: list[tuple[str, str]] = []
    for index, (private, alias) in enumerate(replacements):
        if private in value:
            number = index
            letters = []
            while True:
                letters.append(chr(ord("a") + number % 26))
                number = number // 26 - 1
                if number < 0:
                    break
            token = f"\x00private:{''.join(reversed(letters))}\x00"
            value = value.replace(private, token)
            protected.append((token, alias))

    def replace_number(match: re.Match[str]) -> str:
        alias = mapping.alias("numbers", match.group(0), prefix="number")
        return f"[{alias}]"

    value = _QQ_LIKE_NUMBER.sub(replace_number, value)
    for token, alias in protected:
        value = value.replace(token, alias)
    return value


def _safe_event(
    event: SocialEventEnvelope,
    *,
    mapping: _IdentityMapping,
    first_timestamp_ms: int,
    replacements: tuple[tuple[str, str], ...],
    reference_trigger: ReferenceTriggerMatch | None = None,
) -> dict[str, object]:
    timestamp_ms = int(event.payload.get("source_timestamp_ms") or event.occurred_at * 1_000)
    is_self = bool(event.payload.get("is_self"))
    message_id = mapping.alias(
        "messages", event.source_message_id or event.event_id, prefix="message"
    )
    reply_to = mapping.alias(
        "messages", event.payload.get("reply_to"), prefix="message"
    )
    mentions = tuple(
        alias
        for alias in (
            mapping.actor(value, is_self=str(value) == TARGET_BOT_QQ)
            for value in event.payload.get("mentions", ())
        )
        if alias is not None
    )
    media = event.payload.get("media", ())
    media_types = [
        str(item.get("type"))
        for item in media
        if isinstance(item, Mapping) and item.get("type")
    ]
    raw_reply = event.payload.get("reply_evidence")
    reply_evidence: dict[str, object] | None = None
    if isinstance(raw_reply, Mapping) and not bool(raw_reply.get("resolved")):
        reply_timestamp = raw_reply.get("timestamp")
        reply_timestamp = (
            int(reply_timestamp) if reply_timestamp not in (None, "", 0) else None
        )
        if (
            reply_timestamp is not None
            and timestamp_ms >= 100_000_000_000
            and 1_000_000_000 <= reply_timestamp < 100_000_000_000
        ):
            reply_timestamp *= 1_000
        reply_evidence = {
            "resolved": False,
            "sender_id": mapping.actor(
                raw_reply.get("sender_id"),
                is_self=str(raw_reply.get("sender_id") or "") == TARGET_BOT_QQ,
            ),
            "age_ms": (
                max(0, timestamp_ms - reply_timestamp)
                if reply_timestamp is not None
                else None
            ),
            "content": _safe_text(raw_reply.get("content"), replacements, mapping),
        }
    result = {
        "event_id": message_id,
        "offset_ms": timestamp_ms - first_timestamp_ms,
        "group_id": mapping.alias("groups", event.group_id, prefix="group"),
        "actor_id": mapping.actor(event.actor_id, is_self=is_self),
        "text": _safe_text(event.payload.get("text"), replacements, mapping),
        "reply_to": reply_to,
        "reply_evidence": reply_evidence,
        "mentions": list(mentions),
        "media": media_types,
        "recalled": bool(event.payload.get("recalled")),
        "system": bool(event.payload.get("system")),
        "is_self": is_self,
        "evidence_ref": mapping.alias(
            "evidence", event.payload.get("raw_evidence_hash"), prefix="evidence"
        ),
    }
    if reference_trigger is not None:
        result.update(
            {
                "reference_interaction_origin": "REFERENCE_EXTERNAL_TRIGGER",
                "social_evaluation_eligible": False,
                "reference_capability_hint": reference_trigger.capability_hint,
                "reference_trigger_kind": reference_trigger.trigger_kind,
                "reference_trigger_value": reference_trigger.trigger_value,
                "ownership_note": (
                    "reference_evidence_only;does_not_imply_target_installation"
                ),
            }
        )
    elif is_self:
        result.update(
            {
                "reference_interaction_origin": "UNCLASSIFIED_OUTPUT",
                "social_evaluation_eligible": False,
                "ownership_note": "same_account_output_owner_unknown",
            }
        )
    else:
        result.update(
            {
                "reference_interaction_origin": "UNCLASSIFIED",
                "social_evaluation_eligible": True,
                "ownership_note": "eligible_social_decision_candidate",
            }
        )
    return result


def build_candidate_corpus(
    events: Iterable[SocialEventEnvelope],
    *,
    output_path: str | Path,
    mapping_path: str | Path,
    context_size: int = 20,
    reference_trigger_policy: ReferenceTriggerPolicy | None = None,
) -> CorpusBuildSummary:
    """Write de-identified, unlabeled context windows for human selection."""

    if type(context_size) is not int or context_size <= 0:
        raise ValueError("context_size must be a positive integer")
    normalized_events = tuple(events)
    output = Path(output_path)
    mapping = _IdentityMapping(Path(mapping_path))
    replacements = _private_replacements(normalized_events, mapping)
    first_timestamp_ms = (
        int(
            normalized_events[0].payload.get("source_timestamp_ms")
            or normalized_events[0].occurred_at * 1_000
        )
        if normalized_events
        else 0
    )
    reference_matches = tuple(
        (
            reference_trigger_policy.classify(event.payload.get("text"))
            if reference_trigger_policy is not None
            and not bool(event.payload.get("is_self"))
            else None
        )
        for event in normalized_events
    )
    safe_events = [
        _safe_event(
            event,
            mapping=mapping,
            first_timestamp_ms=first_timestamp_ms,
            replacements=replacements,
            reference_trigger=reference_matches[index],
        )
        for index, event in enumerate(normalized_events)
    ]
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8") as handle:
        for index, event in enumerate(safe_events):
            start = max(0, index - context_size + 1)
            value = {
                "scenario_id": f"candidate:{index + 1:06d}",
                "status": "unlabeled",
                "group_id": event["group_id"],
                "focus_event_id": event["event_id"],
                "context": safe_events[start : index + 1],
                "label": None,
            }
            handle.write(json.dumps(value, ensure_ascii=False, sort_keys=True) + "\n")
    mapping.save()
    return CorpusBuildSummary(len(safe_events), output, mapping.path)


def _event_timestamp_ms(event: SocialEventEnvelope) -> int:
    return int(event.payload.get("source_timestamp_ms") or event.occurred_at * 1_000)


def _review_focus_indices(
    events: tuple[SocialEventEnvelope, ...],
) -> tuple[int, ...]:
    indices: list[int] = []
    seen: set[str] = set()
    for index, event in enumerate(events):
        event_id = str(event.source_message_id or event.event_id)
        if (
            event_id in seen
            or event.event_type != "platform.message"
            or bool(event.payload.get("is_self"))
            or bool(event.payload.get("system"))
            or bool(event.payload.get("recalled"))
        ):
            continue
        seen.add(event_id)
        indices.append(index)
    return tuple(indices)


def _historical_bot_action(
    events: tuple[SocialEventEnvelope, ...],
    *,
    focus_index: int,
    end_index: int,
    response_window_ms: int,
    reference_matches: tuple[ReferenceTriggerMatch | None, ...] | None = None,
) -> bool | None:
    focus_at = _event_timestamp_ms(events[focus_index])
    for index, event in enumerate(
        events[focus_index + 1 : end_index], start=focus_index + 1
    ):
        delay = _event_timestamp_ms(event) - focus_at
        if delay > response_window_ms:
            break
        if reference_matches is not None and reference_matches[index] is not None:
            return None
        if delay >= 0 and bool(event.payload.get("is_self")):
            return True
    return False


def _scene_context_start(
    events: tuple[SocialEventEnvelope, ...],
    *,
    focus_index: int,
    split_start: int,
    max_context_events: int,
    max_idle_gap_ms: int,
) -> int:
    """Find bounded history for one decision point without using future events."""

    hard_start = max(split_start, focus_index - max_context_events + 1)
    context_start = focus_index
    while context_start > hard_start:
        current_at = _event_timestamp_ms(events[context_start])
        previous_at = _event_timestamp_ms(events[context_start - 1])
        if current_at - previous_at > max_idle_gap_ms:
            break
        context_start -= 1

    reply_to = str(events[focus_index].payload.get("reply_to") or "").strip()
    if reply_to:
        for candidate_index in range(focus_index - 1, hard_start - 1, -1):
            candidate_id = str(
                events[candidate_index].source_message_id
                or events[candidate_index].event_id
            )
            if candidate_id == reply_to:
                context_start = min(context_start, candidate_index)
                break
    return context_start


def _evenly_spaced(values: list[int], count: int) -> tuple[int, ...]:
    if len(values) < count:
        raise ValueError("not enough review candidates")
    if count == 1:
        return (values[len(values) // 2],)
    return tuple(
        values[(position * (len(values) - 1)) // (count - 1)]
        for position in range(count)
    )


def _observable_tags(event: Mapping[str, object]) -> list[str]:
    tags = []
    if "bot:target" in event.get("mentions", ()):
        tags.append("direct_mention")
    if event.get("reply_to") or event.get("reply_evidence"):
        tags.append("reply_context")
    if event.get("media"):
        tags.append("media")
    if str(event.get("text") or "").strip():
        tags.append("text")
    return tags


def _direct_bot_interaction_indices(
    events: tuple[SocialEventEnvelope, ...],
    *,
    start_index: int,
    end_index: int,
) -> frozenset[int]:
    message_is_self: dict[str, bool] = {}
    result = set()
    for index in range(start_index, end_index):
        event = events[index]
        payload = event.payload
        reply_to = str(payload.get("reply_to") or "").strip()
        reply_evidence = payload.get("reply_evidence")
        direct_reply = bool(reply_to and message_is_self.get(reply_to) is True)
        unresolved_bot_reply = bool(
            isinstance(reply_evidence, Mapping)
            and str(reply_evidence.get("sender_id") or "") == TARGET_BOT_QQ
        )
        if bool(payload.get("mentions_bot")) or direct_reply or unresolved_bot_reply:
            result.add(index)
        message_id = str(event.source_message_id or event.event_id)
        message_is_self[message_id] = bool(payload.get("is_self"))
    return frozenset(result)


def _is_clear_ambient_text(event: SocialEventEnvelope) -> bool:
    payload = event.payload
    return bool(str(payload.get("text") or "").strip()) and not any(
        (
            payload.get("mentions"),
            payload.get("reply_to"),
            payload.get("reply_evidence"),
            payload.get("media"),
        )
    )


def build_review_queue(
    events: Iterable[SocialEventEnvelope],
    *,
    output_path: str | Path,
    mapping_path: str | Path,
    per_split: int = 200,
    context_size: int = 20,
    response_window_ms: int = 60_000,
    scene_gap_ms: int = 300_000,
    selection_profile: str = "broad",
    reference_trigger_policy: ReferenceTriggerPolicy | None = None,
) -> ReviewQueueSummary:
    """Select fixed decision points with bounded, variable-length scene history."""

    if type(per_split) is not int or per_split <= 0 or per_split % 2:
        raise ValueError("per_split must be a positive even integer")
    if type(context_size) is not int or context_size <= 0:
        raise ValueError("context_size must be a positive integer")
    if type(response_window_ms) is not int or response_window_ms <= 0:
        raise ValueError("response_window_ms must be a positive integer")
    if type(scene_gap_ms) is not int or scene_gap_ms <= 0:
        raise ValueError("scene_gap_ms must be a positive integer")
    if selection_profile not in {"broad", "bootstrap_clear"}:
        raise ValueError("selection_profile is unsupported")

    normalized_events = tuple(events)
    reference_matches = tuple(
        (
            reference_trigger_policy.classify(event.payload.get("text"))
            if reference_trigger_policy is not None
            and not bool(event.payload.get("is_self"))
            else None
        )
        for event in normalized_events
    )
    focus_indices = _review_focus_indices(normalized_events)
    midpoint = len(focus_indices) // 2
    if midpoint == 0 or midpoint == len(focus_indices):
        raise ValueError("not enough review candidates")
    split_boundary = focus_indices[midpoint]
    split_ranges = {
        "calibration": (0, split_boundary, focus_indices[:midpoint]),
        "holdout": (split_boundary, len(normalized_events), focus_indices[midpoint:]),
    }

    selected: list[tuple[str, str, int, int]] = []
    signal_count = per_split // 2
    for split, (start_index, end_index, candidates) in split_ranges.items():
        action: list[int] = []
        silence: list[int] = []
        direct_indices = _direct_bot_interaction_indices(
            normalized_events,
            start_index=start_index,
            end_index=end_index,
        )
        for focus_index in candidates:
            if reference_matches[focus_index] is not None:
                continue
            historical_action = _historical_bot_action(
                normalized_events,
                focus_index=focus_index,
                end_index=end_index,
                response_window_ms=response_window_ms,
                reference_matches=(
                    reference_matches if reference_trigger_policy is not None else None
                ),
            )
            if historical_action is None:
                continue
            if selection_profile == "bootstrap_clear":
                if historical_action and focus_index in direct_indices:
                    action.append(focus_index)
                elif (
                    not historical_action
                    and focus_index not in direct_indices
                    and _is_clear_ambient_text(normalized_events[focus_index])
                ):
                    silence.append(focus_index)
                continue
            (action if historical_action else silence).append(focus_index)
        selected.extend(
            (split, "historical_bot_action", index, start_index)
            for index in _evenly_spaced(action, signal_count)
        )
        selected.extend(
            (split, "historical_silence", index, start_index)
            for index in _evenly_spaced(silence, signal_count)
        )

    mapping = _IdentityMapping(Path(mapping_path))
    replacements = _private_replacements(normalized_events, mapping)
    first_timestamp_ms = (
        _event_timestamp_ms(normalized_events[0]) if normalized_events else 0
    )
    safe_events = [
        _safe_event(
            event,
            mapping=mapping,
            first_timestamp_ms=first_timestamp_ms,
            replacements=replacements,
            reference_trigger=reference_matches[index],
        )
        for index, event in enumerate(normalized_events)
    ]
    selected.sort(key=lambda item: (item[0] == "holdout", item[2], item[1]))
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    counts = {"calibration": 0, "holdout": 0}
    with output.open("w", encoding="utf-8") as handle:
        for split, signal, focus_index, split_start in selected:
            event = safe_events[focus_index]
            context_start = _scene_context_start(
                normalized_events,
                focus_index=focus_index,
                split_start=split_start,
                max_context_events=context_size,
                max_idle_gap_ms=scene_gap_ms,
            )
            context = safe_events[context_start : focus_index + 1]
            evidence_ref = str(event.get("evidence_ref") or "")
            scenario_suffix = evidence_ref.partition(":")[2] or str(
                event["event_id"]
            )
            value = {
                "scenario_id": f"scenario:{scenario_suffix}",
                "split": split,
                "status": "needs_human_review",
                "selection_signal": signal,
                "selection_profile": selection_profile,
                "evaluation_lane": "SOCIAL_CONVERSATION",
                "core_social_eligible": True,
                "observable_tags": _observable_tags(event),
                "group_id": event["group_id"],
                "focus_event_id": event["event_id"],
                "scene": {
                    "boundary": "time_gap_or_reply_chain",
                    "history_event_count": len(context) - 1,
                    "max_context_events": context_size,
                    "max_idle_gap_ms": scene_gap_ms,
                },
                "context": context,
                "label": None,
            }
            handle.write(json.dumps(value, ensure_ascii=False, sort_keys=True) + "\n")
            counts[split] += 1
    mapping.save()
    return ReviewQueueSummary(
        calibration_count=counts["calibration"],
        holdout_count=counts["holdout"],
        output_path=output,
        mapping_path=mapping.path,
    )


__all__ = (
    "CorpusBuildSummary",
    "ReviewQueueSummary",
    "build_candidate_corpus",
    "build_review_queue",
)
