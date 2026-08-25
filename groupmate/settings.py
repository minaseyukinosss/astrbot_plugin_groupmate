"""Deployment settings owned by Social Runtime v2."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping
from urllib.parse import urlsplit


SOCIAL_RUNTIME_DATABASE_NAME = "groupmate-social-runtime-v2.db"
DEFAULT_GROUPMATE_PERSONA_ID = "groupmate:default"
DEFAULT_COGNITION_API_BASE = "https://api.deepseek.com"
DEFAULT_COGNITION_MODEL = "deepseek-v4-flash"


@dataclass(frozen=True)
class SocialRuntimeSettings:
    enabled_groups: tuple[str, ...]
    social_runtime_test_groups: tuple[str, ...]
    runtime_mode: str
    generation_provider: str
    vision_provider: str
    persona_id: str
    persona_name: str = "Groupmate"
    persona_aliases: tuple[str, ...] = ()
    cognition_api_key: str = field(default="", repr=False)
    cognition_api_base: str = DEFAULT_COGNITION_API_BASE
    cognition_model: str = DEFAULT_COGNITION_MODEL
    worker_concurrency_limit: int = 12
    cognition_timeout_seconds: int = 8
    control_admin_ids: tuple[str, ...] = ()
    external_command_prefixes: tuple[str, ...] = ()
    external_link_domains: tuple[str, ...] = ()

    @classmethod
    def from_mapping(cls, raw: Mapping[str, object] | None) -> "SocialRuntimeSettings":
        source = dict(raw or {})
        enabled_groups = tuple(
            str(value).strip()
            for value in source.get("enabled_groups", ())
            if str(value).strip()
        )
        generation_provider = str(
            source.get("generation_provider", "") or ""
        ).strip()
        persona_id = DEFAULT_GROUPMATE_PERSONA_ID
        configured = bool(enabled_groups and generation_provider)
        runtime_mode = str(
            source.get("runtime_mode", "SHADOW" if configured else "OFF") or "OFF"
        ).upper()
        explicit_social_runtime_groups = tuple(
            str(value).strip()
            for value in source.get("social_runtime_test_groups", ())
            if str(value).strip()
        )
        social_runtime_groups = explicit_social_runtime_groups
        if runtime_mode == "SOCIAL_RUNTIME" and not social_runtime_groups:
            social_runtime_groups = enabled_groups
        cognition_api_base = cls._cognition_api_base(
            source.get("cognition_api_base", DEFAULT_COGNITION_API_BASE)
        )
        cognition_model = str(
            source.get("cognition_model", DEFAULT_COGNITION_MODEL) or ""
        ).strip()
        if not cognition_model:
            raise ValueError("cognition_model must not be empty")
        persona_name = str(source.get("persona_name", "Groupmate") or "").strip()
        if not persona_name:
            raise ValueError("persona_name must not be empty")
        if len(persona_name) > 24:
            raise ValueError("persona_name must contain at most 24 characters")
        return cls(
            enabled_groups=enabled_groups,
            social_runtime_test_groups=social_runtime_groups,
            runtime_mode=runtime_mode,
            generation_provider=generation_provider,
            vision_provider=str(source.get("vision_provider", "") or "").strip(),
            persona_id=persona_id,
            persona_name=persona_name,
            persona_aliases=cls._persona_aliases(
                persona_name,
                source.get("persona_aliases", ()),
            ),
            cognition_api_key=str(
                source.get("cognition_api_key", "") or ""
            ).strip(),
            cognition_api_base=cognition_api_base,
            cognition_model=cognition_model,
            worker_concurrency_limit=cls._positive_int(
                source.get("worker_concurrency_limit", 12),
                "worker_concurrency_limit",
            ),
            cognition_timeout_seconds=cls._cognition_timeout(
                source.get("cognition_timeout_seconds", 8),
            ),
            control_admin_ids=tuple(
                str(value).strip()
                for value in source.get("control_admin_ids", ())
                if str(value).strip()
            ),
            external_command_prefixes=tuple(
                str(value).strip()
                for value in source.get("external_command_prefixes", ())
                if str(value).strip()
            ),
            external_link_domains=tuple(
                str(value).strip()
                for value in source.get("external_link_domains", ())
                if str(value).strip()
            ),
        )

    @staticmethod
    def _persona_aliases(name: str, values: object) -> tuple[str, ...]:
        source = values if isinstance(values, (list, tuple)) else ()
        normalized = tuple(
            dict.fromkeys(
                str(value or "").strip()
                for value in source
                if str(value or "").strip()
            )
        )
        aliases = tuple(value for value in normalized if value != name)
        if len(aliases) > 12 or any(
            len(value) < 2 or len(value) > 24 for value in aliases
        ):
            raise ValueError(
                "persona aliases must contain 2-24 characters and at most 12 entries"
            )
        return aliases

    @staticmethod
    def _positive_int(value: object, field: str) -> int:
        if isinstance(value, bool):
            raise ValueError(f"{field} must be a positive integer")
        try:
            normalized = int(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{field} must be a positive integer") from exc
        if normalized < 1:
            raise ValueError(f"{field} must be a positive integer")
        return normalized

    @staticmethod
    def _cognition_timeout(value: object) -> int:
        normalized = SocialRuntimeSettings._positive_int(
            value, "cognition_timeout_seconds"
        )
        if normalized == 20:
            return 8
        if not 3 <= normalized <= 15:
            raise ValueError(
                "cognition_timeout_seconds must be between 3 and 15"
            )
        return normalized

    @staticmethod
    def _cognition_api_base(value: object) -> str:
        normalized = str(value or "").strip().rstrip("/")
        parsed = urlsplit(normalized)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.netloc
            or parsed.query
            or parsed.fragment
            or parsed.username is not None
            or parsed.password is not None
        ):
            raise ValueError("cognition_api_base must be a safe HTTP(S) URL")
        return normalized
