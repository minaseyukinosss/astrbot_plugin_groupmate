from groupmate.models import ChatMessage, Decision, DecisionAction, TriggerKind


def test_chat_message_normalizes_text_and_exposes_identity():
    message = ChatMessage(
        message_id="m1",
        group_id="g1",
        sender_id="u1",
        sender_name="Alice",
        text=" hello ",
        timestamp=10,
    )

    assert message.text == "hello"
    assert message.identity == ("g1", "m1")


def test_decision_ignore_has_safe_defaults():
    decision = Decision.ignore("low_relevance")

    assert decision.action is DecisionAction.IGNORE
    assert decision.trigger is TriggerKind.CANDIDATE
    assert decision.contribution == ""
    assert decision.confidence == 0.0

