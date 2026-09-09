"""SQLite sticker lexicon with content-hash identity and disk files."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

from ..persistence.schema import connect_database, initialize_database
from .cognition import (
    InvalidStickerMeaning,
    meaning_is_sendable,
    normalize_attitudes,
    normalize_meaning,
    normalize_phrases,
    require_sendable_meaning,
)
from .contracts import (
    CANDIDATE_POOL_LIMIT,
    DEFAULT_COOLDOWN_SECONDS,
    IngestOutcome,
    LIBRARY_LIMIT,
    MAX_STICKER_BYTES,
    STICKER_LICENSES,
    STICKER_ORIGINS,
    STICKER_STATUSES,
    StickerCard,
    affection_floor_label,
    parse_affection_floor,
)
from .images import extension_for, looks_like_sticker_file, mime_matches


class InvalidStickerAsset(ValueError):
    """Raised when sticker bytes or metadata cannot be stored."""


class UnsafeStickerPath(ValueError):
    """Raised when a sticker path would leave the plugin data directory."""


class StickerLexicon:
    def __init__(self, data_dir: Path, database_path: Path) -> None:
        self.data_dir = Path(data_dir).resolve()
        self.database_path = Path(database_path)
        self.files_dir = (self.data_dir / "persona_media" / "stickers" / "files").resolve()
        initialize_database(self.database_path)
        self.files_dir.mkdir(parents=True, exist_ok=True)

    def ingest(
        self,
        content: bytes,
        *,
        mime_type: str,
        origin_kind: str,
        license_status: str,
        now: int,
        source_group_id: str | None = None,
        source_event_id: str | None = None,
        skip_coarse_filter: bool = False,
    ) -> IngestOutcome:
        payload = bytes(content)
        mime = str(mime_type or "").strip().casefold()
        origin = str(origin_kind or "").strip()
        license_name = str(license_status or "").strip()
        if origin not in STICKER_ORIGINS:
            raise InvalidStickerAsset("sticker origin is invalid")
        if origin == "group_captured":
            license_name = "group_captured"
        elif license_name not in STICKER_LICENSES - {"group_captured"}:
            raise InvalidStickerAsset("sticker license is not approved")
        if not payload or len(payload) > MAX_STICKER_BYTES:
            raise InvalidStickerAsset("sticker size is invalid")
        if not mime_matches(payload, mime):
            raise InvalidStickerAsset("sticker MIME or magic does not match")
        digest = hashlib.sha256(payload).hexdigest()
        with connect_database(self.database_path) as db:
            db.execute("BEGIN IMMEDIATE")
            rejected = db.execute(
                "SELECT reason FROM sticker_rejects WHERE sha256=?", (digest,)
            ).fetchone()
            if rejected is not None:
                db.execute("COMMIT")
                return IngestOutcome(None, False, "rejected_hash")
            existing = db.execute(
                "SELECT * FROM sticker_assets WHERE sha256=?", (digest,)
            ).fetchone()
            if existing is not None:
                card = self._from_row(existing)
                if card.status == "rejected":
                    db.execute("COMMIT")
                    return IngestOutcome(card, False, "rejected_hash")
                db.execute(
                    "UPDATE sticker_assets SET sighting_count=sighting_count+1, "
                    "updated_at=? WHERE asset_id=?",
                    (int(now), card.asset_id),
                )
                refreshed = db.execute(
                    "SELECT * FROM sticker_assets WHERE asset_id=?",
                    (card.asset_id,),
                ).fetchone()
                db.execute("COMMIT")
                return IngestOutcome(self._from_row(refreshed), False, "seen_before")
            if origin == "group_captured" and not skip_coarse_filter:
                if not looks_like_sticker_file(payload, mime):
                    db.execute("COMMIT")
                    return IngestOutcome(None, False, "coarse_filter")
            capacity = self._capacity_locked(db)
            if origin == "group_captured":
                if capacity["candidate_count"] >= CANDIDATE_POOL_LIMIT:
                    db.execute("COMMIT")
                    return IngestOutcome(None, False, "candidate_pool_full")
                if capacity["library_count"] >= LIBRARY_LIMIT:
                    db.execute("COMMIT")
                    return IngestOutcome(None, False, "library_full")
            elif capacity["candidate_count"] >= CANDIDATE_POOL_LIMIT:
                db.execute("COMMIT")
                return IngestOutcome(None, False, "candidate_pool_full")
            asset_id = f"sticker:{digest[:24]}"
            relative = Path("persona_media") / "stickers" / "files" / f"{digest}{extension_for(mime)}"
            target = self._registered_file_path(relative)
            target.write_bytes(payload)
            try:
                db.execute(
                    "INSERT INTO sticker_assets("
                    "asset_id, sha256, mime_type, size_bytes, relative_path, "
                    "origin_kind, license_status, source_group_id, source_event_id, "
                    "status, meaning, use_when_json, do_not_use_json, attitudes_json, "
                    "intensity, min_familiarity, max_boundary_pressure, caption_source, "
                    "is_sticker_judgment, judgment_reason, sighting_count, use_count, "
                    "last_used_at, last_used_group_id, cooldown_seconds, created_at, "
                    "updated_at) VALUES("
                    "?,?,?,?,?,?,?,?,?,'candidate','','[]','[]','[]',50,0,100,'',"
                    "'','',1,0,NULL,NULL,?,?,?)",
                    (
                        asset_id,
                        digest,
                        mime,
                        len(payload),
                        relative.as_posix(),
                        origin,
                        license_name,
                        str(source_group_id or "").strip() or None,
                        str(source_event_id or "").strip() or None,
                        DEFAULT_COOLDOWN_SECONDS,
                        int(now),
                        int(now),
                    ),
                )
            except sqlite3.IntegrityError:
                if target.exists() and target.stat().st_size == len(payload):
                    pass
                row = db.execute(
                    "SELECT * FROM sticker_assets WHERE sha256=?", (digest,)
                ).fetchone()
                if row is None:
                    db.execute("ROLLBACK")
                    raise
                db.execute(
                    "UPDATE sticker_assets SET sighting_count=sighting_count+1, "
                    "updated_at=? WHERE sha256=?",
                    (int(now), digest),
                )
                refreshed = db.execute(
                    "SELECT * FROM sticker_assets WHERE sha256=?", (digest,)
                ).fetchone()
                db.execute("COMMIT")
                return IngestOutcome(self._from_row(refreshed), False, "seen_before")
            row = db.execute(
                "SELECT * FROM sticker_assets WHERE asset_id=?", (asset_id,)
            ).fetchone()
            db.execute("COMMIT")
            return IngestOutcome(self._from_row(row), True, "candidate")

    def get(self, asset_id: str) -> StickerCard | None:
        with connect_database(self.database_path) as db:
            row = db.execute(
                "SELECT * FROM sticker_assets WHERE asset_id=?",
                (str(asset_id or "").strip(),),
            ).fetchone()
        return None if row is None else self._from_row(row)

    def list_cards(self, *, statuses: tuple[str, ...] | None = None) -> tuple[StickerCard, ...]:
        allowed = tuple(status for status in (statuses or ()) if status in STICKER_STATUSES)
        with connect_database(self.database_path) as db:
            if allowed:
                placeholders = ",".join("?" for _ in allowed)
                rows = db.execute(
                    f"SELECT * FROM sticker_assets WHERE status IN ({placeholders}) "
                    "ORDER BY updated_at DESC, created_at DESC",
                    allowed,
                ).fetchall()
            else:
                rows = db.execute(
                    "SELECT * FROM sticker_assets ORDER BY updated_at DESC, created_at DESC"
                ).fetchall()
        return tuple(self._from_row(row) for row in rows)

    def inbox(self) -> tuple[StickerCard, ...]:
        cards = self.list_cards(statuses=("candidate",))
        return tuple(
            card
            for card in cards
            if card.status == "candidate"
        )

    def library(self) -> tuple[StickerCard, ...]:
        return self.list_cards(statuses=("ready", "disabled"))

    def ready_sendable(self) -> tuple[StickerCard, ...]:
        return tuple(
            card
            for card in self.list_cards(statuses=("ready",))
            if card.sendable() and meaning_is_sendable(card.meaning)
        )

    def capacity(self) -> dict[str, object]:
        with connect_database(self.database_path) as db:
            counts = self._capacity_locked(db)
        return {
            **counts,
            "candidate_limit": CANDIDATE_POOL_LIMIT,
            "library_limit": LIBRARY_LIMIT,
            "candidate_full": counts["candidate_count"] >= CANDIDATE_POOL_LIMIT,
            "library_full": counts["library_count"] >= LIBRARY_LIMIT,
        }

    def resolved_file(self, asset_id: str) -> Path:
        card = self.get(asset_id)
        if card is None:
            raise InvalidStickerAsset("sticker is missing")
        return self.validate_file(card)

    def validate_file(self, card: StickerCard) -> Path:
        target = self._registered_file_path(Path(card.relative_path))
        if target.is_symlink() or not target.is_file():
            raise InvalidStickerAsset("registered sticker file is missing or unsafe")
        content = target.read_bytes()
        if len(content) != card.size_bytes or not mime_matches(content, card.mime_type):
            raise InvalidStickerAsset("registered sticker file does not match")
        if hashlib.sha256(content).hexdigest() != card.sha256:
            raise InvalidStickerAsset("registered sticker checksum does not match")
        return target

    def preview_payload(self, asset_id: str) -> dict[str, str]:
        card = self.get(asset_id)
        if card is None or card.status == "rejected":
            raise LookupError("sticker not found")
        path = self.validate_file(card)
        import base64

        encoded = base64.b64encode(path.read_bytes()).decode("ascii")
        return {
            "kind": "image",
            "mime_type": card.mime_type,
            "data_uri": f"data:{card.mime_type};base64,{encoded}",
        }

    def write_cognition(
        self,
        asset_id: str,
        *,
        meaning: object,
        use_when: object = (),
        do_not_use: object = (),
        attitudes: object = (),
        intensity: int = 50,
        min_familiarity: int = 0,
        max_boundary_pressure: int = 100,
        caption_source: str = "admin",
        now: int,
        require_meaning: bool = False,
    ) -> StickerCard:
        card = self._require(asset_id)
        if card.status == "rejected":
            raise InvalidStickerAsset("rejected stickers cannot be recaptioned")
        text = normalize_meaning(meaning)
        if require_meaning:
            text = require_sendable_meaning(text)
        min_familiarity = parse_affection_floor(min_familiarity)
        if not -100 <= int(max_boundary_pressure) <= 100:
            raise InvalidStickerAsset("sticker boundary restriction is invalid")
        if not 0 <= int(intensity) <= 100:
            raise InvalidStickerAsset("sticker intensity is invalid")
        source = str(caption_source or "admin").strip()
        if card.caption_source == "vision" and source == "admin":
            source = "mixed"
        elif source not in {"admin", "vision", "mixed"}:
            source = "admin"
        with connect_database(self.database_path) as db:
            db.execute(
                "UPDATE sticker_assets SET meaning=?, use_when_json=?, do_not_use_json=?, "
                "attitudes_json=?, intensity=?, min_familiarity=?, max_boundary_pressure=?, "
                "caption_source=?, updated_at=? WHERE asset_id=?",
                (
                    text,
                    json.dumps(list(normalize_phrases(use_when)), ensure_ascii=False),
                    json.dumps(list(normalize_phrases(do_not_use)), ensure_ascii=False),
                    json.dumps(list(normalize_attitudes(attitudes)), ensure_ascii=False),
                    int(intensity),
                    int(min_familiarity),
                    int(max_boundary_pressure),
                    source,
                    int(now),
                    card.asset_id,
                ),
            )
        return self._require(asset_id)

    def record_judgment(self, asset_id: str, *, reason: str, now: int) -> StickerCard:
        card = self._require(asset_id)
        if card.status == "rejected":
            raise InvalidStickerAsset("rejected stickers cannot be judged again")
        detail = str(reason or "").strip()[:80]
        with connect_database(self.database_path) as db:
            db.execute(
                "UPDATE sticker_assets SET is_sticker_judgment='sticker', "
                "judgment_reason=?, updated_at=? WHERE asset_id=?",
                (detail, int(now), card.asset_id),
            )
        return self._require(asset_id)

    def note_vision_failure(self, asset_id: str, *, reason: str, now: int) -> StickerCard:
        card = self._require(asset_id)
        if card.status == "rejected":
            return card
        detail = str(reason or "vision_unavailable").strip()[:80]
        with connect_database(self.database_path) as db:
            db.execute(
                "UPDATE sticker_assets SET judgment_reason=?, updated_at=? WHERE asset_id=?",
                (detail, int(now), card.asset_id),
            )
        return self._require(asset_id)

    def confirm(self, asset_id: str, *, now: int) -> StickerCard:
        card = self._require(asset_id)
        if card.status not in {"candidate", "disabled"}:
            raise InvalidStickerAsset("only candidate or disabled stickers can be confirmed")
        if not meaning_is_sendable(card.meaning):
            raise InvalidStickerMeaning("sticker meaning is empty or too generic")
        with connect_database(self.database_path) as db:
            counts = self._capacity_locked(db)
            if card.status != "disabled" and counts["library_count"] >= LIBRARY_LIMIT:
                raise InvalidStickerAsset("sticker library is full")
            db.execute(
                "UPDATE sticker_assets SET status='ready', updated_at=? WHERE asset_id=?",
                (int(now), card.asset_id),
            )
        return self._require(asset_id)

    def disable(self, asset_id: str, *, now: int) -> StickerCard:
        card = self._require(asset_id)
        if card.status not in {"ready", "candidate"}:
            raise InvalidStickerAsset("sticker cannot be disabled")
        with connect_database(self.database_path) as db:
            db.execute(
                "UPDATE sticker_assets SET status='disabled', updated_at=? WHERE asset_id=?",
                (int(now), card.asset_id),
            )
        return self._require(asset_id)

    def reject(self, asset_id: str, *, now: int, reason: str = "admin_rejected") -> StickerCard:
        card = self._require(asset_id)
        with connect_database(self.database_path) as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute(
                "UPDATE sticker_assets SET status='rejected', updated_at=? WHERE asset_id=?",
                (int(now), card.asset_id),
            )
            db.execute(
                "INSERT INTO sticker_rejects(sha256, reason, rejected_at, asset_id) "
                "VALUES(?,?,?,?) ON CONFLICT(sha256) DO UPDATE SET "
                "reason=excluded.reason, rejected_at=excluded.rejected_at, asset_id=excluded.asset_id",
                (card.sha256, str(reason or "admin_rejected")[:80], int(now), card.asset_id),
            )
            db.execute("COMMIT")
        path = self.data_dir / card.relative_path
        if path.is_file() and not path.is_symlink():
            path.unlink()
        return self._require(asset_id)

    def delete(self, asset_id: str) -> None:
        card = self._require(asset_id)
        with connect_database(self.database_path) as db:
            db.execute("DELETE FROM sticker_assets WHERE asset_id=?", (card.asset_id,))
        path = self.data_dir / card.relative_path
        if path.is_file() and not path.is_symlink():
            path.unlink()

    def mark_used(
        self,
        asset_id: str,
        *,
        used_at: int,
        group_id: str,
        plan_id: str,
    ) -> None:
        del plan_id
        card = self._require(asset_id)
        with connect_database(self.database_path) as db:
            db.execute(
                "UPDATE sticker_assets SET use_count=use_count+1, last_used_at=?, "
                "last_used_group_id=?, updated_at=? WHERE asset_id=?",
                (
                    int(used_at),
                    str(group_id or "").strip() or None,
                    int(used_at),
                    card.asset_id,
                ),
            )

    def public_card(self, card: StickerCard) -> dict[str, object]:
        from .cognition import display_meaning

        return {
            "asset_id": card.asset_id,
            "preview_ref": card.asset_id,
            "mime_type": card.mime_type,
            "origin_kind": card.origin_kind,
            "license_status": card.license_status,
            "source_group_id": card.source_group_id,
            "status": card.status,
            "meaning": display_meaning(card.meaning),
            "meaning_raw": card.meaning,
            "meaning_formed": meaning_is_sendable(card.meaning),
            "use_when": list(card.use_when),
            "do_not_use": list(card.do_not_use),
            "attitudes": list(card.attitudes),
            "intensity": card.intensity,
            "min_familiarity": card.min_familiarity,
            "affection_floor_label": affection_floor_label(card.min_familiarity),
            "max_boundary_pressure": card.max_boundary_pressure,
            "caption_source": card.caption_source,
            "is_sticker_judgment": card.is_sticker_judgment,
            "judgment_reason": card.judgment_reason,
            "sighting_count": card.sighting_count,
            "use_count": card.use_count,
            "last_used_at": card.last_used_at,
            "cooldown_seconds": card.cooldown_seconds,
            "created_at": card.created_at,
            "updated_at": card.updated_at,
        }

    def _require(self, asset_id: str) -> StickerCard:
        card = self.get(asset_id)
        if card is None:
            raise InvalidStickerAsset("sticker is missing")
        return card

    def _registered_file_path(self, relative: Path) -> Path:
        if relative.is_absolute() or relative.parts[:3] != ("persona_media", "stickers", "files"):
            raise UnsafeStickerPath("sticker must be stored in persona_media/stickers/files")
        if len(relative.parts) != 4:
            raise UnsafeStickerPath("sticker filename must be a single path segment")
        target = (self.data_dir / relative).resolve()
        if not target.is_relative_to(self.data_dir) or target.parent != self.files_dir:
            raise UnsafeStickerPath("sticker path is outside the sticker directory")
        return target

    @staticmethod
    def _capacity_locked(db: sqlite3.Connection) -> dict[str, int]:
        candidate_count = int(
            db.execute(
                "SELECT COUNT(*) FROM sticker_assets WHERE status='candidate'"
            ).fetchone()[0]
        )
        library_count = int(
            db.execute(
                "SELECT COUNT(*) FROM sticker_assets WHERE status IN ('ready','disabled')"
            ).fetchone()[0]
        )
        return {
            "candidate_count": candidate_count,
            "library_count": library_count,
        }

    @staticmethod
    def _from_row(row: sqlite3.Row) -> StickerCard:
        return StickerCard(
            asset_id=str(row["asset_id"]),
            sha256=str(row["sha256"]),
            mime_type=str(row["mime_type"]),
            size_bytes=int(row["size_bytes"]),
            relative_path=str(row["relative_path"]),
            origin_kind=str(row["origin_kind"]),
            license_status=str(row["license_status"]),
            source_group_id=row["source_group_id"],
            source_event_id=row["source_event_id"],
            status=str(row["status"]),
            meaning=str(row["meaning"] or ""),
            use_when=_json_tuple(row["use_when_json"]),
            do_not_use=_json_tuple(row["do_not_use_json"]),
            attitudes=_json_tuple(row["attitudes_json"]),
            intensity=int(row["intensity"]),
            min_familiarity=int(row["min_familiarity"]),
            max_boundary_pressure=int(row["max_boundary_pressure"]),
            caption_source=str(row["caption_source"] or ""),
            is_sticker_judgment=str(row["is_sticker_judgment"] or ""),
            judgment_reason=str(row["judgment_reason"] or ""),
            sighting_count=int(row["sighting_count"]),
            use_count=int(row["use_count"]),
            last_used_at=row["last_used_at"],
            last_used_group_id=row["last_used_group_id"],
            cooldown_seconds=int(row["cooldown_seconds"]),
            created_at=int(row["created_at"]),
            updated_at=int(row["updated_at"]),
        )


def _json_tuple(raw: object) -> tuple[str, ...]:
    try:
        payload = json.loads(str(raw or "[]"))
    except json.JSONDecodeError:
        return ()
    if not isinstance(payload, list):
        return ()
    return tuple(str(item) for item in payload if str(item).strip())


__all__ = ("InvalidStickerAsset", "StickerLexicon", "UnsafeStickerPath")
