from __future__ import annotations

import json
import threading
from contextlib import contextmanager
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from eval.review import build_label_suggestions
from eval.review_cli import ReviewSession
from eval.review_server import create_review_server


def _write_jsonl(path, values):
    path.write_text(
        "".join(json.dumps(value, ensure_ascii=False) + "\n" for value in values),
        encoding="utf-8",
    )


def _session(tmp_path):
    queue = tmp_path / "queue.jsonl"
    suggestions = tmp_path / "suggestions.jsonl"
    decisions = tmp_path / "private" / "decisions.jsonl"
    _write_jsonl(
        queue,
        [
            {
                "scenario_id": "scenario:001",
                "split": "calibration",
                "status": "needs_human_review",
                "selection_signal": "historical_bot_action",
                "observable_tags": ["text", "direct_mention"],
                "group_id": "group:000001",
                "focus_event_id": "message:001",
                "context": [
                    {
                        "event_id": "message:001",
                        "group_id": "group:000001",
                        "actor_id": "member:001",
                        "text": "这条记录只用于本机测试",
                        "evidence_ref": "evidence:private",
                    }
                ],
                "label": None,
            }
        ],
    )
    build_label_suggestions(queue, output_path=suggestions)
    return ReviewSession(
        queue,
        suggestions,
        decisions_path=decisions,
        reviewer_id="admin:test",
        clock=lambda: 1_723_456_789,
    )


@contextmanager
def _running_server(session):
    server = create_review_server(session, host="127.0.0.1", port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def _request_json(base_url, path, payload=None):
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    request = Request(
        base_url + path,
        data=body,
        headers={"Content-Type": "application/json"} if body else {},
    )
    with urlopen(request, timeout=2) as response:
        return response.status, dict(response.headers), json.load(response)


def test_current_review_api_exposes_only_safe_context_and_progress(tmp_path):
    with _running_server(_session(tmp_path)) as base_url:
        status, headers, payload = _request_json(base_url, "/api/current")

    encoded = json.dumps(payload, ensure_ascii=False)
    assert status == 200
    assert headers["Cache-Control"] == "no-store"
    assert payload["progress"] == {
        "completed": 0,
        "insufficient": 0,
        "remaining": 1,
        "total": 1,
        "usable": 0,
    }
    assert payload["item"]["scenario_id"] == "scenario:001"
    assert payload["item"]["focus_event_id"] == "message:001"
    assert payload["item"]["suggestion"]["presentation"]["headline"] == (
        "建议回应 member:001"
    )
    assert "selection_signal" not in encoded
    assert "evidence_ref" not in encoded


def test_approval_requires_exact_scenario_confirmation(tmp_path):
    session = _session(tmp_path)
    with _running_server(session) as base_url:
        with pytest.raises(HTTPError) as error:
            _request_json(
                base_url,
                "/api/approve",
                {"scenario_id": "scenario:001", "confirmation": "wrong"},
            )
        assert error.value.code == 400
        assert session.next_pending() is not None

        status, _, payload = _request_json(
            base_url,
            "/api/approve",
            {
                "scenario_id": "scenario:001",
                "confirmation": "scenario:001",
            },
        )

    assert status == 200
    assert payload == {
        "item": None,
        "progress": {
            "completed": 1,
            "insufficient": 0,
            "remaining": 0,
            "total": 1,
            "usable": 1,
        },
    }
    assert session.decisions["scenario:001"]["decision"] == "approved"


def test_completed_scenario_cannot_be_overwritten_by_a_stale_page(tmp_path):
    session = _session(tmp_path)
    with _running_server(session) as base_url:
        _request_json(
            base_url,
            "/api/approve",
            {
                "scenario_id": "scenario:001",
                "confirmation": "scenario:001",
            },
        )
        with pytest.raises(HTTPError) as error:
            _request_json(
                base_url,
                "/api/correct",
                {
                    "scenario_id": "scenario:001",
                    "confirmation": "scenario:001",
                    "categories": ["correct_silence"],
                    "label": {
                        "attention": False,
                        "action": False,
                        "target": None,
                        "acceptable_intents": [],
                        "unacceptable_intents": ["interrupt"],
                        "modalities": [],
                        "sensitivity": "group",
                        "expires_after_ms": 0,
                    },
                },
            )

    assert error.value.code == 400
    assert session.decisions["scenario:001"]["decision"] == "approved"


def test_insufficient_evidence_endpoint_advances_without_creating_a_label(tmp_path):
    session = _session(tmp_path)
    with _running_server(session) as base_url:
        status, _, payload = _request_json(
            base_url,
            "/api/insufficient",
            {
                "scenario_id": "scenario:001",
                "confirmation": "scenario:001",
            },
        )

    assert status == 200
    assert payload == {
        "item": None,
        "progress": {
            "completed": 1,
            "insufficient": 1,
            "remaining": 0,
            "total": 1,
            "usable": 0,
        },
    }
    assert session.decisions["scenario:001"]["decision"] == (
        "insufficient_evidence"
    )
    assert session.decisions["scenario:001"]["label"] is None


def test_review_server_refuses_non_loopback_binding(tmp_path):
    with pytest.raises(ValueError, match="loopback"):
        create_review_server(_session(tmp_path), host="0.0.0.0", port=0)


def test_review_server_serves_the_submission_state_module(tmp_path):
    with _running_server(_session(tmp_path)) as base_url:
        with urlopen(base_url + "/review_state.mjs", timeout=2) as response:
            body = response.read().decode("utf-8")

    assert response.status == 200
    assert response.headers["Content-Type"] == "text/javascript; charset=utf-8"
    assert "deriveSubmissionState" in body
