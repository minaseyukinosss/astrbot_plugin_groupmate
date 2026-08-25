"""Transactional repositories for authoritative Social Runtime state."""

from __future__ import annotations

import hashlib
import json
import time
from contextlib import closing
from dataclasses import asdict, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ..contracts import GlobalSelfState, GlobalStateEffect
from .schema import connect_database, initialize_database
from ..society.relationships import (
    PublicAffection,
    RelationshipEvidence,
    RelationshipProjector,
    RelationshipProjection,
)
from ..society.relationship_events import (
    RelationshipEventDecision,
    RelationshipEventPolicy,
    RelationshipEventProposal,
)
from ..society.impressions import Impression
from ..society.culture import CultureArtifact


class StateVersionConflict(RuntimeError):
    """Raised when an effect was based on an obsolete state snapshot."""


class EffectIdentityConflict(RuntimeError):
    """Raised when an effect id is reused for different content or ownership."""


class InvalidGlobalStateEffect(ValueError):
    """Raised when an effect cannot be applied to authoritative self state."""


class ScopeRequiredError(ValueError):
    """Raised before SQL when a group-private query lacks its full scope."""


class RelationshipEventIdentityConflict(RuntimeError):
    """Raised when a relationship event ID is reused for different facts."""


_RANGES = {
    "energy_delta": ("energy", 0, 100),
    "valence_delta": ("valence", -100, 100),
    "arousal_delta": ("arousal", -100, 100),
    "irritation_delta": ("irritation", -100, 100),
    "cognitive_load_delta": ("cognitive_load", 0, 100),
}


def _canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _state_to_json(state: GlobalSelfState) -> str:
    return _canonical_json(asdict(state))


def _state_from_json(payload: str) -> GlobalSelfState:
    return GlobalSelfState(**json.loads(payload))


def _effect_to_json(effect: GlobalStateEffect) -> str:
    return _canonical_json(asdict(effect))


def _validate_effect(effect: GlobalStateEffect) -> None:
    if not effect.effect_id.strip():
        raise InvalidGlobalStateEffect("effect_id must not be empty")
    if not effect.source_event_id.strip():
        raise InvalidGlobalStateEffect("source_event_id must not be empty")
    if effect.expected_version < 0:
        raise InvalidGlobalStateEffect("expected_version must not be negative")
    if effect.kind not in _RANGES:
        raise InvalidGlobalStateEffect(f"unsupported effect kind: {effect.kind}")
    if effect.source_event_id not in effect.evidence_event_ids:
        raise InvalidGlobalStateEffect("source event must be included in evidence")


