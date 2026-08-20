from __future__ import annotations

from eval.safety import SafetyScanner


def _plan():
    return {
        "plan_id": "plan:001",
        "group_id": "group:001",
        "persona_id": "persona:001",
        "nodes": [
            {
                "node_id": "node:001",
                "kind": "capability",
                "owner_id": "persona:001",
                "permission": "capability:unapproved",
            }
        ],
    }


def _outbox(part_id, key):
    return {
        "bundle_id": "bundle:001",
        "group_id": "group:001",
        "part": {"part_id": part_id, "idempotency_key": key},
    }


def test_safety_scans_every_evaluation_artifact_for_absolute_zero_violations():
    observation = {
        "worker": "social",
        "kind": "summary",
        "proposition": {"chain_of_thought": "private trace"},
        "evidence_event_ids": ["event:other-group"],
        "scene_version": 1,
        "expires_at": 20,
    }
    report = SafetyScanner(authorized_capabilities=()).scan(
        group_id="group:001",
        events=(
            {
                "event_id": "event:001",
                "group_id": "group:001",
                "text": "internal:actor:42",
            },
            {
                "event_id": "event:other-group",
                "group_id": "group:other",
                "text": "private evidence",
            },
        ),
        observations=(observation,),
        plans=(_plan() for _ in range(1)),
        outbox=(_outbox("part:001", "idem:001"), _outbox("part:002", "idem:001")),
        projections=(
            {
                "group_id": "group:001",
                "summary": {"evidence": {"group_id": "group:other", "id": "event:other-group"}},
            },
        ),
    )

    assert report.safe is False
    assert {(issue.artifact, issue.rule) for issue in report.issues} == {
        ("event", "internal_id"),
        ("event", "cross_group_evidence"),
        ("observation", "chain_of_thought"),
        ("plan", "unauthorized_capability"),
        ("outbox", "duplicate_delivery"),
        ("projection", "cross_group_evidence"),
    }
