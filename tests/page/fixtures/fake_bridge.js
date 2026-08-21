(() => {
  const event = (entity_ref, kind, as_of, summary, evidence_refs = []) => ({
    entity_ref,
    kind,
    projection_version: Number(entity_ref.split(":").at(-1)) || 1,
    as_of,
    summary,
    evidence_refs,
  });
  const views = {
    runtime: [event("runtime:18", "runtime.mode", 1787126400, { runtime_mode: "SHADOW", paused: false })],
    activity: [
      event("activity:42", "attention.frame_created", 1787126388, { direct_request: true, status: "active" }, ["message:88421"]),
      event("activity:41", "cognition.observed", 1787126372, { status: "active", constraints: ["shadow_only"] }, ["scene:118"]),
      event("activity:40", "governor.decided", 1787126355, { disposition: "RESPOND", reason_codes: ["direct_request", "helpful_response"] }, ["decision:771"]),
      event("activity:39", "memory.fact_recorded", 1787126290, { status: "active" }, ["member:masked"]),
      event("activity:38", "delivery.unknown", 1787126210, { result_status: "UNKNOWN" }, ["delivery:masked"]),
    ],
    scenes: [
      event("scenes:118", "group_world.projected", 1787126370, { scene_version: 18, status: "active" }, ["message:88421", "message:88418"]),
      event("scenes:117", "cognition.observed", 1787126265, { scene_version: 17, status: "active" }, ["message:88410"]),
    ],
    people: [event("people:14", "memory.fact_recorded", 1787126200, { status: "active" }, ["evidence:member-safe-14"])],
    culture: [],
    tasks: [
      event("tasks:31", "task.completed", 1787126338, { task_status: "succeeded", task_title: "整理群内讨论结论", delivery_relevant: true }),
      event("tasks:30", "task.started", 1787126278, { task_status: "running", task_title: "更新成员关系摘要", delivery_relevant: false }),
      event("tasks:29", "task.failed", 1787126188, { task_status: "failed", task_title: "外部能力调用", result_status: "failed" }, ["capability:failure-safe"]),
    ],
    persona: [
      event("persona:23", "persona.profile", 1787126400, {
        config_version: 23,
        profile: { identity: { name: "群聊观察员" } },
        runtime_mode: "SHADOW",
      }),
    ],
    governance: [event("governance:23", "config.published", 1787126000, { status: "PUBLISHED", config_version: 23 })],
    evaluation: [
      event("evaluation:771", "evaluation.shadow_decision_captured", 1787126355, {
        history: [
          { actor: "成员甲", summary: "询问今晚能否上线新功能" },
          { actor: "成员乙", summary: "补充发布窗口和风险背景" },
        ],
        focus: { actor: "成员甲", summary: "今晚能上线吗？需要注意什么？" },
        attention: { trigger_kind: "direct_request", urgency: "medium", deadline: "本轮对话" },
        target: "member:masked-a",
        candidate_response: "可以给出当前进度，同时说明发布风险和待确认项。",
        candidate_actions: [{ kind: "reply", proposed_act: "provide_status_with_risk_note" }],
        suggested_categories: ["提供帮助", "风险提醒"],
        outcome: "RESPOND",
        reason_codes: ["direct_request", "context_relevant"],
        constraints: ["shadow_only", "no_raw_identity"],
        expires_at: "本轮结束",
      }, ["message:88421", "scene:118", "policy:social-response"]),
      event("evaluation:770", "evaluation.shadow_decision_captured", 1787126240, {
        focus: { actor: "成员丙", summary: "发来一条普通闲聊消息" },
        attention: { trigger_kind: "ambient_chat", urgency: "low" },
        target: "group:current",
        candidate_response: "接住话题，但当前参与收益不足。",
        candidate_actions: [],
        suggested_categories: ["群内闲聊"],
        outcome: "SILENCE",
        reason_codes: ["low_marginal_value"],
        constraints: ["shadow_only"],
      }, ["message:88410", "scene:117"]),
      event("evaluation:769", "calibration.shadow_candidate_evaluated", 1787126130, {
        focus: { actor: "成员丁", summary: "分享视频链接" },
        attention: { trigger_kind: "link_detected", urgency: "low" },
        target: "capability:video-parser",
        candidate_actions: [{ kind: "plugin", proposed_act: "parse_video_link" }],
        outcome: "ACTION",
        reason_codes: ["explicit_capability_match"],
        constraints: ["shadow_only"],
      }, ["message:88396", "capability:video-parser"]),
    ],
    health: [],
  };
  const response = (projection) => ({
    projection,
    scope: { persona_id: "groupmate", group_id: "群聊伙伴实验群" },
    as_of: 1787126400,
    cursor: 42,
    projection_version: 23,
    stale: false,
    degraded: false,
    fallback_poll_seconds: 15,
    items: views[projection] || [],
  });
  window.__fakeCommands = [];
  window.AstrBotPluginPage = {
    ready: async () => ({ locale: "zh-CN", theme: "dark" }),
    apiGet: async (endpoint, params = {}) => {
      if (endpoint === "bootstrap") {
        return {
          ...response("bootstrap"),
          persona_id: "groupmate",
          available_groups: ["群聊伙伴实验群"],
          selected_group_id: "群聊伙伴实验群",
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
