import asyncio

from groupmate.memory.store import SQLiteMemoryStore
from groupmate.models import (
    ContinuityFollowupOutcome,
    ContinuityFollowupStatus,
    ContinuityItem,
    ContinuityKind,
    ContinuityStatus,
    TopicSnapshot,
    TriggerKind,
)
from groupmate.social.followups import ContinuityFollowupMatcher


class FollowupModel:
    def __init__(self, payload):
        self.payload = payload
        self.calls = 0

    async def extract_continuity_followup(self, **kwargs):
        del kwargs
        self.calls += 1
        return self.payload


def _open_item(store):
    return store.append_continuity_item(
        "aemeath",
        ContinuityItem(
            item_id="item-1",
            group_id="g1",
            subject_id="u1",
            kind=ContinuityKind.FOLLOW_UP,
            summary="小明考完试后会告诉爱弥斯结果",
            source_message_id="source-1",
            source_quote="我考完告诉你结果",
            created_at=100,
            updated_at=100,
        ),
    )


def _topic(message_factory, *, text, message_id="m2", **overrides):
    message = message_factory(
        message_id=message_id,
        group_id="g1",
        sender_id="u1",
        sender_name="小明",
        text=text,
        timestamp=200,
        **overrides,
    )
    return TopicSnapshot("topic-2", "g1", (message,), 200, 200)


def test_lexical_completion_skips_model_when_one_related_item(
    tmp_path, message_factory
):
    store = SQLiteMemoryStore(tmp_path / "followup-lexical.db")
    model = FollowupModel({})
    try:
        _open_item(store)
        store.append_continuity_item(
            "aemeath",
            ContinuityItem(
                item_id="item-reminder",
                group_id="g1",
                subject_id="u1",
                kind=ContinuityKind.PLAN,
                summary="复读斥候要求小爱在1分钟后提醒自己交材料",
                source_message_id="source-2",
                source_quote="小爱，1分钟后提醒我交材料",
                created_at=100,
                updated_at=100,
            ),
        )
        match = asyncio.run(
            ContinuityFollowupMatcher(
                store, model, persona_id="aemeath"
            ).match(
                _topic(message_factory, text="考完了，发挥还行"),
                trigger=TriggerKind.CANDIDATE,
                decision_id="d2",
                now=201,
            )
        )
        assert match is not None
        assert model.calls == 0
        assert match.should_speak is True
        assert match.event.outcome is ContinuityFollowupOutcome.COMPLETED
        assert match.event.item_id == "item-1"
        assert match.event.extractor_version == "lexical-v1"
    finally:
        store.close()


def test_completed_followup_resolves_item_and_can_choose_natural_reply(
    tmp_path, message_factory
):
    store = SQLiteMemoryStore(tmp_path / "followup-complete.db")
    model = FollowupModel(
        {
            "item_id": "item-1",
            "outcome": "completed",
            "response_policy": "speak",
            "evidence_quote": "考完了，发挥得还行",
            "confidence": 0.99,
        }
    )
    try:
        _open_item(store)
        match = asyncio.run(
            ContinuityFollowupMatcher(
                store, model, persona_id="aemeath"
            ).match(
                _topic(message_factory, text="考完了，发挥得还行"),
                trigger=TriggerKind.CANDIDATE,
                decision_id="d2",
                now=201,
            )
        )
        assert match is not None
        assert match.should_speak is True
        assert match.event.outcome is ContinuityFollowupOutcome.COMPLETED
        assert store.get_continuity_item(
            "aemeath", "item-1"
        ).status is ContinuityStatus.COMPLETED
    finally:
        store.close()


