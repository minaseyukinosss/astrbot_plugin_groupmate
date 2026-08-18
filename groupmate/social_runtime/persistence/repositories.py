"""Transactional repositories for authoritative Social Runtime state."""

from __future__ import annotations

import hashlib
import json
import time
from contextlib import closing
from dataclasses import asdict, replace
from pathlib import Path

from ..contracts import GlobalSelfState, GlobalStateEffect
from .schema import connect_database, initialize_database
from ..society.relationships import RelationshipProjector, RelationshipProjection
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
    "SQLiteSocietyRepository",
    "SQLitePersonaStateRepository",
    "ScopeRequiredError",
    "StateVersionConflict",
)
