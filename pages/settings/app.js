const bridge = window.AstrBotPluginPage;

const els = {
  badge: document.getElementById("badge"),
  error: document.getElementById("error"),
  pausedText: document.getElementById("paused-text"),
  groupCount: document.getElementById("group-count"),
  activePersona: document.getElementById("active-persona"),
  providerMode: document.getElementById("provider-mode"),
  visionStatus: document.getElementById("vision-status"),
  configHealth: document.getElementById("config-health"),
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
  els.activePersona.textContent = payload.active_persona || "—";
  els.providerMode.textContent = payload.generation_provider_mode || "—";
  els.visionStatus.textContent = payload.vision_status || "—";
  els.configHealth.textContent = payload.config_health || "—";
  els.preview.textContent = JSON.stringify(
    {
      enabled_scope: payload.enabled_scope,
      alias_count: payload.alias_count,
      relationship_seed_count: payload.relationship_seed_count,
      database_schema: payload.database_schema,
      ignored_legacy_keys: payload.ignored_legacy_keys || [],
    },
    null,
    2,
  );
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
