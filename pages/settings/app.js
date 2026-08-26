import { ApiBridge } from "./bridge.js";
import { renderInspector } from "./components/inspector.js";
import { workspaceCopy } from "./i18n.js";
import { createRouter } from "./router.js";
import { ProjectionStore } from "./store.js";
import { renderRuntime } from "./workspaces/runtime.js";
import { renderProfiles } from "./workspaces/profiles.js";
import { safeMediaPreview } from "./components/security.js";

const bridge = new ApiBridge();
const store = new ProjectionStore();
const router = createRouter();
let locale = "zh-CN";
let activeRoute = router.current();
let refreshRequest = null;
let activeInspectorQuery = null;
const avatarCache = new Map();
const avatarRequests = new Map();
const mediaCache = new Map();
const mediaRequests = new Map();

const WORKSPACE_RENDERERS = Object.freeze({
  "/runtime": renderRuntime,
  "/profiles": renderProfiles,
});

const WORKSPACE_PROJECTIONS = Object.freeze({
  "/runtime": ["runtime", "traces", "health", "persona", "governance"],
  "/profiles": ["profiles", "group-portrait"],
});

const elements = {
  group: document.getElementById("group-select"),
  persona: document.getElementById("persona-name"),
  version: document.getElementById("config-version"),
  connection: document.getElementById("connection-state"),
  system: document.getElementById("system-status"),
  sidebarMode: document.getElementById("sidebar-mode"),
  sidebarGroup: document.getElementById("sidebar-group"),
  sidebarSync: document.getElementById("sidebar-sync"),
  runtimeMode: document.getElementById("runtime-mode"),
  visibleEvents: document.getElementById("visible-events"),
  pendingTasks: document.getElementById("pending-tasks"),
  themeToggle: document.getElementById("theme-toggle"),
  title: document.getElementById("workspace-title"),
  context: document.getElementById("workspace-context"),
  description: document.getElementById("workspace-description"),
  workspace: document.getElementById("workspace"),
  error: document.getElementById("error-banner"),
  inspector: document.getElementById("inspector"),
  inspectorContent: document.getElementById("inspector-content"),
  closeInspector: document.getElementById("close-inspector"),
};

function scopeParams() {
  const scope = store.snapshot().scope;
  return { persona_id: scope.persona_id, group_id: scope.group_id };
}

function renderNavigation(route) {
  document.querySelectorAll("[data-route]").forEach((link) => {
    const selected = link.dataset.route === route.path;
    link.toggleAttribute("aria-current", selected);
  });
  const [title, description] = workspaceCopy(route.path, locale);
  elements.title.textContent = title;
  elements.description.textContent = description;
  elements.context.textContent = route.path === "/profiles"
    ? "持续认知 · 当前群组成员与关系"
    : "此刻 · 当前群组实时消息链路";
}

function renderConnection(connection) {
  elements.connection.dataset.state = connection.state;
  elements.connection.lastChild.textContent = ` ${connection.impact}`;
  elements.system.textContent = connection.state === "connected" ? "系统运行正常" : connection.impact;
  elements.system.parentElement.dataset.state = connection.state;
  elements.sidebarSync.textContent = connection.state === "connected" ? "已同步" : connection.impact;
}

function renderError(error) {
  elements.error.hidden = !error;
  elements.error.textContent = error ? error.impact : "";
}

function renderWorkspace(route = activeRoute) {
  const renderer = WORKSPACE_RENDERERS[route.path] || renderRuntime;
  const workspaceQuery = route.path === "/runtime"
    ? loadMoreTraces
    : queryWorkspaceData;
  elements.workspace.replaceChildren();
  elements.workspace.setAttribute("aria-busy", "false");
  elements.workspace.append(renderer(
    (projection) => store.selectView(projection),
    submitWorkspaceCommand,
    refreshWorkspaceData,
    workspaceQuery,
    hydrateAvatars,
  ));
  hydrateAvatars(elements.workspace);
  hydrateMedia(elements.workspace);
}

function queryWorkspaceData(endpoint, params = {}, options = {}) {
  return bridge.query(endpoint, { ...scopeParams(), ...params }, options);
}

