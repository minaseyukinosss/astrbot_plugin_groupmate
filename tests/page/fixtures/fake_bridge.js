(() => {
  const views = {
    runtime: [{ entity_ref: "runtime:fake", kind: "runtime.mode", projection_version: 1, as_of: 1787126400, summary: { runtime_mode: "SHADOW", paused: false }, evidence_refs: [] }],
    activity: [{ entity_ref: "activity:fake", kind: "governor.decided", projection_version: 1, as_of: 1787126340, summary: { disposition: "SILENCE", constraints: ["shadow_only"] }, evidence_refs: ["evidence:fake"] }],
    scenes: [{ entity_ref: "scenes:fake", kind: "cognition.observed", projection_version: 1, as_of: 1787126280, summary: { scene_version: 4 }, evidence_refs: [] }],
    people: [{ entity_ref: "people:fake", kind: "memory.fact_recorded", projection_version: 1, as_of: 1787126200, summary: { status: "active" }, evidence_refs: ["evidence:fake"] }],
    culture: [],
    tasks: [{ entity_ref: "tasks:fake", kind: "capability.result", projection_version: 1, as_of: 1787126220, summary: { task_status: "succeeded", delivery_relevant: true }, evidence_refs: [] }],
    persona: [{ entity_ref: "persona:fake", kind: "persona.mode", projection_version: 1, as_of: 1787126160, summary: { runtime_mode: "SHADOW" }, evidence_refs: [] }],
    governance: [],
    evaluation: [],
    health: [],
  };
  const response = (projection) => ({
    projection,
    scope: { persona_id: "aemeath", group_id: "group-1" },
    as_of: 1787126400,
    cursor: 1,
    projection_version: 1,
    stale: false,
    items: views[projection] || [],
  });
  window.__fakeCommands = [];
  window.AstrBotPluginPage = {
    ready: async () => ({ locale: "zh-CN", theme: "light" }),
    apiGet: async (endpoint, params = {}) => {
      if (endpoint === "bootstrap") {
        return {
          ...response("bootstrap"),
          persona_id: "aemeath",
          available_groups: ["group-1"],
          selected_group_id: "group-1",
          items: Object.keys(views).map((projection) => response(projection)),
        };
      }
      const value = response(endpoint);
      if (!params.entity_ref) return value;
      return { ...value, items: value.items.filter((item) => item.entity_ref === params.entity_ref) };
    },
    apiPost: async (_endpoint, body) => {
      window.__fakeCommands.push(body);
      return { accepted: true, command_id: body.command_id, version: Number(body.expected_version || 0) + 1 };
    },
    subscribeSSE: async (_endpoint, handlers) => {
      window.__fakeSSEError = handlers.onError;
      queueMicrotask(() => handlers.onOpen());
      return "fake-subscription";
    },
    unsubscribeSSE: async () => {},
  };
})();
