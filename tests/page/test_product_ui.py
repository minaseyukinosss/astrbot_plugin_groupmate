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


def test_expression_summary_leads_with_relationship_and_material_result():
    result = _run_presenter(
        "console.log(JSON.stringify({"
        "plain: presenter.expressionSummary({core_response_goal:'reply',"
        "relationship_stage:'陌生',reaction_stance:'attentive',"
        "followup_hook:'only_if_it_adds_value',message_count:1,"
        "explicit_material_selected:false}),"
        "relevant: presenter.expressionSummary({core_response_goal:'reply',"
        "relationship_stage:'熟悉',reaction_stance:'continue_current_exchange',"
        "followup_hook:'optional_if_natural',message_count:1,"
        "explicit_material_selected:true})"
        "}));"
    )

    assert result == {
        "plain": "陌生 · 直接回应当前内容 · 未使用显式人设素材",
        "relevant": "熟悉 · 承接上一轮内容 · 已使用当前话题相关素材",
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


def test_direct_cognition_presenter_explains_backend_and_safe_failures():
    result = _run_presenter(
        "console.log(JSON.stringify({"
        "backend: presenter.cognitionBackendLabel({backend:'direct_deepseek',"
        "model:'deepseek-v4-flash'}),"
        "timeout: presenter.cognitionDiagnosticExplanation({"
        "diagnostic_code:'direct_timeout',backend:'direct_deepseek'}),"
        "auth: presenter.cognitionDiagnosticExplanation({"
        "diagnostic_code:'direct_auth_failed',backend:'direct_deepseek'}),"
        "rate: presenter.cognitionDiagnosticExplanation({"
        "diagnostic_code:'direct_rate_limited',backend:'direct_deepseek'}),"
        "network: presenter.cognitionDiagnosticExplanation({"
        "diagnostic_code:'direct_network_failed',backend:'direct_deepseek'}),"
        "upstream: presenter.cognitionDiagnosticExplanation({"
        "diagnostic_code:'direct_upstream_failed',backend:'direct_deepseek'}),"
        "invalid: presenter.cognitionDiagnosticExplanation({"
        "diagnostic_code:'direct_invalid_output',backend:'direct_deepseek'}),"
        "empty: presenter.cognitionDiagnosticExplanation({"
        "diagnostic_code:'direct_response_empty'}),"
        "badJson: presenter.cognitionDiagnosticExplanation({"
        "diagnostic_code:'direct_response_json_invalid'}),"
        "badShape: presenter.cognitionDiagnosticExplanation({"
        "diagnostic_code:'direct_response_shape_invalid'}),"
        "missing: presenter.cognitionDiagnosticExplanation({"
        "diagnostic_code:'direct_missing_field'}),"
        "decision: presenter.cognitionDiagnosticExplanation({"
        "diagnostic_code:'direct_invalid_decision'}),"
        "signal: presenter.cognitionDiagnosticExplanation({"
        "diagnostic_code:'direct_invalid_signal'}),"
        "speakSignal: presenter.cognitionDiagnosticExplanation({"
        "diagnostic_code:'direct_speak_without_signal'}),"
        "target: presenter.cognitionDiagnosticExplanation({"
        "diagnostic_code:'direct_unknown_target'}),"
        "speakEvidence: presenter.cognitionDiagnosticExplanation({"
        "diagnostic_code:'direct_empty_speak_evidence'}),"
        "evidence: presenter.cognitionDiagnosticExplanation({"
        "diagnostic_code:'direct_unknown_evidence'}),"
        "score: presenter.cognitionDiagnosticExplanation({"
        "diagnostic_code:'direct_invalid_score'}),"
        "metrics: presenter.cognitionDiagnosticMetricRows({"
        "backend:'direct_deepseek',provider_latency_ms:1300})"
        "}));"
    )

    assert result == {
        "backend": "直连 DeepSeek · deepseek-v4-flash",
        "timeout": "直连模型在 6 秒内未返回，本次已转为保守观察。",
        "auth": "认知模型鉴权失败，请管理员检查 API Key。",
        "rate": "认知模型触发限流，本次已转为保守观察。",
        "network": "无法连接认知模型服务，本次已转为保守观察。",
        "upstream": "认知模型服务暂时异常，本次已转为保守观察。",
        "invalid": "认知模型返回内容未通过本地校验，本次未采用。",
        "empty": "认知模型返回了空内容，本次未采用。",
        "badJson": "认知模型返回内容不是可解析的 JSON，本次未采用。",
        "badShape": "认知模型返回结构不完整，本次未采用。",
        "missing": "认知模型返回结果缺少必填字段，本次未采用。",
        "decision": "认知模型给出了无效的参与决定，本次未采用。",
        "signal": "认知模型给出了无效的群聊信号，本次未采用。",
        "speakSignal": "认知模型建议参与，但没有给出有效信号，本次未采用。",
        "target": "认知模型引用了当前候选成员之外的对象，本次未采用。",
        "speakEvidence": "认知模型建议参与，但没有提供消息证据，本次未采用。",
        "evidence": "认知模型引用了当前上下文之外的消息，本次未采用。",
        "score": "认知模型返回的评分不在有效范围内，本次未采用。",
        "metrics": [["模型请求", "1 秒"]],
    }


def test_legacy_cognition_diagnostic_keeps_provider_wording():
    result = _run_presenter(
        "console.log(JSON.stringify({"
        "backend: presenter.cognitionBackendLabel({}),"
        "metrics: presenter.cognitionDiagnosticMetricRows({provider_latency_ms:1300})"
        "}));"
    )

    assert result == {
        "backend": "",
        "metrics": [["Provider 等待", "1 秒"]],
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


def test_trace_result_presenters_lead_with_outcome_reason_and_safe_fallbacks():
    result = _run_presenter(
        "const silence={route:{owner:'GROUPMATE'},"
        "judgement:{source:'model',status:'accepted',decision:'silence',"
        "label:'继续观察',reason:'成员正在自然交流，现在插话会打断对话。'},"
        "decision:{outcome:'OBSERVE',would_reply:false,label:'继续观察'}};"
        "const speak={route:{owner:'GROUPMATE'},"
        "judgement:{source:'model',status:'accepted',decision:'speak',"
        "label:'准备回复',reason:'成员明确提出了可以帮助的问题。'},"
        "decision:{outcome:'ACT',would_reply:true,label:'准备回复'}};"
        "const explicitSilence={route:{owner:'GROUPMATE'},"
        "decision:{outcome:'SILENCE',would_reply:false,label:'保持沉默',"
        "reasons:['当前没有合适的参与意图']}};"
        "const deferred={route:{owner:'GROUPMATE'},"
        "decision:{outcome:'DEFER',would_reply:false,label:'稍后再判断',"
        "reasons:['触发频率限制']}};"
        "const unavailable={route:{owner:'GROUPMATE'},"
        "judgement:{source:'model',status:'unavailable',label:'模型判断未采用'},"
        "understanding:{diagnostics:[{status:'TIMED_OUT',"
        "diagnostic_code:'direct_timeout'}]},"
        "decision:{outcome:'OBSERVE',would_reply:false,reasons:["
        "'认知降级或参与条件不足']}};"
        "const policy={route:{owner:'GROUPMATE',address_kind:'AT',"
        "address_reason:'明确 @ 机器人'},"
        "judgement:{source:'policy',status:'not_required',label:'准备回复'},"
        "decision:{outcome:'ACT',would_reply:true,participation_lane:'DIRECT_FAST',"
        "reasons:['明确 @ 机器人']}};"
        "const pending={route:{owner:'GROUPMATE'},decision:{outcome:'PENDING'}};"
        "const external={route:{owner:'EXTERNAL_PLUGIN',label:'交给外部能力',"
        "reason:'匹配视频解析规则'},decision:{outcome:'PENDING'}};"
        "console.log(JSON.stringify([silence,speak,explicitSilence,deferred,"
        "unavailable,policy,pending,external]"
        ".map(item=>[presenter.traceResultHeadline(item),"
        "presenter.traceResultState(item),presenter.traceResultReason(item)])));"
    )

    assert result == [
        ["本轮暂不参与", "继续观察", "成员正在自然交流，现在插话会打断对话。"],
        ["适合加入当前话题", "准备回复", "成员明确提出了可以帮助的问题。"],
        ["本轮不回复", "保持沉默", "当前没有合适的参与意图"],
        ["稍后重新判断", "稍后再判断", "触发频率限制"],
        [
            "判断未完成",
            "模型判断未采用",
            "直连模型在 6 秒内未返回，本次已转为保守观察。",
        ],
        ["会回应这次呼唤", "准备回复", "明确 @ 机器人"],
        ["等待完成判断", "等待判断", "消息仍在处理中。"],
        ["由外部能力处理", "交给外部能力", "匹配视频解析规则"],
    ]


def test_runtime_result_prefers_human_trigger_basis():
    result = _run_presenter(
        "const item={route:{owner:'GROUPMATE',address_kind:'ALIAS_PREFIX',"
        "matched_alias:'小爱',address_reason:'命中人格别称：小爱'},"
        "judgement:{status:'accepted',reason:'模型给出的次要理由'},"
        "decision:{outcome:'ACT',participation_lane:'DIRECT_FAST'}};"
        "console.log(JSON.stringify(presenter.traceResultReason(item)));"
    )

    assert result == "命中人格别称：小爱"


def test_continuation_result_has_a_distinct_human_headline():
    result = _run_presenter(
        "const item={decision:{outcome:'ACT',would_reply:true,"
        "participation_lane:'CONTINUATION'}};"
        "console.log(JSON.stringify(presenter.traceResultHeadline(item)));"
    )

    assert result == "继续当前对话"


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
