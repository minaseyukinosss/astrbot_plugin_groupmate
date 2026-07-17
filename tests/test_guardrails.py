import pytest

from groupmate.guardrails import AemeathOutputGuard


@pytest.mark.parametrize(
    "text,code",
    [
        ("(没人叫我，不回复)", "decision_narration"),
        ("有什么可以帮你的吗？", "customer_service_template"),
        ("prompt 调好了就行", "system_vocabulary"),
    ],
)
def test_aemeath_guard_rejects_known_failures(text, code):
    result = AemeathOutputGuard(max_chars=60).validate(text, recent_outputs=[])

    assert result.accepted is False
    assert code in result.codes


def test_guard_accepts_short_natural_reply():
    result = AemeathOutputGuard(max_chars=60).validate(
        "这也太离谱了呀。", recent_outputs=[]
    )

    assert result.accepted is True
    assert result.text == "这也太离谱了呀。"


def test_guard_rejects_recent_duplicate():
    result = AemeathOutputGuard(max_chars=60).validate(
        "这也太离谱了呀。", recent_outputs=["这也太离谱了呀。"]
    )

    assert "duplicate_output" in result.codes
    assert result.repairable is False

