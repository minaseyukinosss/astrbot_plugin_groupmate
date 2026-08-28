"""Fail-closed knowledge observation and group convention learning."""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import time
import unicodedata
from contextlib import suppress
from dataclasses import dataclass
from typing import Callable

from ..contracts import SocialEventEnvelope
from .contracts import KnowledgeObservation, OriginClass
from .repository import KnowledgeRepository, StoredKnowledgeObservation


STALE_CONVENTION_SECONDS = 90 * 24 * 60 * 60
_URL_ONLY = re.compile(r"\s*https?://\S+\s*", re.IGNORECASE)
_DEFINITION = re.compile(
    r"^\s*(?P<expression>[^\s=，。！？]{2,24}?)\s*"
    r"(?:就是|指的是|是指|=)\s*"
    r"(?P<target>[^，。！？]{1,80})\s*[。！？]?$"
)
_COMMAND_PREFIXES = ("/", "!", "！", ".", "。")
_FORWARD_SEGMENTS = frozenset(
    {"forward", "node", "json", "xml", "card", "share"}
)
_BOT_HINTS = frozenset({"bot", "known_bot", "automation", "automated"})
_UNKNOWN_HINTS = frozenset(
    {"unknown", "unknown_actor", "possible_bot", "unknown_automation"}
)
_ADMIN_ROLES = frozenset({"admin", "owner"})


@dataclass(frozen=True)
class OriginDecision:
    origin_class: OriginClass
    admitted: bool
    diagnostic_code: str
    priority: int
    admin_confirmed: bool = False