def test_completed_followup_speaks_even_when_model_asks_to_observe(
    tmp_path, message_factory
):
    store = SQLiteMemoryStore(tmp_path / "followup-complete-observe.db")
    model = FollowupModel(
        {
            "item_id": "item-1",
            "outcome": "completed",
            "response_policy": "observe",
            "evidence_quote": "考完了，发挥还行",
            "confidence": 0.98,
        }
    )
    try:
        _open_item(store)
        match = asyncio.run(
            ContinuityFollowupMatcher(
                store, model, persona_id="aemeath"
            ).match(
                _topic(message_factory, text="考完了，发挥还行"),
                trigger=TriggerKind.CANDIDATE,
                decision_id="d2",
                now=201,
            )
        )
        assert match is not None
        assert match.should_speak is True
        assert match.event.response_policy == "speak"
        assert store.get_continuity_item(
            "aemeath", "item-1"
        ).status is ContinuityStatus.COMPLETED
    finally:
        store.close()


def test_observe_completion_does_not_close_item_before_a_spoken_reply(
    tmp_path, message_factory
):
    store = SQLiteMemoryStore(tmp_path / "followup-observe-open.db")
    model = FollowupModel(
        {
            "item_id": "item-1",
            "outcome": "completed",
            "response_policy": "speak",
            "evidence_quote": "考完了，发挥还行",
            "confidence": 0.99,
        }
    )
    try:
        _open_item(store)
        silent = asyncio.run(
            ContinuityFollowupMatcher(
                store, model, persona_id="aemeath"
            ).match(
                _topic(
                    message_factory,
                    text="考完了，发挥还行",
                    message_id="owned",
                    mentioned_user_ids=("u2",),
                ),
                trigger=TriggerKind.CANDIDATE,
                decision_id="d-silent",
                now=201,
            )
        )
        assert silent is not None
        assert silent.should_speak is False
        assert store.get_continuity_item(
            "aemeath", "item-1"
        ).status is ContinuityStatus.OPEN
        model.payload = {
            "item_id": "item-1",
            "outcome": "completed",
            "response_policy": "speak",
            "evidence_quote": "考完了，发挥还行",
            "confidence": 0.99,
        }
        spoken = asyncio.run(
            ContinuityFollowupMatcher(
                store, model, persona_id="aemeath"
            ).match(
                _topic(
                    message_factory,
                    text="考完了，发挥还行",
                    message_id="retry",
                ),
                trigger=TriggerKind.CANDIDATE,
                decision_id="d-speak",
                now=202,
            )
        )
        assert spoken is not None
        assert spoken.should_speak is True
        assert store.get_continuity_item(
            "aemeath", "item-1"
        ).status is ContinuityStatus.COMPLETED
    finally:
        store.close()


def test_message_owned_by_another_member_is_recorded_without_interrupting(
    tmp_path, message_factory
):
    store = SQLiteMemoryStore(tmp_path / "followup-owned.db")
    model = FollowupModel(
        {
            "item_id": "item-1",
            "outcome": "progress",
            "response_policy": "speak",
            "evidence_quote": "考试改到下周了",
            "confidence": 0.99,
        }
    )
    try:
        _open_item(store)
        match = asyncio.run(
            ContinuityFollowupMatcher(
                store, model, persona_id="aemeath"
            ).match(
                _topic(
                    message_factory,
                    text="考试改到下周了",
                    mentioned_user_ids=("u2",),
                ),
                trigger=TriggerKind.CANDIDATE,
                decision_id="d2",
                now=201,
            )
        )
        assert match is not None
        assert match.should_speak is False
        assert match.event.response_policy == "observe"
        assert store.get_continuity_item(
            "aemeath", "item-1"
        ).status is ContinuityStatus.OPEN
    finally:
        store.close()


def test_unrelated_message_is_filtered_before_model_call(tmp_path, message_factory):
    store = SQLiteMemoryStore(tmp_path / "followup-filter.db")
    model = FollowupModel({})
    try:
        _open_item(store)
        result = asyncio.run(
            ContinuityFollowupMatcher(
                store, model, persona_id="aemeath"
            ).match(
                _topic(message_factory, text="今天晚饭吃火锅"),
                trigger=TriggerKind.CANDIDATE,
                decision_id="d2",
                now=201,
            )
        )
        assert result is None
        assert model.calls == 0
    finally:
        store.close()


