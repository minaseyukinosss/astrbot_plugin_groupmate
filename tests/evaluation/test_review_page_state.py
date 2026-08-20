from __future__ import annotations

import json
import subprocess
from pathlib import Path


_STATE_MODULE = (
    Path(__file__).parents[2] / "eval" / "review_page" / "review_state.mjs"
)


def _submission_state(**values):
    script = (
        f'import {{ deriveSubmissionState }} from {json.dumps(_STATE_MODULE.as_uri())};'
        f"process.stdout.write(JSON.stringify(deriveSubmissionState({json.dumps(values)})));"
    )
    result = subprocess.run(
        ["node", "--input-type=module", "--eval", script],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout)


def test_incomplete_suggestion_exposes_category_completion_instead_of_approval():
    state = _submission_state(
        busy=False,
        confirmed=True,
        suggested_category_count=0,
        manual_category_count=0,
        corrected_category_count=0,
    )

    assert state == {
        "canApprove": False,
        "canCompleteCategories": False,
        "canCorrect": False,
        "needsManualCategories": True,
    }


def test_category_completion_becomes_submittable_after_one_manual_choice():
    state = _submission_state(
        busy=False,
        confirmed=True,
        suggested_category_count=0,
        manual_category_count=1,
        corrected_category_count=0,
    )

    assert state == {
        "canApprove": False,
        "canCompleteCategories": True,
        "canCorrect": False,
        "needsManualCategories": True,
    }


def test_complete_suggestion_keeps_the_direct_approval_path():
    state = _submission_state(
        busy=False,
        confirmed=True,
        suggested_category_count=1,
        manual_category_count=0,
        corrected_category_count=0,
    )

    assert state == {
        "canApprove": True,
        "canCompleteCategories": False,
        "canCorrect": False,
        "needsManualCategories": False,
    }
