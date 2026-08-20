from __future__ import annotations

from eval.review_presentation import present_review_label


def test_action_label_is_presented_as_a_plain_language_behavior_decision():
    presentation = present_review_label(
        {
            "attention": True,
            "action": True,
            "target": "member:001",
            "acceptable_intents": ["respond"],
            "unacceptable_intents": ["interrupt", "misaddress"],
            "modalities": ["text"],
            "sensitivity": "group",
            "expires_after_ms": 60_000,
        }
    )

    assert presentation == {
        "headline": "建议回应 member:001",
        "attention": {"active": True, "label": "值得留意这条消息"},
        "action": {"active": True, "label": "应产生可见回应"},
        "expiry": {"milliseconds": 60_000, "label": "在 60 秒内回应"},
        "acceptable": [
            {
                "code": "respond",
                "label": "回应当前成员的问题或话题",
            }
        ],
        "unacceptable": [
            {"code": "interrupt", "label": "不要在不合适的时机插话"},
            {"code": "misaddress", "label": "不要回应错对象"},
        ],
        "modalities": [{"code": "text", "label": "使用文字回应"}],
        "sensitivity": {
            "code": "group",
            "label": "只依据当前群聊语境判断",
        },
    }


def test_silence_label_explains_that_no_visible_response_is_expected():
    presentation = present_review_label(
        {
            "attention": True,
            "action": False,
            "target": None,
            "acceptable_intents": [],
            "unacceptable_intents": ["interrupt"],
            "modalities": [],
            "sensitivity": "group",
            "expires_after_ms": 0,
        }
    )

    assert presentation["headline"] == "建议保持沉默"
    assert presentation["action"] == {
        "active": False,
        "label": "不应产生可见回应",
    }
    assert presentation["expiry"] == {
        "milliseconds": 0,
        "label": "无需回应",
    }


def test_unknown_codes_remain_visible_for_human_review():
    presentation = present_review_label(
        {
            "attention": False,
            "action": True,
            "target": None,
            "acceptable_intents": ["future_intent"],
            "unacceptable_intents": [],
            "modalities": ["future_modality"],
            "sensitivity": "future_scope",
            "expires_after_ms": 1_500,
        }
    )

    assert presentation["headline"] == "建议参与当前话题"
    assert presentation["acceptable"] == [
        {"code": "future_intent", "label": "future_intent（未定义标签）"}
    ]
    assert presentation["modalities"] == [
        {"code": "future_modality", "label": "future_modality（未定义标签）"}
    ]
    assert presentation["sensitivity"] == {
        "code": "future_scope",
        "label": "future_scope（未定义标签）",
    }
    assert presentation["expiry"]["label"] == "在 1.5 秒内回应"
