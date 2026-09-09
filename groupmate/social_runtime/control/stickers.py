"""Administrator-facing sticker lexicon queries and mutations."""

from __future__ import annotations

import base64
from pathlib import Path
from typing import Callable, Mapping

from ..persistence.schema import initialize_database
from ..stickers.contracts import MAX_STICKER_BYTES
from ..stickers.images import MIME_RULES, extension_for
from ..stickers.lexicon import InvalidStickerAsset, StickerLexicon


class StickerControlService:
    def __init__(
        self,
        database_path: Path,
        data_dir: Path,
        *,
        persona_id: str,
        vision_enqueue: Callable[..., bool] | None = None,
        vision_enabled: Callable[[], bool] | None = None,
    ) -> None:
        self.path = Path(database_path)
        self.data_dir = Path(data_dir)
        initialize_database(self.path)
        self.persona_id = str(persona_id).strip()
        self.lexicon = StickerLexicon(self.data_dir, self.path)
        self._vision_enqueue = vision_enqueue
        self._vision_enabled = vision_enabled

    def overview(self) -> dict[str, object]:
        capacity = self.lexicon.capacity()
        inbox = [self.lexicon.public_card(card) for card in self.lexicon.inbox()]
        library = [self.lexicon.public_card(card) for card in self.lexicon.library()]
        vision_enabled = bool(self._vision_enabled()) if callable(self._vision_enabled) else False
        return {
            "projection": "stickers",
            "capacity": capacity,
            "inbox": inbox,
            "library": library,
            "vision_enabled": vision_enabled,
            "rejected_count": sum(
                1 for card in self.lexicon.list_cards(statuses=("rejected",))
            ),
        }

    def detail(self, asset_id: str) -> dict[str, object]:
        card = self.lexicon.get(str(asset_id or "").strip())
        if card is None:
            raise LookupError("sticker not found")
        return {"item": self.lexicon.public_card(card)}

    def preview(self, asset_id: str) -> dict[str, str]:
        normalized = str(asset_id or "").strip()
        if normalized.startswith("sticker:") is False:
            normalized = f"sticker:{normalized}"
        return self.lexicon.preview_payload(normalized)

    def apply(self, body: Mapping[str, object], *, now: int) -> dict[str, object]:
        action = str(body.get("type") or body.get("action") or "").strip()
        if action == "sticker_upload":
            return self._upload(body, now=now)
        asset_id = str(body.get("asset_id") or "").strip()
        if action == "sticker_caption":
            card = self.lexicon.write_cognition(
                asset_id,
                meaning=body.get("meaning"),
                use_when=body.get("use_when") or (),
                do_not_use=body.get("do_not_use") or (),
                attitudes=body.get("attitudes") or (),
                intensity=int(body.get("intensity") or 50),
                min_familiarity=int(body.get("min_familiarity") or 0),
                max_boundary_pressure=int(body.get("max_boundary_pressure") or 100),
                caption_source="admin",
                now=int(now),
            )
            return {"item": self.lexicon.public_card(card)}
        if action == "sticker_confirm":
            return {"item": self.lexicon.public_card(self.lexicon.confirm(asset_id, now=int(now)))}
        if action == "sticker_disable":
            return {"item": self.lexicon.public_card(self.lexicon.disable(asset_id, now=int(now)))}
        if action == "sticker_reject":
            return {
                "item": self.lexicon.public_card(
                    self.lexicon.reject(asset_id, now=int(now))
                )
            }
        if action == "sticker_delete":
            self.lexicon.delete(asset_id)
            return {"deleted": True, "asset_id": asset_id}
        if action == "sticker_recaption":
            card = self.lexicon.get(asset_id)
            if card is None:
                raise LookupError("sticker not found")
            if self._vision_enqueue is None or (
                callable(self._vision_enabled) and not self._vision_enabled()
            ):
                raise ValueError("vision_provider_missing")
            queued = bool(self._vision_enqueue(asset_id, force=True))
            refreshed = self.lexicon.get(asset_id)
            return {
                "queued": queued,
                "asset_id": asset_id,
                "item": self.lexicon.public_card(refreshed or card),
            }
        raise ValueError("unsupported_sticker_action")

    def _upload(self, body: Mapping[str, object], *, now: int) -> dict[str, object]:
        mime_type = str(body.get("mime_type") or "").strip().casefold()
        filename = str(body.get("filename") or "").strip()
        if mime_type not in MIME_RULES:
            raise InvalidStickerAsset("unsupported sticker MIME")
        suffix = extension_for(mime_type)
        if filename and not filename.lower().endswith(suffix):
            raise InvalidStickerAsset("sticker filename extension does not match")
        raw = str(body.get("content_base64") or "").strip()
        try:
            content = base64.b64decode(raw, validate=True)
        except (ValueError, TypeError) as exc:
            raise InvalidStickerAsset("sticker bytes are invalid") from exc
        if not content or len(content) > MAX_STICKER_BYTES:
            raise InvalidStickerAsset("sticker size is invalid")
        outcome = self.lexicon.ingest(
            content,
            mime_type=mime_type,
            origin_kind="admin_import",
            license_status=str(body.get("license_status") or "owned"),
            now=int(now),
            skip_coarse_filter=True,
        )
        if outcome.card is None:
            raise InvalidStickerAsset(outcome.reason)
        card = outcome.card
        meaning = str(body.get("meaning") or "").strip()
        if meaning:
            card = self.lexicon.write_cognition(
                card.asset_id,
                meaning=meaning,
                use_when=body.get("use_when") or (),
                do_not_use=body.get("do_not_use") or (),
                attitudes=body.get("attitudes") or (),
                intensity=int(body.get("intensity") or 50),
                caption_source="admin",
                now=int(now),
            )
        elif outcome.created and self._vision_enqueue is not None:
            self._vision_enqueue(card.asset_id)
        return {
            "item": self.lexicon.public_card(card),
            "created": outcome.created,
            "reason": outcome.reason,
        }


__all__ = ("StickerControlService",)