async function avatarSource(avatarRef) {
  const key = String(avatarRef || "");
  if (!key.startsWith("participant:")) return null;
  if (avatarCache.has(key)) return avatarCache.get(key);
  if (avatarRequests.has(key)) return avatarRequests.get(key);
  const pending = bridge.query("avatar", { ...scopeParams(), avatar_ref: key })
    .then((result) => {
      const source = String(result?.data_uri || "");
      const safe = source.startsWith("data:image/") ? source : null;
      avatarCache.set(key, safe);
      avatarRequests.delete(key);
      return safe;
    })
    .catch(() => {
      avatarCache.set(key, null);
      avatarRequests.delete(key);
      return null;
    });
  avatarRequests.set(key, pending);
  return pending;
}

function hydrateAvatars(root) {
  root.querySelectorAll("[data-avatar-ref]").forEach(async (node) => {
    if (node.dataset.avatarLoaded === "true") return;
    const source = await avatarSource(node.dataset.avatarRef);
    if (!source || !node.isConnected) return;
    const image = document.createElement("img");
    image.src = source;
    image.alt = "";
    image.decoding = "async";
    node.replaceChildren(image);
    node.dataset.avatarLoaded = "true";
  });
}

function mediaCacheKey(mediaRef, mediaKind) {
  const ref = String(mediaRef || "");
  const kind = String(mediaKind || "").toLowerCase();
  return `${ref}:${kind}`;
}

async function mediaSource(mediaRef, mediaKind, { force = false } = {}) {
  const ref = String(mediaRef || "");
  const kind = String(mediaKind || "").toLowerCase();
  const key = mediaCacheKey(ref, kind);
  if (!ref.startsWith("media:") || !["image", "audio", "video"].includes(kind)) return null;
  if (force) mediaCache.delete(key);
  if (mediaCache.has(key)) return mediaCache.get(key);
  if (mediaRequests.has(key)) return mediaRequests.get(key);
  const pending = bridge.query("media", { ...scopeParams(), media_ref: ref })
    .then((result) => {
      const source = safeMediaPreview(result, kind);
      if (!source) throw new Error("媒体预览格式不受支持");
      mediaCache.set(key, source);
      return source;
    })
    .catch((error) => {
      mediaCache.delete(key);
      throw error;
    })
    .finally(() => {
      mediaRequests.delete(key);
    });
  mediaRequests.set(key, pending);
  return pending;
}

function mediaElement(kind, source, label) {
  if (kind === "image") {
    const image = document.createElement("img");
    image.src = source;
    image.alt = label;
    image.decoding = "async";
    image.loading = "lazy";
    return image;
  }
  const media = document.createElement(kind);
  media.src = source;
  media.controls = true;
  media.preload = kind === "video" ? "metadata" : "none";
  media.setAttribute("aria-label", label);
  return media;
}

function showMediaFailure(node, error) {
  node.querySelector(".media-load-error")?.remove();
  node.classList.remove("is-loaded");
  node.classList.add("is-unavailable");
  const status = document.createElement("span");
  status.className = "media-load-error";
  const message = document.createElement("span");
  message.textContent = "预览加载失败";
  message.title = String(error?.message || "媒体地址可能已过期");
  const retry = document.createElement("button");
  retry.type = "button";
  retry.className = "media-retry";
  retry.textContent = "重新加载";
  retry.addEventListener("click", (event) => {
    event.stopPropagation();
    hydrateMediaNode(node, { force: true });
  });
  status.append(message, retry);
  node.append(status);
}

async function hydrateMediaNode(node, { force = false } = {}) {
    if (node.dataset.mediaLoaded === "true" || node.dataset.mediaLoading === "true") return;
    node.dataset.mediaLoading = "true";
    node.querySelector(".media-load-error")?.remove();
    const kind = String(node.dataset.mediaKind || "").toLowerCase();
    try {
      const source = await mediaSource(node.dataset.mediaRef, kind, { force });
      if (!source || !node.isConnected) return;
      const label = node.querySelector(".message-part-label")?.textContent || "媒体内容";
      const media = mediaElement(kind, source, label);
      media.addEventListener("error", () => {
        media.remove();
        mediaCache.delete(mediaCacheKey(node.dataset.mediaRef, kind));
        delete node.dataset.mediaLoaded;
        showMediaFailure(node, new Error("媒体内容无法解码"));
      }, { once: true });
      node.prepend(media);
      node.dataset.mediaLoaded = "true";
      node.classList.remove("is-unavailable");
      node.classList.add("is-loaded");
    } catch (error) {
      if (node.isConnected) showMediaFailure(node, error);
    } finally {
      delete node.dataset.mediaLoading;
    }
}

