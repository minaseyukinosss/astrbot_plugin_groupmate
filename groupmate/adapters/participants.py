"""Private QQ participant identity mapping and safe avatar presentation data."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import inspect
import json
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Awaitable, Callable, Mapping

from ..social_runtime.contracts import SocialEventEnvelope
from ..social_runtime.persistence.schema import connect_database, initialize_database


AvatarFetcher = Callable[[str], Awaitable[tuple[bytes, str]] | tuple[bytes, str]]
_ALLOWED_IMAGE_TYPES = {"image/png", "image/jpeg", "image/webp", "image/gif"}
_MAX_AVATAR_BYTES = 2 * 1024 * 1024


class ParticipantDirectory:
    """Keeps raw platform identity private and returns opaque page references."""

    def __init__(
        self,
        path: Path,
        cache_dir: Path,
        *,
        fetcher: AvatarFetcher | None = None,
    ) -> None:
        self.path = Path(path)
        self.cache_dir = Path(cache_dir)
        self.fetcher = fetcher or self._fetch_url
        initialize_database(self.path)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._ensure_table()

    def remember(self, event: SocialEventEnvelope) -> dict[str, str]:
        if not event.group_id:
            raise ValueError("participant requires a group scope")
        actor_id = str(event.actor_id or "").strip()
        sender = event.payload.get("sender")
        sender_map = sender if isinstance(sender, Mapping) else {}
        display_name = " ".join(str(sender_map.get("name") or "群成员").split())[:48]
        return self.remember_actor(
            persona_id=event.persona_id,
            group_id=event.group_id,
            actor_id=actor_id,
            display_name=display_name,
            updated_at=int(event.received_at),
        )

    def remember_actor(
        self,
        *,
        persona_id: str,
        group_id: str,
        actor_id: str,
        display_name: str,
        updated_at: int,
    ) -> dict[str, str]:
        normalized_actor_id = str(actor_id or "").strip()
        normalized_name = " ".join(
            str(display_name or "群成员").split()
        )[:48]
        identity = self._digest(group_id, normalized_actor_id or normalized_name)
        avatar_ref = f"participant:{identity}"
        member_ref = f"member:{identity}"
        with connect_database(self.path) as db:
            db.execute(
                "INSERT INTO participant_directory(avatar_ref, member_ref, persona_id, group_id, "
                "actor_id, display_name, updated_at) VALUES(?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(avatar_ref) DO UPDATE SET display_name=excluded.display_name, "
                "updated_at=excluded.updated_at",
                (
                    avatar_ref,
                    member_ref,
                    str(persona_id),
                    str(group_id),
                    normalized_actor_id,
                    normalized_name,
                    int(updated_at),
                ),
            )
        return {
            "member_ref": member_ref,
            "display_name": normalized_name,
            "avatar_ref": avatar_ref,
        }

    def resolve_actor(
        self,
        *,
        persona_id: str,
        group_id: str,
        actor_id: str,
    ) -> dict[str, str] | None:
        """Resolve a private platform id to scoped, page-safe participant data."""
        normalized = str(actor_id or "").strip()
        if not normalized:
            return None
        with connect_database(self.path) as db:
            row = db.execute(
                "SELECT member_ref, display_name, avatar_ref FROM participant_directory "
                "WHERE persona_id=? AND group_id=? AND actor_id=?",
                (str(persona_id), str(group_id), normalized),
            ).fetchone()
        if row is None:
            return None
        return {
            "member_ref": str(row["member_ref"]),
            "display_name": str(row["display_name"]),
            "avatar_ref": str(row["avatar_ref"]),
        }

    def active_members(
        self,
        *,
        persona_id: str,
        group_id: str,
        since: int,
        exclude_actor_ids: tuple[str, ...] = (),
    ) -> tuple[dict[str, object], ...]:
        excluded = {
            str(item or "").strip()
            for item in exclude_actor_ids
            if str(item or "").strip()
        }
        with connect_database(self.path) as db:
            rows = db.execute(
                "SELECT actor_id, display_name, updated_at "
                "FROM participant_directory WHERE persona_id=? AND group_id=? "
                "AND updated_at>=? AND actor_id<>'' "
                "ORDER BY updated_at DESC, display_name, actor_id",
                (str(persona_id), str(group_id), max(0, int(since))),
            ).fetchall()
        return tuple(
            {
                "actor_id": str(row["actor_id"]),
                "display_name": str(row["display_name"]),
                "updated_at": int(row["updated_at"]),
            }
            for row in rows
            if str(row["actor_id"]) not in excluded
        )

    async def avatar_data(self, avatar_ref: str) -> dict[str, str]:
        normalized = str(avatar_ref or "").strip()
        cached = self._read_cache(normalized)
        if cached is not None:
            return cached
        with connect_database(self.path) as db:
            row = db.execute(
                "SELECT actor_id, display_name FROM participant_directory WHERE avatar_ref=?",
                (normalized,),
            ).fetchone()
        display_name = str(row["display_name"]) if row else "群"
        actor_id = str(row["actor_id"]) if row else ""
        if actor_id:
            try:
                url = "https://q1.qlogo.cn/g?b=qq&nk={}&s=100".format(
                    urllib.parse.quote(actor_id, safe="")
                )
                fetched = self.fetcher(url)
                data, mime_type = (
                    await fetched if inspect.isawaitable(fetched) else fetched
                )
                mime = str(mime_type or "").split(";", 1)[0].lower()
                if mime not in _ALLOWED_IMAGE_TYPES:
                    raise ValueError("unsupported avatar media type")
                if not data or len(data) > _MAX_AVATAR_BYTES:
                    raise ValueError("invalid avatar size")
                result = {
                    "data_uri": "data:{};base64,{}".format(
                        mime, base64.b64encode(data).decode("ascii")
                    ),
                    "source": "qq",
                }
                self._write_cache(normalized, result)
                return result
            except Exception:
                pass
        result = {
            "data_uri": self._fallback_svg(normalized, display_name),
            "source": "fallback",
        }
        self._write_cache(normalized, result)
        return result

    def contains(self, avatar_ref: str, *, persona_id: str, group_id: str) -> bool:
        with connect_database(self.path) as db:
            row = db.execute(
                "SELECT 1 FROM participant_directory WHERE avatar_ref=? "
                "AND persona_id=? AND group_id=?",
                (str(avatar_ref), str(persona_id), str(group_id)),
            ).fetchone()
        return row is not None

    async def _fetch_url(self, url: str) -> tuple[bytes, str]:
        def read() -> tuple[bytes, str]:
            request = urllib.request.Request(
                url,
                headers={"User-Agent": "Groupmate/1.0"},
            )
            with urllib.request.urlopen(request, timeout=4) as response:
                data = response.read(_MAX_AVATAR_BYTES + 1)
                return data, str(response.headers.get_content_type())

        return await asyncio.to_thread(read)

    def _cache_path(self, avatar_ref: str) -> Path:
        key = hashlib.sha256(avatar_ref.encode("utf-8")).hexdigest()
        return self.cache_dir / f"{key}.json"

    def _read_cache(self, avatar_ref: str) -> dict[str, str] | None:
        if not avatar_ref:
            return None
        path = self._cache_path(avatar_ref)
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            return None
        if (
            isinstance(value, dict)
            and str(value.get("data_uri") or "").startswith("data:image/")
            and value.get("source") in {"qq", "fallback"}
        ):
            return {"data_uri": str(value["data_uri"]), "source": str(value["source"])}
        return None

    def _write_cache(self, avatar_ref: str, value: Mapping[str, str]) -> None:
        if not avatar_ref:
            return
        path = self._cache_path(avatar_ref)
        temporary = path.with_suffix(".tmp")
        try:
            temporary.write_text(
                json.dumps(dict(value), ensure_ascii=False, sort_keys=True),
                encoding="utf-8",
            )
            temporary.replace(path)
        except OSError:
            pass

    @staticmethod
    def _fallback_svg(avatar_ref: str, display_name: str) -> str:
        digest = hashlib.sha256(avatar_ref.encode("utf-8")).hexdigest()
        hue = int(digest[:4], 16) % 360
        initial = (display_name.strip() or "群")[0]
        svg = (
            '<svg xmlns="http://www.w3.org/2000/svg" width="96" height="96" viewBox="0 0 96 96">'
            f'<rect width="96" height="96" rx="48" fill="hsl({hue} 55% 35%)"/>'
            f'<text x="48" y="59" text-anchor="middle" font-size="38" fill="white" '
            f'font-family="system-ui,sans-serif">{ParticipantDirectory._xml_escape(initial)}</text>'
            "</svg>"
        )
        return "data:image/svg+xml;base64," + base64.b64encode(svg.encode("utf-8")).decode("ascii")

    def _ensure_table(self) -> None:
        with connect_database(self.path) as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS participant_directory ("
                "avatar_ref TEXT PRIMARY KEY, member_ref TEXT NOT NULL, persona_id TEXT NOT NULL, "
                "group_id TEXT NOT NULL, actor_id TEXT NOT NULL, display_name TEXT NOT NULL, "
                "updated_at INTEGER NOT NULL)"
            )
            db.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_participant_private_identity "
                "ON participant_directory(persona_id, group_id, actor_id)"
            )

    @staticmethod
    def _digest(group_id: str, actor_id: str) -> str:
        return hashlib.sha256(f"{group_id}\0{actor_id}".encode("utf-8")).hexdigest()[:20]

    @staticmethod
    def _xml_escape(value: str) -> str:
        return (
            value.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;")
            .replace("'", "&apos;")
        )


__all__ = ("ParticipantDirectory",)
