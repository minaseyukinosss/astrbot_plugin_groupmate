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
        address_kind: options.addressKind,
        matched_alias: options.matchedAlias,
        addressed_to_bot: Boolean(options.addressKind),
        address_reason: options.addressReason,
      },
      understanding: {
        status: options.understandingStatus || (options.understood ? "READY" : "PENDING"),
        summary: options.understanding || "尚未进入理解链路",
        diagnostics: options.diagnostics || [],
        candidate_count: options.candidateCount || 0,
        candidate_source: options.candidateSource || "none",
        participation_diagnostics: options.participationDiagnostics || [],
      },
      judgement: options.judgement,
      decision: {
        outcome: options.outcome || "PENDING",
        pre_gate_outcome: options.outcome || "PENDING",
        participation_lane: options.lane,
        would_reply: options.wouldReply,
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
      expression: options.expression,
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
        addressKind: "ALIAS_PREFIX",
        matchedAlias: "小爱",
        addressReason: "命中人格别称：小爱",
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
          { worker: "level0.rules", status: "SUCCEEDED", latency_ms: 0, diagnostic_code: null },
        ],
        lane: "DIRECT_FAST",
        wouldReply: true,
        candidateCount: 1,
        candidateSource: "deterministic",
        participationDiagnostics: ["deterministic_direct_fast"],
        outcome: "ACT",
        decision: "准备回复",
        reasons: ["这是一个明确问题，当前上下文足够回答"],
        judgement: {
          source: "policy",
          status: "not_required",
          decision: "speak",
          would_reply: true,
          label: "准备回复",
        },
        candidateResponse: "当前还有两项发布风险需要确认，建议确认后再上线。",
        expression: {
          reaction_stance: "attentive",
          core_response_goal: "respond_to_direct_interaction",
          followup_hook: "optional_if_natural",
          message_count: 1,
        },
        status: "BLOCKED_BY_SHADOW",
        result: "SHADOW：已完成判断，未发送",
        stages: ["NapCat 消息已到达 AstrBot", "AstrBot 已路由至 Groupmate", "观察群聊上下文", "已理解当前群聊场景", "准备回复", "回复方案已生成"],
      }),
      trace("b2", 58, {
        name: "小雨",
        message: "感觉这个风险有点大，先别急着发吧。",
        route: "进入 Groupmate",
        understandingStatus: "DEGRADED",
        understanding: "普通群聊理解未完成",
        diagnostics: [
          { worker: "level0.rules", status: "SUCCEEDED", latency_ms: 0, diagnostic_code: null },
          {
            worker: "ambient_social_assessor",
            status: "TIMED_OUT",
            latency_ms: 6000,
            diagnostic_code: "direct_timeout",
            queue_wait_ms: 0,
            provider_latency_ms: 6000,
            input_bytes: 4096,
            timeout_ms: 8000,
            backend: "direct_deepseek",
            model: "deepseek-v4-flash",
          },
        ],
        lane: "AMBIENT",
        wouldReply: false,
        candidateSource: "none",
        participationDiagnostics: ["model_gated_ambient"],
        outcome: "OBSERVE",
        decision: "继续观察",
        reasons: ["认知降级或参与条件不足"],
        judgement: {
          source: "model",
          status: "unavailable",
          decision: null,
          would_reply: false,
          label: "模型判断未采用",
        },
        status: "OBSERVED",
        result: "SHADOW：仅观察，不发送",
        stages: ["NapCat 消息已到达 AstrBot", "AstrBot 已路由至 Groupmate", "观察群聊上下文", "认知未在时限内完成", "继续观察"],
      }),
      trace("b3", 76, {
        name: "青禾",
        message: "这个报错有人知道怎么处理吗？",
        route: "进入 Groupmate",
        understood: true,
        understandingStatus: "READY",
        understanding: "成员公开求助，当前上下文适合提供帮助",
        diagnostics: [
          { worker: "level0.rules", status: "SUCCEEDED", latency_ms: 0, diagnostic_code: null },
          {
            worker: "ambient_social_assessor",
            status: "SUCCEEDED",
            latency_ms: 1420,
            diagnostic_code: null,
            queue_wait_ms: 0,
            provider_latency_ms: 1410,
            input_bytes: 3584,
            timeout_ms: 8000,
            backend: "direct_deepseek",
            model: "deepseek-v4-flash",
          },
        ],
        lane: "AMBIENT",
        wouldReply: true,
        candidateCount: 1,
        candidateSource: "model",
        participationDiagnostics: ["model_gated_ambient"],
        outcome: "ACT",
        decision: "准备回复",
        reasons: ["识别到有明确证据的公开求助"],
        judgement: {
          source: "model",
          status: "accepted",
          decision: "speak",
          would_reply: true,
          label: "准备回复",
          reason: "成员公开求助，关键证据来自当前公开群聊消息。",
          evidence: {
            actor: actor("青禾", "b3"),
            message: {
              summary: "这个报错有人知道怎么处理吗？",
              media_types: [],
              parts: [{ kind: "text", text: "这个报错有人知道怎么处理吗？" }],
            },
          },
        },
        candidateResponse: "可以先把报错第一行和前后的日志贴一下。",
        expression: {
          reaction_stance: "attentive",
          core_response_goal: "answer_help_request",
          followup_hook: "only_if_it_adds_value",
          message_count: 1,
        },
        status: "BLOCKED_BY_SHADOW",
        result: "SHADOW：已完成判断，未发送",
        stages: ["NapCat 消息已到达 AstrBot", "AstrBot 已路由至 Groupmate", "观察群聊上下文", "已理解当前群聊场景", "准备回复"],
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
        lane: "CONTINUATION",
        wouldReply: true,
        candidateCount: 1,
        candidateSource: "deterministic",
        participationDiagnostics: ["deterministic_continuation"],
        outcome: "ACT",
        decision: "准备回复",
        reasons: ["需要说明当前发送状态"],
        judgement: {
          source: "policy",
          status: "not_required",
          decision: "speak",
          would_reply: true,
          label: "准备回复",
        },
        expression: {
          reaction_stance: "continue_current_exchange",
          core_response_goal: "continue_dialogue",
          followup_hook: "optional_if_natural",
          message_count: 1,
        },
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
    profiles: [
      {
        entity_ref: "profiles:member:a1", kind: "member.profile", projection_version: 8, as_of: now,
        evidence_refs: [], summary: {
          ...actor("玲151", "a1"), one_line_portrait: "会持续追问到问题真正落地",
          maturity: "established", group_roles: ["体验把关者"], fact_count: 9,
          episode_count: 3, relation_count: 2, personalization_enabled: true, updated_at: now,
        },
      },
      {
        entity_ref: "profiles:member:b2", kind: "member.profile", projection_version: 5, as_of: now - 120,
        evidence_refs: [], summary: {
          ...actor("小赛151", "b2"), one_line_portrait: "擅长把复杂技术问题拆成可执行步骤",
          maturity: "forming", group_roles: ["技术同伴"], fact_count: 5,
          episode_count: 2, relation_count: 1, personalization_enabled: true, updated_at: now - 120,
        },
      },
      {
        entity_ref: "profiles:member:c3", kind: "member.profile", projection_version: 1, as_of: now - 300,
        evidence_refs: [], summary: {
          ...actor("青禾", "c3"), one_line_portrait: "画像正在形成", maturity: "new",
          group_roles: [], fact_count: 1, episode_count: 0, relation_count: 0,
          personalization_enabled: true, updated_at: now - 300,
        },
      },
    ],
    "group-portrait": [{
      entity_ref: "group-portrait:current", kind: "group.profile", projection_version: 8,
      as_of: now, evidence_refs: [], summary: {
        summary: "这个群重视问题落地、习惯直接反馈",
        common_topics: ["插件开发", "群聊体验", "上线问题"], activity_rhythm: "晚间活跃",
        role_counts: { 体验把关者: 1, 技术同伴: 1 }, relation_counts: { technical_peer: 1 },
        member_count: 3, source_revision: 8, generated_at: now,
      },
    }],
  };
  const profileDetail = {
    projection: "profile", scope: { persona_id: "groupmate:default", group_id: "72819823" },
    as_of: now, cursor: 8, projection_version: 8, stale: false, items: [{
      entity_ref: "profile:member:a1", kind: "member.profile.detail", projection_version: 8,
      as_of: now, evidence_refs: [], summary: {
        member: actor("玲151", "a1"), personalization_enabled: true, profile_revision: 8,
        snapshot: {
          one_line_portrait: "会持续追问到问题真正落地",
          group_roles: ["体验把关者"],
          individual_fingerprints: ["对模糊结论会继续追问", "能快速发现线上体验与设计预期的差距", "更看重真实可用而不是技术自证"],
          preferences_and_boundaries: ["喜欢直接、清楚、能验证的说明", "不接受只展示过程却没有明确结论"],
          relationship_summary: "长期共同打磨 Groupmate 的开发同伴",
          maturity: "established", source_revision: 8, generated_at: now,
        },
        facts: [
          { fact_ref: "fact:1", category: "沟通偏好", summary: "喜欢直接、清楚、能验证的说明", status: "confirmed", evidence_count: 4, confidence: 0.96 },
          { fact_ref: "fact:2", category: "行为模式", summary: "对没有落地的问题会持续跟进", status: "confirmed", evidence_count: 6, confidence: 0.94 },
        ],
        episodes: [
          { episode_ref: "episode:1", title: "一起修复线上图片错行", summary: "持续对比真实群聊与预览效果，直到字体和间距问题落地。", occurred_at: now - 86400 },
          { episode_ref: "episode:2", title: "确定策略优先、模型增强", summary: "在两天 SHADOW 数据后推动参与判断从单纯模型调用转为可靠策略主线。", occurred_at: now - 172800 },
        ],
        relations: [{
          other_member_ref: "member:b2", other_display_name: "小赛151", other_avatar_ref: "participant:b2",
          relation_type: "technical_peer", direction: "bidirectional", strength: 0.78,
          confidence: 0.91, status: "confirmed", last_observed_at: now,
        }],
        audit: [{ action_type: "画像快照已更新", created_at: now }],
      },
    }],
  };
  const libraryScope = { kind: "library" };
  const knowledgeScope = { kind: "group", group_id: "72819823" };
  const releaseStates = [
    { version_slot_id: "slot:delta:current", entity_id: "game:delta", canonical_name: "三角洲行动", official_label: "S7", official_state: "released", region: "CN", platform: "all", last_successful_check_at: now - 1800, last_attempt_at: now - 900, fresh_until: now + 86400, fresh: true, status: "active", revision: 3 },
    { version_slot_id: "slot:wuthering:next", entity_id: "game:wuthering", canonical_name: "鸣潮", official_label: null, official_state: "none", region: "CN", platform: "all", last_successful_check_at: now - 86400, last_attempt_at: now - 600, fresh_until: now - 120, fresh: false, status: "active", revision: 2 },
  ];
  const recentKnowledgeUsage = [
    { source_domains: ["df.qq.com"], latency_ms: 24, cache_hit: false, result_kind: "grounded_reply", diagnostic: null, recorded_at: now - 60 },
    { source_domains: [], latency_ms: 3, cache_hit: true, result_kind: "local_resolution", diagnostic: null, recorded_at: now - 180 },
  ];
  const knowledgeViews = {
    "knowledge/library/overview": {
      scope: libraryScope,
      as_of: now,
      revision: 8,
      counts: { entities: 18, active_claims: 42, disputed_claims: 1, stale_claims: 2, global_jobs: 1 },
      operations: {
        window_seconds: 86400,
        local_resolution: { count: 184, p95_ms: 8 },
        provider: { calls: 12, cache_hits: 31, quota_rejects: 0 },
        queue: { depth: 1, running: 0, job_lag_seconds: 0 },
        freshness: { stale_slots: 1, max_lag_seconds: 120 },
        safety: { scene_invalidations: 2, grounding_rejects: 0, ambient_silences: 9 },
      },
      release_states: releaseStates,
    },
    "knowledge/group/overview": {
      scope: knowledgeScope,
      as_of: now,
      revision: 8,
      counts: { review_conventions: 2, retry_jobs: 1 },
      ambient_canary_enabled: false,
      popular_games: [
        { entity_id: "game:delta", canonical_name: "三角洲行动", status: "active", salience: 0.92, qualified_mention_count: 34, distinct_actor_count: 8, distinct_scene_count: 11, last_seen_at: now },
        { entity_id: "game:wuthering", canonical_name: "鸣潮", status: "active", salience: 0.74, qualified_mention_count: 21, distinct_actor_count: 6, distinct_scene_count: 8, last_seen_at: now - 1200 },
        { entity_id: "game:starrail", canonical_name: "崩坏：星穹铁道", status: "active", salience: 0.51, qualified_mention_count: 13, distinct_actor_count: 5, distinct_scene_count: 5, last_seen_at: now - 3600 },
      ],
      recent_usage: recentKnowledgeUsage,
    },
    "knowledge/library/entities": { scope: libraryScope, revision: 8, next_cursor: null, items: [
      { entity_id: "game:delta", entity_type: "game", canonical_name: "三角洲行动", canonical_game_id: "game:delta", canonical_game_name: "三角洲行动", status: "active", revision: now - 1800 },
      { entity_id: "game:wuthering", entity_type: "game", canonical_name: "鸣潮", canonical_game_id: "game:wuthering", canonical_game_name: "鸣潮", status: "active", revision: now - 86400 },
    ] },
    "knowledge/library/claims": {
      scope: libraryScope, revision: 8, next_cursor: null, items: [
        { claim_id: "claim:delta:genre", entity_id: "game:delta", canonical_name: "三角洲行动", predicate: "genre", safe_summary: "多人战术射击游戏", claim_kind: "stable_semantic", evidence_level: "official", status: "active", checked_at: now - 1800, revision: 3, sources: [{ domain: "df.qq.com", source_class: "official" }] },
        { claim_id: "claim:wuthering:next", entity_id: "game:wuthering", canonical_name: "鸣潮", predicate: "next_version", safe_summary: "下一版本资料需要重新核验", claim_kind: "public_fact", evidence_level: "secondary", status: "stale", checked_at: now - 86400, revision: 2, sources: [] },
      ],
    },
    "knowledge/group/aliases": { scope: knowledgeScope, revision: 8, next_cursor: null, items: [
      { alias_id: "alias:group:wuthering", entity_id: "game:wuthering", canonical_name: "鸣潮", expression: "潮", confidence: 1, last_used_at: now - 300, status: "active" },
    ] },
    "knowledge/group/conventions": {
      scope: knowledgeScope, revision: 8, next_cursor: null, items: [
        { convention_id: "convention:delta", expression: "洲", entity_id: "game:delta", canonical_name: "三角洲行动", meaning_summary: "群内通常指三角洲行动", alias_id: null, scope: "group", group_id: "72819823", status: "candidate", confidence: 0.7, distinct_actor_count: 4, distinct_scene_count: 3, first_seen_at: now - 86400, last_seen_at: now - 600, revision: 7 },
        { convention_id: "convention:wuthering", expression: "潮", entity_id: "game:wuthering", canonical_name: "鸣潮", meaning_summary: "群内简称鸣潮", alias_id: "alias:group:wuthering", scope: "group", group_id: "72819823", status: "active", confidence: 1, distinct_actor_count: 6, distinct_scene_count: 5, first_seen_at: now - 172800, last_seen_at: now - 300, revision: 6 },
      ],
    },
    "knowledge/group/jobs": {
      scope: knowledgeScope, revision: 8, next_cursor: null, items: [
        { job_id: "job:learning", job_kind: "unknown_entity_learning", scope: "group", group_id: "72819823", entity_id: "game:delta", canonical_name: "三角洲行动", status: "retry", attempt: 2, next_attempt_at: now + 3600, diagnostic: "source_unavailable", created_at: now - 7200, revision: 5 },
      ],
    },
    "knowledge/group/usage": { scope: knowledgeScope, revision: 8, next_cursor: null, items: recentKnowledgeUsage },
    "knowledge/library/jobs": { scope: libraryScope, revision: 8, next_cursor: null, items: [
      { job_id: "job:official", job_kind: "official_daily_probe", scope: "library", group_id: null, entity_id: "game:delta", canonical_name: "三角洲行动", status: "completed", attempt: 1, next_attempt_at: now + 86400, diagnostic: null, created_at: now - 3600, revision: now - 600 },
    ] },
    "knowledge/library/entity-detail": {
      scope: libraryScope,
      revision: 8,
      entity: { entity_id: "game:delta", entity_type: "game", canonical_name: "三角洲行动", canonical_game_id: "game:delta", canonical_game_name: "三角洲行动", status: "active" },
      related_entities: [{ entity_id: "character:delta:red-wolf", entity_type: "character", canonical_name: "红狼", status: "active" }],
      release_states: [{ version_slot_id: "slot:delta:current", official_label: "S7", official_state: "released", region: "CN", platform: "all", release_at: now - 86400, fresh_until: now + 86400, status: "active", revision: 3 }],
      claims_truncated: false,
      claims: [{ claim_id: "claim:delta:s7", predicate: "current_version", safe_summary: "S7 已正式发布", claim_kind: "public_fact", evidence_level: "official", status: "active", version_slot_id: "slot:delta:current", region: "CN", platform: "all", valid_from: now - 86400, valid_until: null, checked_at: now - 1800, supersedes_claim_id: null, superseded_by_claim_ids: [], sources: [{ publisher: "腾讯游戏", domain: "df.qq.com", source_class: "official", url: "https://df.qq.com/news/s7", published_at: now - 90000, fetched_at: now - 1800, excerpt: "官方公告确认 S7 已正式发布。", relation_kind: "supports" }] }],
    },
    "knowledge/group/entity-context": {
      scope: knowledgeScope,
      revision: 8,
      entity_id: "game:delta",
      affinity: { salience: 0.92, qualified_mention_count: 34, distinct_actor_count: 8, distinct_scene_count: 11, first_seen_at: now - 864000, last_seen_at: now },
      group_aliases: [{ expression: "洲", confidence: 0.9, last_used_at: now - 60, status: "active" }],
      group_conventions: [{ expression: "洲", meaning_summary: "群内通常指三角洲行动", distinct_actor_count: 4, distinct_scene_count: 3, confidence: 0.7, status: "candidate", first_seen_at: now - 86400, last_seen_at: now - 600 }],
    },
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
      if (endpoint === "profile") return profileDetail;
      if (endpoint.startsWith("knowledge/")) {
        const value = knowledgeViews[endpoint] || { scope: knowledgeScope, revision: 8, next_cursor: null, items: [] };
        if (!params.status || !Array.isArray(value.items)) return value;
        return { ...value, items: value.items.filter((item) => item.status === params.status) };
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
