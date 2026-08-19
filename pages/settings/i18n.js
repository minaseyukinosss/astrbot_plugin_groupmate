const COPY = Object.freeze({
  zh: {
    runtime: ["运行中心", "读取实际运行状态、任务和交付健康。"],
    persona: ["人格工作室", "检查版本化人格、行为边界与工具策略。"],
    people: ["人与记忆", "审阅关系、事实、经历和群文化证据。"],
    activity: ["活动与任务", "沿因果链检查决策、计划、任务和交付。"],
    governance: ["治理与评估", "处理复核、纠正、遗忘、版本和校准。"],
  },
  en: {
    runtime: ["Runtime", "Inspect actual runtime, task, and delivery health."],
    persona: ["Persona", "Inspect versioned behavior, boundaries, and tools."],
    people: ["People & memory", "Review relationships, facts, and evidence."],
    activity: ["Activity & tasks", "Trace decisions, plans, tasks, and delivery."],
    governance: ["Governance", "Review corrections, versions, and calibration."],
  },
});

export function localeKey(locale) {
  return String(locale || "zh-CN").toLowerCase().startsWith("en") ? "en" : "zh";
}

export function workspaceCopy(path, locale) {
  const key = String(path || "/runtime").replace(/^\//, "");
  return COPY[localeKey(locale)][key] || COPY[localeKey(locale)].runtime;
}
