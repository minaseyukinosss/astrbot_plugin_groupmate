"""Group culture promotion and evidence decay."""

from __future__ import annotations

from dataclasses import dataclass, replace


@dataclass(frozen=True)
class CultureArtifact:
    persona_id: str
    group_id: str
    artifact_id: str
    status: str
    evidence_event_ids: tuple[str, ...]
    last_evidence_at: int | None
    confirmed_by_admin: str | None
    version: int


class CultureProjector:
    DECAY_SECONDS = 30 * 24 * 60 * 60

    def empty(self, persona_id: str, group_id: str, artifact_id: str) -> CultureArtifact:
        if not persona_id or not group_id or not artifact_id:
            raise ValueError("culture scope and artifact_id are required")
        return CultureArtifact(
            persona_id, group_id, artifact_id, "candidate", (), None, None, 0
        )

    def observe(
        self, artifact: CultureArtifact, event_id: str, *, now: int
    ) -> CultureArtifact:
        if not event_id or event_id in artifact.evidence_event_ids:
            return artifact
        evidence = artifact.evidence_event_ids + (event_id,)
        return replace(
            artifact,
            evidence_event_ids=evidence,
            status="active" if len(evidence) >= 3 else "candidate",
            last_evidence_at=int(now),
            version=artifact.version + 1,
        )

    def confirm_by_admin(
        self, artifact: CultureArtifact, *, admin_id: str, now: int
    ) -> CultureArtifact:
        if not admin_id.startswith("admin:"):
            raise PermissionError("culture confirmation requires administrator")
        return replace(
            artifact,
            status="active",
            last_evidence_at=int(now),
            confirmed_by_admin=admin_id,
            version=artifact.version + 1,
        )

    def decay(self, artifact: CultureArtifact, *, now: int) -> CultureArtifact:
        if (
            artifact.status == "active"
            and artifact.confirmed_by_admin is None
            and artifact.last_evidence_at is not None
            and int(now) - artifact.last_evidence_at > self.DECAY_SECONDS
        ):
            return replace(artifact, status="candidate", version=artifact.version + 1)
        return artifact


__all__ = ("CultureArtifact", "CultureProjector")