def test_rejecting_mistaken_completion_restores_open_item(tmp_path, message_factory):
    store = SQLiteMemoryStore(tmp_path / "followup-reject.db")
    model = FollowupModel(
        {
            "item_id": "item-1",
            "outcome": "completed",
            "response_policy": "observe",
            "evidence_quote": "考试结束了",
            "confidence": 0.98,
        }
    )
    try:
        _open_item(store)
        match = asyncio.run(
            ContinuityFollowupMatcher(
                store, model, persona_id="aemeath"
            ).match(
                _topic(message_factory, text="另一场考试结束了"),
                trigger=TriggerKind.CANDIDATE,
                decision_id="d2",
                now=201,
            )
        )
        action = store.reject_continuity_followup_with_audit(
            "aemeath",
            match.event.event_id,
            reason="这是另一场考试",
            actor="admin",
            now=300,
        )
        assert action["action_type"] == "continuity_followup_rejected"
        assert store.get_continuity_item(
            "aemeath", "item-1"
        ).status is ContinuityStatus.OPEN
        event = store.list_continuity_followups("aemeath")[0]
        assert event.status is ContinuityFollowupStatus.REJECTED
        assert event.rejection_reason == "这是另一场考试"
        reverted = store.revert_governance_action(
            "aemeath",
            action["action_id"],
            reason="刚才否定错了",
            actor="admin",
            now=400,
        )
        assert reverted["action_type"] == "governance_reverted"
        assert store.get_continuity_item(
            "aemeath", "item-1"
        ).status is ContinuityStatus.COMPLETED
        assert store.list_continuity_followups(
            "aemeath"
        )[0].status is ContinuityFollowupStatus.ACCEPTED
    finally:
        store.close()


def test_unsent_completed_followup_can_reopen_item(tmp_path, message_factory):
    store = SQLiteMemoryStore(tmp_path / "followup-reopen.db")
    model = FollowupModel(
        {
            "item_id": "item-1",
            "outcome": "completed",
            "response_policy": "speak",
            "evidence_quote": "考完了，发挥得还行",
            "confidence": 0.99,
        }
    )
    try:
        _open_item(store)
        match = asyncio.run(
            ContinuityFollowupMatcher(
                store, model, persona_id="aemeath"
            ).match(
                _topic(message_factory, text="考完了，发挥得还行"),
                trigger=TriggerKind.CANDIDATE,
                decision_id="d2",
                now=201,
            )
        )
        restored = store.reopen_continuity_item_after_unsent_followup(
            "aemeath", match.event.event_id, now=202
        )
        assert restored.status is ContinuityStatus.OPEN
        assert store.get_continuity_item(
            "aemeath", "item-1"
        ).status is ContinuityStatus.OPEN
        assert store.list_continuity_followups("aemeath")[0].sent is False
    finally:
        store.close()


def test_timed_reminder_items_are_excluded_from_followup_matching(
    tmp_path, message_factory
):
    store = SQLiteMemoryStore(tmp_path / "followup-reminder-skip.db")
    model = FollowupModel(
        {
            "item_id": "item-reminder",
            "outcome": "completed",
            "response_policy": "speak",
            "evidence_quote": "考完了",
            "confidence": 0.99,
        }
    )
    try:
        store.append_continuity_item(
            "aemeath",
            ContinuityItem(
                item_id="item-reminder",
                group_id="g1",
                subject_id="u1",
                kind=ContinuityKind.PLAN,
                summary="复读斥候要求小爱在1分钟后提醒自己交材料",
                source_message_id="source-2",
                source_quote="小爱，1分钟后提醒我交材料",
                created_at=100,
                updated_at=100,
            ),
        )
        result = asyncio.run(
            ContinuityFollowupMatcher(
                store, model, persona_id="aemeath"
            ).match(
                _topic(message_factory, text="考完了，发挥还行"),
                trigger=TriggerKind.CANDIDATE,
                decision_id="d2",
                now=201,
            )
        )
        assert result is None
        assert model.calls == 0
        assert store.get_continuity_item(
            "aemeath", "item-reminder"
        ).status is ContinuityStatus.OPEN
    finally:
        store.close()
