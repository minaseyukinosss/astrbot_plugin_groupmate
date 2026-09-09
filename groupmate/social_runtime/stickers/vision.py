"""Background sticker vision: judge, then caption. Never on the chat hot path."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Callable, Mapping, Protocol

from .cognition import (
    meaning_is_sendable,
    normalize_attitudes,
    normalize_meaning,
    normalize_phrases,
)
from .contracts import parse_affection_floor
from .images import vision_data_uri
from .lexicon import InvalidStickerAsset, StickerLexicon


VISION_QUEUE_LIMIT = 30
_JSON_FENCE = "```"


class StickerVisionPort(Protocol):
    async def judge(self, *, image_data_uri: str) -> Mapping[str, object]: ...

    async def caption(self, *, image_data_uri: str) -> Mapping[str, object]: ...


@dataclass(frozen=True)
class StickerJudgment:
    is_sticker: bool
    reason: str


@dataclass(frozen=True)
class StickerCaption:
    meaning: str
    use_when: tuple[str, ...]
    do_not_use: tuple[str, ...]
    attitudes: tuple[str, ...]
    intensity: int
    min_familiarity: int


def parse_json_object(raw: object) -> dict[str, object]:
    text = str(raw or "").strip()
    if text.startswith(_JSON_FENCE):
        text = text.strip("`")
        newline = text.find("\n")
        if newline >= 0:
            text = text[newline + 1 :]
        text = text.strip()
        if text.endswith(_JSON_FENCE):
            text = text[: -len(_JSON_FENCE)].strip()
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("vision response is not a JSON object")
    payload = json.loads(text[start : end + 1])
    if not isinstance(payload, dict):
        raise ValueError("vision response is not a JSON object")
    return payload


def parse_judgment(payload: Mapping[str, object]) -> StickerJudgment:
    reason = normalize_meaning(payload.get("reason") or payload.get("judgment_reason"))
    flag = payload.get("is_sticker")
    if isinstance(flag, str):
        flag = flag.strip().casefold() in {"true", "1", "yes", "sticker"}
    return StickerJudgment(is_sticker=bool(flag), reason=reason[:80])


def parse_caption(payload: Mapping[str, object]) -> StickerCaption:
    intensity = payload.get("intensity", 50)
    try:
        value = int(intensity)
    except (TypeError, ValueError):
        value = 50
    floor = payload.get("affection_floor", payload.get("min_familiarity", 0))
    return StickerCaption(
        meaning=normalize_meaning(payload.get("meaning")),
        use_when=normalize_phrases(payload.get("use_when") or ()),
        do_not_use=normalize_phrases(payload.get("do_not_use") or ()),
        attitudes=normalize_attitudes(payload.get("attitudes") or ()),
        intensity=max(0, min(100, value)),
        min_familiarity=parse_affection_floor(floor),
    )


class StickerVisionWorker:
    def __init__(
        self,
        lexicon: StickerLexicon,
        client: StickerVisionPort | None,
        *,
        clock: Callable[[], float],
    ) -> None:
        self.lexicon = lexicon
        self._client = client
        self._clock = clock
        self._queue: asyncio.Queue[tuple[str, bool]] = asyncio.Queue()
        self._queued: set[str] = set()
        self._inflight: set[str] = set()
        self._accepting = False
        self._worker: asyncio.Task[None] | None = None

    @property
    def available(self) -> bool:
        return self._client is not None

    def enqueue(self, asset_id: str, *, force: bool = False) -> bool:
        if self._client is None or not self._accepting:
            return False
        normalized = str(asset_id or "").strip()
        if not normalized:
            return False
        if normalized in self._queued or normalized in self._inflight:
            return False
        if len(self._queued) + len(self._inflight) >= VISION_QUEUE_LIMIT:
            return False
        card = self.lexicon.get(normalized)
        if card is None or card.status == "rejected":
            return False
        if not force and not self._needs_work(card):
            return False
        self._queued.add(normalized)
        self._queue.put_nowait((normalized, bool(force)))
        return True

    async def start(self) -> None:
        if self._accepting or self._client is None:
            return
        self._accepting = True
        for card in self.lexicon.inbox():
            self.enqueue(card.asset_id)
        self._worker = asyncio.create_task(
            self._run(), name="groupmate-sticker-vision"
        )

    async def close(self) -> None:
        self._accepting = False
        worker = self._worker
        self._worker = None
        if worker is None:
            return
        worker.cancel()
        try:
            await worker
        except asyncio.CancelledError:
            pass

    async def _run(self) -> None:
        while self._accepting:
            try:
                asset_id, force = await asyncio.wait_for(self._queue.get(), timeout=60.0)
            except asyncio.TimeoutError:
                continue
            self._queued.discard(asset_id)
            self._inflight.add(asset_id)
            try:
                await self._run_card(asset_id, force=force)
            except asyncio.CancelledError:
                raise
            except Exception:
                try:
                    self.lexicon.note_vision_failure(
                        asset_id, reason="vision_unavailable", now=int(self._clock())
                    )
                except (InvalidStickerAsset, LookupError):
                    pass
            finally:
                self._inflight.discard(asset_id)

    async def _run_card(self, asset_id: str, *, force: bool) -> None:
        client = self._client
        if client is None:
            return
        card = self.lexicon.get(asset_id)
        if card is None or card.status == "rejected":
            return
        if not force and not self._needs_work(card):
            return
        path = self.lexicon.validate_file(card)
        uri = vision_data_uri(path.read_bytes(), card.mime_type)
        now = int(self._clock())
        if force or card.is_sticker_judgment != "sticker":
            judgment = parse_judgment(await client.judge(image_data_uri=uri))
            if not judgment.is_sticker:
                self.lexicon.reject(
                    asset_id, now=now, reason=judgment.reason or "not_sticker"
                )
                return
            card = self.lexicon.record_judgment(
                asset_id, reason=judgment.reason, now=now
            )
        if (
            not force
            and card.caption_source in {"admin", "mixed"}
            and meaning_is_sendable(card.meaning)
        ):
            return
        caption = parse_caption(await client.caption(image_data_uri=uri))
        source = "mixed" if card.caption_source in {"admin", "mixed"} else "vision"
        self.lexicon.write_cognition(
            asset_id,
            meaning=caption.meaning,
            use_when=caption.use_when,
            do_not_use=caption.do_not_use,
            attitudes=caption.attitudes,
            intensity=caption.intensity,
            min_familiarity=caption.min_familiarity,
            caption_source=source,
            now=int(self._clock()),
        )

    @staticmethod
    def _needs_work(card) -> bool:
        if card.status not in {"candidate", "disabled"}:
            return False
        if card.caption_source in {"admin", "mixed"} and meaning_is_sendable(card.meaning):
            return False
        if card.is_sticker_judgment == "sticker" and meaning_is_sendable(card.meaning):
            return False
        return True


__all__ = (
    "StickerCaption",
    "StickerJudgment",
    "StickerVisionPort",
    "StickerVisionWorker",
    "VISION_QUEUE_LIMIT",
    "parse_caption",
    "parse_judgment",
    "parse_json_object",
)
