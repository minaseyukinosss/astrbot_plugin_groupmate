export const ROUTES = Object.freeze([
  { path: "/runtime", label: "运行中心", endpoint: "runtime" },
  { path: "/persona", label: "人格工作室", endpoint: "persona" },
  { path: "/people", label: "人与记忆", endpoint: "people" },
  { path: "/activity", label: "活动与任务", endpoint: "activity" },
  { path: "/governance", label: "治理与评估", endpoint: "governance" },
]);

const ROUTE_PATHS = new Set(ROUTES.map((route) => route.path));

export function normalizeHash(hash) {
  const raw = String(hash || "").replace(/^#/, "").split("?", 1)[0];
  return ROUTE_PATHS.has(raw) ? raw : "/runtime";
}

export function createRouter(browserWindow = window) {
  const listeners = new Set();

  function current() {
    const path = normalizeHash(browserWindow.location.hash);
    return ROUTES.find((route) => route.path === path) || ROUTES[0];
  }

  function notify() {
    const route = current();
    listeners.forEach((listener) => listener(route));
  }

  function start(listener) {
    listeners.add(listener);
    browserWindow.addEventListener("hashchange", notify);
    if (!browserWindow.location.hash) {
      browserWindow.location.hash = "#/runtime";
    }
    listener(current());
    return () => {
      listeners.delete(listener);
      if (listeners.size === 0) {
        browserWindow.removeEventListener("hashchange", notify);
      }
    };
  }

  return { current, start };
}
