import asyncio

from groupmate.capabilities import CapabilityResult, CapabilityStatus
from groupmate.core.addressee import AddresseeResolver
from groupmate.models import (
    SelfCommitment,
    SelfCommitmentStatus,
    TopicSnapshot,
    TriggerKind,
)
from groupmate.social.commitments import SelfCommitmentWriter
from groupmate.memory.store import SQLiteMemoryStore


class CommitmentModel:
    def __init__(self, payload):
        self.payload = payload

    async def extract_self_commitment(self, **kwargs):
        del kwargs
        return self.payload


def direct_targeting(topic):
    return AddresseeResolver().resolve(
        topic, TriggerKind.ALIAS_DIRECT, aliases=("爱弥斯",)
    )


def topic_for(message_factory, text="爱弥斯，帮我记一下"):
    return TopicSnapshot(
        "t1",
        "g",
        (
            message_factory(
                message_id="m1",
                sender_id="u1",
                sender_name="小明",
                text=text,
                timestamp=100,
                mentions_bot=True,
            ),
        ),
        100,
        100,
    )


def test_self_commitment_opens_from_delivered_source_quote(tmp_path, message_factory):
    store = SQLiteMemoryStore(tmp_path / "self-commitment.db")
    try:
        topic = topic_for(message_factory)
        writer = SelfCommitmentWriter(
            store,
            CommitmentModel(
                {
                    "action": "OPEN",
                    "summary": "爱弥斯会记住小明周五交材料",
                    "evidence_quote": "我会记着你周五交材料",
                    "required_capability": "",
                    "confidence": 0.97,
                }
            ),
            persona_id="aemeath",
        )
        item = asyncio.run(
            writer.process(
                topic,
                direct_targeting(topic),
                decision_id="d1",
                now=101,
                reply_text="好，我会记着你周五交材料。",
            )
        )
        assert item is not None
        assert item.status is SelfCommitmentStatus.PENDING
        assert item.beneficiary_subject_id == "u1"
    finally:
        store.close()


def test_capability_backed_commitment_completes_only_with_verified_result(
    tmp_path, message_factory
):
    store = SQLiteMemoryStore(tmp_path / "self-commitment-capability.db")
    try:
        topic = topic_for(message_factory, "帮我看看图里是什么")
        payload = {
            "action": "OPEN",
            "summary": "爱弥斯查看图片内容",
            "evidence_quote": "我现在帮你看图",
            "required_capability": "vision",
            "confidence": 0.98,
        }
        writer = SelfCommitmentWriter(
            store, CommitmentModel(payload), persona_id="aemeath"
        )
        completed = asyncio.run(
            writer.process(
                topic,
                direct_targeting(topic),
                decision_id="d1",
                now=101,
                reply_text="我现在帮你看图，图里是一盆花。",
                capability_result=CapabilityResult(
                    CapabilityStatus.SUCCESS,
                    "vision",
                    facts=("图片里是一盆花",),
                ),
            )
        )
        assert completed.status is SelfCommitmentStatus.COMPLETED
        assert completed.result_facts == ("图片里是一盆花",)

        blocked = asyncio.run(
            writer.process(
                topic,
                direct_targeting(topic),
                decision_id="d2",
                now=102,
                reply_text="我现在帮你看图。",
                capability_result=None,
            )
        )
        assert blocked.status is SelfCommitmentStatus.BLOCKED
        assert blocked.failure_code == "capability_not_executed"
    finally:
        store.close()


def test_invalid_capability_name_does_not_open_commitment(
    tmp_path, message_factory
):
    store = SQLiteMemoryStore(tmp_path / "self-commitment-invalid-capability.db")
    try:
        topic = topic_for(message_factory, "帮我查一下")
        writer = SelfCommitmentWriter(
            store,
            CommitmentModel(
                {
                    "action": "OPEN",
                    "summary": "爱弥斯查看结果",
                    "evidence_quote": "我现在帮你查",
                    "required_capability": "帮我查看",
                    "confidence": 0.99,
                }
            ),
            persona_id="aemeath",
        )
        item = asyncio.run(
            writer.process(
                topic,
                direct_targeting(topic),
                decision_id="d1",
                now=101,
                reply_text="我现在帮你查。",
            )
        )
        assert item is None
        assert store.list_self_commitments("aemeath") == []
    finally:
        store.close()