function hydrateMedia(root) {
  root.querySelectorAll("[data-media-ref]").forEach((node) => {
    if (node.dataset.mediaLoaded === "true" || node.dataset.mediaLoading === "true") {
      return;
    }
    hydrateMediaNode(node);
  });
}

function render(snapshot) {
  renderConnection(snapshot.connection);
  renderError(snapshot.error);
  const profile = (snapshot.views.persona?.items || [])
    .find((item) => item.kind === "persona.profile")?.summary;
  const bootstrap = snapshot.views.bootstrap || {};
  elements.persona.textContent = bootstrap.resolved_persona?.name
    || profile?.profile?.identity?.name
    || "Groupmate";
  const version = Number(profile?.config_version ?? Math.max(0, ...(snapshot.views.governance?.items || [])
    .filter((item) => item.summary?.status === "PUBLISHED")
    .map((item) => Number(item.summary?.config_version || 0))));
  elements.version.textContent = `v${version}`;
  const runtimeItems = snapshot.views.runtime?.items || [];
  const runtimeSummary = [...runtimeItems].reverse().find((item) => item.summary?.runtime_mode)?.summary || {};
  const mode = bootstrap.effective_runtime_mode
    || bootstrap.configured_runtime_mode
    || runtimeSummary.runtime_mode
    || "OFF";
  const traceView = snapshot.views.traces || {};
  const traceItems = traceView.items || [];
  const waiting = traceItems.filter((item) =>
    ["RECEIVED", "PLANNING", "READY", "DEFERRED"].includes(String(item.summary?.delivery?.status || "").toUpperCase()),
  ).length;
  elements.runtimeMode.textContent = mode === "SHADOW" ? "SHADOW" : mode;
  elements.runtimeMode.dataset.mode = mode;
  elements.sidebarMode.textContent = mode === "SHADOW" ? "仅观察" : mode;
  elements.sidebarGroup.textContent = snapshot.scope.group_id || "—";
  elements.visibleEvents.textContent = String(
    Math.max(Number(traceView.total_count || 0), traceItems.length),
  );
  elements.pendingTasks.textContent = String(waiting);
  renderWorkspace(activeRoute);
}

async function loadWorkspace(route = activeRoute, { timeoutMs } = {}) {
  const projections = WORKSPACE_PROJECTIONS[route.path] || [route.endpoint];
  const results = await Promise.all(projections.map(async (projection) => {
    try {
      const view = await bridge.query(projection, scopeParams(), { timeoutMs });
      if (projection === "traces") store.mergeTracePage(view);
      else store.merge(view);
      return { projection, error: null };
    } catch (error) {
      return { projection, error };
    }
  }));
  const failedProjections = results.filter((result) => result.error);
  if (failedProjections.length) {
    const detail = failedProjections.map((result) => result.projection).join("、");
    store.setError({
      status: 408,
      code: "refresh_incomplete",
      impact: `以下数据未能及时更新：${detail}。现有数据仍可继续查看。`,
    });
  } else {
    store.setError(null);
  }
  return { failedProjections };
}

async function loadMoreTraces(before) {
  const cursor = String(before || "").trim();
  if (!cursor) return null;
  const view = await bridge.query(
    "traces",
    { ...scopeParams(), limit: 100, before: cursor },
    { timeoutMs: 4_000 },
  );
  store.mergeTracePage(view, { append: true });
  return view;
}

async function refreshWorkspaceData() {
  if (refreshRequest) return refreshRequest;
  refreshRequest = loadWorkspace(activeRoute, { timeoutMs: 4_000 })
    .then(async ({ failedProjections }) => {
      await refreshOpenInspector({ timeoutMs: 4_000 });
      const current = store.snapshot().connection;
      const timestamp = new Intl.DateTimeFormat(locale, {
        hour: "2-digit",
        minute: "2-digit",
        second: "2-digit",
        hour12: false,
      }).format(new Date());
      const impact = failedProjections.length
        ? `刷新完成，但 ${failedProjections.length} 项未更新 · ${timestamp}`
        : `数据已刷新 · ${timestamp}`;
      store.setConnection({ ...current, impact });
    })
    .finally(() => {
      refreshRequest = null;
    });
  return refreshRequest;
}

