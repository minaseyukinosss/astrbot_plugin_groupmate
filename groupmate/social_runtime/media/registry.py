"""Persona media persistence and deterministic selection boundaries."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from pathlib import Path
from typing import Iterable

from .contracts import MediaAsset, MediaSelection, MediaSelectionContext, MediaUse


class UnsafeMediaPath(ValueError):
    """Raised when a media path could escape the plugin data directory."""


class InvalidMediaAsset(ValueError):
    """Raised when uploaded bytes or metadata are not admissible."""


_MIME_RULES = {
    "image/png": ((".png",), lambda content: content.startswith(b"\x89PNG\r\n\x1a\n")),
    "image/jpeg": ((".jpg", ".jpeg"), lambda content: content.startswith(b"\xff\xd8\xff")),
    "image/gif": ((".gif",), lambda content: content.startswith((b"GIF87a", b"GIF89a"))),
    "image/webp": (
        (".webp",),
        lambda content: len(content) >= 12
        and content.startswith(b"RIFF")
        and content[8:12] == b"WEBP",
    ),
}
_LICENSED_STATES = frozenset({"owned", "licensed", "public_domain", "cc0", "cc_by"})
_TUPLE_FIELDS = (
    "semantic_tags",
    "emotion_tags",
    "act_tags",
    "allowed_modes",
    "culture_tags",
)


class _MediaFileValidator:
    def __init__(self, data_dir: Path, max_asset_bytes: int) -> None:
        self.data_dir = Path(data_dir).resolve()
        self.files_dir = (self.data_dir / "persona_media" / "files").resolve()
        self.max_asset_bytes = max_asset_bytes

    def validate(self, asset: MediaAsset, *, require_registry_identity: bool) -> Path:
        self._validate_metadata(
            asset, require_registry_identity=require_registry_identity
        )
        target = self._registered_file_path(asset)
        unresolved = self.data_dir / asset.relative_path
        if unresolved.is_symlink() or not target.is_file():
            raise InvalidMediaAsset("registered media file is missing or unsafe")
        try:
            content = target.read_bytes()
        except OSError as exc:
            raise InvalidMediaAsset("registered media file cannot be read") from exc
        if len(content) != asset.size_bytes or not 0 < len(content) <= self.max_asset_bytes:
            raise InvalidMediaAsset("registered media size does not match")
        rule = _MIME_RULES.get(asset.mime_type)
        if rule is None or target.suffix.lower() not in rule[0] or not rule[1](content):
            raise InvalidMediaAsset("registered media MIME or extension does not match")
        if hashlib.sha256(content).hexdigest() != asset.sha256:
            raise InvalidMediaAsset("registered media checksum does not match")
        return target

    def _registered_file_path(self, asset: MediaAsset) -> Path:
        relative = Path(asset.relative_path)
        if (
            relative.is_absolute()
            or len(relative.parts) != 3
            or relative.parts[:2] != ("persona_media", "files")
        ):
            raise UnsafeMediaPath("asset must be stored directly in persona_media/files")
        target = (self.data_dir / relative).resolve()
        if not target.is_relative_to(self.data_dir) or target.parent != self.files_dir:
            raise UnsafeMediaPath("asset path is outside the registered media directory")
        return target

    @staticmethod
    def _validate_metadata(
        asset: MediaAsset, *, require_registry_identity: bool
    ) -> None:
        if not asset.asset_id.strip() or not asset.source.strip():
            raise InvalidMediaAsset("asset identity and source are required")
        if asset.license_status not in _LICENSED_STATES:
            raise InvalidMediaAsset("media license is not approved")
        if asset.mime_type not in _MIME_RULES:
            raise InvalidMediaAsset("unsupported media MIME")
        if len(asset.sha256) != 64 or any(
            character not in "0123456789abcdef" for character in asset.sha256.lower()
        ):
            raise InvalidMediaAsset("media checksum format is invalid")
        if require_registry_identity and asset.asset_id != "media:{0}".format(asset.sha256[:24]):
            raise InvalidMediaAsset("asset identity does not match checksum")
        if not asset.allowed_modes:
            raise InvalidMediaAsset("asset requires at least one allowed mode")
        if not -100 <= asset.min_familiarity <= 100:
            raise InvalidMediaAsset("asset familiarity restriction is invalid")
        if not -100 <= asset.max_boundary_pressure <= 100:
            raise InvalidMediaAsset("asset boundary restriction is invalid")
        if not 0 <= asset.intensity <= 100:
            raise InvalidMediaAsset("asset intensity is invalid")
        if asset.cooldown_seconds < 0:
            raise InvalidMediaAsset("asset cooldown is invalid")


class MediaRegistry:
    def __init__(self, data_dir: Path, *, max_asset_bytes: int) -> None:
        if max_asset_bytes <= 0:
            raise ValueError("max_asset_bytes must be positive")
        self.data_dir = Path(data_dir).resolve()
        self.max_asset_bytes = max_asset_bytes
        self._media_dir = self.data_dir / "persona_media"
        self._files_dir = self._media_dir / "files"
        self._manifest_path = self._media_dir / "index.json"
        self._file_validator = _MediaFileValidator(
            self.data_dir, self.max_asset_bytes
        )
        self._assets = self._load_manifest()

    def register(
        self,
        *,
        filename: str,
        content: bytes,
        mime_type: str,
        source: str,
        license_status: str,
        semantic_tags: tuple[str, ...],
        emotion_tags: tuple[str, ...],
        act_tags: tuple[str, ...],
        allowed_modes: tuple[str, ...],
        min_familiarity: int,
        max_boundary_pressure: int,
        intensity: int,
        cooldown_seconds: int,
        enabled: bool,
        expected_sha256: str,
        culture_tags: tuple[str, ...] = (),
    ) -> MediaAsset:
        suffix = self._validate_upload(
            filename, content, mime_type, license_status, expected_sha256
        )
        digest = hashlib.sha256(content).hexdigest()
        asset_id = "media:{0}".format(digest[:24])
        existing = self._assets.get(asset_id)
        if existing is not None:
            return existing

        normalized_source = self._required_text(source, "source")
        normalized_semantic = self._tags(semantic_tags)
        normalized_emotion = self._tags(emotion_tags)
        normalized_acts = self._tags(act_tags)
        normalized_modes = self._tags(allowed_modes)
        normalized_culture = self._tags(culture_tags)
        relative_path = (
            Path("persona_media")
            / "files"
            / (asset_id.replace(":", "-") + suffix)
        )
        asset = MediaAsset(
            asset_id=asset_id,
            source=normalized_source,
            license_status=license_status,
            mime_type=mime_type,
            size_bytes=len(content),
            sha256=digest,
            relative_path=str(relative_path),
            semantic_tags=normalized_semantic,
            emotion_tags=normalized_emotion,
            act_tags=normalized_acts,
            allowed_modes=normalized_modes,
            min_familiarity=int(min_familiarity),
            max_boundary_pressure=int(max_boundary_pressure),
            intensity=int(intensity),
            enabled=bool(enabled),
            cooldown_seconds=max(0, int(cooldown_seconds)),
            culture_tags=normalized_culture,
        )
        _MediaFileValidator._validate_metadata(
            asset, require_registry_identity=True
        )
        target = self._file_validator._registered_file_path(asset)
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_suffix(target.suffix + ".tmp")
        self._inside_data_dir(temporary.relative_to(self.data_dir))
        temporary.write_bytes(content)
        temporary.replace(target)
        self._file_validator.validate(asset, require_registry_identity=True)
        self._assets[asset_id] = asset
        self._persist_manifest()
        return asset

    def get(self, asset_id: str) -> MediaAsset | None:
        return self._assets.get(asset_id)

    def assets(self) -> tuple[MediaAsset, ...]:
        return tuple(self._assets[key] for key in sorted(self._assets))

    def _validate_upload(
        self,
        filename: str,
        content: bytes,
        mime_type: str,
        license_status: str,
        expected_sha256: str,
    ) -> str:
        candidate = Path(filename)
        if (
            not filename
            or candidate.is_absolute()
            or candidate.name != filename
            or any(part in {"..", "."} for part in candidate.parts)
        ):
            raise UnsafeMediaPath("media filename must be a single safe name")
        if not isinstance(content, bytes) or not 0 < len(content) <= self.max_asset_bytes:
            raise InvalidMediaAsset("media size is outside the configured limit")
        rule = _MIME_RULES.get(mime_type)
        if rule is None or not rule[1](content):
            raise InvalidMediaAsset("unsupported or mismatched MIME content")
        suffix = candidate.suffix.lower()
        if suffix not in rule[0]:
            raise InvalidMediaAsset("filename extension does not match MIME")
        digest = hashlib.sha256(content).hexdigest()
        if expected_sha256.lower() != digest:
            raise InvalidMediaAsset("media checksum does not match")
        if license_status not in _LICENSED_STATES:
            raise InvalidMediaAsset("media license is not approved")
        return suffix

    def _load_manifest(self) -> dict[str, MediaAsset]:
        if not self._manifest_path.exists():
            return {}
        try:
            payload = json.loads(self._manifest_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise InvalidMediaAsset("media manifest is invalid") from exc
        if not isinstance(payload, dict) or not isinstance(payload.get("assets", ()), list):
            raise InvalidMediaAsset("media manifest must contain an asset list")
        assets: dict[str, MediaAsset] = {}
        try:
            for raw in payload.get("assets", ()):
                values = dict(raw)
                for field in _TUPLE_FIELDS:
                    values[field] = tuple(values.get(field, ()))
                asset = MediaAsset(**values)
                self._file_validator.validate(asset, require_registry_identity=True)
                if asset.asset_id in assets:
                    raise InvalidMediaAsset("media manifest contains duplicate asset IDs")
                assets[asset.asset_id] = asset
        except (KeyError, TypeError, ValueError) as exc:
            if isinstance(exc, (InvalidMediaAsset, UnsafeMediaPath)):
                raise
            raise InvalidMediaAsset("media manifest contains an invalid asset") from exc
        return assets

    def _persist_manifest(self) -> None:
        self._media_dir.mkdir(parents=True, exist_ok=True)
        payload = {
            "assets": [asdict(self._assets[key]) for key in sorted(self._assets)]
        }
        encoded = json.dumps(
            payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        temporary = self._manifest_path.with_suffix(".json.tmp")
        self._inside_data_dir(temporary.relative_to(self.data_dir))
        temporary.write_text(encoded, encoding="utf-8")
        temporary.replace(self._manifest_path)

    def _inside_data_dir(self, relative_path: Path) -> Path:
        target = (self.data_dir / relative_path).resolve()
        if not target.is_relative_to(self.data_dir):
            raise UnsafeMediaPath("media path escapes plugin data directory")
        return target

    @staticmethod
    def _required_text(value: str, field: str) -> str:
        normalized = str(value).strip()
        if not normalized:
            raise InvalidMediaAsset("{0} is required".format(field))
        return normalized

    @staticmethod
    def _tags(values: Iterable[str]) -> tuple[str, ...]:
        return tuple(sorted({str(value).strip() for value in values if str(value).strip()}))


class MediaSelector:
    def __init__(self, data_dir: Path, *, max_asset_bytes: int = 8 * 1024 * 1024) -> None:
        if max_asset_bytes <= 0:
            raise ValueError("max_asset_bytes must be positive")
        self.data_dir = Path(data_dir).resolve()
        self._file_validator = _MediaFileValidator(
            self.data_dir, max_asset_bytes
        )

    def select(
        self,
        assets: tuple[MediaAsset, ...],
        context: MediaSelectionContext,
    ) -> MediaSelection:
        if context.text_sufficient:
            return MediaSelection(None, ("text_sufficient",))

        eligible: list[tuple[int, MediaAsset, tuple[str, ...]]] = []
        rejected: list[str] = []
        irrelevant = False
        for asset in sorted(assets, key=lambda item: item.asset_id):
            reason = self._ineligible_reason(asset, context)
            if reason is not None:
                self._append_once(rejected, reason)
                continue
            score, reasons = self._score(asset, context)
            if score <= 0:
                irrelevant = True
                continue
            eligible.append((score, asset, reasons))

        if not eligible:
            if irrelevant:
                return MediaSelection(None, ("no_relevant_asset",))
            return MediaSelection(None, tuple(rejected or ("no_eligible_asset",)))

        eligible.sort(key=lambda item: (-item[0], item[1].asset_id))
        best_score, best, reasons = eligible[0]
        tied = sum(1 for score, _, _ in eligible if score == best_score) > 1
        if tied:
            reasons += ("deterministic_tiebreak",)
        return MediaSelection(best.asset_id, reasons or ("eligible_asset",))

    def _ineligible_reason(
        self, asset: MediaAsset, context: MediaSelectionContext
    ) -> str | None:
        if asset.license_status not in _LICENSED_STATES:
            return "license_not_allowed"
        if not asset.enabled:
            return "asset_disabled"
        if context.mode not in asset.allowed_modes:
            return "mode_not_allowed"
        if (
            context.familiarity < asset.min_familiarity
            or context.boundary_pressure > asset.max_boundary_pressure
        ):
            return "relationship_restricted"
        if self._cooling_down(asset, context.recent_uses, context.now):
            return "asset_cooldown"
        try:
            self._file_validator.validate(asset, require_registry_identity=False)
        except UnsafeMediaPath:
            return "invalid_media_file"
        except InvalidMediaAsset as exc:
            if "checksum" in str(exc):
                return "checksum_mismatch"
            return "invalid_media_file"
        return None

    @staticmethod
    def _cooling_down(
        asset: MediaAsset, recent_uses: tuple[MediaUse, ...], now: int
    ) -> bool:
        return any(
            usage.asset_id == asset.asset_id
            and 0 <= now - usage.used_at < asset.cooldown_seconds
            for usage in recent_uses
        )

    @staticmethod
    def _score(
        asset: MediaAsset, context: MediaSelectionContext
    ) -> tuple[int, tuple[str, ...]]:
        score = 0
        reasons: tuple[str, ...] = ()
        if set(asset.semantic_tags) & set(context.semantic_tags):
            score += 4
            reasons += ("semantic_match",)
        if set(asset.emotion_tags) & set(context.emotion_tags):
            score += 3
            reasons += ("emotion_match",)
        if set(asset.act_tags) & set(context.act_tags):
            score += 2
            reasons += ("act_match",)
        if set(asset.culture_tags) & set(context.culture_tags):
            score += 2
            reasons += ("culture_match",)
        return score, reasons

    @staticmethod
    def _append_once(values: list[str], value: str) -> None:
        if value not in values:
            values.append(value)


__all__ = ("InvalidMediaAsset", "MediaRegistry", "MediaSelector", "UnsafeMediaPath")
