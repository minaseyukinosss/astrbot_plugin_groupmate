import { element, textValue } from "./dom.js";
import { fieldLabel, formatTimestamp, kindLabel, visibleFacts } from "./presenters.js";

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
  const summary = item?.summary || {};
  const content = element("div", { className: "inspector-fields" }, [
    element("div", { className: "inspector-event-heading" }, [
      element("strong", { text: kindLabel(item?.kind) }),
      element("time", { text: formatTimestamp(item?.as_of) }),
    ]),
  ]);
  const facts = visibleFacts(summary);
  if (facts.length) {
    content.append(element("dl", { className: "inspector-facts" }, facts.map((fact) => element("div", {}, [
      element("dt", { text: fact.label }), element("dd", { text: fact.value }),
    ]))));
  }
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
  for (const [field, label] of INSPECTOR_FIELDS) {
    if (safe[field] === undefined || safe[field] === null || safe[field] === "") continue;
    content.append(element("section", {}, [
      element("h3", { text: label || fieldLabel(field) }),
      element("p", { text: textValue(safe[field]) }),
    ]));
  }
  return content;
}
