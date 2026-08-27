"""Dedicated DeepSeek boundary for qualitative member speech-style distillation."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Mapping

from .deepseek_cognition import (
    AioHttpJsonTransport,
    JsonHttpTransport,
    TransportNetworkError,
)


_BACKEND = "direct_deepseek_member_style"
_SYSTEM_MESSAGE = (
    "你是Groupmate的群友说话方式分析器，只分析怎么说，不分析这个人是谁。"
    "从给定事件归纳稳定的起句、句间推进、收尾、长度节奏、直接程度、反对方式、"
    "调侃方式、关心方式、称呼条件以及语气词和标点条件。"
    "不得计算或输出词频、百分比、平均句长和口癖排名；不得复制长原句。"
    "不得提取或推断身份、观点、经历、兴趣、隐私、人际关系、能力、攻击对象。"
    "只输出JSON对象，字段必须且只能是opening_patterns、progression_patterns、"
    "closing_patterns、length_rhythm、directness、disagreement_style、play_style、"
    "care_style、addressing_style、particles_punctuation、stable_traits、"
    "occasional_traits。前三项和occasional_traits是字符串数组；"
    "stable_traits每项只能含description和evidence_event_ids，且每项必须引用至少"
    "两个不同输入事件。所有事件ID必须原样复制输入，不得创造ID。"
)


@dataclass(frozen=True)
class MemberStyleModelResponse:
    payload: Mapping[str, object]
    latency_ms: int
    request_bytes: int
    backend: str
    model: str


class MemberStyleModelError(RuntimeError):
    def __init__(
        self,
        code: str,
        *,
        latency_ms: int = 0,
        request_bytes: int = 0,
        backend: str = _BACKEND,
        model: str = "",
    ) -> None:
        self.code = str(code)
        self.latency_ms = max(0, int(latency_ms))
        self.request_bytes = max(0, int(request_bytes))
        self.backend = str(backend)
        self.model = str(model)
        super().__init__(self.code)


class DeepSeekMemberStyleClient:
    def __init__(
        self,
        *,
        api_key: str,
        api_base: str,
        model: str,
        transport: JsonHttpTransport | None = None,
        timeout_seconds: float = 30.0,
    ) -> None:
        self._api_key = str(api_key).strip()
        self.api_base = str(api_base).strip().rstrip("/")
        self.model = str(model).strip()
        self.timeout_seconds = float(timeout_seconds)
        self._transport = transport or AioHttpJsonTransport()
        self._closed = False

    def request_payload(self, batch: Mapping[str, object]) -> dict[str, object]:
        return {
            "model": self.model,
            "messages": [
                {"role": "system", "content": _SYSTEM_MESSAGE},
                {
                    "role": "user",
                    "content": json.dumps(
                        dict(batch),
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                },
            ],
            "stream": False,
            "thinking": {"type": "disabled"},
            "response_format": {"type": "json_object"},
            "max_tokens": 1800,
            "temperature": 0.1,
        }

    def input_bytes(self, batch: Mapping[str, object]) -> int:
        return len(
            json.dumps(
                self.request_payload(batch),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        )

    async def distill(
        self, batch: Mapping[str, object]
    ) -> MemberStyleModelResponse:
        payload = self.request_payload(batch)
        request_bytes = self.input_bytes(batch)
        started = time.monotonic_ns()
        try:
            response = await self._transport.post_json(
                url=f"{self.api_base}/chat/completions",
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "Content-Type": "application/json",
                },
                payload=payload,
                timeout_seconds=self.timeout_seconds,
            )
        except TimeoutError:
            raise self._error("member_style_timeout", started, request_bytes) from None
        except TransportNetworkError:
            raise self._error("member_style_network_failed", started, request_bytes) from None
        except Exception:
            raise self._error("member_style_network_failed", started, request_bytes) from None
        if response.status in {401, 403}:
            raise self._error("member_style_auth_failed", started, request_bytes)
        if response.status == 429:
            raise self._error("member_style_rate_limited", started, request_bytes)
        if not 200 <= response.status < 300:
            raise self._error("member_style_upstream_failed", started, request_bytes)
        try:
            content = response.body["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError):
            raise self._error("member_style_response_shape_invalid", started, request_bytes) from None
        if not isinstance(content, str) or not content.strip():
            raise self._error("member_style_response_shape_invalid", started, request_bytes)
        try:
            result = json.loads(content)
        except json.JSONDecodeError:
            raise self._error("member_style_response_json_invalid", started, request_bytes) from None
        if not isinstance(result, dict):
            raise self._error("member_style_response_shape_invalid", started, request_bytes)
        return MemberStyleModelResponse(
            payload=result,
            latency_ms=self._elapsed_ms(started),
            request_bytes=request_bytes,
            backend=_BACKEND,
            model=self.model,
        )

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        await self._transport.close()

    def _error(
        self, code: str, started: int, request_bytes: int
    ) -> MemberStyleModelError:
        return MemberStyleModelError(
            code,
            latency_ms=self._elapsed_ms(started),
            request_bytes=request_bytes,
            model=self.model,
        )

    @staticmethod
    def _elapsed_ms(started: int) -> int:
        return max(0, (time.monotonic_ns() - started) // 1_000_000)


__all__ = (
    "DeepSeekMemberStyleClient",
    "MemberStyleModelError",
    "MemberStyleModelResponse",
)