@dataclass(frozen=True)
class _ConventionCandidate:
    kind: str
    expression: str
    entity_id: str
    meaning_summary: str
    admin_confirmed: bool

    def safe_summary(self) -> str:
        return json.dumps(
            {
                "kind": self.kind,
                "expression": self.expression,
                "entity_id": self.entity_id,
                "meaning_summary": self.meaning_summary,
                "admin_confirmed": self.admin_confirmed,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )

    @classmethod
    def from_summary(cls, value: str) -> "_ConventionCandidate":
        data = json.loads(value)
        return cls(
            kind=str(data["kind"]),
            expression=str(data["expression"]),
            entity_id=str(data["entity_id"]),
            meaning_summary=str(data["meaning_summary"]),
            admin_confirmed=bool(data.get("admin_confirmed")),
        )


@dataclass
class _QueuedObservation:
    observation: StoredKnowledgeObservation
    candidate: _ConventionCandidate
    priority: int
    attempt: int = 0


class KnowledgeOriginClassifier:
    """Classify only preserved platform facts; never infer a human identity."""

    def classify(self, event: SocialEventEnvelope) -> OriginDecision:
        payload = event.payload
        if event.event_type != "platform.message" or not event.group_id:
            return OriginDecision(
                OriginClass.UNKNOWN_ACTOR,
                False,
                "knowledge_not_group_message",
                0,
            )
        if bool(payload.get("is_self")):
            return OriginDecision(
                OriginClass.OWN_OUTPUT,
                False,
                "knowledge_own_output",
                0,
            )
        segments = payload.get("segments")
        segment_kinds = {
            str(item.get("type") or "").strip().casefold()
            for item in (segments if isinstance(segments, list) else ())
            if isinstance(item, dict)
        }
        if segment_kinds & _FORWARD_SEGMENTS:
            return OriginDecision(
                OriginClass.FORWARD,
                False,
                "knowledge_forward_or_card",
                10,
            )
        if (
            payload.get("social_eligible") is False
            or str(payload.get("interaction_owner") or "").upper()
            == "EXTERNAL_PLUGIN"
        ):
            return OriginDecision(
                OriginClass.COMMAND,
                False,
                "knowledge_external_command",
                10,
            )
        text = str(payload.get("text") or "").strip()
        if text.startswith(_COMMAND_PREFIXES):
            return OriginDecision(
                OriginClass.COMMAND,
                False,
                "knowledge_command",
                10,
            )
        sender_role = str(payload.get("sender_role") or "").casefold()
        automation_hint = str(
            payload.get("automation_hint") or ""
        ).casefold()
        if sender_role == "bot" or automation_hint in _BOT_HINTS:
            return OriginDecision(
                OriginClass.EXTERNAL_BOT,
                False,
                "knowledge_external_bot",
                5,
            )
        if automation_hint in _UNKNOWN_HINTS:
            return OriginDecision(
                OriginClass.UNKNOWN_ACTOR,
                False,
                "knowledge_unknown_automation",
                1,
            )
        if _URL_ONLY.fullmatch(text):
            return OriginDecision(
                OriginClass.UNKNOWN_ACTOR,
                False,
                "knowledge_link_only",
                1,
            )
        admin = sender_role in _ADMIN_ROLES
        return OriginDecision(
            OriginClass.HUMAN_CHAT,
            True,
            "knowledge_human_chat",
            120 if admin else 100,
            admin_confirmed=admin,
        )


class KnowledgeObservationService:
    """Persist bounded semantic observations and project them off-mainline."""

    def __init__(
        self,
        *,
        repository: KnowledgeRepository,
        group_ids: tuple[str, ...],
        install_salt: str,
        queue_capacity: int = 256,
        interval_seconds: int = 60,
        classifier: KnowledgeOriginClassifier | None = None,
        clock: Callable[[], float] | None = None,
    ) -> None:
        salt = str(install_salt or "")
        if not salt:
            raise ValueError("install_salt must not be empty")
        self.repository = repository
        self.group_ids = tuple(dict.fromkeys(str(item) for item in group_ids))
        self.install_salt = salt
        self.queue_capacity = max(1, int(queue_capacity))
        self.interval_seconds = max(1, int(interval_seconds))
        self.classifier = classifier or KnowledgeOriginClassifier()
        self.clock = time.time if clock is None else clock
        self._queue: list[_QueuedObservation] = []
        self._queued_ids: set[str] = set()
        self._wake = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self._dropped_count = 0
        self._last_attempt_at: int | None = None
        self._last_success_at: int | None = None
        self._last_diagnostic: str | None = None

    async def observe(self, event: SocialEventEnvelope) -> bool:
        if (
            event.group_id not in self.group_ids
            or not event.actor_id
            or event.event_type != "platform.message"
        ):
            return False
        decision = self.classifier.classify(event)
        if not decision.admitted:
            return False
        candidate = self._candidate(event, decision)
        if candidate is None:
            return False
        summary = candidate.safe_summary()
        group_id = str(event.group_id)
        scene_ref = str(
            event.payload.get("scene_ref")
            or event.correlation_id
            or event.event_id
        )[:128]
        observation_id = "knowledge-observation:" + hashlib.sha256(
            event.event_id.encode("utf-8")
        ).hexdigest()[:32]
        content_hash = hashlib.sha256(
            "{}\0{}\0{}".format(group_id, event.event_id, summary).encode(
                "utf-8"
            )
        ).hexdigest()
        observation = KnowledgeObservation.create(
            observation_id=observation_id,
            origin_class="human_chat",
            scope_kind="group",
            group_id=group_id,
            author_ref=self._author_ref(group_id, str(event.actor_id)),
            source_event_id=event.event_id,
            source_id=scene_ref,
            entity_hint=candidate.entity_id,
            safe_summary=summary,
            content_hash=content_hash,
            occurred_at=event.occurred_at,
            recorded_at=int(self.clock()),
            status="pending",
        )
        if not self.repository.append_observation(observation):
            return False
        stored = StoredKnowledgeObservation(
            observation_id=observation.observation_id,
            group_id=group_id,
            author_ref=str(observation.author_ref),
            source_event_id=event.event_id,
            scene_ref=scene_ref,
            entity_hint=candidate.entity_id,
            safe_summary=summary,
            occurred_at=event.occurred_at,
            recorded_at=observation.recorded_at,
            status="pending",
        )
        work = _QueuedObservation(stored, candidate, decision.priority)
        if len(self._queue) >= self.queue_capacity:
            lowest = min(
                self._queue,
                key=lambda item: (
                    item.priority,
                    item.observation.recorded_at,
                    item.observation.observation_id,
                ),
            )
            if work.priority > lowest.priority:
                self._queue.remove(lowest)
                self._queued_ids.discard(lowest.observation.observation_id)
                self.repository.set_observation_status(
                    lowest.observation.observation_id, "rejected"
                )
            else:
                self.repository.set_observation_status(
                    observation.observation_id, "rejected"
                )
                self._dropped_count += 1
                self._last_diagnostic = "knowledge_queue_full"
                return False
            self._dropped_count += 1
            self._last_diagnostic = "knowledge_queue_evicted_lower_trust"
        self._queue.append(work)
        self._queued_ids.add(observation.observation_id)
        self._wake.set()
        return True

    async def start(self) -> None:
        self._recover_pending()
        if self._task is None:
            self._task = asyncio.create_task(
                self._run(), name="groupmate-knowledge-observation"
            )
        if self._queue:
            self._wake.set()

    async def close(self) -> None:
        task = self._task
        self._task = None
        while self._queue:
            before = len(self._queue)
            await self.process_pending()
            if len(self._queue) >= before:
                break
        if task is not None:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task

    async def process_pending(self) -> int:
        processed = 0
        while self._queue:
            work = self._queue.pop(0)
            self._queued_ids.discard(work.observation.observation_id)
            self._last_attempt_at = int(self.clock())
            try:
                self._process_observation(work)
            except Exception:
                work.attempt += 1
                self._queue.append(work)
                self._queued_ids.add(work.observation.observation_id)
                self._last_diagnostic = "knowledge_worker_failed"
                return processed
            self.repository.set_observation_status(
                work.observation.observation_id, "admitted"
            )
            self._last_success_at = int(self.clock())
            self._last_diagnostic = None
            processed += 1
        return processed

    def expire_stale(self, *, now: int | None = None) -> int:
        decision_now = int(self.clock()) if now is None else int(now)
        return self.repository.expire_stale_conventions(
            now=decision_now,
            max_age_seconds=STALE_CONVENTION_SECONDS,
        )

    def health(self) -> dict[str, object]:
        task = self._task
        return {
            "enabled": True,
            "task_running": bool(task is not None and not task.done()),
            "pending_count": len(self._queue),
            "dropped_count": self._dropped_count,
            "last_attempt_at": self._last_attempt_at,
            "last_success_at": self._last_success_at,
            "last_diagnostic": self._last_diagnostic,
        }

    def _candidate(
        self, event: SocialEventEnvelope, decision: OriginDecision
    ) -> _ConventionCandidate | None:
        text = unicodedata.normalize(
            "NFKC", str(event.payload.get("text") or "")
        ).strip()
        definition = _DEFINITION.fullmatch(text)
        if definition is not None:
            expression = self._expression(definition.group("expression"))
            target_text = definition.group("target").strip()
            matches = self.repository.aliases_for_text(
                target_text, group_id=str(event.group_id)
            )
            targets = tuple(dict.fromkeys(item.entity_id for item in matches))
            if len(targets) == 1:
                return _ConventionCandidate(
                    kind="definition",
                    expression=expression,
                    entity_id=targets[0],
                    meaning_summary="群内表达“{}”指向“{}”".format(
                        expression, target_text
                    ),
                    admin_confirmed=decision.admin_confirmed,
                )
        normalized_text = text.casefold()
        for expression in self.repository.convention_expressions(
            str(event.group_id)
        ):
            if expression not in normalized_text:
                continue
            conventions = tuple(
                item
                for item in self.repository.conventions_for_expression(
                    str(event.group_id), expression
                )
                if item.status in {"candidate", "active"}
            )
            targets = tuple(
                dict.fromkeys(item.resolved_entity_id for item in conventions)
            )
            if len(targets) != 1:
                continue
            selected = next(
                item
                for item in conventions
                if item.resolved_entity_id == targets[0]
            )
            return _ConventionCandidate(
                kind="usage",
                expression=expression,
                entity_id=targets[0],
                meaning_summary=selected.meaning_summary,
                admin_confirmed=False,
            )
        game_matches = self.repository.game_alias_matches(text)
        game_ids = tuple(
            dict.fromkeys(entity_id for entity_id, _alias in game_matches)
        )
        if len(game_ids) == 1:
            matched_alias = next(
                alias
                for entity_id, alias in game_matches
                if entity_id == game_ids[0]
            )
            return _ConventionCandidate(
                kind="topic_mention",
                expression=matched_alias,
                entity_id=game_ids[0],
                meaning_summary="群聊提及已知游戏实体“{}”".format(
                    matched_alias
                ),
                admin_confirmed=False,
            )
        return None

    def _process_observation(self, work: _QueuedObservation) -> None:
        candidate = work.candidate
        if candidate.kind == "topic_mention":
            self.repository.record_qualified_mention(
                work.observation.observation_id,
                candidate.entity_id,
                work.observation.scene_ref,
            )
            return
        self.repository.record_convention_evidence(
            group_id=work.observation.group_id,
            expression=candidate.expression,
            entity_id=candidate.entity_id,
            meaning_summary=candidate.meaning_summary,
            observation_id=work.observation.observation_id,
            evidence_kind=candidate.kind,
            admin_confirmed=candidate.admin_confirmed,
            now=int(self.clock()),
        )

    def _recover_pending(self) -> None:
        for observation in self.repository.pending_observations():
            if observation.observation_id in self._queued_ids:
                continue
            try:
                candidate = _ConventionCandidate.from_summary(
                    observation.safe_summary
                )
            except (KeyError, TypeError, ValueError, json.JSONDecodeError):
                self.repository.set_observation_status(
                    observation.observation_id, "rejected"
                )
                self._last_diagnostic = "knowledge_observation_malformed"
                continue
            priority = 120 if candidate.admin_confirmed else 100
            if len(self._queue) >= self.queue_capacity:
                self.repository.set_observation_status(
                    observation.observation_id, "rejected"
                )
                self._dropped_count += 1
                self._last_diagnostic = "knowledge_recovery_queue_full"
                continue
            self._queue.append(
                _QueuedObservation(observation, candidate, priority)
            )
            self._queued_ids.add(observation.observation_id)

    async def _run(self) -> None:
        while True:
            try:
                await asyncio.wait_for(
                    self._wake.wait(), timeout=self.interval_seconds
                )
            except TimeoutError:
                pass
            self._wake.clear()
            try:
                await self.process_pending()
                self.expire_stale()
            except asyncio.CancelledError:
                raise
            except Exception:
                self._last_diagnostic = "knowledge_scheduler_failed"

    def _author_ref(self, group_id: str, actor_id: str) -> str:
        digest = hashlib.sha256(
            "{}\x1f{}\x1f{}".format(
                group_id, actor_id, self.install_salt
            ).encode("utf-8")
        ).hexdigest()[:24]
        return "author:" + digest

    @staticmethod
    def _expression(value: str) -> str:
        normalized = " ".join(
            unicodedata.normalize("NFKC", value).split()
        ).casefold()
        if not 2 <= len(normalized) <= 24:
            raise ValueError("convention expression length is invalid")
        return normalized


__all__ = (
    "KnowledgeObservationService",
    "KnowledgeOriginClassifier",
    "OriginDecision",
    "STALE_CONVENTION_SECONDS",
)
