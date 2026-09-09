"""Post-text sticker accompaniment: eligibility, lexical retrieval, random pick."""

from __future__ import annotations

import random
import re

from ..social_moves import KnowledgePolicy, SocialMove, SocialMovePlan
from ..stances import Attitude, StanceDecision
from .cognition import meaning_is_sendable
from .contracts import AccompanimentDecision, STICKER_PACK_LIMIT, StickerCard
from .lexicon import StickerLexicon
from .request import StickerAsk


_BLOCKED_MOVES = frozenset(
    {
        SocialMove.SILENCE,
        SocialMove.JOIN_CHORUS,
        SocialMove.SAFETY_MINIMUM,
        SocialMove.FIRM_BOUNDARY,
        SocialMove.CORRECT_SELF,
    }
)
_ASK_BLOCKED_MOVES = frozenset(
    {
        SocialMove.SILENCE,
        SocialMove.JOIN_CHORUS,
        SocialMove.SAFETY_MINIMUM,
        SocialMove.CORRECT_SELF,
    }
)
_TOKEN = re.compile(r"[\w\u4e00-\u9fff]+")
_MOVE_ATTITUDES = {
    SocialMove.PLAYFUL_RESISTANCE: ("tease", "amused", "refuse"),
    SocialMove.TEASE_FROM_CONTEXT: ("tease", "amused"),
    SocialMove.REFUSE: ("refuse", "helpless", "wronged"),
    SocialMove.ACCEPT: ("agree", "warm", "acknowledge"),
    SocialMove.LIMITED_ACCEPT: ("acknowledge", "helpless", "agree"),
    SocialMove.CONCRETE_CARE: ("warm",),
    SocialMove.GROUP_RESPONSE: ("amused", "acknowledge", "startle"),
    SocialMove.PROACTIVE_JOIN: ("amused", "warm", "startle"),
    SocialMove.COUNTER: ("tease", "proud"),
    SocialMove.DIRECT_ANSWER: ("acknowledge", "confused"),
    SocialMove.REQUEST_NEEDED_EVIDENCE: ("confused", "acknowledge"),
}
_ATTITUDE_MAP = {
    Attitude.AMUSED: ("amused", "tease", "startle"),
    Attitude.WARM: ("warm", "close", "agree"),
    Attitude.NEUTRAL: ("acknowledge", "confused"),
    Attitude.IRRITATED: ("refuse", "wronged"),
    Attitude.GUARDED: ("refuse", "helpless"),
}


