const bridge = window.AstrBotPluginPage;

const els = {
  badge: document.getElementById("badge"),
  error: document.getElementById("error"),
  pausedText: document.getElementById("paused-text"),
  groupCount: document.getElementById("group-count"),
  characterName: document.getElementById("character-name"),
  maxChars: document.getElementById("max-chars"),
  preview: document.getElementById("config-preview"),
  refresh: document.getElementById("refresh"),
  pause: document.getElementById("pause"),
  resume: document.getElementById("resume"),
};

function showError(message) {
  els.error.hidden = !message;
  els.error.textContent = message || "";
}

function render(payload) {
  const paused = Boolean(payload.paused);
  els.badge.textContent = paused ? "已暂停" : "观察中";
  els.badge.dataset.tone = paused ? "warn" : "ok";
  els.pausedText.textContent = paused ? "暂停观察" : "正在观察";
  els.groupCount.textContent = String(payload.bootstrapped?.length ?? 0);
  els.characterName.textContent = payload.config?.character_name || "—";
  els.maxChars.textContent = String(payload.config?.max_reply_chars ?? "—");
  els.preview.textContent = JSON.stringify(payload.config || {}, null, 2);
}

async function loadStatus() {
  showError("");
  const payload = await bridge.apiGet("status");
  render(payload);
}

async function setPaused(paused) {
  showError("");
  await bridge.apiPost("runtime", { paused: Boolean(paused) });
  await loadStatus();
}

await bridge.ready();
els.refresh.addEventListener("click", () => {
  loadStatus().catch((error) => showError(error.message || String(error)));
});
els.pause.addEventListener("click", () => {
  setPaused(true).catch((error) => showError(error.message || String(error)));
});
els.resume.addEventListener("click", () => {
  setPaused(false).catch((error) => showError(error.message || String(error)));
});

try {
  await loadStatus();
} catch (error) {
  showError(error.message || String(error));
}
