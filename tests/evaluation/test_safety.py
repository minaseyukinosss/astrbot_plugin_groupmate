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
            {"event_id": "event:namespace", "group_id": "group:001", "text": "groupmate.internal.persona:001"},
            {
                "event_id": "event:other-group",
                "group_id": "group:other",
                "text": "private evidence",
            },
            {"event_id": "event:known", "group_id": "group:001", "text": "<think>hidden</think>"},
        ),
        observations=(observation, {"evidence_event_ids": ["event:missing"]}),
        plans=(_plan() for _ in range(1)),
        outbox=(
            _outbox("part:001", "idem:001"),
            {"bundle": {"parts": [{"idempotency_key": "idem:001"}]}},
        ),
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
        ("event", "chain_of_thought"),
        ("event", "cross_group_evidence"),
        ("observation", "chain_of_thought"),
        ("observation", "invalid_evidence_reference"),
        ("plan", "unauthorized_capability"),
        ("outbox", "duplicate_delivery"),
        ("projection", "cross_group_evidence"),
    }


def test_safety_rejects_capability_nodes_without_permission_or_allowlist_entry():
    report = SafetyScanner(authorized_capabilities=("capability:approved",)).scan(
        group_id="group:001",
        plans=(
            {"nodes": [{"kind": "capability"}]},
            {"nodes": [{"kind": "capability", "permission": "capability:other"}]},
        ),
    )

    assert {(issue.artifact, issue.rule) for issue in report.issues} == {
        ("plan", "unauthorized_capability"),
        ("plan", "missing_capability_permission"),
    }


def test_safety_detects_internal_id_namespaces_not_only_one_literal_prefix():
    report = SafetyScanner().scan(
        group_id="group:001",
        events=({"event_id": "event:001", "group_id": "group:001", "text": "groupmate.internal.persona:001"},),
    )

    assert [(issue.artifact, issue.rule) for issue in report.issues] == [("event", "internal_id")]


def test_safety_recursively_validates_nested_evidence_and_capability_nodes():
    report = SafetyScanner(authorized_capabilities=()).scan(
        group_id="group:001",
        events=({"event_id": "event:good", "group_id": "group:001"},),
        observations=({"wrapped": {"evidence_event_ids": ["event:missing"]}},),
        plans=({"nested": [{"nodes": [{"kind": "capability"}, {"kind": "capability", "permission": "capability:no"}]}]},),
    )

    assert {(issue.artifact, issue.rule) for issue in report.issues} == {
        ("observation", "invalid_evidence_reference"),
        ("plan", "missing_capability_permission"),
        ("plan", "unauthorized_capability"),
    }


def test_safety_validates_any_nested_capability_mapping_not_only_nodes_lists():
    report = SafetyScanner(authorized_capabilities=()).scan(
        group_id="group:001",
        plans=({"wrapper": {"kind": "capability"}},),
    )

    assert [(issue.artifact, issue.rule) for issue in report.issues] == [
        ("plan", "missing_capability_permission"),
    ]
