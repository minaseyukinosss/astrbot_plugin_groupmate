export const ROUTES = Object.freeze([
  { path: "/runtime", label: "运行中心", endpoint: "runtime" },
  { path: "/profiles", label: "群成员画像", endpoint: "profiles" },
  { path: "/knowledge", label: "游戏知识", endpoint: "knowledge/group/overview" },
  { path: "/stickers", label: "表情图鉴", endpoint: "stickers" },
]);

const ROUTE_PATHS = new Set(ROUTES.map((route) => route.path));

export function normalizeHash(hash) {
  const raw = String(hash || "").replace(/^#/, "").split("?", 1)[0];
  return ROUTE_PATHS.has(raw) ? raw : "/runtime";
}

export function hashQuery(hash) {
  const raw = String(hash || "").replace(/^#/, "");
  const query = raw.split("?", 2)[1] || "";
  return Object.fromEntries(new URLSearchParams(query));
}

export function profileHref(memberRef) {
  const encoded = encodeURIComponent(String(memberRef || "").trim());
  return encoded ? `#/profiles?member=${encoded}` : "#/profiles";
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
