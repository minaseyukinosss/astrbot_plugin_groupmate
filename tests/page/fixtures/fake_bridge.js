(() => {
  const now = 1787126400;
  const previewMode = new URLSearchParams(window.location.search).get("mode") === "OFF" ? "OFF" : "SHADOW";
  const actor = (name, id) => ({
    member_ref: `member:${id}`,
    display_name: name,
    avatar_ref: `participant:${id}`,
  });
  const stages = (labels) => labels.map((label, index) => ({
    kind: `STAGE_${index}`,
    label,
    at: now - 60 + index,
    status: "DONE",
  }));
  const trace = (id, offset, options) => ({
    entity_ref: `traces:${id}`,
    kind: "message.trace",
    projection_version: options.version || 1,
    as_of: now - offset,
    evidence_refs: [],
    summary: {
      actor: actor(options.name, id),
      message: {
        summary: options.message,
        media_types: options.media || [],
        parts: options.parts || [{ kind: "text", text: options.message }],
      },
      route: {
        owner: options.owner || "GROUPMATE",
        label: options.route,
        reason: options.routeReason || "未被前置命令或外部插件截获",
      },
      understanding: {
        status: options.understood ? "READY" : "PENDING",
        summary: options.understanding || "尚未进入理解链路",
        diagnostics: options.diagnostics || [],
      },
      decision: {
        outcome: options.outcome || "PENDING",
        pre_gate_outcome: options.outcome || "PENDING",
        label: options.decision || "等待判断",
        reasons: options.reasons || [],
        candidate_response: options.candidateResponse || null,
        reply_diagnostic: options.replyDiagnostic || null,
      },
      delivery: {
        mode: options.mode || "SHADOW",
        status: options.status,
        label: options.result,
      },
      timing: { received_at: now - offset, updated_at: now - offset + 2, total_ms: options.ms || 1200 },
      stages: stages(options.stages || ["NapCat 消息已到达 AstrBot"]),
    },
  });

  const views = {
    runtime: [{
      entity_ref: "runtime:current",
      kind: "runtime.mode",
      projection_version: 3,
      as_of: now,
      summary: { runtime_mode: previewMode, paused: false },
      evidence_refs: [],
    }],
    traces: [
      trace("media-image", 4, {
        name: "大大方方",
        message: "图片",
        media: ["image"],
        parts: [{
          kind: "image",
          label: "图片",
          name: "群聊图片",
          size: 184320,
          preview: "image",
          media_ref: "media:fixture-image",
        }],
        route: "进入 Groupmate",
        status: "RECEIVED",
        result: "等待处理",
        stages: ["NapCat 消息已到达 AstrBot", "AstrBot 已路由至 Groupmate"],
      }),
      trace("media-audio", 8, {
        name: "大大方方",
        message: "语音 · 文件",
        media: ["record", "file"],
        parts: [
          { kind: "record", label: "语音", size: 35840 },
          { kind: "file", label: "文件", name: "发布清单.pdf", size: 245760 },
        ],
        route: "进入 Groupmate",
        status: "RECEIVED",
        result: "等待处理",
        stages: ["NapCat 消息已到达 AstrBot", "AstrBot 已路由至 Groupmate"],
      }),
      trace("a1", 12, {
        name: "阿杰",
        message: "@小雨 · 产品今晚能上线吗？如果有风险也一起说下。",
        parts: [
          {
            kind: "at",
            label: "@小雨",
            display_name: "小雨",
            member_ref: "member:b2",
            avatar_ref: "participant:b2",
          },
          { kind: "text", text: " 产品今晚能上线吗？如果有风险也一起说下。" },
        ],
        route: "进入 Groupmate",
        understood: true,
        understanding: "成员在询问上线进度，并希望同时了解发布风险",
        diagnostics: [
          { worker: "direct_interaction", status: "SUCCEEDED", latency_ms: 118, diagnostic_code: null },
          { worker: "social_risk", status: "SUCCEEDED", latency_ms: 96, diagnostic_code: null },
        ],
        outcome: "ACT",
        decision: "准备回复",
        reasons: ["这是一个明确问题，当前上下文足够回答"],
        candidateResponse: "当前还有两项发布风险需要确认，建议确认后再上线。",
        status: "BLOCKED_BY_SHADOW",
        result: "SHADOW：已完成判断，未发送",
        stages: ["NapCat 消息已到达 AstrBot", "AstrBot 已路由至 Groupmate", "观察群聊上下文", "已理解当前群聊场景", "准备回复", "回复方案已生成"],
      }),
      trace("b2", 58, {
        name: "小雨",
        message: "感觉这个风险有点大，先别急着发吧。",
        route: "进入 Groupmate",
        understood: true,
        understanding: "成员表达了对当前发布风险的担忧",
        outcome: "SILENCE",
        decision: "保持沉默",
        reasons: ["当前不需要额外插话"],
        status: "SILENT",
        result: "本次不回复",
        stages: ["NapCat 消息已到达 AstrBot", "AstrBot 已路由至 Groupmate", "观察群聊上下文", "已理解当前群聊场景", "保持沉默"],
      }),
      trace("c3", 93, {
        name: "老张",
        message: "bq 熊猫头 加班",
        owner: "EXTERNAL_PLUGIN",
        route: "交给外部能力",
        routeReason: "匹配已配置的外部触发规则",
        status: "HANDED_OFF",
        result: "结果由外部插件负责",
        stages: ["NapCat 消息已到达 AstrBot"],
      }),
      trace("d4", 152, {
        name: "青禾",
        message: "https://v.douyin.com/example/",
        owner: "EXTERNAL_PLUGIN",
        route: "交给外部能力",
        routeReason: "识别到已配置的视频链接",
        status: "HANDED_OFF",
        result: "结果由外部插件负责",
        stages: ["NapCat 消息已到达 AstrBot"],
      }),
      trace("e5", 226, {
        name: "小白",
        message: "刚才那条好像没有发出来？",
        route: "进入 Groupmate",
        understood: true,
        understanding: "成员在确认上一条回复的发送结果",
        outcome: "ACT",
        decision: "准备回复",
        reasons: ["需要说明当前发送状态"],
        mode: "SOCIAL_RUNTIME",
        status: "UNKNOWN",
        result: "发送结果未知",
        stages: ["NapCat 消息已到达 AstrBot", "AstrBot 已路由至 Groupmate", "已理解当前群聊场景", "准备回复", "发送结果未知"],
      }),
    ],
    health: [],
    persona: [{
      entity_ref: "persona:profile",
      kind: "persona.profile",
      projection_version: 3,
      as_of: now,
      summary: { config_version: 3, profile: { identity: { name: "群聊观察员" } } },
      evidence_refs: [],
    }],
    governance: [{
      entity_ref: "governance:3",
      kind: "config.published",
      projection_version: 3,
      as_of: now,
      summary: { status: "PUBLISHED", config_version: 3 },
      evidence_refs: [],
    }],
  };
  const response = (projection) => ({
    projection,
    scope: { persona_id: "groupmate:default", group_id: "72819823" },
    as_of: now,
    cursor: 42,
    projection_version: 3,
    stale: false,
    degraded: false,
    fallback_poll_seconds: 15,
    items: views[projection] || [],
  });

  window.__fakeCommands = [];
  let fixtureImagePromise;
  const fixtureImageData = async () => {
    if (!fixtureImagePromise) {
      fixtureImagePromise = fetch("/pages/settings/assets/groupmate-bot.png")
        .then((response) => response.blob())
        .then((blob) => new Promise((resolve, reject) => {
          const reader = new FileReader();
          reader.onload = () => resolve(String(reader.result || ""));
          reader.onerror = reject;
          reader.readAsDataURL(blob);
        }));
    }
    return fixtureImagePromise;
  };
  window.AstrBotPluginPage = {
    ready: async () => ({ locale: "zh-CN", theme: "dark" }),
    apiGet: async (endpoint, params = {}) => {
      if (endpoint === "bootstrap") {
        return {
          ...response("bootstrap"),
          persona_id: "groupmate:default",
          available_groups: ["72819823"],
          selected_group_id: "72819823",
          configured_runtime_mode: previewMode,
          runtime_ready: previewMode !== "OFF",
          runtime_blockers: previewMode === "OFF" ? ["运行模式为 OFF"] : [],
          items: Object.keys(views).map((projection) => response(projection)),
        };
      }
      if (endpoint === "avatar") throw new Error("preview uses initials fallback");
      if (endpoint === "media" && params.media_ref === "media:fixture-image") {
        return {
          data_uri: await fixtureImageData(),
          kind: "image",
          mime_type: "image/png",
          name: "群聊图片",
        };
      }
      if (endpoint === "media") throw new Error("preview unavailable");
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
