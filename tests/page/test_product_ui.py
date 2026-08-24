from __future__ import annotations

import json
import base64
import subprocess
from pathlib import Path


ROOT = Path(__file__).parents[2]
PAGE = ROOT / "pages" / "settings"


def _run_presenter(body: str) -> object:
    source = (PAGE / "components" / "presenters.js").read_bytes()
    presenter = f"data:text/javascript;base64,{base64.b64encode(source).decode('ascii')}"
    script = f"import * as presenter from {json.dumps(presenter)};\n{body}"
    result = subprocess.run(
        ["node", "--input-type=module", "--eval", script],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout)


def test_projection_presenter_translates_runtime_facts_for_people():
    result = _run_presenter(
        "console.log(JSON.stringify({"
        "label: presenter.kindLabel('group_world.projected'),"
        "mode: presenter.valueLabel('runtime_mode', 'SHADOW'),"
        "facts: presenter.visibleFacts({kind:'group_world.projected',scene_version:4,"
        "runtime_mode:'SHADOW',paused:false,disposition:'SILENCE'})"
        "}));"
    )

    assert result == {
        "label": "群聊现场已更新",
        "mode": "观察模式（不发送）",
        "facts": [
            {"key": "runtime_mode", "label": "运行模式", "value": "观察模式（不发送）"},
            {"key": "paused", "label": "运行状态", "value": "运行中"},
            {"key": "disposition", "label": "处理结果", "value": "保持沉默"},
        ],
    }


def test_shell_uses_approved_product_hierarchy_instead_of_projection_console():
    html = (PAGE / "index.html").read_text(encoding="utf-8")

    assert "群聊伙伴" in html
    assert "此刻" in html
    assert 'class="topbar-context"' in html
    assert 'id="pause-runtime"' not in html
    assert "所有状态来自版本化 Projection" not in html


def test_layout_preserves_product_navigation_at_normal_iframe_width():
    layout = (PAGE / "styles" / "layout.css").read_text(encoding="utf-8")

    assert "max-width: 60rem" in layout
    assert ".nav-label" in layout


def test_product_palette_is_neutral_with_green_reserved_for_status():
    tokens = (PAGE / "styles" / "tokens.css").read_text(encoding="utf-8")
    components = (PAGE / "styles" / "components.css").read_text(encoding="utf-8")

    assert "--color-canvas: oklch(0.965 0.002 260)" in tokens
    assert "--color-surface: oklch(0.965 0.002 260)" in tokens
    assert ".sidebar nav a[aria-current]" in components
    assert "background: var(--color-surface-active)" in components


def test_runtime_is_the_single_message_trace_product_view():
    runtime = (PAGE / "workspaces" / "runtime.js").read_text(encoding="utf-8")

    for label in ("运行概览", "消息链路", "最终结果"):
        assert label in runtime
    assert not (PAGE / "workspaces" / "activity.js").exists()
    assert "projectionList(runtime)" not in runtime


def test_message_presenter_uses_non_text_parts_instead_of_generic_placeholder():
    result = _run_presenter(
        "console.log(JSON.stringify({"
        "onlyMedia: presenter.messageSummary({summary:'',parts:["
        "{kind:'image',label:'图片'},{kind:'record',label:'语音'}]}),"
        "mixed: presenter.messageSummary({summary:'看看 · 图片',parts:["
        "{kind:'text',text:'看看'},{kind:'image',label:'图片'}]})"
        "}));"
    )

    assert result == {
        "onlyMedia": "图片 · 语音",
        "mixed": "看看 · 图片",
    }


def test_full_message_renderer_keeps_segment_order():
    source = (PAGE / "components" / "message.js").read_text(encoding="utf-8")

    assert "for (const part of parts)" in source
    assert 'parts.filter((part) => part?.kind !== "text")' not in source


def test_message_presenter_formats_media_sizes_for_people():
    result = _run_presenter(
        "console.log(JSON.stringify(["
        "presenter.formatBytes(800),"
        "presenter.formatBytes(2048),"
        "presenter.formatBytes(1572864),"
        "presenter.formatBytes(null)"
        "]));"
    )

    assert result == ["800 B", "2 KB", "1.5 MB", ""]