def test_bot_authored_trigger_does_not_open_commitment(tmp_path, message_factory):
    store = SQLiteMemoryStore(tmp_path / "self-commitment-bot-trigger.db")
    try:
        topic = TopicSnapshot(
            "t1",
            "g",
            (
                message_factory(
                    message_id="m1",
                    sender_id="bot-other",
                    sender_name="其他机器人",
                    text="爱弥斯，帮我记一下",
                    timestamp=100,
                    mentions_bot=True,
                    is_bot=True,
                ),
            ),
            100,
            100,
        )
        writer = SelfCommitmentWriter(
            store,
            CommitmentModel(
                {
                    "action": "OPEN",
                    "summary": "爱弥斯会记住",
                    "evidence_quote": "我会记着",
                    "required_capability": "",
                    "confidence": 0.99,
                }
            ),
            persona_id="aemeath",
        )
        item = asyncio.run(
            writer.process(
                topic,
                direct_targeting(topic),
                decision_id="d1",
                now=101,
                reply_text="我会记着。",
            )
        )
        assert item is None
    finally:
        store.close()


def test_existing_self_commitment_can_complete_from_later_reply(
    tmp_path, message_factory
):
    store = SQLiteMemoryStore(tmp_path / "self-commitment-complete.db")
    try:
        store.append_self_commitment(
            "aemeath",
            SelfCommitment(
                commitment_id="c1",
                group_id="g",
                beneficiary_subject_id="u1",
                summary="爱弥斯之后告诉小明结果",
                source_decision_id="d0",
                source_message_id="m0",
                source_quote="有结果我告诉你",
                created_at=50,
                updated_at=50,
            ),
        )
        topic = topic_for(message_factory, "有结果了吗")
        writer = SelfCommitmentWriter(
            store,
            CommitmentModel(
                {
                    "action": "COMPLETE",
                    "commitment_id": "c1",
                    "summary": "",
                    "evidence_quote": "结果出来了",
                    "confidence": 0.96,
                }
            ),
            persona_id="aemeath",
        )
        completed = asyncio.run(
            writer.process(
                topic,
                direct_targeting(topic),
                decision_id="d1",
                now=101,
                reply_text="结果出来了，已经确认通过。",
            )
        )
        assert completed.status is SelfCommitmentStatus.COMPLETED
        assert completed.result_quote == "结果出来了"
    finally:
        store.close()


def test_self_commitment_governance_is_revertible(tmp_path):
    store = SQLiteMemoryStore(tmp_path / "self-commitment-governance.db")
    try:
        store.append_self_commitment(
            "aemeath",
            SelfCommitment(
                commitment_id="c1",
                group_id="g",
                beneficiary_subject_id="u1",
                summary="爱弥斯之后回复结果",
                source_decision_id="d0",
                source_message_id="m0",
                source_quote="我之后回复你",
                created_at=50,
                updated_at=50,
            ),
        )
        action = store.update_self_commitment_with_audit(
            "aemeath",
            "c1",
            status=SelfCommitmentStatus.WITHDRAWN,
            reason="管理员确认无法继续",
            actor="admin",
            now=100,
        )
        assert action["can_revert"] is True
        assert store.get_self_commitment(
            "aemeath", "c1"
        ).status is SelfCommitmentStatus.WITHDRAWN
        assert store.get_self_commitment(
            "aemeath", "c1"
        ).result_quote == "管理员修正：管理员确认无法继续"
        store.revert_governance_action(
            "aemeath",
            action["action_id"],
            reason="恢复误操作",
            actor="admin",
            now=120,
        )
        assert store.get_self_commitment(
            "aemeath", "c1"
        ).status is SelfCommitmentStatus.PENDING
    finally:
        store.close()
