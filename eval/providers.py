"""OpenAI-compatible evaluation provider with no third-party dependencies."""

from __future__ import annotations

import json
import os
import socket
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Dict, Mapping, Optional


class ProviderError(RuntimeError):
    """Sanitized provider failure safe to persist in evaluation results."""


@dataclass(frozen=True)
class OpenAICompatibleConfig:
    base_url: str
    model: str
    api_key: str = ""
    timeout_seconds: float = 60.0
    temperature: float = 0.4

    @classmethod
    def from_env(cls) -> "OpenAICompatibleConfig":
        base_url = os.environ.get("GROUPMATE_EVAL_BASE_URL", "").strip()
        model = os.environ.get("GROUPMATE_EVAL_MODEL", "").strip()
        if not base_url:
            raise ProviderError("GROUPMATE_EVAL_BASE_URL is required for model mode")
        if not model:
            raise ProviderError("GROUPMATE_EVAL_MODEL is required for model mode")
        try:
            timeout = float(os.environ.get("GROUPMATE_EVAL_TIMEOUT", "60"))
            temperature = float(os.environ.get("GROUPMATE_EVAL_TEMPERATURE", "0.4"))
        except ValueError:
            raise ProviderError("provider timeout and temperature must be numeric")
        if timeout <= 0:
            raise ProviderError("provider timeout must be positive")
        if not 0 <= temperature <= 2:
            raise ProviderError("provider temperature must be between 0 and 2")
        return cls(
            base_url=base_url,
            model=model,
            api_key=os.environ.get("GROUPMATE_EVAL_API_KEY", "").strip(),
            timeout_seconds=timeout,
            temperature=temperature,
        )

    @property
    def endpoint(self) -> str:
        base = self.base_url.rstrip("/")
        if base.endswith("/chat/completions"):
            return base
        return base + "/chat/completions"

    def public_dict(self) -> Dict[str, Any]:
        return {
            "base_url": self.base_url,
            "model": self.model,
            "timeout_seconds": self.timeout_seconds,
            "temperature": self.temperature,
        }


class OpenAICompatibleClient:
    def __init__(self, config: OpenAICompatibleConfig) -> None:
        self.config = config

    def complete(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        temperature: Optional[float] = None,
        json_mode: bool = False,
    ) -> str:
        payload: Dict[str, Any] = {
            "model": self.config.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": (
                self.config.temperature
                if temperature is None
                else float(temperature)
            ),
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        headers = {"Content-Type": "application/json"}
        if self.config.api_key:
            headers["Authorization"] = "Bearer " + self.config.api_key
        request = urllib.request.Request(
            self.config.endpoint,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(
                request, timeout=self.config.timeout_seconds
            ) as response:
                raw_body = response.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise ProviderError(
                "provider HTTP {}: {}".format(
                    exc.code, self._sanitize(body[:500])
                )
            )
        except (urllib.error.URLError, socket.timeout, TimeoutError) as exc:
            raise ProviderError(
                "provider request failed: {}".format(
                    self._sanitize(str(getattr(exc, "reason", exc)))
                )
            )
        try:
            decoded = json.loads(raw_body)
            content = decoded["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError, json.JSONDecodeError):
            raise ProviderError(
                "provider returned an invalid response: {}".format(
                    self._sanitize(raw_body[:500])
                )
            )
        if not isinstance(content, str):
            raise ProviderError("provider response content must be a string")
        return content.strip()

    def _sanitize(self, text: str) -> str:
        cleaned = str(text)
        if self.config.api_key:
            cleaned = cleaned.replace(self.config.api_key, "[REDACTED]")
        return cleaned.replace("\n", " ").strip()


class OpenAICompatibleGenerationModel:
    def __init__(self, client: OpenAICompatibleClient) -> None:
        self.client = client

    async def generate(self, plan, topic, memories) -> str:
        del topic, memories
        return self.client.complete(
            system_prompt=plan.persona_prompt,
            user_prompt=plan.user_prompt,
        )

    async def repair(self, text: str, violations) -> str:
        prompt = "\n".join(
            [
                "违规项：" + "、".join(str(item) for item in violations),
                "只修改表达，不新增事实。只输出修改后的最终回复。",
                "原文：",
                text,
            ]
        )
        return self.client.complete(
            system_prompt="你在按爱弥斯的口吻修复一条群聊短回复。",
            user_prompt=prompt,
            temperature=0.2,
        )


def public_model_config(
    config: Optional[OpenAICompatibleConfig],
) -> Mapping[str, Any]:
    return config.public_dict() if config is not None else {"mode": "scripted"}
