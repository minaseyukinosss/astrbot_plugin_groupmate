const COPY = Object.freeze({
  zh: {
    runtime: ["此刻", "查看这个群里的运行状态、近期活动与健康情况。"],
    persona: ["人格工作室", "配置机器人在群里的身份、参与方式与表达边界。"],
    people: ["人与记忆", "查看成员、关系、记忆和群文化。"],
    activity: ["活动与任务", "筛选运行事件，并检查决策、任务和交付结果。"],
    governance: ["治理与评估", "处理复核、纠正、版本、校准和安全事项。"],
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
