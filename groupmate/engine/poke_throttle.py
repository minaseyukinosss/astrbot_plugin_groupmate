"""In-process poke reaction throttle (direct + bystander)."""

from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Callable, Dict, List, Tuple

from ..policies import InteractionPolicy


@dataclass(frozen=True)
class PokeThrottleDecision:
    """Whether a poke may produce an outbound reaction."""

    allow: bool
    reason_code: str = ""


class PokeThrottle:
    """Per-persona poke cooldown and session rate limits."""

    def __init__(
        self,
        *,
        rng: Callable[[], float] = random.random,
    ) -> None:
        self._rng = rng
        self._last_react_at: Dict[Tuple[str, str, str], int] = {}
        self._session_reacts: Dict[Tuple[str, str], List[int]] = {}
        self._last_bystander_at: Dict[Tuple[str, str], int] = {}

    def configure_rng(self, rng: Callable[[], float]) -> None:
        self._rng = rng

    def evaluate_direct(
        self,
        *,
        persona_id: str,
        group_id: str,
        sender_id: str,
        now: int,
        policy: InteractionPolicy,
    ) -> PokeThrottleDecision:
        persona_id = str(persona_id or "").strip()
        group_id = str(group_id or "").strip()
        sender_id = str(sender_id or "").strip()
        now = int(now or 0)
        if not persona_id or not group_id or not sender_id:
            return PokeThrottleDecision(False, "poke_invalid_identity")

        cooldown = max(0, int(policy.poke_cooldown_seconds))
        last = self._last_react_at.get((persona_id, group_id, sender_id), 0)
        if cooldown > 0 and last > 0 and now - last < cooldown:
            return PokeThrottleDecision(False, "poke_cooldown")

        per_minute = max(0, int(policy.poke_session_per_minute))
        if per_minute > 0:
            key = (persona_id, group_id)
            window = [
                stamp
                for stamp in self._session_reacts.get(key, ())
                if now - int(stamp) < 60
            ]
            self._session_reacts[key] = window
            if len(window) >= per_minute:
                return PokeThrottleDecision(False, "poke_rate_limited")

        probability = float(policy.poke_react_probability)
        if probability <= 0:
            return PokeThrottleDecision(False, "poke_skip")
        if probability < 1.0 and self._rng() > probability:
            return PokeThrottleDecision(False, "poke_skip")
        return PokeThrottleDecision(True, "")

    def mark_direct_reacted(
        self,
        *,
        persona_id: str,
        group_id: str,
        sender_id: str,
        now: int,
    ) -> None:
        persona_id = str(persona_id or "").strip()
        group_id = str(group_id or "").strip()
        sender_id = str(sender_id or "").strip()
        now = int(now or 0)
        if not persona_id or not group_id or not sender_id:
            return
        self._last_react_at[(persona_id, group_id, sender_id)] = now
        key = (persona_id, group_id)
        stamps = [
            stamp
            for stamp in self._session_reacts.get(key, ())
            if now - int(stamp) < 60
        ]
        stamps.append(now)
        self._session_reacts[key] = stamps

    def evaluate_bystander(
        self,
        *,
        persona_id: str,
        group_id: str,
        now: int,
        policy: InteractionPolicy,
    ) -> PokeThrottleDecision:
        persona_id = str(persona_id or "").strip()
        group_id = str(group_id or "").strip()
        now = int(now or 0)
        if not persona_id or not group_id:
            return PokeThrottleDecision(False, "poke_invalid_identity")

        cooldown = max(0, int(policy.poke_bystander_cooldown_seconds))
        last = self._last_bystander_at.get((persona_id, group_id), 0)
        if cooldown > 0 and last > 0 and now - last < cooldown:
            return PokeThrottleDecision(False, "poke_bystander_cooldown")

        probability = float(policy.poke_bystander_probability)
        if probability <= 0:
            return PokeThrottleDecision(False, "poke_bystander_skip")
        if probability < 1.0 and self._rng() > probability:
            return PokeThrottleDecision(False, "poke_bystander_skip")
        return PokeThrottleDecision(True, "")

    def mark_bystander_reacted(
        self,
        *,
        persona_id: str,
        group_id: str,
        now: int,
    ) -> None:
        persona_id = str(persona_id or "").strip()
        group_id = str(group_id or "").strip()
        now = int(now or 0)
        if not persona_id or not group_id:
            return
        self._last_bystander_at[(persona_id, group_id)] = now

    def pick_bystander_target(
        self,
        *,
        poker_id: str,
        victim_id: str,
        policy: InteractionPolicy,
    ) -> str:
        poker_id = str(poker_id or "").strip()
        victim_id = str(victim_id or "").strip()
        strategy = str(policy.poke_bystander_target or "victim").strip().lower()
        if strategy == "poker":
            return poker_id or victim_id
        if strategy == "random":
            choices = [item for item in (poker_id, victim_id) if item]
            if not choices:
                return ""
            if len(choices) == 1:
                return choices[0]
            return choices[0] if self._rng() < 0.5 else choices[1]
        return victim_id or poker_id
