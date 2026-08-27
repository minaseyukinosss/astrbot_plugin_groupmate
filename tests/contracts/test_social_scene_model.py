import asyncio
import json

import pytest

from groupmate.adapters.social_scene_model import SceneJsonModel


class FixedTextModel:
    def __init__(self, response):
        self.response = response
        self.calls = []

    async def complete_text(self, *, system_prompt, prompt):
        self.calls.append({"system_prompt": system_prompt, "prompt": prompt})
        return self.response


def test_scene_model_prompt_contains_no_surface_meta_language():
    prompt = SceneJsonModel.system_prompt()
    assert "情绪承接" not in prompt
    assert "人格化补充" not in prompt
    assert "续聊接口" not in prompt
    assert "先接住" not in prompt


def test_scene_model_serializes_facts_and_returns_one_json_object():
    payload = {
        "scene_kind": "fact_question",
        "target_scope": "INDIVIDUAL",
        "target_id": "u1",
        "literal_subject": "插件用途",
        "user_move": "asks_fact",
        "continuity_event_ids": ["m1"],
        "confidence": 0.9,
    }
    model = FixedTextModel(json.dumps(payload, ensure_ascii=False))

    result = asyncio.run(SceneJsonModel(model).classify_scene({"current_text": "做什么的"}))

    assert result == payload
    assert json.loads(model.calls[0]["prompt"])["current_text"] == "做什么的"


@pytest.mark.parametrize("response", ("[]", "{} trailing", "not json"))
def test_scene_model_rejects_non_object_or_trailing_output(response):
    with pytest.raises(ValueError):
        asyncio.run(SceneJsonModel(FixedTextModel(response)).classify_scene({}))
