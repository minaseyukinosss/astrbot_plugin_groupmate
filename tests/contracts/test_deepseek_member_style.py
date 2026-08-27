from __future__ import annotations

import asyncio
import json

import pytest

from groupmate.adapters.deepseek_cognition import JsonHttpResponse
from groupmate.adapters.deepseek_member_style import (
    DeepSeekMemberStyleClient,
    MemberStyleModelError,
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


def _batch():
    return {
        "group_id": "group-1",
        "member_id": "member-1",
        "events": [
            {
                "event_id": "event-1",
                "occurred_at": 100,
                "scene_type": "banter",
                "text": "这也能算对啊，笑死",
            }
        ],
    }


def _payload():
    return {
        "opening_patterns": ["先直接指出现场问题"],
        "progression_patterns": ["短句表态后补一句理由"],
        "closing_patterns": ["用轻微反问自然收口"],
        "length_rhythm": "以一到两句为主",
        "directness": "直接但不替别人下结论",
        "disagreement_style": "先指出不合理处，再给具体原因",
        "play_style": "从当前用词形成轻微调侃",
        "care_style": "关心时给出具体可做的动作",
        "addressing_style": "明确多人对象时才使用称呼",
        "particles_punctuation": "语气词少量出现，不连续感叹",
        "stable_traits": [
            {"description": "结论在前", "evidence_event_ids": ["event-1"]}
        ],
        "occasional_traits": ["偶尔省略主语"],
    }


def test_distiller_request_asks_for_qualitative_structure_not_word_percentages():
    client = DeepSeekMemberStyleClient(
        api_key="k", api_base="https://example", model="m", transport=_Transport()
    )

    payload = client.request_payload(_batch())
    system = payload["messages"][0]["content"]

    assert "起句" in system and "推进" in system and "收尾" in system
    assert "词频" in system and "不得" in system
    assert "身份" in system and "观点" in system and "经历" in system
    assert payload["response_format"] == {"type": "json_object"}


def test_distiller_returns_json_object_with_bounded_transport_metadata():
    transport = _Transport(
        JsonHttpResponse(
            200,
            {"choices": [{"message": {"content": json.dumps(_payload())}}]},
        )
    )
    client = DeepSeekMemberStyleClient(
        api_key="k", api_base="https://example", model="m", transport=transport
    )

    response = asyncio.run(client.distill(_batch()))

    assert response.payload == _payload()
    assert response.request_bytes > 0
    assert response.backend == "direct_deepseek_member_style"


def test_distiller_timeout_hides_private_exception_text():
    client = DeepSeekMemberStyleClient(
        api_key="k",
        api_base="https://example",
        model="m",
        transport=_Transport(error=TimeoutError("private endpoint")),
    )

    with pytest.raises(MemberStyleModelError) as captured:
        asyncio.run(client.distill(_batch()))

    assert captured.value.code == "member_style_timeout"
    assert "private endpoint" not in str(captured.value)
