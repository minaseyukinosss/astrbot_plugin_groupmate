"""Durable, bounded temporal opportunities that can only re-enter Event Fabric."""

from __future__ import annotations

import hashlib
import json
from contextlib import closing
from dataclasses import asdict, dataclass, replace
from enum import Enum
from pathlib import Path
from typing import Awaitable, Callable

from .contracts import SocialEventEnvelope
from .persistence.schema import connect_database, initialize_database


ALLOWED_OPPORTUNITY_KINDS = frozenset(
    {
        "commitment",
        "task",
        "member-event",
        "group-ritual",
        "delayed-scene",
        "self-open-loop",
    }
)
MAX_OPPORTUNITY_ATTEMPTS = 2
MAX_AUTONOMOUS_FOLLOWUPS = 1


class OpportunityStatus(str, Enum):
    SCHEDULED = "scheduled"
    EMITTING = "emitting"
    EMITTED = "emitted"
    EXPIRED = "expired"


class OpportunityIdentityConflict(RuntimeError):
    """Raised when a durable opportunity identity is reused."""


class OpportunityLimitReached(RuntimeError):
    """Raised when an opportunity exceeds its bounded follow-up budget."""


def _normalized_texts(values: tuple[str, ...], field: str) -> tuple[str, ...]:
    normalized = tuple(dict.fromkeys(str(value).strip() for value in values))
    if not normalized or any(not value for value in normalized):
        raise ValueError(f"opportunity {field} must not be empty")
    return normalized


