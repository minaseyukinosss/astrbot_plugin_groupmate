const COPY = Object.freeze({
  zh: {
    runtime: ["运行中心", "查看每条群消息经过 NapCat、AstrBot 与 Groupmate 后发生了什么。"],
    profiles: ["群成员画像", "查看 Groupmate 对每位群友逐步形成的独立认知与群内关系。"],
    knowledge: ["游戏知识", "审查当前群的热门游戏、版本时效、群内约定与后台学习任务。"],
  },
  en: {
    runtime: ["Runtime", "Inspect actual runtime, task, and delivery health."],
    profiles: ["Member profiles", "Review evolving member cognition and in-group relationships."],
    knowledge: ["Game knowledge", "Review scoped game topics, freshness, conventions, and learning jobs."],
  },
});

export function localeKey(locale) {
  return String(locale || "zh-CN").toLowerCase().startsWith("en") ? "en" : "zh";
}

export function workspaceCopy(path, locale) {
  const key = String(path || "/runtime").replace(/^\//, "");
  return COPY[localeKey(locale)][key] || COPY[localeKey(locale)].runtime;
}
