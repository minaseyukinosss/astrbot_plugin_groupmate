"""Versioned behavior configuration lifecycle for the control plane."""

from __future__ import annotations

import copy
import json
import math
import re
import sqlite3
import time
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Callable, Mapping, Sequence

from ..persistence.schema import connect_database, initialize_database


class ConfigStatus(str, Enum):
    DRAFT = "DRAFT"
    VALIDATED = "VALIDATED"
    PUBLISHED = "PUBLISHED"
    SUPERSEDED = "SUPERSEDED"


class ConfigVersionError(RuntimeError):
    """Base error for invalid configuration lifecycle operations."""


class ConfigNotFound(ConfigVersionError, LookupError):
    """Raised when a scoped configuration version does not exist."""


class InvalidConfigTransition(ConfigVersionError):
    """Raised when a configuration lifecycle transition is illegal."""


class UnsafeBehaviorConfig(ValueError):
    """Raised before deployment secrets or internal prompts can be persisted."""


@dataclass(frozen=True)
class ConfigVersion:
    config_id: str
    version: int
    persona_id: str
    group_id: str | None
    status: ConfigStatus
    config: dict[str, object]
    created_at: int


@dataclass(frozen=True)
class ConfigSnapshot:
    version: int
    config: dict[str, object]


_PROTECTED_KEYS = frozenset(
    {
        "secret",
        "secrets",
        "token",
        "api_key",
        "apikey",
        "auth_code",
        "authcode",
        "password",
        "prompt",
        "system_prompt",
        "systemprompt",
        "chain_of_thought",
        "chainofthought",
        "private_memory",
        "privatememory",
    }
)


