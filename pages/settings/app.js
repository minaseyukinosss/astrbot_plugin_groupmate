import { ApiBridge } from "./bridge.js";
import { workspaceCopy } from "./i18n.js";
import { createRouter } from "./router.js";
import { ProjectionStore } from "./store.js";

const bridge = new ApiBridge();
const store = new ProjectionStore();
const router = createRouter();
let locale = "zh-CN";
let activeRoute = router.current();

const elements = {
  group: document.getElementById("group-select"),
  persona: document.getElementById("persona-name"),
  version: document.getElementById("config-version"),
  connection: document.getElementById("connection-state"),
  title: document.getElementById("workspace-title"),
  description: document.getElementById("workspace-description"),
  workspace: document.getElementById("workspace"),
  error: document.getElementById("error-banner"),
  pause: document.getElementById("pause-runtime"),
  inspector: document.getElementById("inspector"),
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
}

function renderConnection(connection) {
  elements.connection.dataset.state = connection.state;
  elements.connection.lastChild.textContent = ` ${connection.impact}`;
}

function renderError(error) {
  elements.error.hidden = !error;
  elements.error.textContent = error ? error.impact : "";
}

function renderWorkspace() {
  const view = store.selectView(activeRoute.endpoint);
  elements.workspace.replaceChildren();
  elements.workspace.setAttribute("aria-busy", "false");
  const summary = document.createElement("div");
  summary.className = "workspace-summary";
  const heading = document.createElement("h2");
  heading.textContent = view ? `${activeRoute.label}已同步` : `${activeRoute.label}暂无 Projection`;
  const metadata = document.createElement("p");
  metadata.textContent = view
    ? `游标 ${view.cursor} · Projection v${view.projection_version}${view.stale ? " · 数据滞后" : " · 已同步"}`
    : "页面不会从领域写表拼装状态。等待 Projection Consumer 提供数据。";
  summary.append(heading, metadata);
  elements.workspace.append(summary);
}

function render(snapshot) {
  renderConnection(snapshot.connection);
  renderError(snapshot.error);
  elements.persona.textContent = snapshot.scope.persona_id || "—";
  const version = snapshot.views.governance?.projection_version ?? 0;
  elements.version.textContent = `v${version}`;
  renderWorkspace();
}

async function loadView(route = activeRoute) {
  try {
    const view = await bridge.query(route.endpoint, scopeParams());
    store.merge(view);
    store.setError(null);
  } catch (error) {
    store.setError(ApiBridge.describeError(error));
  }
}

async function selectGroup(groupId) {
  store.scope.group_id = groupId;
  await loadView(activeRoute);
  await bridge.connect({
    params: scopeParams(),
    onEvent: (event) => store.applyProjectionEvent(event),
    onState: (state) => store.setConnection(state),
    onPoll: () => loadView(activeRoute),
  });
}

async function initialize() {
  const context = await bridge.ready();
  locale = context?.locale || "zh-CN";
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
  if (store.snapshot().scope.group_id) await loadView(route);
});

store.subscribe(render);
elements.group.addEventListener("change", () => selectGroup(elements.group.value));
elements.closeInspector.addEventListener("click", () => {
  elements.inspector.hidden = true;
});
elements.pause.addEventListener("click", async () => {
  const expectedVersion = store.selectView("governance")?.projection_version || 0;
  const commandId = crypto.randomUUID();
  store.trackCommand({ command_id: commandId, expected_version: expectedVersion });
  try {
    await bridge.command({
      type: "pause",
      command_id: commandId,
      expected_version: expectedVersion,
      reason: "管理员从运行工具栏请求暂停",
      confirmed: false,
      payload: { paused: true },
      ...scopeParams(),
    });
    store.setConnection({ state: "connected", impact: "命令已接受，等待 Projection 确认" });
  } catch (error) {
    store.rejectCommand(commandId);
    store.setError(ApiBridge.describeError(error));
  }
});

initialize().catch((error) => {
  store.setConnection({ state: "disconnected", impact: "控制面初始化失败" });
  store.setError(ApiBridge.describeError(error));
});

window.addEventListener("beforeunload", () => bridge.disconnect());
