"""Reference-only ownership annotations for historical chat exports."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

if "." in (__package__ or ""):
    from ..groupmate.social_runtime.ownership import ExternalTriggerPolicy
else:  # Repository-local offline evaluation entry point.
    from groupmate.social_runtime.ownership import ExternalTriggerPolicy


@dataclass(frozen=True)
class ReferenceTriggerMatch:
    capability_hint: str
    trigger_kind: str
    trigger_value: str


class ReferenceTriggerPolicy:
    """Classifies evidence without claiming the target deployment has a plugin."""

    def __init__(self, policy: ExternalTriggerPolicy) -> None:
        self._policy = policy

    @classmethod
    def create(
        cls,
        *,
        command_prefixes: Mapping[str, str] | None = None,
        link_domains: Mapping[str, str] | None = None,
    ) -> "ReferenceTriggerPolicy":
        return cls(
            ExternalTriggerPolicy.create(
                command_prefixes=command_prefixes,
                link_domains=link_domains,
            )
        )

    def classify(self, text: object) -> ReferenceTriggerMatch | None:
        match = self._policy.classify(text)
        if match is None:
            return None
        assert match.owner_ref is not None
        assert match.trigger_kind is not None
        assert match.trigger_value is not None
        return ReferenceTriggerMatch(
            capability_hint=match.owner_ref,
            trigger_kind=match.trigger_kind,
            trigger_value=match.trigger_value,
        )


@dataclass(frozen=True)
class ReferenceOwnershipSummary:
    total_count: int
    social_count: int
    compatibility_count: int
    output_path: Path


def _annotated_event(
    event: Mapping[str, object], policy: ReferenceTriggerPolicy
) -> dict[str, object]:
    value = dict(event)
    match = None if bool(value.get("is_self")) else policy.classify(value.get("text"))
    if match is not None:
        value.update(
            {
                "reference_interaction_origin": "REFERENCE_EXTERNAL_TRIGGER",
                "social_evaluation_eligible": False,
                "reference_capability_hint": match.capability_hint,
                "reference_trigger_kind": match.trigger_kind,
                "reference_trigger_value": match.trigger_value,
                "ownership_note": (
                    "reference_evidence_only;does_not_imply_target_installation"
                ),
            }
        )
    elif bool(value.get("is_self")):
        value.update(
            {
                "reference_interaction_origin": "UNCLASSIFIED_OUTPUT",
                "social_evaluation_eligible": False,
                "ownership_note": "same_account_output_owner_unknown",
            }
        )
    else:
        value.update(
            {
                "reference_interaction_origin": "UNCLASSIFIED",
                "social_evaluation_eligible": True,
                "ownership_note": "eligible_social_decision_candidate",
            }
        )
    return value


def annotate_review_queue(
    path: str | Path,
    *,
    policy: ReferenceTriggerPolicy,
    output_path: str | Path | None = None,
) -> ReferenceOwnershipSummary:
    """Atomically add reference ownership without changing review decisions."""

    source = Path(path)
    target = Path(output_path) if output_path is not None else source
    records = []
    social_count = 0
    compatibility_count = 0
    for line_number, line in enumerate(
        source.read_text(encoding="utf-8").splitlines(), start=1
    ):
        if not line.strip():
            continue
        record = json.loads(line)
        if not isinstance(record, dict):
            raise ValueError(f"review queue record must be an object at line {line_number}")
        context = record.get("context")
        if not isinstance(context, list) or not context:
            raise ValueError("review queue context must not be empty")
        annotated_context = []
        for event in context:
            if not isinstance(event, Mapping):
                raise ValueError("review context event must be an object")
            annotated_context.append(_annotated_event(event, policy))
        record["context"] = annotated_context
        external_focus = (
            annotated_context[-1].get("reference_interaction_origin")
            == "REFERENCE_EXTERNAL_TRIGGER"
        )
        if external_focus:
            compatibility_count += 1
            record.update(
                {
                    "evaluation_lane": "EXTERNAL_PLUGIN_COMPATIBILITY",
                    "core_social_eligible": False,
                    "ownership_note": (
                        "reference_external_feature;"
                        "does_not_imply_target_installation"
                    ),
                }
            )
        else:
            social_count += 1
            record.update(
                {
                    "evaluation_lane": "SOCIAL_CONVERSATION",
                    "core_social_eligible": True,
                    "ownership_note": "core_social_bootstrap_candidate",
                }
            )
        records.append(record)

    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.ownership.tmp")
    descriptor = os.open(
        temporary,
        os.O_WRONLY | os.O_CREAT | os.O_TRUNC,
        0o600,
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            for record in records:
                handle.write(
                    json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n"
                )
        os.chmod(temporary, 0o600)
        os.replace(temporary, target)
        os.chmod(target, 0o600)
    finally:
        if temporary.exists():
            temporary.unlink()
    return ReferenceOwnershipSummary(
        total_count=len(records),
        social_count=social_count,
        compatibility_count=compatibility_count,
        output_path=target,
    )


__all__ = (
    "ReferenceOwnershipSummary",
    "ReferenceTriggerMatch",
    "ReferenceTriggerPolicy",
    "annotate_review_queue",
)
