from __future__ import annotations

import asyncio
import json

import pytest

from groupmate.adapters.deepseek_cognition import (
    DeepSeekCognitionClient,
    DirectCognitionError,
    JsonHttpResponse,
    TransportNetworkError,
)


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


def _success(verdict=None):
    value = verdict or {
        "decision": "silence",
        "opportunity_kind": "none",
        "anchor_event_id": None,
        "evidence_event_ids": ["qq:1"],
        "confidence": 0.8,
        "disruption": 0.2,
        "novelty": 0.3,
        "reason": "话题仍在成员之间自然进行",
    }
    return JsonHttpResponse(
        200,
        {"choices": [{"message": {"content": json.dumps(value)}}]},
    )


def _client(transport):
    return DeepSeekCognitionClient(
        api_key="sk-sensitive",
        api_base="https://api.deepseek.com",
        model="deepseek-v4-flash",
        transport=transport,
    )


def test_direct_client_sends_bounded_non_thinking_json_request():
    transport = FakeTransport(_success())
    client = _client(transport)

    result = asyncio.run(client.classify({"events": [{"id": "qq:1"}]}))

    request = transport.calls[0]
    assert request["url"] == "https://api.deepseek.com/chat/completions"
    assert request["headers"]["Authorization"] == "Bearer sk-sensitive"
    assert request["timeout_seconds"] == 6.0
    body = request["payload"]
    assert body["model"] == "deepseek-v4-flash"
    assert body["stream"] is False
    assert body["thinking"] == {"type": "disabled"}
    assert body["response_format"] == {"type": "json_object"}
    assert body["max_tokens"] == 512
    assert body["temperature"] == 0.1
    system_message = body["messages"][0]["content"]
    assert '"decision":"silence"' in system_message
    assert '"evidence_event_ids":[]' in system_message
    assert "silence时允许证据为空" in system_message
    assert "speak时锚点和证据不得为空" in system_message
    assert "不判断是否插话" not in system_message
    assert "opportunity_kind" in system_message
    assert "anchor_event_id" in system_message
    assert "relationship_events" in system_message
    assert "relationship_memories" in system_message
    assert "member_context.relations" in system_message
    assert "member_context.members.boundaries" in system_message
    assert "不得输出amount、delta、score" in system_message
    assert json.loads(body["messages"][1]["content"]) == {
        "events": [{"id": "qq:1"}]
    }
    assert result.verdict["decision"] == "silence"
    assert result.backend == "direct_deepseek"
    assert result.model == "deepseek-v4-flash"
    assert result.request_bytes == client.input_bytes(
        {"events": [{"id": "qq:1"}]}
    )
    assert "sk-sensitive" not in repr(client)


def test_owned_candidates_use_relation_only_protocol():
    transport = FakeTransport(_success({
        "dialogue_relation": {
            "kind": "answers_bot",
            "anchor_event_id": "e1",
            "bot_event_id": "b1",
            "confidence": 0.88,
        }
    }))
    client = _client(transport)
    facts = {
        "events": [{"id": "e1"}],
        "dialogue_candidates": [
            {"anchor_event_id": "e1", "bot_event_id": "b1", "target_id": "u1"}
        ],
    }
    asyncio.run(client.classify(facts))
    body = transport.calls[0]["payload"]
    assert body["temperature"] == 0
    system_message = body["messages"][0]["content"]
    assert "不判断是否插话" in system_message
    assert "简短确认" in system_message
    assert "decision只能是speak或silence" not in system_message
    assert json.loads(body["messages"][1]["content"]) == facts


@pytest.mark.parametrize(
    ("response", "error", "expected"),
    (
        (JsonHttpResponse(401, {"error": "raw-secret-body"}), None, "direct_auth_failed"),
        (JsonHttpResponse(403, {}), None, "direct_auth_failed"),
        (JsonHttpResponse(429, {}), None, "direct_rate_limited"),
        (JsonHttpResponse(503, {}), None, "direct_upstream_failed"),
        (None, TimeoutError("raw timeout detail"), "direct_timeout"),
        (None, TransportNetworkError("raw network detail"), "direct_network_failed"),
        (
            JsonHttpResponse(200, {"choices": []}),
            None,
            "direct_response_shape_invalid",
        ),
        (
            JsonHttpResponse(
                200,
                {"choices": [{"message": {"content": ""}}]},
            ),
            None,
            "direct_response_empty",
        ),
        (
            JsonHttpResponse(200, {"choices": [{"message": {"content": "not json"}}]}),
            None,
            "direct_response_json_invalid",
        ),
        (
            JsonHttpResponse(200, {"choices": [{"message": {"content": "[]"}}]}),
            None,
            "direct_response_shape_invalid",
        ),
    ),
)
def test_direct_client_maps_failures_without_leaking_remote_content(
    response, error, expected
):
    client = _client(FakeTransport(response, error))

    with pytest.raises(DirectCognitionError) as caught:
        asyncio.run(client.classify({"events": [{"id": "qq:1"}]}))

    assert caught.value.code == expected
    rendered = f"{caught.value!r} {caught.value}"
    assert "sk-sensitive" not in rendered
    assert "raw-secret-body" not in rendered
    assert "raw timeout detail" not in rendered
    assert "raw network detail" not in rendered


def test_direct_client_reuses_transport_and_closes_it_once():
    transport = FakeTransport(_success())
    client = _client(transport)

    async def scenario():
        await client.classify({"events": [{"id": "qq:1"}]})
        await client.classify({"events": [{"id": "qq:2"}]})
        await client.close()
        await client.close()

    asyncio.run(scenario())

    assert len(transport.calls) == 2
    assert transport.close_calls == 1
