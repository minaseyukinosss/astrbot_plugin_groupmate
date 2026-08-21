import { element, textValue } from "./dom.js";
import { fieldLabel, formatTimestamp, kindLabel, valueLabel, visibleFacts } from "./presenters.js";

export const INSPECTOR_FIELDS = Object.freeze([
  ["evidence_refs", "证据"],
  ["observation", "结构化 Observation"],
  ["candidate_intentions", "候选意图"],
  ["utility_contributions", "效用贡献"],
  ["constraints", "安全约束"],
  ["plan", "Plan"],
  ["projection_version", "版本"],
  ["result", "结果"],
]);

function icon(name) {
  return element("img", { className: "ui-icon", attrs: { src: `./assets/icons/${name}.svg`, alt: "" } });
}

function inspectorSection(title, iconName, children, className = "") {
  return element("section", { className: `inspector-section ${className}`.trim() }, [
    element("h3", {}, [icon(iconName), element("span", { text: title })]),
    ...children,
  ]);
}

function definitionRows(rows) {
  return element("dl", { className: "inspector-facts" }, rows
    .filter(([, value]) => value !== undefined && value !== null && value !== "")
    .map(([label, value]) => element("div", {}, [
      element("dt", { text: label }),
      element("dd", { text: Array.isArray(value) || typeof value === "object" ? textValue(value) : value }),
    ])));
}

function renderShadowDecision(summary, item) {
  const nodes = [];
  if (summary.focus || summary.history) {
    const contextRows = [];
    if (summary.focus) contextRows.push(["关注消息", summary.focus.summary || summary.focus.content || summary.focus]);
    if (summary.history?.length) contextRows.push(["安全上下文", summary.history.map((entry) => entry.summary || entry.content || entry)]);
    nodes.push(inspectorSection("上下文", "message-circle", [definitionRows(contextRows)]));
  }
  if (summary.attention || summary.target) {
    nodes.push(inspectorSection("动机识别", "target-arrow", [definitionRows([
      ["触发方式", summary.attention?.trigger_kind],
      ["紧急程度", summary.attention?.urgency],
      ["处理期限", summary.attention?.deadline],
      ["参与对象", summary.target],
    ])]));
  }
  if (summary.candidate_response || summary.candidate_actions?.length || summary.outcome || summary.disposition) {
    nodes.push(inspectorSection("决策 / 行动", "brain", [definitionRows([
      ["参与判断", valueLabel("outcome", summary.outcome || summary.disposition)],
      ["候选回复", summary.candidate_response],
      ["候选动作", summary.candidate_actions],
      ["建议类别", summary.suggested_categories],
    ])], "decision-section"));
  }
  const governanceRows = [
    ["判断依据", summary.reason_codes ? valueLabel("reason_codes", summary.reason_codes) : undefined],
    ["安全约束", summary.constraints ? valueLabel("constraints", summary.constraints) : undefined],
    ["有效期", summary.expires_at],
    ["投影版本", item?.projection_version],
  ];
  if (governanceRows.some(([, value]) => value !== undefined)) {
    nodes.push(inspectorSection("治理信息", "shield-check", [definitionRows(governanceRows)]));
  }
  return nodes;
}

export function renderInspector(item) {
  const summary = item?.summary || {};
  const content = element("div", { className: "inspector-fields" }, [
    element("div", { className: "inspector-event-heading" }, [
      element("span", { className: "inspector-kind-icon" }, [icon(item?.kind?.includes("evaluation") ? "eye" : "activity")]),
      element("div", {}, [
        element("strong", { text: kindLabel(item?.kind) }),
        element("time", { text: formatTimestamp(item?.as_of) }),
      ]),
    ]),
  ]);

  const shadowNodes = renderShadowDecision(summary, item);
  if (shadowNodes.length) content.append(...shadowNodes);

  const facts = visibleFacts(summary).filter((fact) => !["outcome", "constraints", "expires_at"].includes(fact.key));
  if (facts.length) {
    content.append(inspectorSection("事件事实", "list-details", [definitionRows(facts.map((fact) => [fact.label, fact.value]))]));
  }

  if (item?.evidence_refs?.length) {
    content.append(inspectorSection("证据", "notes", [
      element("ul", { className: "evidence-list" }, item.evidence_refs.map((reference) => element("li", { text: reference }))),
    ]));
  } else {
    content.append(inspectorSection("证据", "notes", [
      element("p", { className: "inspector-empty", text: "此安全投影没有公开证据引用。" }),
    ]));
  }

  const legacySafe = {
    observation: summary.observation,
    candidate_intentions: summary.candidate_intentions,
    utility_contributions: summary.utility_contributions,
    plan: summary.plan,
    result: summary.result_status || summary.task_status,
  };
  const legacyRows = Object.entries(legacySafe)
    .filter(([, value]) => value !== undefined && value !== null && value !== "")
    .map(([field, value]) => [fieldLabel(field), textValue(value)]);
  if (legacyRows.length) content.append(inspectorSection("补充信息", "adjustments-horizontal", [definitionRows(legacyRows)]));

  return content;
}
