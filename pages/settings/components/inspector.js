import { element, textValue } from "./dom.js";
import { formatTimestamp, formatTraceDuration, messageSummary } from "./presenters.js";
import { renderMessageContent } from "./message.js";

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

function section(title, children, className = "") {
  return element("section", { className: `inspector-section ${className}`.trim() }, [
    element("h3", { text: title }),
    ...children,
  ]);
}

function stateLabel(value) {
  return ({
    READY: "已完成",
    PENDING: "等待处理",
    FAILED: "失败",
  })[String(value || "").toUpperCase()] || value;
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

function workerLabel(value) {
  return ({
    direct_interaction: "直接互动识别",
    social_risk: "社交风险识别",
    context_reconstruction: "上下文重建",
    relationship: "成员关系理解",
  })[String(value || "")] || String(value || "认知模块");
}

function diagnosticStatus(value) {
  return ({
    SUCCEEDED: "完成",
    TIMED_OUT: "超时",
    MODEL_FAILED: "模型调用失败",
    INVALID_OUTPUT: "输出无效",
    REJECTED: "结果未采用",
    FAILED: "失败",
    MISSING: "未执行",
    BUDGET_EXHAUSTED: "预算已用尽",
  })[String(value || "").toUpperCase()] || String(value || "未知");
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
        element("strong", { text: workerLabel(diagnostic.worker) }),
        element("span", { text: diagnosticStatus(diagnostic.status) }),
      ]),
      definitionRows([
        ["耗时", `${Number(diagnostic.latency_ms || 0)} ms`],
        ["诊断码", diagnostic.diagnostic_code],
      ]),
    ])),
  );
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
  const decision = summary.decision || {};
  const delivery = summary.delivery || {};
  const timing = summary.timing || {};

  return element("div", { className: "inspector-fields trace-inspector" }, [
    element("div", { className: "inspector-event-heading" }, [
      avatar(actor),
      element("div", {}, [
        element("span", { text: actor.display_name || "群成员" }),
        element("strong", { text: messageSummary(message) }),
        element("time", { text: formatTimestamp(timing.received_at || item?.as_of) }),
      ]),
    ]),
    section("收到的消息", [renderMessageContent(message)]),
    section("处理路径", [definitionRows([
      ["当前归属", route.label || "等待路由"],
      ["原因", route.reason],
    ])]),
    section("处理阶段", [stageTimeline(summary.stages)]),
    section("Groupmate 的理解", [
      definitionRows([
        ["状态", stateLabel(understanding.status)],
        ["理解摘要", understanding.summary],
      ]),
      element("h4", { className: "inspector-subheading", text: "认知模块" }),
      cognitionDiagnostics(understanding.diagnostics),
    ]),
    section("决定", [definitionRows([
      ["参与判断", decision.label],
      ["SHADOW 前判断", decisionLabel(decision.pre_gate_outcome || decision.outcome)],
      ["判断依据", decision.reasons],
      ["候选回复", decision.candidate_response || (delivery.mode === "SHADOW" ? "本次未生成候选回复" : null)],
      ["生成说明", decision.reply_diagnostic],
    ])]),
    section("最终结果", [definitionRows([
      ["运行模式", delivery.mode],
      ["状态", delivery.label],
      ["错误", delivery.error],
    ])], "delivery-section"),
    element("details", { className: "technical-details" }, [
      element("summary", { text: "技术信息" }),
      definitionRows([
        ["追踪引用", item?.entity_ref],
        ["链路历时", formatTraceDuration(timing.total_ms)],
        ["更新时间", formatTimestamp(timing.updated_at || item?.as_of)],
      ]),
    ]),
  ]);
}