def _opportunity_identity(values: dict[str, object]) -> str:
    identity = {
        "source_event_ids": values["source_event_ids"],
        "group_id": values["group_id"],
        "audience": values["audience"],
        "earliest_at": values["created_earliest_at"],
        "expires_at": values["expires_at"],
        "max_attempts": values["max_attempts"],
        "kind": values["kind"],
    }
    digest = hashlib.sha256(
        json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()[:24]
    return f"opportunity:{digest}"


@dataclass(frozen=True)
class AutonomousOpportunity:
    source_event_ids: tuple[str, ...]
    group_id: str
    audience: tuple[str, ...]
    earliest_at: int
    expires_at: int
    max_attempts: int
    kind: str
    opportunity_id: str = ""
    attempts: int = 0
    followup_count: int = 0
    status: OpportunityStatus = OpportunityStatus.SCHEDULED
    created_earliest_at: int = -1
    last_scene_version: int = 0
    last_relationship_version: int = 0
    last_event_id: str | None = None

    def __post_init__(self) -> None:
        sources = _normalized_texts(tuple(self.source_event_ids), "source")
        if any(value.startswith("autonomy:") for value in sources):
            raise ValueError("recursive autonomous opportunity source is forbidden")
        audience = _normalized_texts(tuple(self.audience), "audience")
        group_id = str(self.group_id).strip()
        if not group_id:
            raise ValueError("opportunity group must not be empty")
        earliest_at = int(self.earliest_at)
        expires_at = int(self.expires_at)
        if earliest_at < 0 or expires_at <= earliest_at:
            raise ValueError("opportunity expiry must follow earliest_at")
        max_attempts = int(self.max_attempts)
        if not 1 <= max_attempts <= MAX_OPPORTUNITY_ATTEMPTS:
            raise ValueError("opportunity attempt limit must be between 1 and 2")
        kind = str(self.kind).strip()
        if kind not in ALLOWED_OPPORTUNITY_KINDS:
            raise ValueError("opportunity source kind is not allowed")
        attempts = int(self.attempts)
        followup_count = int(self.followup_count)
        if not 0 <= attempts <= max_attempts:
            raise ValueError("opportunity attempts exceed the declared limit")
        if not 0 <= followup_count <= MAX_AUTONOMOUS_FOLLOWUPS:
            raise ValueError("autonomous follow-up limit is one")
        created_earliest_at = int(self.created_earliest_at)
        if created_earliest_at < 0:
            created_earliest_at = earliest_at
        if created_earliest_at > earliest_at:
            raise ValueError("created earliest time cannot follow current earliest time")
        last_scene_version = int(self.last_scene_version)
        last_relationship_version = int(self.last_relationship_version)
        if last_scene_version < 0 or last_relationship_version < 0:
            raise ValueError("revalidation versions must not be negative")
        status = OpportunityStatus(self.status)
        event_id = None if self.last_event_id is None else str(self.last_event_id).strip()
        if status in {OpportunityStatus.EMITTING, OpportunityStatus.EMITTED}:
            if attempts < 1 or not event_id:
                raise ValueError("emitted opportunity requires attempt and event identity")
        identity_values = {
            "source_event_ids": sources,
            "group_id": group_id,
            "audience": audience,
            "created_earliest_at": created_earliest_at,
            "expires_at": expires_at,
            "max_attempts": max_attempts,
            "kind": kind,
        }
        expected_id = _opportunity_identity(identity_values)
        supplied_id = str(self.opportunity_id).strip()
        if supplied_id and supplied_id != expected_id:
            raise ValueError("opportunity identity does not match immutable inputs")
        object.__setattr__(self, "source_event_ids", sources)
        object.__setattr__(self, "group_id", group_id)
        object.__setattr__(self, "audience", audience)
        object.__setattr__(self, "earliest_at", earliest_at)
        object.__setattr__(self, "expires_at", expires_at)
        object.__setattr__(self, "max_attempts", max_attempts)
        object.__setattr__(self, "kind", kind)
        object.__setattr__(self, "opportunity_id", expected_id)
        object.__setattr__(self, "attempts", attempts)
        object.__setattr__(self, "followup_count", followup_count)
        object.__setattr__(self, "status", status)
        object.__setattr__(self, "created_earliest_at", created_earliest_at)
        object.__setattr__(self, "last_scene_version", last_scene_version)
        object.__setattr__(
            self, "last_relationship_version", last_relationship_version
        )
        object.__setattr__(self, "last_event_id", event_id)


@dataclass(frozen=True)
class OpportunityRevalidation:
    scene_version: int
    relationship_version: int
    scene_allows: bool
    relationship_allows: bool
    boundary_active: bool
    budget_available: bool
    quiet_until: int | None = None

    def __post_init__(self) -> None:
        for field in (
            "scene_allows",
            "relationship_allows",
            "boundary_active",
            "budget_available",
        ):
            if type(getattr(self, field)) is not bool:
                raise ValueError("opportunity policy decisions must be boolean")
        scene_version = int(self.scene_version)
        relationship_version = int(self.relationship_version)
        if scene_version < 1:
            raise ValueError("opportunity revalidation requires a projected scene")
        if relationship_version < 0:
            raise ValueError("relationship version must not be negative")
        quiet_until = None if self.quiet_until is None else int(self.quiet_until)
        if quiet_until is not None and quiet_until < 0:
            raise ValueError("quiet_until must not be negative")
        object.__setattr__(self, "scene_version", scene_version)
        object.__setattr__(self, "relationship_version", relationship_version)
        object.__setattr__(self, "quiet_until", quiet_until)


EventSink = Callable[[SocialEventEnvelope], Awaitable[object]]
OpportunityRevalidator = Callable[
    [AutonomousOpportunity], OpportunityRevalidation
]


class AutonomousOpportunityScheduler:
    """Persists bounded opportunities and emits only temporal Fabric events."""

    def __init__(
        self,
        path: Path,
        *,
        persona_id: str,
        event_sink: EventSink,
    ) -> None:
        self.path = Path(path)
        initialize_database(self.path)
        self.persona_id = str(persona_id).strip()
        if not self.persona_id:
            raise ValueError("opportunity scheduler requires persona_id")
        self._event_sink = event_sink
        self._ensure_table()

    def schedule(
        self, opportunity: AutonomousOpportunity, *, now: int
    ) -> AutonomousOpportunity:
        normalized = _opportunity_from_dict(_opportunity_to_dict(opportunity))
        if (
            normalized.status is not OpportunityStatus.SCHEDULED
            or normalized.attempts
            or normalized.followup_count
            or normalized.last_event_id is not None
        ):
            raise ValueError("only a fresh autonomous opportunity may be scheduled")
        now = int(now)
        if now < 0:
            raise ValueError("schedule time must not be negative")
        with closing(connect_database(self.path)) as db:
            try:
                db.execute("BEGIN IMMEDIATE")
                row = db.execute(
                    "SELECT state_json FROM autonomous_opportunities "
                    "WHERE persona_id=? AND opportunity_id=?",
                    (self.persona_id, normalized.opportunity_id),
                ).fetchone()
                if row is not None:
                    existing = _opportunity_from_dict(json.loads(row["state_json"]))
                    if not self._same_spec(existing, normalized):
                        raise OpportunityIdentityConflict(
                            "opportunity identity was reused for different inputs"
                        )
                    db.commit()
                    return existing
                db.execute(
                    "INSERT INTO autonomous_opportunities("
                    "persona_id, opportunity_id, group_id, status, earliest_at, "
                    "expires_at, state_json, updated_at) VALUES(?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        self.persona_id,
                        normalized.opportunity_id,
                        normalized.group_id,
                        normalized.status.value,
                        normalized.earliest_at,
                        normalized.expires_at,
                        _canonical_json(_opportunity_to_dict(normalized)),
                        now,
                    ),
                )
                db.commit()
                return normalized
            except BaseException:
                db.rollback()
                raise

    def get(self, opportunity_id: str) -> AutonomousOpportunity:
        with closing(connect_database(self.path)) as db:
            row = db.execute(
                "SELECT state_json FROM autonomous_opportunities "
                "WHERE persona_id=? AND opportunity_id=?",
                (self.persona_id, str(opportunity_id).strip()),
            ).fetchone()
        if row is None:
            raise KeyError(opportunity_id)
        return _opportunity_from_dict(json.loads(row["state_json"]))

    async def run_due(
        self,
        *,
        now: int,
        revalidate: OpportunityRevalidator,
    ) -> tuple[AutonomousOpportunity, ...]:
        now = int(now)
        if now < 0:
            raise ValueError("scheduler time must not be negative")
        emitted = []
        for opportunity_id in self._due_ids(now):
            opportunity = self.get(opportunity_id)
            if opportunity.expires_at <= now:
                self._persist(replace(opportunity, status=OpportunityStatus.EXPIRED), now)
                continue
            context = revalidate(opportunity)
            if not isinstance(context, OpportunityRevalidation):
                raise ValueError("opportunity revalidator returned an invalid context")
            opportunity = replace(
                opportunity,
                last_scene_version=context.scene_version,
                last_relationship_version=context.relationship_version,
            )
            if context.quiet_until is not None and context.quiet_until > now:
                if context.quiet_until >= opportunity.expires_at:
                    self._persist(
                        replace(opportunity, status=OpportunityStatus.EXPIRED), now
                    )
                else:
                    self._persist(
                        replace(opportunity, earliest_at=context.quiet_until), now
                    )
                continue
            if not self._all_gates_allow(context):
                self._persist(opportunity, now)
                continue
            if opportunity.status is OpportunityStatus.SCHEDULED:
                opportunity = self._claim(opportunity, now)
            if opportunity.status is not OpportunityStatus.EMITTING:
                continue
            event = self._due_event(opportunity, now)
            await self._event_sink(event)
            opportunity = replace(opportunity, status=OpportunityStatus.EMITTED)
            self._persist(opportunity, now)
            emitted.append(opportunity)
        return tuple(emitted)

    def schedule_followup(
        self,
        opportunity_id: str,
        *,
        earliest_at: int,
        now: int,
    ) -> AutonomousOpportunity:
        current = self.get(opportunity_id)
        earliest_at = int(earliest_at)
        now = int(now)
        if current.status is not OpportunityStatus.EMITTED:
            raise OpportunityLimitReached("only an emitted opportunity may follow up")
        if (
            current.attempts >= current.max_attempts
            or current.followup_count >= MAX_AUTONOMOUS_FOLLOWUPS
        ):
            raise OpportunityLimitReached("autonomous follow-up budget is exhausted")
        if earliest_at < now or earliest_at >= current.expires_at:
            raise ValueError("follow-up must remain inside the opportunity window")
        updated = replace(
            current,
            earliest_at=earliest_at,
            followup_count=current.followup_count + 1,
            status=OpportunityStatus.SCHEDULED,
        )
        self._persist(updated, now)
        return updated

    def _claim(
        self, opportunity: AutonomousOpportunity, now: int
    ) -> AutonomousOpportunity:
        if opportunity.attempts >= opportunity.max_attempts:
            self._persist(
                replace(opportunity, status=OpportunityStatus.EXPIRED), now
            )
            return self.get(opportunity.opportunity_id)
        attempt = opportunity.attempts + 1
        claimed = replace(
            opportunity,
            attempts=attempt,
            status=OpportunityStatus.EMITTING,
            last_event_id=f"autonomy:{opportunity.opportunity_id}:{attempt}",
        )
        with closing(connect_database(self.path)) as db:
            try:
                db.execute("BEGIN IMMEDIATE")
                changed = db.execute(
                    "UPDATE autonomous_opportunities SET status=?, earliest_at=?, "
                    "state_json=?, updated_at=? WHERE persona_id=? AND opportunity_id=? "
                    "AND status='scheduled'",
                    (
                        claimed.status.value,
                        claimed.earliest_at,
                        _canonical_json(_opportunity_to_dict(claimed)),
                        now,
                        self.persona_id,
                        claimed.opportunity_id,
                    ),
                ).rowcount
                db.commit()
            except BaseException:
                db.rollback()
                raise
        return claimed if changed == 1 else self.get(opportunity.opportunity_id)

    def _persist(self, opportunity: AutonomousOpportunity, now: int) -> None:
        with closing(connect_database(self.path)) as db:
            changed = db.execute(
                "UPDATE autonomous_opportunities SET status=?, earliest_at=?, "
                "expires_at=?, state_json=?, updated_at=? "
                "WHERE persona_id=? AND opportunity_id=?",
                (
                    opportunity.status.value,
                    opportunity.earliest_at,
                    opportunity.expires_at,
                    _canonical_json(_opportunity_to_dict(opportunity)),
                    int(now),
                    self.persona_id,
                    opportunity.opportunity_id,
                ),
            ).rowcount
            db.commit()
        if changed != 1:
            raise KeyError(opportunity.opportunity_id)

    def _due_ids(self, now: int) -> tuple[str, ...]:
        with closing(connect_database(self.path)) as db:
            rows = db.execute(
                "SELECT opportunity_id FROM autonomous_opportunities "
                "WHERE persona_id=? AND status IN ('scheduled','emitting') "
                "AND earliest_at<=? ORDER BY earliest_at, opportunity_id",
                (self.persona_id, now),
            ).fetchall()
        return tuple(str(row["opportunity_id"]) for row in rows)

    def _due_event(
        self, opportunity: AutonomousOpportunity, now: int
    ) -> SocialEventEnvelope:
        return SocialEventEnvelope.create(
            event_id=opportunity.last_event_id,
            event_type="temporal.opportunity_due",
            occurred_at=now,
            received_at=now,
            persona_id=self.persona_id,
            group_id=opportunity.group_id,
            actor_id=None,
            source_message_id=None,
            correlation_id=f"autonomy:{opportunity.opportunity_id}",
            causation_id=opportunity.source_event_ids[-1],
            payload={
                "opportunity_id": opportunity.opportunity_id,
                "source_event_ids": list(opportunity.source_event_ids),
                "audience": list(opportunity.audience),
                "earliest_at": opportunity.earliest_at,
                "expires_at": opportunity.expires_at,
                "max_attempts": opportunity.max_attempts,
                "attempt": opportunity.attempts,
                "followup_count": opportunity.followup_count,
                "kind": opportunity.kind,
                "scene_version": opportunity.last_scene_version,
                "relationship_version": opportunity.last_relationship_version,
            },
        )

    @staticmethod
    def _all_gates_allow(context: OpportunityRevalidation) -> bool:
        return bool(
            context.scene_allows
            and context.relationship_allows
            and not context.boundary_active
            and context.budget_available
        )

    @staticmethod
    def _same_spec(
        first: AutonomousOpportunity, second: AutonomousOpportunity
    ) -> bool:
        return bool(
            first.source_event_ids == second.source_event_ids
            and first.group_id == second.group_id
            and first.audience == second.audience
            and first.created_earliest_at == second.created_earliest_at
            and first.expires_at == second.expires_at
            and first.max_attempts == second.max_attempts
            and first.kind == second.kind
        )

    def _ensure_table(self) -> None:
        with closing(connect_database(self.path)) as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS autonomous_opportunities ("
                "persona_id TEXT NOT NULL, opportunity_id TEXT NOT NULL, "
                "group_id TEXT NOT NULL, status TEXT NOT NULL "
                "CHECK(status IN ('scheduled','emitting','emitted','expired')), "
                "earliest_at INTEGER NOT NULL, expires_at INTEGER NOT NULL, "
                "state_json TEXT NOT NULL, updated_at INTEGER NOT NULL, "
                "PRIMARY KEY(persona_id, opportunity_id))"
            )
            db.execute(
                "CREATE INDEX IF NOT EXISTS idx_autonomous_opportunities_due "
                "ON autonomous_opportunities(persona_id, status, earliest_at)"
            )


def _canonical_json(value: dict[str, object]) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _opportunity_to_dict(opportunity: AutonomousOpportunity) -> dict[str, object]:
    values = asdict(opportunity)
    values["source_event_ids"] = list(opportunity.source_event_ids)
    values["audience"] = list(opportunity.audience)
    values["status"] = opportunity.status.value
    return values


def _opportunity_from_dict(values: dict[str, object]) -> AutonomousOpportunity:
    normalized = dict(values)
    normalized["source_event_ids"] = tuple(normalized["source_event_ids"])
    normalized["audience"] = tuple(normalized["audience"])
    normalized["status"] = OpportunityStatus(normalized["status"])
    return AutonomousOpportunity(**normalized)


__all__ = (
    "ALLOWED_OPPORTUNITY_KINDS",
    "AutonomousOpportunity",
    "AutonomousOpportunityScheduler",
    "MAX_AUTONOMOUS_FOLLOWUPS",
    "MAX_OPPORTUNITY_ATTEMPTS",
    "OpportunityIdentityConflict",
    "OpportunityLimitReached",
    "OpportunityRevalidation",
    "OpportunityStatus",
)
