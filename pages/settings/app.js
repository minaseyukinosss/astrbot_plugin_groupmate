import { ApiBridge } from "./bridge.js";
import { renderInspector } from "./components/inspector.js";
import { controlVersion } from "./components/projection.js";
import { workspaceCopy } from "./i18n.js";
import { createRouter } from "./router.js";
import { ProjectionStore } from "./store.js";
import { renderActivity } from "./workspaces/activity.js";
import { renderGovernance } from "./workspaces/governance.js";
import { renderPeople } from "./workspaces/people.js";
import { renderPersona } from "./workspaces/persona.js";
import { renderRuntime } from "./workspaces/runtime.js";

const bridge = new ApiBridge();
const store = new ProjectionStore();
const router = createRouter();
let locale = "zh-CN";
let activeRoute = router.current();

const WORKSPACE_RENDERERS = Object.freeze({
  "/runtime": renderRuntime,
  "/persona": renderPersona,
  "/people": renderPeople,
  "/activity": renderActivity,
  "/governance": renderGovernance,
});

const WORKSPACE_PROJECTIONS = Object.freeze({
  "/runtime": ["runtime", "activity", "tasks", "health", "governance"],
  "/persona": ["persona", "governance"],
  "/people": ["people", "culture", "governance"],
  "/activity": ["activity", "scenes", "tasks", "governance"],
  "/governance": ["governance", "evaluation"],
});

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
}

function renderConnection(connection) {
  elements.connection.dataset.state = connection.state;
  elements.connection.lastChild.textContent = ` ${connection.impact}`;
}

function renderError(error) {
  elements.error.hidden = !error;
  elements.error.textContent = error ? error.impact : "";
}

function renderWorkspace(route = activeRoute) {
  const renderer = WORKSPACE_RENDERERS[route.path] || renderRuntime;
  elements.workspace.replaceChildren();
  elements.workspace.setAttribute("aria-busy", "false");
  elements.workspace.append(renderer(
    (projection) => store.selectView(projection),
    submitWorkspaceCommand,
  ));
}

function render(snapshot) {
  renderConnection(snapshot.connection);
  renderError(snapshot.error);
  elements.persona.textContent = snapshot.scope.persona_id || "—";
  const version = snapshot.views.governance?.projection_version ?? 0;
  elements.version.textContent = `v${version}`;
  renderWorkspace(activeRoute);
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

async function loadWorkspace(route = activeRoute) {
  const projections = WORKSPACE_PROJECTIONS[route.path] || [route.endpoint];
  await Promise.all(projections.map(async (projection) => {
    try {
      store.merge(await bridge.query(projection, scopeParams()));
    } catch (error) {
      store.setError(ApiBridge.describeError(error));
    }
  }));
}

async function submitWorkspaceCommand(spec) {
  const commandId = spec.command_id || crypto.randomUUID();
  store.trackCommand({
    command_id: commandId,
    expected_version: Number(spec.expected_version || 0),
  });
  try {
    const result = await bridge.command({ ...spec, command_id: commandId, ...scopeParams() });
    store.setConnection({ state: "connected", impact: "命令已接受，等待 Projection 确认" });
    return result;
  } catch (error) {
    store.rejectCommand(commandId);
    store.setError(ApiBridge.describeError(error));
    throw error;
  }
}

async function openInspector(projection, entityRef) {
  elements.inspector.hidden = false;
  elements.inspectorContent.replaceChildren();
  try {
    const view = await bridge.query(projection, { ...scopeParams(), entity_ref: entityRef });
    const item = (view.items || []).find((candidate) => candidate.entity_ref === entityRef);
    elements.inspectorContent.append(renderInspector(item || { entity_ref: entityRef }));
  } catch (error) {
    elements.inspectorContent.textContent = ApiBridge.describeError(error).impact;
  }
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
});
elements.pause.addEventListener("click", async () => {
  const expectedVersion = controlVersion(store.selectView("governance"));
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
