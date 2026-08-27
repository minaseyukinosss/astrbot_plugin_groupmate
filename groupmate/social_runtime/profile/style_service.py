"""Low-frequency publication of mature member speech-style assets."""

from __future__ import annotations

import asyncio
import time
from contextlib import suppress
from typing import Callable, Mapping

from ...adapters.deepseek_member_style import MemberStyleModelError
from .speech_style import MemberSpeechStyle, MemberStyleMaturity
from .style_repository import MemberStyleRepository


_PAYLOAD_FIELDS = frozenset(
    {
        "opening_patterns",
        "progression_patterns",
        "closing_patterns",
        "length_rhythm",
        "directness",
        "disagreement_style",
        "play_style",
        "care_style",
        "addressing_style",
        "particles_punctuation",
        "stable_traits",
        "occasional_traits",
    }
)


class MemberStyleService:
    def __init__(
        self,
        *,
        repository: MemberStyleRepository,
        client,
        interval_seconds: int = 600,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self.repository = repository
        self.client = client
        self.interval_seconds = max(60, int(interval_seconds))
        self.clock = time.time if clock is None else clock
        self._wake = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self._diagnostics: dict[tuple[str, str], str | None] = {}

    def wake(self) -> None:
        self._wake.set()

    async def process_due(self, *, now: int) -> None:
        for setting in self.repository.enabled_members():
            evidence = self.repository.eligible_observations(
                setting.group_id, setting.member_id
            )
            maturity = MemberStyleMaturity.from_evidence(evidence)
            if not maturity.ready:
                continue
            previous = self.repository.latest_ready(
                setting.group_id, setting.member_id
            )
            if previous is not None and (
                maturity.eligible_message_count
                < previous.eligible_message_count + 20
            ):
                continue
            batch = {
                "group_id": setting.group_id,
                "member_id": setting.member_id,
                "events": [
                    {
                        "event_id": item.event_id,
                        "occurred_at": item.occurred_at,
                        "scene_type": item.scene_type,
                        "text": item.text,
                    }
                    for item in evidence[-120:]
                ],
            }
            key = (setting.group_id, setting.member_id)
            try:
                response = await self.client.distill(batch)
                style = self._style(
                    response.payload,
                    group_id=setting.group_id,
                    member_id=setting.member_id,
                    version=(previous.version + 1 if previous is not None else 1),
                    maturity=maturity,
                    allowed_event_ids={item.event_id for item in evidence[-120:]},
                    generated_at=int(now),
                )
                self.repository.publish(style)
            except MemberStyleModelError as exc:
                self._diagnostics[key] = exc.code
                continue
            except (TypeError, ValueError):
                self._diagnostics[key] = "member_style_output_invalid"
                continue
            except Exception:
                self._diagnostics[key] = "member_style_worker_failed"
                continue
            self._diagnostics[key] = None

    def member_status(self, group_id: str, member_id: str) -> dict[str, object]:
        setting = self.repository.setting(group_id, member_id)
        evidence = self.repository.eligible_observations(group_id, member_id)
        maturity = MemberStyleMaturity.from_evidence(evidence)
        style = self.repository.latest_ready(group_id, member_id)
        status = (
            "DISABLED"
            if not setting.enabled
            else "READY"
            if style is not None
            else "FAILED"
            if self._diagnostics.get((str(group_id), str(member_id)))
            else "ACCUMULATING"
        )
        return {
            "enabled": setting.enabled,
            "setting_version": setting.version,
            "status": status,
            "eligible_message_count": maturity.eligible_message_count,
            "active_day_count": maturity.active_day_count,
            "scene_types": list(maturity.scene_types),
            "style_version": style.version if style is not None else None,
            "generated_at": style.generated_at if style is not None else None,
            "last_diagnostic": self._diagnostics.get(
                (str(group_id), str(member_id))
            ),
        }

    async def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._run(), name="member-style-worker")

    async def close(self) -> None:
        task = self._task
        self._task = None
        if task is not None:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task

    async def _run(self) -> None:
        while True:
            try:
                await asyncio.wait_for(
                    self._wake.wait(), timeout=self.interval_seconds
                )
            except TimeoutError:
                pass
            self._wake.clear()
            await self.process_due(now=int(self.clock()))

    @staticmethod
    def _style(
        payload: Mapping[str, object],
        *,
        group_id: str,
        member_id: str,
        version: int,
        maturity: MemberStyleMaturity,
        allowed_event_ids: set[str],
        generated_at: int,
    ) -> MemberSpeechStyle:
        if not isinstance(payload, Mapping) or set(payload) != _PAYLOAD_FIELDS:
            raise ValueError("member style payload fields are invalid")

        def strings(name: str) -> tuple[str, ...]:
            value = payload.get(name)
            if not isinstance(value, list) or not value or any(
                not isinstance(item, str) for item in value
            ):
                raise ValueError(f"{name} must be a non-empty string list")
            return tuple(value)

        raw_traits = payload.get("stable_traits")
        if not isinstance(raw_traits, list) or not raw_traits:
            raise ValueError("stable_traits must be a non-empty list")
        traits = []
        evidence_ids = []
        for raw in raw_traits:
            if not isinstance(raw, Mapping) or set(raw) != {
                "description", "evidence_event_ids"
            }:
                raise ValueError("stable trait shape is invalid")
            ids = tuple(dict.fromkeys(raw.get("evidence_event_ids") or ()))
            if len(ids) < 2 or not set(ids) <= allowed_event_ids:
                raise ValueError("stable trait evidence is invalid")
            description = str(raw.get("description") or "").strip()
            if not description:
                raise ValueError("stable trait description is empty")
            traits.append(description)
            evidence_ids.extend(ids)
        return MemberSpeechStyle(
            group_id=group_id,
            member_id=member_id,
            version=version,
            status="READY",
            opening_patterns=strings("opening_patterns"),
            progression_patterns=strings("progression_patterns"),
            closing_patterns=strings("closing_patterns"),
            length_rhythm=str(payload["length_rhythm"]),
            directness=str(payload["directness"]),
            disagreement_style=str(payload["disagreement_style"]),
            play_style=str(payload["play_style"]),
            care_style=str(payload["care_style"]),
            addressing_style=str(payload["addressing_style"]),
            particles_punctuation=str(payload["particles_punctuation"]),
            stable_traits=tuple(traits),
            occasional_traits=strings("occasional_traits"),
            evidence_event_ids=tuple(dict.fromkeys(evidence_ids)),
            eligible_message_count=maturity.eligible_message_count,
            active_day_count=maturity.active_day_count,
            scene_types=maturity.scene_types,
            generated_at=generated_at,
        )


__all__ = ("MemberStyleService",)
