const COPY = Object.freeze({
  zh: {
    runtime: ["运行中心", "查看每条群消息经过 NapCat、AstrBot 与 Groupmate 后发生了什么。"],
  },
  en: {
    runtime: ["Runtime", "Inspect actual runtime, task, and delivery health."],
  },
});

export function localeKey(locale) {
  return String(locale || "zh-CN").toLowerCase().startsWith("en") ? "en" : "zh";
}

export function workspaceCopy(path, locale) {
  const key = String(path || "/runtime").replace(/^\//, "");
  return COPY[localeKey(locale)][key] || COPY[localeKey(locale)].runtime;
}