class SQLitePersonaStateRepository:
    """Persists versioned self state and causal-effect receipts atomically."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        initialize_database(self.path)

    def load(self, persona_id: str) -> GlobalSelfState:
        if not persona_id.strip():
            raise ValueError("persona_id must not be empty")
        with closing(connect_database(self.path)) as db:
            row = db.execute(
                "SELECT state_json FROM persona_state WHERE persona_id=?",
                (persona_id,),
            ).fetchone()
        if row is None:
            return GlobalSelfState(persona_id=persona_id)
        return _state_from_json(row["state_json"])

    def apply_effect(
        self, persona_id: str, effect: GlobalStateEffect
    ) -> GlobalSelfState:
        """Apply once under a write lock; retries return their original result."""

        if not persona_id.strip():
            raise ValueError("persona_id must not be empty")
        _validate_effect(effect)
        effect_json = _effect_to_json(effect)
        now = int(time.time())

        with closing(connect_database(self.path)) as db:
            try:
                db.execute("BEGIN IMMEDIATE")
                receipt = db.execute(
                    "SELECT persona_id, effect_json, result_state_json "
                    "FROM persona_effects WHERE effect_id=?",
                    (effect.effect_id,),
                ).fetchone()
                if receipt is not None:
                    if (
                        receipt["persona_id"] != persona_id
                        or receipt["effect_json"] != effect_json
                    ):
                        raise EffectIdentityConflict(
                            f"effect id already belongs to different content: {effect.effect_id}"
                        )
                    db.commit()
                    return _state_from_json(receipt["result_state_json"])

                row = db.execute(
                    "SELECT state_json FROM persona_state WHERE persona_id=?",
                    (persona_id,),
                ).fetchone()
                current = (
                    GlobalSelfState(persona_id=persona_id)
                    if row is None
                    else _state_from_json(row["state_json"])
                )
                if effect.expected_version != current.version:
                    raise StateVersionConflict(
                        f"expected {effect.expected_version}, current {current.version}"
                    )

                field, lower, upper = _RANGES[effect.kind]
                value = max(lower, min(upper, getattr(current, field) + effect.amount))
                updated = replace(
                    current,
                    **{field: value},
                    last_transition_at=now,
                    version=current.version + 1,
                )
                state_json = _state_to_json(updated)
                db.execute(
                    "INSERT INTO persona_state(persona_id, version, state_json, updated_at) "
                    "VALUES(?, ?, ?, ?) "
                    "ON CONFLICT(persona_id) DO UPDATE SET "
                    "version=excluded.version, state_json=excluded.state_json, "
                    "updated_at=excluded.updated_at",
                    (persona_id, updated.version, state_json, now),
                )
                db.execute(
                    "INSERT INTO persona_effects("
                    "effect_id, persona_id, source_event_id, expected_version, "
                    "effect_json, result_state_json, applied_version, applied_at"
                    ") VALUES(?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        effect.effect_id,
                        persona_id,
                        effect.source_event_id,
                        effect.expected_version,
                        effect_json,
                        state_json,
                        updated.version,
                        now,
                    ),
                )
                db.commit()
                return updated
            except BaseException:
                db.rollback()
                raise


class SQLiteSocietyRepository:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        initialize_database(self.path)
        self._projector = RelationshipProjector()

    def save_relationship(self, state: RelationshipProjection) -> None:
        self._require_scope(state.persona_id, state.group_id, state.subject_id)
        encoded = _canonical_json(self._projector.to_dict(state))
        with closing(connect_database(self.path)) as db:
            db.execute(
                "INSERT INTO relationship_projection("
                "persona_id, group_id, subject_id, version, projection_json, updated_at"
                ") VALUES(?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(persona_id, group_id, subject_id) DO UPDATE SET "
                "version=excluded.version, projection_json=excluded.projection_json, "
                "updated_at=excluded.updated_at",
                (
                    state.persona_id,
                    state.group_id,
                    state.subject_id,
                    state.version,
                    encoded,
                    int(time.time()),
                ),
            )
            db.commit()

    def load_relationship(
        self, persona_id: str, group_id: str, subject_id: str
    ) -> RelationshipProjection:
        self._require_scope(persona_id, group_id, subject_id)
        with closing(connect_database(self.path)) as db:
            row = db.execute(
                "SELECT projection_json FROM relationship_projection "
                "WHERE persona_id=? AND group_id=? AND subject_id=?",
                (persona_id, group_id, subject_id),
            ).fetchone()
        if row is None:
            return self._projector.empty(persona_id, group_id, subject_id)
        return self._projector.from_dict(json.loads(row[0]))

    def relationship_snapshot(
        self, persona_id: str, group_id: str, subject_id: str
    ) -> tuple[RelationshipProjection, PublicAffection]:
        state = self.load_relationship(persona_id, group_id, subject_id)
        return state, PublicAffection.from_projection(state)

    def process_relationship_event(
        self,
        proposal: RelationshipEventProposal,
        *,
        mode: str,
        policy: RelationshipEventPolicy,
    ) -> RelationshipEventDecision:
        resolved_mode = str(mode).upper()
        if resolved_mode not in {"SHADOW", "SOCIAL_RUNTIME"}:
            raise ValueError("relationship mode must be SHADOW or SOCIAL_RUNTIME")
        self._require_scope(
            proposal.persona_id, proposal.group_id, proposal.subject_id
        )
        proposal_payload = self._relationship_proposal_payload(proposal)
        proposal_json = _canonical_json(proposal_payload)
        with closing(connect_database(self.path)) as db:
            try:
                db.execute("BEGIN IMMEDIATE")
                existing = db.execute(
                    "SELECT event_json FROM relationship_events WHERE event_id=?",
                    (proposal.event_id,),
                ).fetchone()
                if existing is not None:
                    stored = json.loads(str(existing["event_json"]))
                    if _canonical_json(stored.get("proposal")) != proposal_json:
                        raise RelationshipEventIdentityConflict(proposal.event_id)
                    prior = self._relationship_decision_from_payload(stored["decision"])
                    db.commit()
                    return RelationshipEventDecision(
                        "DUPLICATE",
                        ("event_already_processed",),
                        proposal,
                        prior.evidence,
                        0.0,
                    )
                row = db.execute(
                    "SELECT projection_json FROM relationship_projection "
                    "WHERE persona_id=? AND group_id=? AND subject_id=?",
                    (proposal.persona_id, proposal.group_id, proposal.subject_id),
                ).fetchone()
                current = (
                    self._projector.empty(
                        proposal.persona_id,
                        proposal.group_id,
                        proposal.subject_id,
                    )
                    if row is None
                    else self._projector.from_dict(json.loads(row["projection_json"]))
                )
                day_start, day_end = self._shanghai_day(proposal.occurred_at)
                rows = db.execute(
                    "SELECT event_json FROM relationship_events "
                    "WHERE persona_id=? AND group_id=? AND subject_id=? "
                    "AND occurred_at>=? AND occurred_at<?",
                    (
                        proposal.persona_id,
                        proposal.group_id,
                        proposal.subject_id,
                        day_start,
                        day_end,
                    ),
                ).fetchall()
                positive_today = sum(
                    max(
                        0.0,
                        self._relationship_decision_from_payload(
                            json.loads(str(item["event_json"]))["decision"]
                        ).public_delta,
                    )
                    for item in rows
                    if json.loads(str(item["event_json"]))["decision"].get(
                        "outcome"
                    )
                    == "ACCEPT"
                )
                policy_decision = policy.decide(
                    proposal,
                    current=current,
                    positive_delta_today=positive_today,
                )
                decision = (
                    RelationshipEventDecision(
                        "SUGGEST",
                        ("shadow_suggestion",),
                        proposal,
                        policy_decision.evidence,
                        policy_decision.public_delta,
                    )
                    if resolved_mode == "SHADOW"
                    and policy_decision.outcome == "ACCEPT"
                    else policy_decision
                )
                event_json = _canonical_json(
                    {
                        "mode": resolved_mode,
                        "proposal": proposal_payload,
                        "decision": self._relationship_decision_payload(decision),
                    }
                )
                db.execute(
                    "INSERT INTO relationship_events(event_id, persona_id, group_id, "
                    "subject_id, event_json, occurred_at) VALUES(?, ?, ?, ?, ?, ?)",
                    (
                        proposal.event_id,
                        proposal.persona_id,
                        proposal.group_id,
                        proposal.subject_id,
                        event_json,
                        proposal.occurred_at,
                    ),
                )
                if (
                    resolved_mode == "SOCIAL_RUNTIME"
                    and decision.outcome == "ACCEPT"
                ):
                    if decision.evidence is not None:
                        updated = self._projector.apply(current, decision.evidence)
                        encoded = _canonical_json(self._projector.to_dict(updated))
                        db.execute(
                            "INSERT INTO relationship_projection(persona_id, group_id, "
                            "subject_id, version, projection_json, updated_at) "
                            "VALUES(?, ?, ?, ?, ?, ?) "
                            "ON CONFLICT(persona_id, group_id, subject_id) DO UPDATE SET "
                            "version=excluded.version, projection_json=excluded.projection_json, "
                            "updated_at=excluded.updated_at",
                            (
                                updated.persona_id,
                                updated.group_id,
                                updated.subject_id,
                                updated.version,
                                encoded,
                                int(time.time()),
                            ),
                        )
                    self._persist_relationship_memory(db, decision)
                db.commit()
                return decision
            except BaseException:
                db.rollback()
                raise

    def relationship_memories(
        self, persona_id: str, group_id: str, subject_id: str
    ) -> tuple[RelationshipMemory, ...]:
        from ..memory.relationship_memory import RelationshipMemory

        self._require_scope(persona_id, group_id, subject_id)
        with closing(connect_database(self.path)) as db:
            rows = db.execute(
                "SELECT memory_json FROM memories WHERE persona_id=? AND group_id=? "
                "AND subject_id=? AND kind='relationship' ORDER BY created_at, memory_id",
                (persona_id, group_id, subject_id),
            ).fetchall()
        return tuple(
            RelationshipMemory(**json.loads(str(row["memory_json"])))
            for row in rows
        )

    def relationship_memories_for_subjects(
        self,
        persona_id: str,
        group_id: str,
        subject_ids: tuple[str, ...],
    ) -> tuple[RelationshipMemory, ...]:
        from ..memory.relationship_memory import RelationshipMemory

        self._require_group_scope(persona_id, group_id)
        subjects = tuple(
            dict.fromkeys(
                str(item or "").strip()
                for item in subject_ids
                if str(item or "").strip()
            )
        )[:8]
        if not subjects:
            return ()
        placeholders = ",".join("?" for _ in subjects)
        with closing(connect_database(self.path)) as db:
            rows = db.execute(
                "SELECT memory_json FROM memories WHERE persona_id=? AND group_id=? "
                f"AND subject_id IN ({placeholders}) AND kind='relationship' "
                "ORDER BY created_at, memory_id",
                (persona_id, group_id, *subjects),
            ).fetchall()
        return tuple(
            RelationshipMemory(**json.loads(str(row["memory_json"])))
            for row in rows
        )

    @staticmethod
    def _persist_relationship_memory(
        db, decision: RelationshipEventDecision
    ) -> None:
        from ..memory.relationship_memory import (
            RelationshipMemory,
            relationship_memory_from_decision,
        )

        proposal = decision.proposal
        if proposal.kind == "repair_confirmed" and proposal.repair_of:
            rows = db.execute(
                "SELECT memory_id, memory_json FROM memories WHERE persona_id=? "
                "AND group_id=? AND subject_id=? AND kind='relationship'",
                (proposal.persona_id, proposal.group_id, proposal.subject_id),
            ).fetchall()
            for row in rows:
                memory = RelationshipMemory(
                    **json.loads(str(row["memory_json"]))
                )
                if memory.relationship_event_id != proposal.repair_of:
                    continue
                resolved = memory.resolve(
                    at=proposal.occurred_at, by=proposal.event_id
                )
                db.execute(
                    "UPDATE memories SET memory_json=? WHERE memory_id=?",
                    (_canonical_json(asdict(resolved)), memory.memory_id),
                )
        memory = relationship_memory_from_decision(decision)
        if memory is None:
            return
        db.execute(
            "INSERT OR IGNORE INTO memories(memory_id, persona_id, group_id, "
            "subject_id, kind, sensitivity, memory_json, created_at, expires_at) "
            "VALUES(?, ?, ?, ?, 'relationship', ?, ?, ?, NULL)",
            (
                memory.memory_id,
                memory.persona_id,
                memory.group_id,
                memory.subject_id,
                memory.sensitivity,
                _canonical_json(asdict(memory)),
                memory.occurred_at,
            ),
        )

    def relationship_decisions(
        self,
        persona_id: str,
        group_id: str,
        subject_id: str,
        *,
        since: int | None = None,
    ) -> tuple[RelationshipEventDecision, ...]:
        self._require_scope(persona_id, group_id, subject_id)
        query = (
            "SELECT event_json FROM relationship_events "
            "WHERE persona_id=? AND group_id=? AND subject_id=?"
        )
        params: tuple[object, ...] = (persona_id, group_id, subject_id)
        if since is not None:
            query += " AND occurred_at>=?"
            params += (int(since),)
        query += " ORDER BY occurred_at, event_id"
        with closing(connect_database(self.path)) as db:
            rows = db.execute(query, params).fetchall()
        return tuple(
            self._relationship_decision_from_payload(
                json.loads(str(row["event_json"]))["decision"]
            )
            for row in rows
        )

    @staticmethod
    def _relationship_proposal_payload(
        proposal: RelationshipEventProposal,
    ) -> dict[str, object]:
        payload = asdict(proposal)
        payload["source_event_ids"] = list(proposal.source_event_ids)
        return payload

    @staticmethod
    def _relationship_decision_payload(
        decision: RelationshipEventDecision,
    ) -> dict[str, object]:
        return {
            "outcome": decision.outcome,
            "reason_codes": list(decision.reason_codes),
            "proposal": SQLiteSocietyRepository._relationship_proposal_payload(
                decision.proposal
            ),
            "evidence": (
                asdict(decision.evidence)
                if decision.evidence is not None
                else None
            ),
            "public_delta": decision.public_delta,
        }

    @staticmethod
    def _relationship_decision_from_payload(
        payload: dict[str, object],
    ) -> RelationshipEventDecision:
        proposal = RelationshipEventProposal(
            **{
                **dict(payload["proposal"]),
                "source_event_ids": tuple(
                    dict(payload["proposal"]).get("source_event_ids", ())
                ),
            }
        )
        evidence_payload = payload.get("evidence")
        evidence = (
            RelationshipEvidence(**dict(evidence_payload))
            if isinstance(evidence_payload, dict)
            else None
        )
        return RelationshipEventDecision(
            str(payload["outcome"]),
            tuple(payload.get("reason_codes", ())),
            proposal,
            evidence,
            float(payload.get("public_delta", 0.0)),
        )

    @staticmethod
    def _shanghai_day(timestamp: int) -> tuple[int, int]:
        zone = timezone(timedelta(hours=8))
        local = datetime.fromtimestamp(int(timestamp), zone)
        start = local.replace(hour=0, minute=0, second=0, microsecond=0)
        return int(start.timestamp()), int((start + timedelta(days=1)).timestamp())

    def save_impression(self, impression: Impression) -> None:
        self._require_scope(
            impression.persona_id, impression.group_id, impression.subject_id
        )
        encoded = _canonical_json(asdict(impression))
        with closing(connect_database(self.path)) as db:
            db.execute(
                "INSERT INTO impressions("
                "impression_id, persona_id, group_id, subject_id, status, "
                "impression_json, expires_at) VALUES(?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(impression_id) DO UPDATE SET "
                "status=excluded.status, impression_json=excluded.impression_json, "
                "expires_at=excluded.expires_at",
                (
                    impression.impression_id,
                    impression.persona_id,
                    impression.group_id,
                    impression.subject_id,
                    impression.status,
                    encoded,
                    impression.expires_at,
                ),
            )
            db.commit()

    def list_impressions(
        self, persona_id: str, group_id: str, subject_id: str
    ) -> tuple[Impression, ...]:
        self._require_scope(persona_id, group_id, subject_id)
        with closing(connect_database(self.path)) as db:
            rows = db.execute(
                "SELECT impression_json FROM impressions "
                "WHERE persona_id=? AND group_id=? AND subject_id=? "
                "ORDER BY impression_id",
                (persona_id, group_id, subject_id),
            ).fetchall()
        result = []
        for row in rows:
            values = json.loads(row[0])
            values["evidence_event_ids"] = tuple(values["evidence_event_ids"])
            values["use_scope"] = tuple(values["use_scope"])
            result.append(Impression(**values))
        return tuple(result)

    def save_culture(self, artifact: CultureArtifact) -> None:
        self._require_group_scope(artifact.persona_id, artifact.group_id)
        storage_id = hashlib.sha256(
            f"{artifact.persona_id}\0{artifact.group_id}\0{artifact.artifact_id}".encode()
        ).hexdigest()
        encoded = _canonical_json(asdict(artifact))
        with closing(connect_database(self.path)) as db:
            db.execute(
                "INSERT INTO culture(artifact_id, persona_id, group_id, status, "
                "artifact_json, updated_at) VALUES(?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(artifact_id) DO UPDATE SET status=excluded.status, "
                "artifact_json=excluded.artifact_json, updated_at=excluded.updated_at",
                (
                    storage_id,
                    artifact.persona_id,
                    artifact.group_id,
                    artifact.status,
                    encoded,
                    int(time.time()),
                ),
            )
            db.commit()

    def list_culture(
        self, persona_id: str, group_id: str
    ) -> tuple[CultureArtifact, ...]:
        self._require_group_scope(persona_id, group_id)
        with closing(connect_database(self.path)) as db:
            rows = db.execute(
                "SELECT artifact_json FROM culture WHERE persona_id=? AND group_id=? "
                "ORDER BY artifact_id",
                (persona_id, group_id),
            ).fetchall()
        result = []
        for row in rows:
            values = json.loads(row[0])
            values["evidence_event_ids"] = tuple(values["evidence_event_ids"])
            result.append(CultureArtifact(**values))
        return tuple(result)

    @staticmethod
    def _require_scope(persona_id: str, group_id: str, subject_id: str) -> None:
        if not persona_id.strip() or not group_id.strip() or not subject_id.strip():
            raise ScopeRequiredError("persona_id, group_id, and subject_id are required")

    @staticmethod
    def _require_group_scope(persona_id: str, group_id: str) -> None:
        if not persona_id.strip() or not group_id.strip():
            raise ScopeRequiredError("persona_id and group_id are required")


__all__ = (
    "EffectIdentityConflict",
    "InvalidGlobalStateEffect",
    "RelationshipEventIdentityConflict",
    "SQLiteSocietyRepository",
    "SQLitePersonaStateRepository",
    "ScopeRequiredError",
    "StateVersionConflict",
)
