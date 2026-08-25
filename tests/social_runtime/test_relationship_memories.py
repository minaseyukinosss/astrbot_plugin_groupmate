from __future__ import annotations

from dataclasses import replace

from groupmate.social_runtime.memory.relationship_memory import (
    RelationshipMemory,
    RelationshipMemorySelector,
)
from groupmate.social_runtime.persistence.repositories import (
    SQLiteSocietyRepository,
)
from groupmate.social_runtime.society.relationship_events import (
    RelationshipEventProposal,
    RelationshipEventService,
)
from groupmate.social_runtime.society.relationships import RelationshipStage


def _proposal(**overrides):
    values = {
        "event_id": "relationship:boundary-1",
        "persona_id": "p",
        "group_id": "g",
        "subject_id": "u",
        "kind": "boundary_pressure",
        "confidence": 0.96,
        "severity": "significant",
        "summary": "成员上次明确辱骂爱弥斯",
        "source_event_ids": ("message:e1",),
        "occurred_at": 100,
        "repair_of": None,
        "sensitivity": "normal",
    }
    values.update(overrides)
    return RelationshipEventProposal(**values)


def test_formal_relationship_event_persists_memory_but_shadow_does_not(tmp_path):
    repository = SQLiteSocietyRepository(tmp_path / "runtime.db")
    service = RelationshipEventService(repository)

    service.process(_proposal(), mode="SOCIAL_RUNTIME")
    service.process(
        _proposal(
            event_id="relationship:shadow-help",
            kind="reliable_help",
            severity="ordinary",
            summary="成员帮忙整理了报告",
        ),
        mode="SHADOW",
    )

    memories = repository.relationship_memories("p", "g", "u")
    assert [item.relationship_event_id for item in memories] == [
        "relationship:boundary-1"
    ]
    assert memories[0].resolved_at is None


def test_confirmed_repair_resolves_the_specific_boundary_memory(tmp_path):
    repository = SQLiteSocietyRepository(tmp_path / "runtime.db")
    service = RelationshipEventService(repository)
    service.process(_proposal(), mode="SOCIAL_RUNTIME")

    service.process(
        _proposal(
            event_id="relationship:repair-1",
            kind="repair_confirmed",
            severity="significant",
            summary="成员认真道歉并停止了冒犯",
            occurred_at=200,
            repair_of="relationship:boundary-1",
        ),
        mode="SOCIAL_RUNTIME",
    )

    memories = repository.relationship_memories("p", "g", "u")
    boundary = next(item for item in memories if item.kind == "boundary_pressure")
    assert boundary.resolved_at == 200
    assert boundary.resolved_by == "relationship:repair-1"


def test_selector_only_exposes_relevant_normal_unresolved_memory():
    base = RelationshipMemory(
        memory_id="memory:1",
        relationship_event_id="relationship:boundary-1",
        persona_id="p",
        group_id="g",
        subject_id="u",
        kind="boundary_pressure",
        summary="成员上次明确辱骂爱弥斯",
        sensitivity="normal",
        confidence=0.96,
        severity="significant",
        occurred_at=100,
    )
    selector = RelationshipMemorySelector()

    relevant = selector.select(
        (base,),
        text="现在倒是陪我聊聊天啊",
        stage=RelationshipStage.GUARDED,
        now=300,
    )
    unrelated = selector.select(
        (base,),
        text="今天天气不错",
        stage=RelationshipStage.GUARDED,
        now=300,
    )
    hidden = selector.select(
        (replace(base, sensitivity="restricted"),),
        text="陪我聊天",
        stage=RelationshipStage.GUARDED,
        now=300,
    )
    resolved = selector.select(
        (replace(base, resolved_at=250),),
        text="陪我聊天",
        stage=RelationshipStage.GUARDED,
        now=300,
    )

    assert relevant == ("未修复边界事件：成员上次明确辱骂爱弥斯",)
    assert unrelated == ()
    assert hidden == ()
    assert resolved == ()


def test_selector_recalls_positive_memory_only_when_topic_matches():
    memory = RelationshipMemory(
        memory_id="memory:help",
        relationship_event_id="relationship:help",
        persona_id="p",
        group_id="g",
        subject_id="u",
        kind="reliable_help",
        summary="成员帮忙整理了课程报告",
        sensitivity="normal",
        confidence=0.93,
        severity="ordinary",
        occurred_at=100,
    )

    cues = RelationshipMemorySelector().select(
        (memory,),
        text="还记得我帮你整理课程报告吗",
        stage=RelationshipStage.FAMILIAR,
        now=200,
    )

    assert cues == ("正向关系记忆：成员帮忙整理了课程报告",)
