from __future__ import annotations

import asyncio
import json

import pytest

from groupmate.adapters.deepseek_cognition import JsonHttpResponse
from groupmate.adapters.deepseek_profile import (
    DeepSeekProfileClient,
    ProfileModelError,
)


class _Transport:
    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error
        self.calls = []

    async def post_json(self, **kwargs):
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return self.response

    async def close(self):
        return None


def _batch(count=1):
    return {
        "events": [
            {
                "event_id": f"event-{index}",
                "actor_id": "member-1",
                "actor_name": "群友甲",
                "text": "我喜欢冷饮",
            }
            for index in range(count)
        ]
    }


def test_profile_model_request_uses_separate_prompt_and_bounded_parameters():
    transport = _Transport(
        JsonHttpResponse(
            200,
            {
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {"facts": [], "episodes": [], "edges": []}
                            )
                        }
                    }
                ]
            },
        )
    )
    client = DeepSeekProfileClient(
        api_key="sk-test",
        api_base="https://api.deepseek.com",
        model="deepseek-profile-test",
        transport=transport,
        timeout_seconds=30,
    )

    response = asyncio.run(client.extract(_batch(20)))
    payload = transport.calls[0]["payload"]

    assert "群成员画像候选提取器" in payload["messages"][0]["content"]
    assert "是否应参与群聊" not in payload["messages"][0]["content"]
    assert payload["response_format"] == {"type": "json_object"}
    assert payload["temperature"] == 0.1
    assert payload["max_tokens"] == 1600
    assert transport.calls[0]["timeout_seconds"] == 30
    assert response.payload == {"facts": [], "episodes": [], "edges": []}


def test_profile_model_request_declares_the_exact_candidate_vocabulary():
    """The model must not have to invent enum values rejected by local policy."""

    client = DeepSeekProfileClient(
        api_key="sk-test",
        api_base="https://api.deepseek.com",
        model="deepseek-profile-test",
        transport=_Transport(),
    )

    system_message = client.request_payload(_batch())["messages"][0]["content"]

    for value in (
        "identity",
        "preference",
        "dislike",
        "boundary",
        "interest",
        "skill",
        "speech_style",
        "behavior_pattern",
        "group_role",
        "self_statement",
        "observed_pattern",
        "shared_achievement",
        "running_joke",
        "frequent_interaction",
        "technical_peer",
        "bidirectional",
    ):
        assert value in system_message
    assert "不要因为尚未达到确认门槛而省略有直接证据的候选" in system_message
    assert '"category":"preference"' in system_message
    assert '"source_kind":"self_statement"' in system_message
    assert "existing_id" in system_message
    assert "已有认知" in system_message
    assert "更新认知" in system_message


def test_profile_model_timeout_has_safe_diagnostic_code():
    client = DeepSeekProfileClient(
        api_key="sk-test",
        api_base="https://api.deepseek.com",
        model="deepseek-profile-test",
        transport=_Transport(error=TimeoutError("private upstream detail")),
    )

    with pytest.raises(ProfileModelError) as captured:
        asyncio.run(client.extract(_batch()))

    assert captured.value.code == "profile_timeout"
    assert "private upstream detail" not in str(captured.value)