class ConfigVersionRepository:
    def __init__(
        self,
        path: Path,
        *,
        publish_hook: Callable[[ConfigVersion], object] | None = None,
    ) -> None:
        self.path = Path(path)
        initialize_database(self.path)
        self._publish_hook = publish_hook

    def load(self, config_id: str, version: int | None = None) -> ConfigVersion:
        normalized = self._required_text(config_id, "config_id")
        with connect_database(self.path) as db:
            if version is None:
                row = db.execute(
                    "SELECT config_id, version, persona_id, group_id, status, "
                    "config_json, created_at FROM config_versions "
                    "WHERE config_id=? ORDER BY version DESC LIMIT 1",
                    (normalized,),
                ).fetchone()
            else:
                row = db.execute(
                    "SELECT config_id, version, persona_id, group_id, status, "
                    "config_json, created_at FROM config_versions "
                    "WHERE config_id=? AND version=?",
                    (normalized, int(version)),
                ).fetchone()
        if row is None:
            raise ConfigNotFound(f"configuration not found: {normalized}")
        return self._decode(row)

    def published_version(
        self,
        *,
        persona_id: str | None = None,
        group_id: str | None = None,
    ) -> int:
        with connect_database(self.path) as db:
            if persona_id is None:
                row = db.execute(
                    "SELECT COALESCE(MAX(version), 0) FROM config_versions "
                    "WHERE status='PUBLISHED'"
                ).fetchone()
            else:
                row = db.execute(
                    "SELECT COALESCE(MAX(version), 0) FROM config_versions "
                    "WHERE status='PUBLISHED' AND persona_id=? "
                    "AND group_id IS ?",
                    (str(persona_id), group_id),
                ).fetchone()
        return int(row[0])

    def snapshot(self, *, persona_id: str, group_id: str | None) -> ConfigSnapshot:
        persona = self._required_text(persona_id, "persona_id")
        with connect_database(self.path) as db:
            row = db.execute(
                "SELECT version, config_json FROM config_versions "
                "WHERE status='PUBLISHED' AND persona_id=? AND group_id IS ? "
                "ORDER BY version DESC LIMIT 1",
                (persona, group_id),
            ).fetchone()
        if row is None:
            return ConfigSnapshot(0, {})
        return ConfigSnapshot(int(row[0]), copy.deepcopy(json.loads(str(row[1]))))

    def create_draft(
        self,
        config_id: str,
        config: Mapping[str, object],
        *,
        persona_id: str,
        group_id: str | None,
        now: int | None = None,
    ) -> ConfigVersion:
        with connect_database(self.path) as db:
            try:
                db.execute("BEGIN IMMEDIATE")
                result = self._create_draft_on(
                    db,
                    config_id,
                    config,
                    persona_id=persona_id,
                    group_id=group_id,
                    now=int(time.time()) if now is None else int(now),
                )
                db.commit()
                return result
            except BaseException:
                db.rollback()
                raise

    def validate(
        self,
        config_id: str,
        *,
        persona_id: str,
        group_id: str | None,
    ) -> ConfigVersion:
        with connect_database(self.path) as db:
            try:
                db.execute("BEGIN IMMEDIATE")
                result = self._validate_on(
                    db,
                    config_id,
                    persona_id=persona_id,
                    group_id=group_id,
                )
                db.commit()
                return result
            except BaseException:
                db.rollback()
                raise

    def dry_run(
        self,
        config_id: str,
        *,
        persona_id: str,
        group_id: str | None,
        historical_events: Sequence[Mapping[str, object]],
        worker_outputs: Sequence[Mapping[str, object]],
    ) -> dict[str, object]:
        with connect_database(self.path) as db:
            return self._dry_run_on(
                db,
                config_id,
                persona_id=persona_id,
                group_id=group_id,
                historical_events=historical_events,
                worker_outputs=worker_outputs,
            )

    def publish(
        self,
        config_id: str,
        *,
        persona_id: str,
        group_id: str | None,
        expected_version: int,
    ) -> ConfigVersion:
        with connect_database(self.path) as db:
            try:
                db.execute("BEGIN IMMEDIATE")
                result = self._publish_on(
                    db,
                    config_id,
                    persona_id=persona_id,
                    group_id=group_id,
                    expected_version=expected_version,
                )
                db.commit()
                return result
            except BaseException:
                db.rollback()
                raise

    def _create_draft_on(
        self,
        db: sqlite3.Connection,
        config_id: str,
        config: Mapping[str, object],
        *,
        persona_id: str,
        group_id: str | None,
        now: int,
    ) -> ConfigVersion:
        identifier = self._required_text(config_id, "config_id")
        persona = self._required_text(persona_id, "persona_id")
        normalized = self._normalize_config(config, require_nonempty=False)
        owner = db.execute(
            "SELECT persona_id, group_id FROM config_versions "
            "WHERE config_id=? LIMIT 1",
            (identifier,),
        ).fetchone()
        if owner is not None and (
            str(owner[0]) != persona or owner[1] != group_id
        ):
            raise ConfigNotFound("configuration scope is not available")
        version = int(
            db.execute(
                "SELECT COALESCE(MAX(version), 0) + 1 FROM config_versions "
                "WHERE persona_id=? AND group_id IS ?",
                (persona, group_id),
            ).fetchone()[0]
        )
        encoded = self._canonical_json(normalized)
        db.execute(
            "INSERT INTO config_versions("
            "config_id, version, persona_id, group_id, status, config_json, created_at"
            ") VALUES(?, ?, ?, ?, ?, ?, ?)",
            (
                identifier,
                version,
                persona,
                group_id,
                ConfigStatus.DRAFT.value,
                encoded,
                int(now),
            ),
        )
        return ConfigVersion(
            identifier,
            version,
            persona,
            group_id,
            ConfigStatus.DRAFT,
            normalized,
            int(now),
        )

    def _validate_on(
        self,
        db: sqlite3.Connection,
        config_id: str,
        *,
        persona_id: str,
        group_id: str | None,
    ) -> ConfigVersion:
        draft = self._latest_on(
            db,
            config_id,
            persona_id=persona_id,
            group_id=group_id,
            statuses=(ConfigStatus.DRAFT,),
        )
        normalized = self._normalize_config(draft.config, require_nonempty=True)
        db.execute(
            "UPDATE config_versions SET status=? "
            "WHERE config_id=? AND version=? AND status=?",
            (
                ConfigStatus.VALIDATED.value,
                draft.config_id,
                draft.version,
                ConfigStatus.DRAFT.value,
            ),
        )
        return ConfigVersion(
            draft.config_id,
            draft.version,
            draft.persona_id,
            draft.group_id,
            ConfigStatus.VALIDATED,
            normalized,
            draft.created_at,
        )

    def _dry_run_on(
        self,
        db: sqlite3.Connection,
        config_id: str,
        *,
        persona_id: str,
        group_id: str | None,
        historical_events: Sequence[Mapping[str, object]],
        worker_outputs: Sequence[Mapping[str, object]],
    ) -> dict[str, object]:
        candidate = self._latest_on(
            db,
            config_id,
            persona_id=persona_id,
            group_id=group_id,
            statuses=(ConfigStatus.VALIDATED,),
        )
        current = self._published_on(
            db, persona_id=persona_id, group_id=group_id
        )
        before = {} if current is None else current.config
        return {
            "config_id": candidate.config_id,
            "candidate_version": candidate.version,
            "base_version": 0 if current is None else current.version,
            "changed": self._semantic_diff(before, candidate.config),
            "historical_event_count": len(tuple(historical_events)),
            "worker_output_count": len(tuple(worker_outputs)),
        }

    def _publish_on(
        self,
        db: sqlite3.Connection,
        config_id: str,
        *,
        persona_id: str,
        group_id: str | None,
        expected_version: int,
    ) -> ConfigVersion:
        current = self._published_version_on(
            db, persona_id=persona_id, group_id=group_id
        )
        if int(expected_version) != current:
            raise ValueError(f"expected config version {expected_version}, current {current}")
        candidate = self._latest_on(
            db,
            config_id,
            persona_id=persona_id,
            group_id=group_id,
            statuses=(ConfigStatus.VALIDATED,),
        )
        published = ConfigVersion(
            candidate.config_id,
            candidate.version,
            candidate.persona_id,
            candidate.group_id,
            ConfigStatus.PUBLISHED,
            copy.deepcopy(candidate.config),
            candidate.created_at,
        )
        if self._publish_hook is not None:
            self._publish_hook(published)
        db.execute(
            "UPDATE config_versions SET status=? "
            "WHERE persona_id=? AND group_id IS ? AND status=?",
            (
                ConfigStatus.SUPERSEDED.value,
                persona_id,
                group_id,
                ConfigStatus.PUBLISHED.value,
            ),
        )
        cursor = db.execute(
            "UPDATE config_versions SET status=? "
            "WHERE config_id=? AND version=? AND status=?",
            (
                ConfigStatus.PUBLISHED.value,
                candidate.config_id,
                candidate.version,
                ConfigStatus.VALIDATED.value,
            ),
        )
        if cursor.rowcount != 1:
            raise InvalidConfigTransition("validated draft changed during publish")
        return published

    def _restore_on(
        self,
        db: sqlite3.Connection,
        config_id: str,
        source_version: int,
        *,
        persona_id: str,
        group_id: str | None,
        expected_version: int,
        now: int,
    ) -> ConfigVersion:
        current = self._published_version_on(
            db, persona_id=persona_id, group_id=group_id
        )
        if int(expected_version) != current:
            raise ValueError(f"expected config version {expected_version}, current {current}")
        source = self._load_on(db, config_id, int(source_version))
        if (
            source.persona_id != persona_id
            or source.group_id != group_id
            or source.status not in {ConfigStatus.PUBLISHED, ConfigStatus.SUPERSEDED}
        ):
            raise ConfigNotFound("restorable configuration version not found")
        next_version = int(
            db.execute(
                "SELECT COALESCE(MAX(version), 0) + 1 FROM config_versions "
                "WHERE persona_id=? AND group_id IS ?",
                (persona_id, group_id),
            ).fetchone()[0]
        )
        restored = ConfigVersion(
            config_id,
            next_version,
            persona_id,
            group_id,
            ConfigStatus.PUBLISHED,
            copy.deepcopy(source.config),
            int(now),
        )
        if self._publish_hook is not None:
            self._publish_hook(restored)
        db.execute(
            "UPDATE config_versions SET status=? "
            "WHERE persona_id=? AND group_id IS ? AND status=?",
            (
                ConfigStatus.SUPERSEDED.value,
                persona_id,
                group_id,
                ConfigStatus.PUBLISHED.value,
            ),
        )
        db.execute(
            "INSERT INTO config_versions("
            "config_id, version, persona_id, group_id, status, config_json, created_at"
            ") VALUES(?, ?, ?, ?, ?, ?, ?)",
            (
                config_id,
                next_version,
                persona_id,
                group_id,
                ConfigStatus.PUBLISHED.value,
                self._canonical_json(source.config),
                int(now),
            ),
        )
        return restored

    def _latest_on(
        self,
        db: sqlite3.Connection,
        config_id: str,
        *,
        persona_id: str,
        group_id: str | None,
        statuses: tuple[ConfigStatus, ...],
    ) -> ConfigVersion:
        placeholders = ",".join("?" for _ in statuses)
        row = db.execute(
            "SELECT config_id, version, persona_id, group_id, status, "
            "config_json, created_at FROM config_versions "
            "WHERE config_id=? AND persona_id=? AND group_id IS ? "
            f"AND status IN ({placeholders}) ORDER BY version DESC LIMIT 1",
            (
                self._required_text(config_id, "config_id"),
                self._required_text(persona_id, "persona_id"),
                group_id,
                *(status.value for status in statuses),
            ),
        ).fetchone()
        if row is None:
            raise ConfigNotFound("scoped configuration draft not found")
        return self._decode(row)

    @staticmethod
    def _load_on(
        db: sqlite3.Connection, config_id: str, version: int
    ) -> ConfigVersion:
        row = db.execute(
            "SELECT config_id, version, persona_id, group_id, status, "
            "config_json, created_at FROM config_versions "
            "WHERE config_id=? AND version=?",
            (config_id, int(version)),
        ).fetchone()
        if row is None:
            raise ConfigNotFound("configuration version not found")
        return ConfigVersionRepository._decode(row)

    @staticmethod
    def _published_on(
        db: sqlite3.Connection, *, persona_id: str, group_id: str | None
    ) -> ConfigVersion | None:
        row = db.execute(
            "SELECT config_id, version, persona_id, group_id, status, "
            "config_json, created_at FROM config_versions "
            "WHERE persona_id=? AND group_id IS ? AND status=? "
            "ORDER BY version DESC LIMIT 1",
            (persona_id, group_id, ConfigStatus.PUBLISHED.value),
        ).fetchone()
        return None if row is None else ConfigVersionRepository._decode(row)

    @staticmethod
    def _published_version_on(
        db: sqlite3.Connection, *, persona_id: str, group_id: str | None
    ) -> int:
        row = db.execute(
            "SELECT COALESCE(MAX(version), 0) FROM config_versions "
            "WHERE persona_id=? AND group_id IS ? AND status=?",
            (persona_id, group_id, ConfigStatus.PUBLISHED.value),
        ).fetchone()
        return int(row[0])

    @staticmethod
    def _decode(row: sqlite3.Row) -> ConfigVersion:
        return ConfigVersion(
            config_id=str(row["config_id"]),
            version=int(row["version"]),
            persona_id=str(row["persona_id"]),
            group_id=None if row["group_id"] is None else str(row["group_id"]),
            status=ConfigStatus(str(row["status"])),
            config=copy.deepcopy(json.loads(str(row["config_json"]))),
            created_at=int(row["created_at"]),
        )

    @classmethod
    def _normalize_config(
        cls, config: Mapping[str, object], *, require_nonempty: bool
    ) -> dict[str, object]:
        if not isinstance(config, Mapping):
            raise UnsafeBehaviorConfig("behavior config must be an object")
        normalized = copy.deepcopy(dict(config))
        if require_nonempty and not normalized:
            raise UnsafeBehaviorConfig("behavior config must not be empty")
        cls._validate_value(normalized, depth=0)
        encoded = cls._canonical_json(normalized)
        if len(encoded.encode("utf-8")) > 256 * 1024:
            raise UnsafeBehaviorConfig("behavior config exceeds size limit")
        return normalized

    @classmethod
    def _validate_value(cls, value: object, *, depth: int) -> None:
        if depth > 12:
            raise UnsafeBehaviorConfig("behavior config nesting is too deep")
        if isinstance(value, Mapping):
            for key, nested in value.items():
                if not isinstance(key, str) or not key.strip():
                    raise UnsafeBehaviorConfig("behavior config keys must be text")
                marker = re.sub(r"[^a-z0-9]", "", key.casefold())
                protected = {
                    re.sub(r"[^a-z0-9]", "", item.casefold())
                    for item in _PROTECTED_KEYS
                }
                if marker in protected:
                    raise UnsafeBehaviorConfig(
                        f"deployment secret or internal field is forbidden: {key}"
                    )
                cls._validate_value(nested, depth=depth + 1)
            return
        if isinstance(value, (list, tuple)):
            for nested in value:
                cls._validate_value(nested, depth=depth + 1)
            return
        if value is None or isinstance(value, (str, int, bool)):
            return
        if isinstance(value, float) and math.isfinite(value):
            return
        raise UnsafeBehaviorConfig("behavior config contains unsupported value")

    @staticmethod
    def _semantic_diff(
        before: Mapping[str, object], after: Mapping[str, object]
    ) -> list[dict[str, object]]:
        left = ConfigVersionRepository._flatten(before)
        right = ConfigVersionRepository._flatten(after)
        return [
            {"path": path, "before": left.get(path), "after": right.get(path)}
            for path in sorted(set(left) | set(right))
            if left.get(path) != right.get(path)
        ]

    @staticmethod
    def _flatten(
        value: Mapping[str, object], prefix: str = ""
    ) -> dict[str, object]:
        result: dict[str, object] = {}
        for key in sorted(value):
            path = f"{prefix}.{key}" if prefix else key
            nested = value[key]
            if isinstance(nested, Mapping):
                result.update(ConfigVersionRepository._flatten(nested, path))
            else:
                result[path] = copy.deepcopy(nested)
        return result

    @staticmethod
    def _canonical_json(value: object) -> str:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )

    @staticmethod
    def _required_text(value: object, label: str) -> str:
        normalized = str(value or "").strip()
        if not normalized:
            raise ValueError(f"{label} must not be empty")
        return normalized


__all__ = (
    "ConfigNotFound",
    "ConfigSnapshot",
    "ConfigStatus",
    "ConfigVersion",
    "ConfigVersionError",
    "ConfigVersionRepository",
    "InvalidConfigTransition",
    "UnsafeBehaviorConfig",
)