async function submitWorkspaceCommand(spec) {
  const commandId = spec.command_id || crypto.randomUUID();
  store.trackCommand({
    command_id: commandId,
    expected_version: Number(spec.expected_version || 0),
  });
  try {
    const result = await bridge.command({ ...spec, command_id: commandId, ...scopeParams() });
    store.setConnection({ state: "connected", impact: "命令已接受，等待运行状态更新" });
    return result;
  } catch (error) {
    store.rejectCommand(commandId);
    store.setError(ApiBridge.describeError(error));
    throw error;
  }
}

async function openInspector(projection, entityRef, { timeoutMs } = {}) {
  const query = { projection, entityRef };
  activeInspectorQuery = query;
  elements.inspector.hidden = false;
  elements.inspectorContent.replaceChildren();
  try {
    const view = await bridge.query(
      projection,
      { ...scopeParams(), entity_ref: entityRef },
      { timeoutMs },
    );
    if (activeInspectorQuery !== query) return;
    const item = (view.items || []).find((candidate) => candidate.entity_ref === entityRef);
    elements.inspectorContent.append(renderInspector(item || { entity_ref: entityRef }));
    hydrateAvatars(elements.inspectorContent);
    hydrateMedia(elements.inspectorContent);
  } catch (error) {
    if (activeInspectorQuery !== query) return;
    elements.inspectorContent.textContent = ApiBridge.describeError(error).impact;
  }
}

async function refreshOpenInspector({ timeoutMs } = {}) {
  if (elements.inspector.hidden || !activeInspectorQuery) return;
  const { projection, entityRef } = activeInspectorQuery;
  await openInspector(projection, entityRef, { timeoutMs });
}

async function selectGroup(groupId) {
  await bridge.disconnect();
  const currentScope = store.snapshot().scope;
  store.setScope({ ...currentScope, group_id: groupId });
  await loadWorkspace(activeRoute);
  await bridge.connect({
    params: scopeParams(),
    onEvent: (event) => store.applyProjectionEvent(event),
    onState: (state) => store.setConnection(state),
    onPoll: () => loadWorkspace(activeRoute),
  });
}

async function initialize() {
  const context = await bridge.ready();
  locale = context?.locale || "zh-CN";
  if (context?.theme === "dark" || context?.theme === "light") {
    document.documentElement.dataset.theme = context.theme;
    elements.themeToggle.setAttribute("aria-label", context.theme === "dark" ? "切换到浅色主题" : "切换到深色主题");
  }
  const bootstrap = await bridge.query("bootstrap");
  store.mergeBootstrap(bootstrap);
  elements.group.replaceChildren();
  for (const groupId of bootstrap.available_groups || []) {
    const option = document.createElement("option");
    option.value = groupId;
    option.textContent = groupId;
    option.selected = groupId === bootstrap.selected_group_id;
    elements.group.append(option);
  }
  await selectGroup(bootstrap.selected_group_id);
}

router.start(async (route) => {
  activeRoute = route;
  renderNavigation(route);
  if (store.snapshot().scope.group_id) await loadWorkspace(route);
});

store.subscribe(render);
elements.group.addEventListener("change", () => selectGroup(elements.group.value));
elements.workspace.addEventListener("click", (event) => {
  const target = event.target.closest("[data-entity-ref]");
  if (target) openInspector(target.dataset.projection, target.dataset.entityRef);
});
elements.closeInspector.addEventListener("click", () => {
  elements.inspector.hidden = true;
  activeInspectorQuery = null;
});
elements.themeToggle.addEventListener("click", () => {
  const next = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
  document.documentElement.dataset.theme = next;
  elements.themeToggle.setAttribute("aria-label", next === "dark" ? "切换到浅色主题" : "切换到深色主题");
});
initialize().catch((error) => {
  store.setConnection({ state: "disconnected", impact: "控制面初始化失败" });
  store.setError(ApiBridge.describeError(error));
});

window.addEventListener("beforeunload", () => bridge.disconnect());
