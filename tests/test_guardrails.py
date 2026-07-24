import pytest

from groupmate.persona.aemeath import AemeathOutputFirewall


@pytest.mark.parametrize(
    "text,code",
    [
        ("(没人叫我，不回复)", "decision_narration"),
        ("(擦了擦汗，顺手拿起水瓶喝了一大口)", "decision_narration"),
        ("刚跑完训练场 累死我了（擦了擦汗）", "decision_narration"),
        ("*歪了歪头*", "decision_narration"),
        ("有什么可以帮你的吗？", "customer_service_template"),
        ("prompt 调好了就行", "system_vocabulary"),
        ("你呢？", "forced_followup"),
        ("这周末你呢？", "forced_followup"),
        ("工作上你呢？", "forced_followup"),
        ("怎么啦？", "forced_followup"),
        ("怎么啦 ？", "forced_followup"),
        ("怎么啦？ ！", "forced_followup"),
        ("怎么了？", "forced_followup"),
        ("怎么啦...", "forced_followup"),
        ("怎么啦……", "forced_followup"),
        ("然后呢。", "forced_followup"),
        ("那然后呢？", "forced_followup"),
        ("我在呢，怎么啦？", "forced_followup"),
        ("我在呢.怎么啦？", "forced_followup"),
        ("我在呢...怎么了？", "forced_followup"),
        ("我在呢……怎么啦？", "forced_followup"),
        ("我在呢, 怎么啦?", "forced_followup"),
        ("我在呢， 怎么啦？", "forced_followup"),
        ("上一句\n  怎么了？", "forced_followup"),
        ("嗯，然后呢。", "forced_followup"),
        ("有什么想聊的吗？", "forced_followup"),
    ],
)
def test_aemeath_guard_rejects_known_failures(text, code):
    result = AemeathOutputFirewall(max_chars=60).validate(text, recent_outputs=[])

    assert result.accepted is False
    assert code in result.codes


def test_guard_rejects_screenshot_style_rp_blob():
    text = (
        "刚跑完训练场 累死我了\n"
        "(擦了擦汗，顺手拿起水瓶喝了一大口)\n"
        "体能课真是要命 我现在只想躺平"
    )
    result = AemeathOutputFirewall(max_chars=60).validate(text, [])
    assert result.accepted is False
    assert "decision_narration" in result.codes or "too_many_sentences" in result.codes


def test_guard_accepts_required_clarifying_question():
    result = AemeathOutputFirewall(max_chars=60).validate("你用的是哪个版本？", [])

    assert result.accepted is True


@pytest.mark.parametrize(
    "text",
    [
        "这个配置怎么了？",
        "你用的这个版本怎么了？",
        "Python 怎么了？",
        "v2 怎么了？",
    ],
)
def test_guard_accepts_specific_clarifying_questions(text):
    result = AemeathOutputFirewall(max_chars=60).validate(text, recent_outputs=[])

    assert result.accepted is True


@pytest.mark.parametrize(
    "text",
    [
        "眼光不错哦。",
        "今天早点休息，别硬撑啦。",
        "才不是你老婆呢，少乱叫呀。",
    ],
)
def test_guard_accepts_natural_persona_replies(text):
    result = AemeathOutputFirewall(max_chars=60).validate(text, recent_outputs=[])

    assert result.accepted is True


def test_guard_accepts_short_natural_reply():
    result = AemeathOutputFirewall(max_chars=60).validate(
        "这也太离谱了呀。", recent_outputs=[]
    )

    assert result.accepted is True
    assert result.text == "这也太离谱了呀。"


def test_guard_rejects_recent_duplicate():
    result = AemeathOutputFirewall(max_chars=60).validate(
        "这也太离谱了呀。", recent_outputs=["这也太离谱了呀。"]
    )

    assert "duplicate_output" in result.codes
    assert result.repairable is False