class StickerAccompanist:
    def __init__(
        self,
        lexicon: StickerLexicon,
        *,
        enabled: bool = True,
        rng: random.Random | None = None,
    ) -> None:
        self.lexicon = lexicon
        self.enabled = bool(enabled)
        self.rng = rng or random.Random()

    def decide(
        self,
        *,
        text: str,
        move: SocialMovePlan,
        stance: StanceDecision | None,
        media_policy: str,
        now: int,
        affection: float,
        boundary_pressure: int,
    ) -> AccompanimentDecision:
        if not self.enabled:
            return AccompanimentDecision(None, None, "disabled")
        if media_policy != "registered_only":
            return AccompanimentDecision(None, None, "media_forbidden")
        if move.primary_move in _BLOCKED_MOVES:
            return AccompanimentDecision(None, None, "ineligible_move")
        if move.knowledge_policy in {KnowledgePolicy.GROUNDED, KnowledgePolicy.STRICT}:
            return AccompanimentDecision(None, None, "ineligible_move")
        body = str(text or "").strip()
        if not body:
            return AccompanimentDecision(None, None, "empty_text")
        ready = self.lexicon.ready_sendable()
        if not ready:
            return AccompanimentDecision(None, None, "no_ready")
        wanted = self._wanted_attitudes(move, stance)
        scored: list[tuple[float, StickerCard]] = []
        for card in ready:
            reason = self._exclude(
                card,
                affection=affection,
                boundary_pressure=boundary_pressure,
                text=body,
            )
            if reason is not None:
                continue
            score = self._score(card, body, wanted)
            if score <= 0:
                continue
            if card.last_used_at is not None and int(now) - int(card.last_used_at) < card.cooldown_seconds:
                score *= 0.2
            elif card.use_count:
                score *= max(0.35, 1.0 - min(card.use_count, 8) * 0.08)
            scored.append((score, card))
        if not scored:
            return AccompanimentDecision(None, None, "no_match")
        weights = [max(0.01, item[0]) for item in scored]
        chosen = self.rng.choices([item[1] for item in scored], weights=weights, k=1)[0]
        path = self.lexicon.validate_file(chosen)
        return AccompanimentDecision(chosen.asset_id, str(path), "accompanied")

    def fulfill(
        self,
        *,
        ask: StickerAsk,
        move: SocialMovePlan,
        stance: StanceDecision | None,
        media_policy: str,
        now: int,
        affection: float,
        boundary_pressure: int,
    ) -> tuple[tuple[StickerCard, ...], str]:
        if move.primary_move in _ASK_BLOCKED_MOVES:
            return (), "ineligible_move"
        if move.knowledge_policy in {KnowledgePolicy.GROUNDED, KnowledgePolicy.STRICT}:
            return (), "ineligible_move"
        if _ask_media_forbidden(move, stance, media_policy):
            return (), "media_forbidden"
        query = str(ask.query or "").strip()
        if not query:
            return (), "empty_text"
        ready = self.lexicon.ready_sendable()
        if not ready:
            return (), "no_ready"
        wanted = self._wanted_attitudes(move, stance)
        scored: list[tuple[float, StickerCard]] = []
        for card in ready:
            reason = self._exclude(
                card,
                affection=affection,
                boundary_pressure=boundary_pressure,
                text=query,
            )
            if reason is not None:
                continue
            score = self._score(card, query, () if ask.specified else wanted)
            if score <= 0:
                if ask.specified:
                    continue
                score = 1.0
            if card.last_used_at is not None and int(now) - int(card.last_used_at) < card.cooldown_seconds:
                score *= 0.2
            elif card.use_count:
                score *= max(0.35, 1.0 - min(card.use_count, 8) * 0.08)
            scored.append((score, card))
        if not scored:
            return (), "no_match"
        limit = STICKER_PACK_LIMIT if ask.pack else 1
        chosen = self._pick_unique(scored, limit)
        return tuple(chosen), "fulfilled"

    def _pick_unique(
        self, scored: list[tuple[float, StickerCard]], limit: int
    ) -> list[StickerCard]:
        remaining = list(scored)
        picked: list[StickerCard] = []
        seen: set[str] = set()
        while remaining and len(picked) < max(1, int(limit)):
            weights = [max(0.01, item[0]) for item in remaining]
            card = self.rng.choices([item[1] for item in remaining], weights=weights, k=1)[0]
            remaining = [
                item
                for item in remaining
                if item[1].asset_id != card.asset_id and item[1].sha256 != card.sha256
            ]
            if card.asset_id in seen or card.sha256 in seen:
                continue
            seen.add(card.asset_id)
            seen.add(card.sha256)
            picked.append(card)
        return picked

    def mark_used(self, asset_id: str, *, used_at: int, group_id: str, plan_id: str) -> None:
        self.lexicon.mark_used(
            asset_id, used_at=used_at, group_id=group_id, plan_id=plan_id
        )

    @staticmethod
    def _exclude(
        card: StickerCard,
        *,
        affection: float,
        boundary_pressure: int,
        text: str,
    ) -> str | None:
        if card.status != "ready" or not meaning_is_sendable(card.meaning):
            return "not_sendable"
        floor = int(card.min_familiarity)
        if floor > 0 and float(affection) < floor:
            return "relationship"
        if boundary_pressure > card.max_boundary_pressure:
            return "relationship"
        folded = text.casefold()
        for phrase in card.do_not_use:
            token = str(phrase or "").strip()
            if token and token.casefold() in folded:
                return "forbidden_scene"
        return None

    @staticmethod
    def _wanted_attitudes(
        move: SocialMovePlan, stance: StanceDecision | None
    ) -> tuple[str, ...]:
        wanted = list(_MOVE_ATTITUDES.get(move.primary_move, ("acknowledge",)))
        if stance is not None:
            wanted.extend(_ATTITUDE_MAP.get(stance.attitude, ()))
        return tuple(dict.fromkeys(wanted))

    @staticmethod
    def _score(card: StickerCard, text: str, wanted: tuple[str, ...]) -> float:
        body_tokens = _tokens(text)
        meaning_tokens = _tokens(card.meaning)
        overlap = body_tokens & meaning_tokens
        score = float(len(overlap) * 3)
        for phrase in card.use_when:
            phrase_tokens = _tokens(phrase)
            if phrase_tokens & body_tokens:
                score += 2
            if str(phrase).strip() and str(phrase).strip() in text:
                score += 2
        if card.attitudes and any(item in wanted for item in card.attitudes):
            score += 2
        elif card.attitudes:
            score *= 0.35
        if score > 0 and card.meaning and any(part in text for part in _compact_parts(card.meaning)):
            score += 1
        return score


def _ask_media_forbidden(
    move: SocialMovePlan,
    stance: StanceDecision | None,
    media_policy: str,
) -> bool:
    if move.primary_move is SocialMove.SAFETY_MINIMUM:
        return True
    if media_policy == "registered_only":
        return False
    if move.primary_move in {SocialMove.FIRM_BOUNDARY, SocialMove.REFUSE}:
        return False
    if stance is not None and stance.attitude is Attitude.FOCUSED:
        return True
    return True


def _tokens(text: str) -> set[str]:
    compact = re.sub(r"\s+", "", str(text or ""))
    grams = {compact[index : index + 2] for index in range(max(0, len(compact) - 1))}
    words = {item.casefold() for item in _TOKEN.findall(str(text or "")) if len(item) >= 2}
    return grams | words


def _compact_parts(meaning: str) -> tuple[str, ...]:
    parts = [item.strip() for item in re.split(r"[，。！？、,.\s]+", meaning) if len(item.strip()) >= 2]
    return tuple(parts[:6])


__all__ = ("StickerAccompanist",)
