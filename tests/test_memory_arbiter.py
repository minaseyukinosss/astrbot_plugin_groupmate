"""MemoryArbiter：权威、冲突、tombstone。"""

from __future__ import annotations

from groupmate.memory.arbiter import MemoryArbiter
from groupmate.memory.privacy import claim_hash
from groupmate.models import (
    CandidateStatus,
    MemoryCandidate,
    MemoryItem,
    MemoryKind,
    MemoryScope,
    MemoryStatus,
    Sensitivity,
)


def _candidate(claim: str, *, subject="u1", group="g1") -> MemoryCandidate:
    return MemoryCandidate(
        candidate_id="c1",
        group_id=group,
        scope=MemoryScope.USER_IN_GROUP,
        subject_id=subject,
        kind=MemoryKind.PROFILE,
        claim=claim,
        source_message_ids=("m1",),
        confidence=0.9,
        sensitivity=Sensitivity.NONE,
        proposed_expires_at=None,
        extractor_version="rules-v1",
        claim_hash=claim_hash(claim),
    )


def _memory(text: str, *, authority: int, memory_id="old") -> MemoryItem:
    return MemoryItem(
        memory_id=memory_id,
        group_id="g1",
        subject_id="u1",
        kind=MemoryKind.PROFILE,
        text=text,
        created_at=1,
        authority=authority,
        status=MemoryStatus.ACCEPTED,
        scope=MemoryScope.USER_IN_GROUP,
    )


def test_tombstone_rejects_candidate():
    decision = MemoryArbiter().decide(
        _candidate("我喜欢猫"),
        existing=[],
        has_tombstone=True,
        now=10,
        authority=8,
    )
    assert decision.status is CandidateStatus.REJECTED
    assert decision.reason == "tombstone_blocks_replay"


def test_lower_authority_does_not_overwrite():
    decision = MemoryArbiter().decide(
        _candidate("我喜欢橘猫"),
        existing=[_memory("我喜欢橘猫很久了", authority=9)],
        has_tombstone=False,
        now=10,
        authority=4,
    )
    assert decision.status is CandidateStatus.CONFLICTED
    assert decision.memory is None


def test_higher_authority_supersedes():
    decision = MemoryArbiter().decide(
        _candidate("我喜欢橘猫"),
        existing=[_memory("我喜欢橘猫很久了", authority=3, memory_id="m-old")],
        has_tombstone=False,
        now=10,
        authority=8,
    )
    assert decision.status is CandidateStatus.ACCEPTED
    assert decision.superseded_memory_id == "m-old"
    assert decision.memory is not None
    assert decision.memory.supersedes_memory_id == "m-old"


def test_sensitive_candidate_rejected():
    sensitive = MemoryCandidate(
        candidate_id="c2",
        group_id="g1",
        scope=MemoryScope.USER_IN_GROUP,
        subject_id="u1",
        kind=MemoryKind.PROFILE,
        claim="密码是123456",
        source_message_ids=("m1",),
        confidence=0.9,
        sensitivity=Sensitivity.CREDENTIAL,
        proposed_expires_at=None,
        extractor_version="rules-v1",
        claim_hash=claim_hash("密码是123456"),
    )
    decision = MemoryArbiter().decide(
        sensitive,
        existing=[],
        has_tombstone=False,
        now=10,
        authority=8,
    )
    assert decision.status is CandidateStatus.REJECTED
    assert decision.reason.startswith("sensitivity:")
