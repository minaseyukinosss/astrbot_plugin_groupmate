"""Plugin-owned DeepSeek boundary for background member profiling."""

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


_BACKEND = "direct_deepseek_profile"
_SYSTEM_MESSAGE = (
    "你是Groupmate的群成员画像候选提取器。只从输入事件提取有明确证据的候选，"
    "不得生成回复，不得推断敏感关系，不得把第三方说法当事实。只输出JSON对象，"
    "顶层必须包含facts、episodes、edges三个数组。facts每项仅含subject_id、category、"
    "summary、source_kind、source_actor_id、evidence_event_ids、confidence。subject_id、"
    "source_actor_id和证据ID必须原样复制输入。episodes每项仅含title、summary、"
    "participants、episode_type、valence、importance、confidence、evidence_event_ids；"
    "edges每项仅含source_member_id、target_member_id、relation_type、direction、"
    "strength、confidence、evidence_event_ids。成员ID与证据ID必须原样复制输入，"
    "经历至少需要两条证据，关系至少需要三条重复证据；不确定就不输出。"
)


@dataclass(frozen=True)
class ProfileModelResponse:
    payload: Mapping[str, object]
    latency_ms: int
    request_bytes: int
    backend: str
    model: str


class ProfileModelError(RuntimeError):
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


class DeepSeekProfileClient:
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
                        separators=(",", ":"),
                        sort_keys=True,
                    ),
                },
            ],
            "stream": False,
            "thinking": {"type": "disabled"},
            "response_format": {"type": "json_object"},
            "max_tokens": 1600,
            "temperature": 0.1,
        }

    def input_bytes(self, batch: Mapping[str, object]) -> int:
        return len(
            json.dumps(
                self.request_payload(batch),
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
        )

    async def extract(
        self, batch: Mapping[str, object]
    ) -> ProfileModelResponse:
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
            raise self._error("profile_timeout", started, request_bytes) from None
        except TransportNetworkError:
            raise self._error(
                "profile_network_failed", started, request_bytes
            ) from None
        except Exception:
            raise self._error(
                "profile_network_failed", started, request_bytes
            ) from None
        if response.status in {401, 403}:
            raise self._error("profile_auth_failed", started, request_bytes)
        if response.status == 429:
            raise self._error("profile_rate_limited", started, request_bytes)
        if not 200 <= response.status < 300:
            raise self._error("profile_upstream_failed", started, request_bytes)
        try:
            content = response.body["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError):
            raise self._error(
                "profile_response_shape_invalid", started, request_bytes
            ) from None
        if not isinstance(content, str) or not content.strip():
            raise self._error(
                "profile_response_shape_invalid", started, request_bytes
            ) from None
        try:
            result = json.loads(content)
        except json.JSONDecodeError:
            raise self._error(
                "profile_response_json_invalid", started, request_bytes
            ) from None
        if not isinstance(result, dict):
            raise self._error(
                "profile_response_shape_invalid", started, request_bytes
            ) from None
        return ProfileModelResponse(
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
    ) -> ProfileModelError:
        return ProfileModelError(
            code,
            latency_ms=self._elapsed_ms(started),
            request_bytes=request_bytes,
            model=self.model,
        )

    @staticmethod
    def _elapsed_ms(started: int) -> int:
        return max(0, (time.monotonic_ns() - started) // 1_000_000)


__all__ = (
    "DeepSeekProfileClient",
    "ProfileModelError",
    "ProfileModelResponse",
)