def test_trace_presenter_translates_strategy_and_cognition_diagnostics():
    result = _run_presenter(
        "console.log(JSON.stringify({"
        "direct: presenter.participationLaneLabel('DIRECT_FAST'),"
        "continuation: presenter.participationLaneLabel('CONTINUATION'),"
        "ambient: presenter.participationLaneLabel('AMBIENT'),"
        "degraded: presenter.cognitionStateLabel('DEGRADED'),"
        "scene: presenter.cognitionWorkerLabel('scene_interpreter'),"
        "assessor: presenter.cognitionWorkerLabel('participation_assessor'),"
        "combined: presenter.cognitionWorkerLabel('ambient_social_assessor'),"
        "timeout: presenter.cognitionDiagnosticExplanation({"
        "diagnostic_code:'worker_timeout',latency_ms:10000,timeout_ms:8000}),"
        "reply: presenter.replyExpectation({would_reply:true}, {mode:'SHADOW'}),"
        "candidate: presenter.candidateSummary({candidate_count:1,"
        "candidate_source:'deterministic'})"
        "}));"
    )

    assert result == {
        "direct": "直接互动",
        "continuation": "延续对话",
        "ambient": "普通群聊观察",
        "degraded": "认知降级",
        "scene": "群聊场景理解",
        "assessor": "插话时机判断",
        "combined": "群聊理解与插话评估",
        "timeout": "模型等待约 8 秒仍未返回，本次已转为保守观察。",
        "reply": "正式运行会回复",
        "candidate": "策略生成 · 1 个参与方案",
    }


def test_timeout_explanation_distinguishes_queue_from_provider_wait():
    result = _run_presenter(
        "console.log(JSON.stringify({"
        "queue: presenter.cognitionDiagnosticExplanation({"
        "diagnostic_code:'worker_timeout',queue_wait_ms:8000,"
        "provider_latency_ms:0,timeout_ms:8000}),"
        "provider: presenter.cognitionDiagnosticExplanation({"
        "diagnostic_code:'worker_timeout',queue_wait_ms:10,"
        "provider_latency_ms:7990,timeout_ms:8000})"
        "}));"
    )

    assert result == {
        "queue": "等待认知执行名额约 8 秒仍未开始，本次已转为保守观察。",
        "provider": "模型等待约 8 秒仍未返回，本次已转为保守观察。",
    }


def test_trace_presenter_detects_cognition_failure_separately_from_delivery():
    result = _run_presenter(
        "console.log(JSON.stringify(["
        "presenter.traceHasCognitionFailure({understanding:{status:'DEGRADED'}}),"
        "presenter.traceHasCognitionFailure({understanding:{status:'READY',"
        "diagnostics:[{status:'TIMED_OUT'}]}}),"
        "presenter.traceHasCognitionFailure({understanding:{status:'READY',"
        "diagnostics:[{status:'SUCCEEDED'}]}})"
        "]));"
    )

    assert result == [True, True, False]


def test_trace_presenter_does_not_claim_a_reply_before_decision_or_handoff():
    result = _run_presenter(
        "console.log(JSON.stringify(["
        "presenter.strategySummary({route:{owner:'GROUPMATE'},"
        "decision:{outcome:'PENDING'}}),"
        "presenter.strategySummary({route:{owner:'EXTERNAL_PLUGIN'},"
        "decision:{outcome:'PENDING'}})"
        "]));"
    )

    assert result == ["等待进入策略判断", "Groupmate 不参与判断"]


def test_trace_presenter_only_treats_completed_silence_as_observed():
    result = _run_presenter(
        "console.log(JSON.stringify(["
        "presenter.traceIsObserved({decision:{outcome:'PENDING',would_reply:false}}),"
        "presenter.traceIsObserved({decision:{outcome:'DEFER',would_reply:false}}),"
        "presenter.traceIsObserved({decision:{outcome:'OBSERVE'}}),"
        "presenter.traceIsObserved({decision:{would_reply:false}}),"
        "presenter.traceIsObserved({delivery:{status:'OBSERVED'}})"
        "]));"
    )

    assert result == [False, False, True, True, True]


def test_cognition_presenter_formats_safe_latency_metrics():
    result = _run_presenter(
        "console.log(JSON.stringify(presenter.cognitionDiagnosticMetricRows({"
        "queue_wait_ms:120,provider_latency_ms:1300,input_bytes:2048,"
        "timeout_ms:8000})));"
    )

    assert result == [
        ["排队等待", "不足 1 秒"],
        ["Provider 等待", "1 秒"],
        ["输入大小", "2 KB"],
        ["本次截止", "8 秒"],
    ]
