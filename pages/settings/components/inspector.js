import { element, textValue } from "./dom.js";

export const INSPECTOR_FIELDS = Object.freeze([
  ["evidence_refs", "证据"],
  ["observation", "结构化 Observation"],
  ["candidate_intentions", "候选意图"],
  ["utility_contributions", "效用贡献"],
  ["constraints", "硬约束"],
  ["plan", "Plan"],
  ["projection_version", "版本"],
  ["result", "结果"],
]);

export function renderInspector(item) {
  const content = element("div", { className: "inspector-fields" });
  const summary = item?.summary || {};
  const safe = {
    evidence_refs: item?.evidence_refs,
    observation: summary.observation,
    candidate_intentions: summary.candidate_intentions,
    utility_contributions: summary.utility_contributions,
    constraints: summary.constraints,
    plan: summary.plan,
    projection_version: item?.projection_version,
    result: summary.outcome || summary.result_status || summary.task_status,
  };
  content.append(element("p", {
    className: "entity-reference",
    text: `Entity ref: ${textValue(item?.entity_ref)}`,
  }));
  for (const [field, label] of INSPECTOR_FIELDS) {
    content.append(element("section", {}, [
      element("h3", { text: label }),
      element("p", { text: textValue(safe[field]) }),
    ]));
  }
  return content;
}
