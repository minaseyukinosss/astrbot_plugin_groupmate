from __future__ import annotations

import asyncio
import json

import pytest

from groupmate.adapters.deepseek_cognition import JsonHttpResponse
from groupmate.adapters.deepseek_text import DeepSeekTextClient, TextModelError


class FakeTransport:
    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error
        self.calls = []
        self.close_calls = 0

    async def post_json(self, **request):
        self.calls.append(request)
        if self.error is not None:
            raise self.error
        return self.response

    async def close(self):
        self.close_calls += 1


def _success(text='{"scene_kind":"direct_question"}'):
    return JsonHttpResponse(
        200,
        {"choices": [{"message": {"content": text}}]},
    )


def test_scene_client_disables_thinking_and_forces_json():
    transport = FakeTransport(_success())
    client = DeepSeekTextClient(
        api_key="sk-test",
        api_base="https://api.deepseek.com",
        model="deepseek-v4-flash",
        transport=transport,
        timeout_seconds=8,
        max_tokens=512,
        temperature=0.1,
    )

    text = asyncio.run(
        client.complete_text(system_prompt="识别场景", prompt='{"text":"在吗"}')
    )

    request = transport.calls[0]
    body = request["payload"]
    assert request["url"] == "https://api.deepseek.com/chat/completions"
    assert request["timeout_seconds"] == 8
    assert body["thinking"] == {"type": "disabled"}
    assert body["response_format"] == {"type": "json_object"}
    assert body["max_tokens"] == 512
    assert body["temperature"] == 0.1
    assert text == '{"scene_kind":"direct_question"}'


def test_imitation_client_allows_prose_without_json_mode():
    transport = FakeTransport(_success("好，我学阿甲说话。"))
    client = DeepSeekTextClient(
        api_key="sk-test",
        api_base="https://api.deepseek.com",
        model="deepseek-v4-flash",
        transport=transport,
        timeout_seconds=15,
        max_tokens=400,
        temperature=0.7,
        json_object=False,
    )

    text = asyncio.run(
        client.complete_text(system_prompt="模仿确认", prompt="确认")
    )

    body = transport.calls[0]["payload"]
    assert "response_format" not in body
    assert body["thinking"] == {"type": "disabled"}
    assert text == "好，我学阿甲说话。"


def test_timeout_becomes_text_timeout():
    transport = FakeTransport(error=TimeoutError())
    client = DeepSeekTextClient(
        api_key="sk-test",
        api_base="https://api.deepseek.com",
        model="deepseek-v4-flash",
        transport=transport,
    )

    with pytest.raises(TextModelError) as caught:
        asyncio.run(client.complete_text(system_prompt="s", prompt="p"))

    assert caught.value.code == "text_timeout"
