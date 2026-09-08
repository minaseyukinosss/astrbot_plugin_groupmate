import { element, textValue } from "./dom.js";
import {
  cognitionBackendLabel,
  cognitionDiagnosticExplanation,
  cognitionDiagnosticMetricRows,
  cognitionDiagnosticStatusLabel,
  cognitionStateLabel,
  cognitionWorkerLabel,
  formatTimestamp,
  formatTraceDuration,
  expressionSummary,
  leaseStatusLabel,
  messageSummary,
  participationOpportunityLabel,
  participationDiagnosticLabel,
  participationLaneLabel,
  hardBlockReasonLabel,
  candidateSourceLabel,
  traceResultHeadline,
  traceResultReason,
  traceResultState,
  triggerBasisLabel,
  valueLabel,
  deliveryOutcomeLabel,
} from "./presenters.js";
import { renderMessageContent } from "./message.js";
import { profileHref } from "../router.js";

export const INSPECTOR_FIELDS = Object.freeze([
  ["actor", "参与者"],
  ["message", "收到的消息"],
  ["route", "处理路径"],
  ["understanding", "Groupmate 的理解"],
  ["decision", "决定"],
  ["delivery", "最终结果"],
  ["stages", "处理阶段"],
]);

function initials(name) {
  const normalized = String(name || "群成员").trim();
  return normalized ? [...normalized][0] : "群";
}

function avatar(actor = {}) {
  return element("span", {
    className: "participant-avatar participant-avatar-large",
    text: initials(actor.display_name),
    dataset: { avatarRef: actor.avatar_ref },
    attrs: { "aria-hidden": "true" },
  });
}

function profileLink(actor = {}) {
  return element("a", {
    className: "inspector-profile-link",
    text: "查看画像",
    attrs: { href: profileHref(actor.member_ref) },
  });
}

function section(title, children, className = "") {
  return element("section", { className: `inspector-section ${className}`.trim() }, [
    element("h3", { text: title }),
    ...children,
  ]);
}

function decisionLabel(value) {
  return ({
    ACT: "应当参与并准备回复",
    OBSERVE: "继续观察",
    DEFER: "稍后重新判断",
    SILENCE: "保持沉默",
    PENDING: "等待判断",
  })[String(value || "").toUpperCase()] || value;
}

function relationshipOutcomeLabel(value) {
  return ({
    ACCEPT: "已计入关系",
    SUGGEST: "仅观察，未计入",
    REJECT: "未通过本地规则",
    DUPLICATE: "该事件已处理",
  })[String(value || "").toUpperCase()] || "等待判断";
}

function cognitionDiagnostics(diagnostics = []) {
  if (!Array.isArray(diagnostics) || !diagnostics.length) {
    return element("p", { className: "inspector-empty", text: "本条消息没有认知模块诊断记录。" });
  }
  return element("ul", { className: "cognition-diagnostics", attrs: { "aria-label": "认知模块诊断" } },
    diagnostics.map((diagnostic) => element("li", {
      attrs: { "data-status": String(diagnostic.status || "FAILED").toLowerCase() },
    }, [
      element("div", {}, [
        element("strong", { text: cognitionWorkerLabel(diagnostic.worker) }),
        element("span", { text: cognitionDiagnosticStatusLabel(diagnostic.status) }),
      ]),
      definitionRows([
        ["认知后端", cognitionBackendLabel(diagnostic)],
        ["耗时", formatTraceDuration(diagnostic.latency_ms)],
        ...cognitionDiagnosticMetricRows(diagnostic),
        ["说明", cognitionDiagnosticExplanation(diagnostic)],
      ]),
    ])),
  );
}

function renderResultSummary(summary) {
  const judgement = summary.judgement || {};
  const decision = summary.decision || {};
  const evidence = judgement.evidence || null;
  const evidenceActor = evidence?.actor || {};
  const status = String(judgement.status || "").toLowerCase();
  const children = [
    element("div", {
      className: "result-summary",
      attrs: { "data-status": status || "pending" },
    }, [
      element("div", { className: "result-summary-heading" }, [
        element("strong", { text: traceResultHeadline(summary) }),
        element("span", { className: "result-state", text: traceResultState(summary) }),
      ]),
      element("div", { className: "result-reason" }, [
        element("span", { text: "判断原因" }),
        element("p", { text: traceResultReason(summary) }),
      ]),
    ]),
  ];
  if (evidence?.message) {
    children.push(element("div", { className: "result-evidence" }, [
      element("h4", { text: "关键证据" }),
      avatar(evidenceActor),
      element("div", {}, [
        element("strong", { text: evidenceActor.display_name || "群成员" }),
        renderMessageContent(evidence.message),
      ]),
    ]));
  }
  if (decision.candidate_response) {
    children.push(element("div", { className: "result-candidate" }, [
      element("span", { text: "候选回复" }),
      element("p", { text: decision.candidate_response }),
    ]));
  }
  return section("处理结果", children, "result-section");
}

