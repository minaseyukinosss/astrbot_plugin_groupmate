"""Plugin-owned DeepSeek boundary for scene parsing and reply text."""

from __future__ import annotations

import json
import time

from .deepseek_cognition import (
    AioHttpJsonTransport,
    JsonHttpTransport,
    TransportNetworkError,
)


_BACKEND = "direct_deepseek_text"
_MAX_RESPONSE_CHARS = 16_384


class TextModelError(RuntimeError):
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


class DeepSeekTextClient:
    """One bounded chat completion owned by the plugin.

    Scene parsing and reply generation both depend on the thinking mode, the
    output ceiling and the request timeout.  A host provider abstraction hides
    those three parameters, so the plugin issues the request itself.
    """

    def __init__(
        self,
        *,
        api_key: str,
        api_base: str,
        model: str,
        transport: JsonHttpTransport | None = None,
        timeout_seconds: float = 8.0,
        max_tokens: int = 512,
        temperature: float = 0.1,
        json_object: bool = True,
    ) -> None:
        self._api_key = str(api_key).strip()
        self.api_base = str(api_base).strip().rstrip("/")
        self.model = str(model).strip()
        self.timeout_seconds = float(timeout_seconds)
        self.max_tokens = max(1, int(max_tokens))
        self.temperature = float(temperature)
        self.json_object = bool(json_object)
        self._transport = transport or AioHttpJsonTransport()
        self._closed = False

    def request_payload(
        self, *, system_prompt: str, prompt: str
    ) -> dict[str, object]:
        payload: dict[str, object] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": str(system_prompt)},
                {"role": "user", "content": str(prompt)},
            ],
            "stream": False,
            "thinking": {"type": "disabled"},
            "max_tokens": self.max_tokens,
            "temperature": self.temperature,
        }
        if self.json_object:
            payload["response_format"] = {"type": "json_object"}
        return payload

    def input_bytes(self, *, system_prompt: str, prompt: str) -> int:
        return len(
            json.dumps(
                self.request_payload(
                    system_prompt=system_prompt, prompt=prompt
                ),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        )

    async def complete_text(self, *, system_prompt: str, prompt: str) -> str:
        payload = self.request_payload(
            system_prompt=system_prompt, prompt=prompt
        )
        request_bytes = self.input_bytes(
            system_prompt=system_prompt, prompt=prompt
        )
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
            raise self._error("text_timeout", started, request_bytes) from None
        except TransportNetworkError:
            raise self._error(
                "text_network_failed", started, request_bytes
            ) from None
        except Exception:
            raise self._error(
                "text_network_failed", started, request_bytes
            ) from None
        if response.status in {401, 403}:
            raise self._error("text_auth_failed", started, request_bytes)
        if response.status == 429:
            raise self._error("text_rate_limited", started, request_bytes)
        if not 200 <= response.status < 300:
            raise self._error("text_upstream_failed", started, request_bytes)
        try:
            content = response.body["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError):
            raise self._error(
                "text_response_shape_invalid", started, request_bytes
            ) from None
        if not isinstance(content, str) or not content.strip():
            raise self._error(
                "text_response_shape_invalid", started, request_bytes
            )
        if len(content) > _MAX_RESPONSE_CHARS:
            raise self._error("text_response_too_large", started, request_bytes)
        return self._unfenced(content)

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        await self._transport.close()

    @staticmethod
    def _unfenced(content: str) -> str:
        text = content.strip()
        if text.startswith("```") and text.endswith("```"):
            first_newline = text.find("\n")
            if first_newline >= 0:
                text = text[first_newline + 1 : -3].strip()
        return text

    def _error(
        self, code: str, started: int, request_bytes: int
    ) -> TextModelError:
        return TextModelError(
            code,
            latency_ms=self._elapsed_ms(started),
            request_bytes=request_bytes,
            model=self.model,
        )

    @staticmethod
    def _elapsed_ms(started: int) -> int:
        return max(0, (time.monotonic_ns() - started) // 1_000_000)


__all__ = ("DeepSeekTextClient", "TextModelError")
