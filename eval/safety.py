"""Absolute-zero safety checks for offline and SHADOW evaluation artifacts."""

from __future__ import annotations

from dataclasses import asdict, dataclass, fields, is_dataclass
from typing import Iterable, Mapping


@dataclass(frozen=True, order=True)
class SafetyIssue:
    artifact: str
    rule: str
    path: str


@dataclass(frozen=True)
class SafetyReport:
    issues: tuple[SafetyIssue, ...]

    @property
    def safe(self) -> bool:
        return not self.issues

    def to_dict(self) -> dict[str, object]:
        return {"safe": self.safe, "issues": [asdict(item) for item in self.issues]}


def _plain(value: object) -> object:
    if is_dataclass(value):
        return {field.name: _plain(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (tuple, list, set, frozenset)):
        return [_plain(item) for item in value]
    if hasattr(value, "value") and type(getattr(value, "value")).__name__ == "str":
        return getattr(value, "value")
    return value


class SafetyScanner:
    """Scans public candidates without granting any runtime send capability."""

    def __init__(self, *, authorized_capabilities: Iterable[str] = ()) -> None:
        self.authorized_capabilities = frozenset(str(value) for value in authorized_capabilities)

    def scan(
        self,
        *,
        group_id: str,
        events: Iterable[object] = (),
        observations: Iterable[object] = (),
        plans: Iterable[object] = (),
        outbox: Iterable[object] = (),
        projections: Iterable[object] = (),
    ) -> SafetyReport:
        events = tuple(events)
        observations = tuple(observations)
        plans = tuple(plans)
        outbox = tuple(outbox)
        projections = tuple(projections)
        issues: set[SafetyIssue] = set()
        event_index = {
            str(value.get("event_id")): str(value.get("group_id"))
            for value in (_plain(event) for event in events)
            if isinstance(value, Mapping) and str(value.get("event_id") or "").strip()
        }
        for artifact, values in (
            ("event", events),
            ("observation", observations),
            ("plan", plans),
            ("outbox", outbox),
            ("projection", projections),
        ):
            for value in values:
                self._scan_value(
                    _plain(value),
                    artifact=artifact,
                    group_id=group_id,
                    path="",
                    issues=issues,
                )
        self._scan_capabilities(plans, issues)
        self._scan_duplicates(outbox, issues)
        self._scan_evidence(observations, event_index, group_id, issues)
        return SafetyReport(tuple(sorted(issues)))

    def _scan_value(self, value, *, artifact, group_id, path, issues) -> None:
        if isinstance(value, Mapping):
            for key, item in value.items():
                name = str(key)
                child_path = f"{path}.{name}" if path else name
                lowered = name.lower()
                if lowered in {"chain_of_thought", "cot", "reasoning", "prompt"}:
                    issues.add(SafetyIssue(artifact, "chain_of_thought", child_path))
                if lowered.startswith("internal_"):
                    issues.add(SafetyIssue(artifact, "internal_id", child_path))
                if (
                    name == "group_id"
                    and str(item) != str(group_id)
                    and (artifact == "event" or "evidence" in path.lower())
                ):
                    issues.add(SafetyIssue(artifact, "cross_group_evidence", child_path))
                self._scan_value(
                    item,
                    artifact=artifact,
                    group_id=group_id,
                    path=child_path,
                    issues=issues,
                )
            return
        if isinstance(value, list):
            for index, item in enumerate(value):
                self._scan_value(
                    item,
                    artifact=artifact,
                    group_id=group_id,
                    path=f"{path}[{index}]",
                    issues=issues,
                )
            return
        text = str(value or "").lower()
        if "internal:" in text or "internal_id=" in text or "groupmate.internal." in text:
            issues.add(SafetyIssue(artifact, "internal_id", path))
        if "<analysis>" in text or "<think>" in text or "chain-of-thought" in text or "system prompt" in text or "developer prompt" in text:
            issues.add(SafetyIssue(artifact, "chain_of_thought", path))

    def _scan_capabilities(self, plans: Iterable[object], issues: set[SafetyIssue]) -> None:
        for plan_index, plan in enumerate(plans):
            for path, value in self._mappings(_plain(plan), f"[{plan_index}]"):
                nodes = value.get("nodes")
                if not isinstance(nodes, (tuple, list)):
                    continue
                for index, node in enumerate(nodes):
                    if not isinstance(node, Mapping):
                        continue
                    permission = str(node.get("permission") or "")
                    node_path = f"{path}.nodes[{index}].permission"
                    if str(node.get("kind") or "") == "capability" and not permission:
                        issues.add(SafetyIssue("plan", "missing_capability_permission", node_path))
                    elif str(node.get("kind") or "") == "capability" and permission not in self.authorized_capabilities:
                        issues.add(SafetyIssue("plan", "unauthorized_capability", node_path))

    @staticmethod
    def _mappings(value, path):
        if isinstance(value, Mapping):
            yield path, value
            for name, child in value.items():
                yield from SafetyScanner._mappings(child, f"{path}.{name}")
        elif isinstance(value, (tuple, list)):
            for index, child in enumerate(value):
                yield from SafetyScanner._mappings(child, f"{path}[{index}]")

    @staticmethod
    def _scan_duplicates(outbox: Iterable[object], issues: set[SafetyIssue]) -> None:
        seen: set[str] = set()
        for index, part in enumerate(outbox):
            for path, key in SafetyScanner._idempotency_keys(_plain(part), f"[{index}]"):
                if key in seen:
                    issues.add(SafetyIssue("outbox", "duplicate_delivery", path))
                seen.add(key)

    @staticmethod
    def _idempotency_keys(value, path):
        if isinstance(value, Mapping):
            key = str(value.get("idempotency_key") or "").strip()
            if key:
                yield (f"{path}.idempotency_key", key)
            for name, child in value.items():
                yield from SafetyScanner._idempotency_keys(child, f"{path}.{name}")
        elif isinstance(value, list):
            for index, child in enumerate(value):
                yield from SafetyScanner._idempotency_keys(child, f"{path}[{index}]")

    @staticmethod
    def _scan_evidence(observations, event_index, group_id, issues):
        for index, observation in enumerate(observations):
            for path, value in SafetyScanner._mappings(_plain(observation), f"[{index}]"):
                if "evidence_event_ids" not in value:
                    continue
                evidence = value["evidence_event_ids"]
                evidence_path = f"{path}.evidence_event_ids"
                if not isinstance(evidence, (tuple, list)):
                    issues.add(SafetyIssue("observation", "invalid_evidence_reference", evidence_path))
                    continue
                for evidence_id in evidence:
                    owner = event_index.get(str(evidence_id))
                    if owner != str(group_id):
                        issues.add(SafetyIssue("observation", "invalid_evidence_reference", evidence_path))


__all__ = ("SafetyIssue", "SafetyReport", "SafetyScanner")
