import io
import json
import urllib.error
from pathlib import Path

import pytest

from eval.providers import (
    OpenAICompatibleClient,
    OpenAICompatibleConfig,
    ProviderError,
)
from eval.scorers import LLMJudge
from eval.schema import load_scenarios


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        del exc_type, exc, traceback
        return False

    def read(self):
        return self.payload


def config(api_key="secret-key"):
    return OpenAICompatibleConfig(
        base_url="https://provider.example/v1",
        model="demo-model",
        api_key=api_key,
        timeout_seconds=3,
        temperature=0.2,
    )


def test_openai_compatible_provider_parses_completion(monkeypatch):
    captured = {}

    def fake_urlopen(request, timeout):
        captured["request"] = request
        captured["timeout"] = timeout
        body = {"choices": [{"message": {"content": " 在呢。 "}}]}
        return FakeResponse(json.dumps(body).encode("utf-8"))

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    client = OpenAICompatibleClient(config())

    result = client.complete(system_prompt="system", user_prompt="user")

    assert result == "在呢。"
    assert captured["timeout"] == 3
    assert captured["request"].full_url.endswith("/v1/chat/completions")
    assert captured["request"].headers["Authorization"] == "Bearer secret-key"


def test_provider_rejects_invalid_json_without_leaking_api_key(monkeypatch):
    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda request, timeout: FakeResponse(b"secret-key is not json"),
    )
    client = OpenAICompatibleClient(config())

    with pytest.raises(ProviderError) as raised:
        client.complete(system_prompt="system", user_prompt="user")

    assert "secret-key" not in str(raised.value)
    assert "[REDACTED]" in str(raised.value)


def test_provider_sanitizes_http_and_transport_errors(monkeypatch):
    http_error = urllib.error.HTTPError(
        "https://provider.example/v1/chat/completions",
        401,
        "unauthorized",
        {},
        io.BytesIO(b"bad secret-key"),
    )
    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda request, timeout: (_ for _ in ()).throw(http_error),
    )
    client = OpenAICompatibleClient(config())

    with pytest.raises(ProviderError) as raised:
        client.complete(system_prompt="system", user_prompt="user")

    assert "401" in str(raised.value)
    assert "secret-key" not in str(raised.value)

    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda request, timeout: (_ for _ in ()).throw(
            urllib.error.URLError("secret-key timed out")
        ),
    )
    with pytest.raises(ProviderError) as raised:
        client.complete(system_prompt="system", user_prompt="user")
    assert "secret-key" not in str(raised.value)


def test_provider_config_reads_environment_without_exposing_key(monkeypatch):
    monkeypatch.setenv("GROUPMATE_EVAL_BASE_URL", "https://provider.example/v1")
    monkeypatch.setenv("GROUPMATE_EVAL_MODEL", "demo")
    monkeypatch.setenv("GROUPMATE_EVAL_API_KEY", "top-secret")
    monkeypatch.setenv("GROUPMATE_EVAL_TIMEOUT", "12")
    monkeypatch.setenv("GROUPMATE_EVAL_TEMPERATURE", "0.1")

    loaded = OpenAICompatibleConfig.from_env()

    assert loaded.api_key == "top-secret"
    assert loaded.public_dict()["model"] == "demo"
    assert "api_key" not in loaded.public_dict()


def test_llm_judge_requires_exact_json_schema():
    class FakeClient:
        def complete(self, **kwargs):
            del kwargs
            return json.dumps(
                {
                    "naturalness": 5,
                    "role_adherence": 5,
                    "relevance": 4,
                    "context_retention": 4,
                    "ai_taste": False,
                    "rationale": "短且贴合当前群聊。",
                },
                ensure_ascii=False,
            )

    scenario = load_scenarios(
        Path(__file__).resolve().parents[1]
        / "eval"
        / "scenarios"
        / "baseline.jsonl"
    )[0]

    result = LLMJudge(FakeClient()).judge(scenario, "在呢。")

    assert result["naturalness"] == 5
    assert result["ai_taste"] is False
