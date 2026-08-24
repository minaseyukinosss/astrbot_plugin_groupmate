"""Plugin-owned DeepSeek cognition boundary."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Mapping, Protocol


_BACKEND = "direct_deepseek"
_SYSTEM_MESSAGE = (
    "判断Groupmate是否应参与群聊，不生成回复或推理。只输出JSON对象："
    "decision(speak|silence),signal(help_request|care_signal|humor_signal|greeting|"
    "boundary_signal|none),target_id,evidence_event_ids,confidence,disruption,novelty,"
    "reason。ID只能选输入值，三个数值为0到1；不确定、对象不明或会打断时选silence。"
)
_MAX_RESPONSE_BYTES = 64 * 1024


@dataclass(frozen=True)
class JsonHttpResponse:
    status: int
    body: object


class TransportNetworkError(RuntimeError):
    pass


class JsonHttpTransport(Protocol):
    async def post_json(
        self,
        *,
        url: str,
        headers: Mapping[str, str],
        payload: Mapping[str, object],
        timeout_seconds: float,
    ) -> JsonHttpResponse: ...

    async def close(self) -> None: ...


class AioHttpJsonTransport:
    """Small reusable aiohttp transport with bounded response reads."""

    def __init__(self) -> None:
        self._session = None

    async def post_json(
        self,
        *,
        url: str,
        headers: Mapping[str, str],
        payload: Mapping[str, object],
        timeout_seconds: float,
    ) -> JsonHttpResponse:
        try:
            import aiohttp

            if self._session is None or self._session.closed:
                self._session = aiohttp.ClientSession()
            timeout = aiohttp.ClientTimeout(total=float(timeout_seconds))
            async with self._session.post(
                url,
                headers=dict(headers),
                json=dict(payload),
                timeout=timeout,
            ) as response:
                raw = await response.content.read(_MAX_RESPONSE_BYTES + 1)
                if len(raw) > _MAX_RESPONSE_BYTES:
                    return JsonHttpResponse(response.status, None)
                try:
                    body = json.loads(raw.decode("utf-8")) if raw else None
                except (UnicodeDecodeError, json.JSONDecodeError):
                    body = None
                return JsonHttpResponse(response.status, body)
        except TimeoutError:
            raise
        except Exception as exc:
            try:
                import aiohttp

                if isinstance(exc, aiohttp.ClientError):
                    raise TransportNetworkError from None
            except ImportError:
                pass
            raise

    async def close(self) -> None:
        session = self._session
        self._session = None
        if session is not None and not session.closed:
            await session.close()


@dataclass(frozen=True)
class DirectCognitionResponse:
    verdict: Mapping[str, object]
    latency_ms: int
    request_bytes: int
    backend: str
    model: str


class DirectCognitionError(RuntimeError):
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


class DeepSeekCognitionClient:
    def __init__(
        self,
        *,
        api_key: str,
        api_base: str,
        model: str,
        transport: JsonHttpTransport | None = None,
        timeout_seconds: float = 6.0,
    ) -> None:
        self._api_key = str(api_key).strip()
        self.api_base = str(api_base).strip().rstrip("/")
        self.model = str(model).strip()
        self.timeout_seconds = float(timeout_seconds)
        self._transport = transport or AioHttpJsonTransport()
        self._closed = False

    def request_payload(
        self, facts: Mapping[str, object]
    ) -> dict[str, object]:
        return {
            "model": self.model,
            "messages": [
                {"role": "system", "content": _SYSTEM_MESSAGE},
                {
                    "role": "user",
                    "content": json.dumps(
                        dict(facts),
                        ensure_ascii=False,
                        separators=(",", ":"),
                        sort_keys=True,
                    ),
                },
            ],
            "stream": False,
            "thinking": {"type": "disabled"},
            "response_format": {"type": "json_object"},
            "max_tokens": 192,
            "temperature": 0.1,
        }

    def input_bytes(self, facts: Mapping[str, object]) -> int:
        return len(
            json.dumps(
                self.request_payload(facts),
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
        )

    async def classify(
        self, facts: Mapping[str, object]
    ) -> DirectCognitionResponse:
        payload = self.request_payload(facts)
        request_bytes = self.input_bytes(facts)
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
            raise self._error("direct_timeout", started, request_bytes) from None
        except TransportNetworkError:
            raise self._error(
                "direct_network_failed", started, request_bytes
            ) from None
        except Exception:
            raise self._error(
                "direct_network_failed", started, request_bytes
            ) from None
        if response.status in {401, 403}:
            raise self._error(
                "direct_auth_failed", started, request_bytes
            ) from None
        if response.status == 429:
            raise self._error(
                "direct_rate_limited", started, request_bytes
            ) from None
        if not 200 <= response.status < 300:
            raise self._error(
                "direct_upstream_failed", started, request_bytes
            ) from None
        try:
            body = response.body
            choices = body["choices"]
            content = choices[0]["message"]["content"]
            verdict = json.loads(content)
            if not isinstance(verdict, dict):
                raise TypeError
        except (KeyError, IndexError, TypeError, json.JSONDecodeError):
            raise self._error(
                "direct_invalid_output", started, request_bytes
            ) from None
        return DirectCognitionResponse(
            verdict=verdict,
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
    ) -> DirectCognitionError:
        return DirectCognitionError(
            code,
            latency_ms=self._elapsed_ms(started),
            request_bytes=request_bytes,
            model=self.model,
        )

    @staticmethod
    def _elapsed_ms(started: int) -> int:
        return max(0, (time.monotonic_ns() - int(started)) // 1_000_000)


__all__ = (
    "AioHttpJsonTransport",
    "DeepSeekCognitionClient",
    "DirectCognitionResponse",
    "DirectCognitionError",
    "JsonHttpTransport",
    "JsonHttpResponse",
    "TransportNetworkError",
)
