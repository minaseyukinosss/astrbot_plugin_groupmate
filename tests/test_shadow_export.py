import inspect
import json
from pathlib import Path
import subprocess
import sys

import pytest

from eval.shadow_export import main
from tests.shadow_fixtures import message, write_export


def _args(root, tmp_path):
    return [
        "--export-dir", str(root),
        "--target-uin", "20002",
        "--target-alias", "小维",
        "--current-alias", "爱弥斯",
        "--id-salt-file", str(tmp_path / "results" / ".salt"),
        "--output", str(tmp_path / "results" / "report.json"),
        "--markdown-output", str(tmp_path / "results" / "report.md"),
        "--review-output", str(tmp_path / "results" / "review.jsonl"),
    ]


def test_cli_writes_private_safe_reports_and_local_review(tmp_path):
    short_numeric_name = message("m1", "10001", "小维", 1000)
    short_numeric_name["sender"]["name"] = "1"
    root = write_export(
        tmp_path / "export",
        [
            short_numeric_name,
            message("m2", "20002", "在", 2000),
            message("m3", "10002", "普通群聊长句", 3000),
        ],
        target_uin="20002",
    )
    assert main(_args(root, tmp_path)) == 0

    output = tmp_path / "results" / "report.json"
    markdown = tmp_path / "results" / "report.md"
    review = tmp_path / "results" / "review.jsonl"
    payload = output.read_text(encoding="utf-8")
    report = json.loads(payload)
    assert report["counts"]["manifest_records"] == 3
    assert report["counts"]["examples"] == 2
    for private in ("20002", "10001", "小维", "普通群聊长句"):
        assert private not in payload
        assert private not in markdown.read_text(encoding="utf-8")
    assert review.is_file()


def test_cli_review_queue_contains_only_local_excerpts_and_anonymous_ids(tmp_path):
    root = write_export(
        tmp_path / "export",
        [
            message("raw-m0", "10001", "小维，在吗", 1000),
            message(
                "raw-bot-0", "20002", "在", 2000,
                message_type="reply", reply_to="raw-m0",
                reply_sender_uin="10001",
            ),
            message("raw-m1", "10001", "第一问", 40000),
            message("raw-m2", "10002", "第二问", 40100),
            message("raw-m3", "20002", "我看看", 40200),
        ],
        target_uin="20002",
    )
    assert main(_args(root, tmp_path)) == 0
    rows = [
        json.loads(line)
        for line in (tmp_path / "results" / "review.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    assert rows
    assert all(item["local_only"] is True for item in rows)
    assert all(item["sample_id"].startswith(("sample-", "run-")) for item in rows)
    serialized = json.dumps(rows, ensure_ascii=False)
    assert "raw-m1" not in serialized
    assert "10001" not in serialized
    assert "第一问" in serialized


def test_cli_returns_nonzero_for_invalid_export(tmp_path, capsys):
    code = main(_args(tmp_path / "missing", tmp_path))
    assert code == 2
    assert "manifest" in capsys.readouterr().err.lower()


@pytest.mark.parametrize(
    "flag,relative",
    (
        ("--id-salt-file", "local.salt"),
        ("--output", "manifest.json"),
        ("--markdown-output", "report.md"),
        ("--review-output", "review.jsonl"),
    ),
)
def test_cli_rejects_writes_inside_source_export_before_mutation(
    tmp_path, capsys, flag, relative
):
    root = write_export(
        tmp_path / "export",
        [message("m1", "20002", "在", 1000)],
        target_uin="20002",
    )
    manifest_before = (root / "manifest.json").read_bytes()
    args = _args(root, tmp_path)
    index = args.index(flag) + 1
    args[index] = str(root / relative)

    assert main(args) == 2
    assert "export directory" in capsys.readouterr().err
    assert (root / "manifest.json").read_bytes() == manifest_before
    assert not (tmp_path / "results" / ".salt").exists()


def test_cli_fails_when_no_high_confidence_examples(tmp_path, capsys):
    root = write_export(
        tmp_path / "export",
        [
            message("m1", "10001", "含义不明确", 1000),
            message(
                "m2", "20002", "嗯", 2000, message_type="reply",
                reply_to="m1", reply_sender_uin="10001",
            ),
        ],
        target_uin="20002",
    )
    assert main(_args(root, tmp_path)) == 2
    assert "high-confidence" in capsys.readouterr().err


def test_cli_and_projector_sources_exclude_effectful_modules():
    import eval.shadow_export as cli_module
    import eval.shadow_projector as projector_module

    source = inspect.getsource(cli_module) + inspect.getsource(projector_module)
    for forbidden in (
        "eval.providers",
        "CognitiveWorkflow",
        "capability_executor",
        "delivery_queue",
        "memory_store",
    ):
        assert forbidden not in source


def test_cli_import_isolated_from_workflow_and_site_packages():
    root = Path(__file__).resolve().parents[1]
    command = (
        "import sys; "
        "import eval.shadow_export; "
        "assert 'groupmate.engine.workflow' not in sys.modules"
    )
    result = subprocess.run(
        [sys.executable, "-S", "-c", command],
        cwd=str(root),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        universal_newlines=True,
    )
    assert result.returncode == 0, result.stderr
