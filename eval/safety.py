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
        if "internal:" in text or "internal_id=" in text:
            issues.add(SafetyIssue(artifact, "internal_id", path))
        if "<analysis>" in text or "chain-of-thought" in text:
            issues.add(SafetyIssue(artifact, "chain_of_thought", path))

    def _scan_capabilities(self, plans: Iterable[object], issues: set[SafetyIssue]) -> None:
        for plan in plans:
            value = _plain(plan)
            if not isinstance(value, Mapping):
                continue
            for index, node in enumerate(value.get("nodes", ())):
                if not isinstance(node, Mapping):
                    continue
                permission = str(node.get("permission") or "")
                if permission.startswith("capability:") and permission not in self.authorized_capabilities:
                    issues.add(SafetyIssue("plan", "unauthorized_capability", f"nodes[{index}].permission"))

    @staticmethod
    def _scan_duplicates(outbox: Iterable[object], issues: set[SafetyIssue]) -> None:
        seen: set[str] = set()
        for index, part in enumerate(outbox):
            value = _plain(part)
            if not isinstance(value, Mapping):
                continue
            nested = value.get("part")
            source = nested if isinstance(nested, Mapping) else value
            key = str(source.get("idempotency_key") or "").strip()
            if key and key in seen:
                issues.add(SafetyIssue("outbox", "duplicate_delivery", f"[{index}].idempotency_key"))
            seen.add(key)


__all__ = ("SafetyIssue", "SafetyReport", "SafetyScanner")
