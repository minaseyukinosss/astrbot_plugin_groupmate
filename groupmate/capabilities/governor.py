"""Governed capability execution boundary."""

from __future__ import annotations

import asyncio
from typing import Dict, Tuple

from .contracts import (
    CapabilityContext,
    CapabilityMediaPolicy,
    CapabilityRequest,
    CapabilityResult,
    CapabilityStatus,
    MediaCandidate,
)
from .registry import CapabilityRegistry


class CapabilityGovernor:
    def __init__(self, registry: CapabilityRegistry) -> None:
        if not isinstance(registry, CapabilityRegistry):
            raise TypeError("registry must be a CapabilityRegistry")
        self.registry = registry
        self._semaphores: Dict[str, asyncio.Semaphore] = {}

    async def execute(
        self,
        request: CapabilityRequest,
        context: CapabilityContext,
        *,
        now: int,
    ) -> CapabilityResult:
        if not isinstance(request, CapabilityRequest):
            raise TypeError("request must be a CapabilityRequest")
        if not isinstance(context, CapabilityContext):
            raise TypeError("context must be a CapabilityContext")
        spec = self.registry.lookup(request.capability_name)
        if spec is None:
            return self._unsupported(
                request.capability_name,
                "capability_not_registered",
            )
        if not spec.available:
            return self._unsupported(request.capability_name, "capability_unavailable")
        if int(context.deadline_at) <= int(now):
            return CapabilityResult(
                CapabilityStatus.TIMEOUT,
                request.capability_name,
                user_text="The capability deadline expired.",
                error_code="deadline_expired",
            )
        required = set(spec.manifest.permission_profile)
        allowed = set(context.allowed_permissions)
        if not required.issubset(allowed):
            return self._unsupported(request.capability_name, "permission_denied")

        timeout = min(
            float(spec.manifest.default_timeout_seconds),
            max(0.001, float(int(context.deadline_at) - int(now))),
        )
        semaphore = self._semaphores.get(spec.name)
        if semaphore is None:
            semaphore = asyncio.Semaphore(spec.manifest.max_concurrency)
            self._semaphores[spec.name] = semaphore

        async with semaphore:
            result = await self.registry.execute(request, timeout_seconds=timeout)

        if result.status is not CapabilityStatus.SUCCESS:
            return result
        result = self._apply_media_policy(result, context.media_policy)
        if self._result_size(result) > spec.manifest.max_result_size:
            return CapabilityResult(
                CapabilityStatus.FAILED,
                request.capability_name,
                user_text="The capability result was too large.",
                error_code="result_too_large",
            )
        return result

    @staticmethod
    def _unsupported(capability_name: str, error_code: str) -> CapabilityResult:
        return CapabilityResult(
            CapabilityStatus.UNSUPPORTED,
            capability_name,
            user_text="This capability is not available.",
            error_code=error_code,
        )

    @staticmethod
    def _apply_media_policy(
        result: CapabilityResult,
        policy: CapabilityMediaPolicy,
    ) -> CapabilityResult:
        if not policy.capability_media_allowed:
            allowed_media: Tuple[MediaCandidate, ...] = ()
        else:
            allowed_kinds = set(policy.allowed_media_kinds)
            allowed_labels = set(policy.allowed_safety_labels)
            allowed_media = tuple(
                candidate
                for candidate in result.media_candidates
                if (
                    (not allowed_kinds or candidate.media_kind in allowed_kinds)
                    and (
                        not allowed_labels
                        or candidate.safety_label in allowed_labels
                    )
                )
            )
        if allowed_media == result.media_candidates:
            return result
        return CapabilityResult(
            result.status,
            result.capability_name,
            facts=result.facts,
            user_text=result.user_text,
            error_code=result.error_code,
            diagnostic=result.diagnostic,
            media_candidates=allowed_media,
        )

    @staticmethod
    def _result_size(result: CapabilityResult) -> int:
        text_size = sum(len(item) for item in result.facts)
        text_size += len(result.user_text)
        text_size += len(result.error_code)
        text_size += len(result.diagnostic)
        media_size = sum(
            len(candidate.media_id)
            + len(candidate.source)
            + len(candidate.locator)
            + len(candidate.media_kind)
            + len(candidate.semantic_label)
            + len(candidate.purpose)
            + len(candidate.safety_label)
            for candidate in result.media_candidates
        )
        return text_size + media_size