function definitionRows(rows) {
  return element("dl", { className: "inspector-facts" }, rows
    .filter(([, value]) => value !== undefined && value !== null && value !== "")
    .map(([label, value]) => element("div", {}, [
      element("dt", { text: label }),
      element("dd", { text: Array.isArray(value) || typeof value === "object" ? textValue(value) : value }),
    ])));
}

function stageTimeline(stages = []) {
  if (!stages.length) return element("p", { className: "inspector-empty", text: "尚未产生后续处理阶段。" });
  return element("ol", { className: "stage-timeline" }, stages.map((stage) => element("li", {
    attrs: { "data-status": String(stage.status || "DONE").toLowerCase() },
  }, [
    element("i", { className: "stage-marker", attrs: { "aria-hidden": "true" } }),
    element("div", {}, [
      element("strong", { text: stage.label || "阶段已完成" }),
      element("time", { text: formatTimestamp(stage.at) }),
    ]),
  ])));
}

export function renderInspector(item) {
  const summary = item?.summary || {};
  const actor = summary.actor || {};
  const message = summary.message || {};
  const route = summary.route || {};
  const understanding = summary.understanding || {};
  const judgement = summary.judgement || {};
  const decision = summary.decision || {};
  const delivery = summary.delivery || {};
  const timing = summary.timing || {};
  const relationship = summary.relationship || null;

  return element("div", { className: "inspector-fields trace-inspector" }, [
    element("div", { className: "inspector-event-heading" }, [
      avatar(actor),
      element("div", {}, [
        element("span", { text: actor.display_name || "群成员" }),
        element("strong", { text: messageSummary(message) }),
        element("time", { text: formatTimestamp(timing.received_at || item?.as_of) }),
        ...(actor.member_ref ? [profileLink(actor)] : []),
      ]),
    ]),
    renderResultSummary(summary),
    ...(relationship ? [section("关系变化", [definitionRows([
      ["关系事件", relationship.kind],
      ["处理结果", relationshipOutcomeLabel(relationship.outcome)],
      ["当前阶段", relationship.stage],
      ["说明", relationship.reason],
    ])], "relationship-section")] : []),
    section("收到的消息", [renderMessageContent(message)]),
    section("触发与回复依据", [definitionRows([
      ["触发方式", triggerBasisLabel(summary)],
      ["命中别称", route.matched_alias],
      ["能力归属", route.label || "等待路由"],
      ["对话对象", decision.participation_lane === "AMBIENT" ? "当前公开群聊" : actor.display_name],
      ["租约状态", leaseStatusLabel(summary)],
      ["表达计划", expressionSummary(summary.expression)],
      ["原因", traceResultReason(summary)],
    ])], "trigger-basis-section"),
    element("details", { className: "technical-details" }, [
      element("summary", { text: "技术信息" }),
      element("h4", { className: "inspector-subheading", text: "处理阶段" }),
      stageTimeline(summary.stages),
      element("h4", { className: "inspector-subheading", text: "认知模块" }),
      cognitionDiagnostics(understanding.diagnostics),
      definitionRows([
        ["理解状态", cognitionStateLabel(understanding.status)],
        ["理解摘要", understanding.summary],
        ["策略通道", participationLaneLabel(decision.participation_lane)],
        ["识别机会", participationOpportunityLabel(judgement.opportunity_kind)],
        ["未参与原因", hardBlockReasonLabel(judgement.hard_block_reason)],
        ["策略依据", (understanding.participation_diagnostics || []).map(participationDiagnosticLabel).join("；")],
        ["原始参与判断", decisionLabel(decision.pre_gate_outcome || decision.outcome)],
        ["运行模式", valueLabel("runtime_mode", delivery.mode)],
        ["交付状态", deliveryOutcomeLabel(delivery)],
        ["交付错误", delivery.error],
        ["生成诊断", decision.reply_diagnostic],
        ["候选来源", candidateSourceLabel(understanding.candidate_source)],
        ["链路历时", formatTraceDuration(timing.total_ms)],
        ["更新时间", formatTimestamp(timing.updated_at || item?.as_of)],
      ]),
    ]),
  ]);
}
