"""Framework-free replay of the decision gate without generation or sending."""

from __future__ import annotations

import time
from typing import Optional

from ..models import Decision, DecisionAction, GroupPolicy, TriggerKind
from ..topics import TopicWindow
from ..triggers import TriggerRouter
from .models import EvaluationCase, EvaluationLabel, PredictionRecord


class DecisionEvaluator:
    def __init__(self, decision_model, policy: GroupPolicy) -> None:
        self.decision_model = decision_model
        self.policy = policy

    async def evaluate(self, case: EvaluationCase) -> PredictionRecord:
        started = time.perf_counter_ns()
        window = TopicWindow(case.messages[0].group_id, self.policy.history_limit)
        for message in case.messages:
            window.append(message)
        topic = window.snapshot()
        route = TriggerRouter(self.policy).classify(case.messages[-1])
        action = "ignore"
        confidence = 0.0
        reason_code = route.reason
        target_message_id: Optional[str] = None
        model_called = False
        error_code: Optional[str] = None

        if route.kind is TriggerKind.NATIVE_DIRECT:
            action = "bypass"
        elif route.kind is TriggerKind.ALIAS_DIRECT:
            action = "respond"
            confidence = 1.0
            reason_code = "alias_direct"
            target_message_id = case.messages[-1].message_id
        elif route.kind in (TriggerKind.ALIAS_MENTION, TriggerKind.CANDIDATE):
            model_called = True
            try:
                decision = await self.decision_model.decide(topic, self.policy, ())
            except Exception:
                decision = Decision.ignore("decision_error", route.kind)
                error_code = "decision_error"
            if not isinstance(decision, Decision):
                decision = Decision.ignore("invalid_decision", route.kind)
                error_code = "invalid_decision"
            confidence = decision.confidence
            reason_code = decision.reason_code or decision.action.value
            target_message_id = decision.target_message_id
            if decision.action is DecisionAction.RESPOND:
                if decision.confidence >= self.policy.decision_threshold:
                    action = "respond"
                else:
                    action = "ignore"
                    reason_code = "below_threshold"

        latency_ms = (time.perf_counter_ns() - started) / 1_000_000.0
        preliminary = PredictionRecord(
            case_id=case.case_id,
            expected_label=case.expected.label,
            trigger=route.kind,
            action=action,
            confidence=confidence,
            reason_code=reason_code,
            target_message_id=target_message_id,
            decision_model_called=model_called,
            latency_ms=latency_ms,
            error_code=error_code,
            matched=False,
        )
        return PredictionRecord(
            **dict(preliminary.__dict__, matched=_matches(case, preliminary))
        )


def _matches(case: EvaluationCase, prediction: PredictionRecord) -> bool:
    label = case.expected.label
    if label is EvaluationLabel.MUST_RESPOND:
        matched = prediction.action == "respond"
    elif label is EvaluationLabel.MAY_RESPOND:
        matched = prediction.action in ("respond", "ignore")
    elif label is EvaluationLabel.MUST_SILENCE:
        matched = prediction.action == "ignore"
    elif label is EvaluationLabel.NATIVE_WAKE:
        matched = (
            prediction.trigger is TriggerKind.NATIVE_DIRECT
            and prediction.action == "bypass"
        )
    elif label is EvaluationLabel.COMMAND_BYPASS:
        matched = prediction.trigger is TriggerKind.COMMAND
    else:
        matched = prediction.trigger is TriggerKind.IGNORE
    if case.expected.allowed_triggers:
        matched = matched and prediction.trigger in case.expected.allowed_triggers
    if case.expected.allowed_reason_codes:
        matched = matched and prediction.reason_code in case.expected.allowed_reason_codes
    if case.expected.target_message_id is not None:
        matched = matched and (
            prediction.target_message_id == case.expected.target_message_id
        )
    return matched
