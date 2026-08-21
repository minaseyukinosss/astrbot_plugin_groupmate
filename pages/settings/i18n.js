const COPY = Object.freeze({
  zh: {
    runtime: ["运行中心", "观察消息如何进入注意、理解、参与判断与行动链路。"],
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
