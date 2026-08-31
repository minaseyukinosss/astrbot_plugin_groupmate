import { element } from "../components/dom.js";
import { governedAction } from "../components/command-dialog.js";

const STATUS_LABELS = Object.freeze({
  active: "有效",
  candidate: "待复核",
  disputed: "有争议",
  stale: "已过期",
  rejected: "已驳回",
  superseded: "已替代",
  pending: "等待中",
  running: "运行中",
  retry: "待重试",
  completed: "已完成",
  discarded: "已停止",
});

const OFFICIAL_LABELS = Object.freeze({
  none: "尚未检索到可靠公开资料",
  teaser: "已有官方预告",
  preview: "已有官方前瞻",
  notice: "已有官方公告",
  released: "已正式发布",
});

function formatDate(value) {
  const timestamp = Number(value || 0);
  if (!timestamp) return "—";
  const milliseconds = timestamp > 10_000_000_000 ? timestamp : timestamp * 1_000;
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(new Date(milliseconds));
}

function status(value) {
  const normalized = String(value || "unknown");
  return element("span", {
    className: "knowledge-status",
    text: STATUS_LABELS[normalized] || normalized,
    dataset: { status: normalized },
  });
}

function sectionHeading(title, description, actions = []) {
  return element("header", { className: "knowledge-section-heading" }, [
    element("div", {}, [
      element("h2", { text: title }),
      element("p", { text: description }),
    ]),
    element("div", { className: "knowledge-section-actions" }, actions),
  ]);
}

function emptyRow(columns, message = "暂无符合条件的记录。") {
  return element("tr", {}, [
    element("td", {
      className: "knowledge-empty",
      text: message,
      attrs: { colspan: String(columns) },
    }),
  ]);
}

function safeAction(label, spec, submitCommand, refresh, options = {}) {
  return governedAction(label, spec, async (command) => {
    await submitCommand(command);
    await refresh();
  }, options);
}

const EVIDENCE_RELATION_LABELS = Object.freeze({
  supports: "支持这条事实",
  refutes: "反驳这条事实",
  context: "提供上下文",
});

function safeSourceHref(value) {
  try {
    const url = new URL(String(value || ""));
    if (!["http:", "https:"].includes(url.protocol)) return null;
    url.search = "";
    url.hash = "";
    return url.href;
  } catch (_error) {
    return null;
  }
}

function detailButton(entityId, openDetail) {
  const button = element("button", {
    className: "button button-quiet knowledge-detail-trigger",
    text: "查看详情",
    attrs: { type: "button" },
  });
  button.addEventListener("click", () => openDetail(entityId, button));
  return button;
}

function detailDefinition(items) {
  return element("dl", { className: "knowledge-detail-definition" }, items.map(([label, value]) =>
    element("div", {}, [
      element("dt", { text: label }),
      element("dd", { text: value || "—" }),
    ]),
  ));
}

function detailSection(title, body, description) {
  return element("section", { className: "knowledge-detail-section" }, [
    element("header", {}, [
      element("h3", { text: title }),
      description ? element("p", { text: description }) : null,
    ]),
    body,
  ]);
}

function sourceEvidence(source) {
  const href = safeSourceHref(source.url);
  const heading = href
    ? element("a", {
      text: `${source.publisher || source.domain} · ${source.domain}`,
      attrs: {
        href,
        target: "_blank",
        rel: "noreferrer noopener",
      },
    })
    : element("strong", { text: `${source.publisher || source.domain} · ${source.domain}` });
  return element("li", { className: "knowledge-detail-source" }, [
    element("div", {}, [
      heading,
      element("span", { text: EVIDENCE_RELATION_LABELS[source.relation_kind] || source.relation_kind }),
    ]),
    element("p", { text: source.excerpt || "该来源没有保留可展示的短证据片段。" }),
    element("small", {
      text: `${source.source_class} · 发布 ${formatDate(source.published_at)} · 获取 ${formatDate(source.fetched_at)}`,
    }),
  ]);
}

function claimDetail(claim, expanded = false) {
  return element("details", {
    className: "knowledge-detail-claim",
    attrs: expanded ? { open: "" } : {},
  }, [
    element("summary", {}, [
      element("span", {}, [
        element("strong", { text: claim.safe_summary }),
        element("small", { text: claim.predicate }),
      ]),
      status(claim.status),
    ]),
    element("div", { className: "knowledge-detail-claim-body" }, [
      detailDefinition([
        ["事实类型", claim.claim_kind],
        ["证据等级", claim.evidence_level],
        ["适用版本", claim.version_slot_id],
        ["区域 / 平台", [claim.region, claim.platform].filter(Boolean).join(" / ")],
        ["有效时间", `${formatDate(claim.valid_from)} — ${formatDate(claim.valid_until)}`],
        ["最近核验", formatDate(claim.checked_at)],
      ]),
      element("h4", { text: "证据链" }),
      (claim.sources || []).length
        ? element("ul", { className: "knowledge-detail-sources" }, claim.sources.map(sourceEvidence))
        : element("p", { className: "knowledge-detail-empty", text: "这条知识没有可展示的公开来源。" }),
      claim.supersedes_claim_id || (claim.superseded_by_claim_ids || []).length
        ? element("p", {
          className: "knowledge-detail-history",
          text: `历史关系：${claim.supersedes_claim_id ? `替代 ${claim.supersedes_claim_id}` : ""}${claim.supersedes_claim_id && claim.superseded_by_claim_ids?.length ? "；" : ""}${claim.superseded_by_claim_ids?.length ? `已被 ${claim.superseded_by_claim_ids.join("、")} 替代` : ""}`,
        })
        : null,
    ]),
  ]);
}

function createKnowledgeDetail(query) {
  let returnFocus = null;
  const title = element("h2", { text: "知识详情", attrs: { id: "knowledge-detail-title" } });
  const subtitle = element("p", { text: "正在读取 Bot 保存的知识…" });
  const content = element("div", {
    className: "knowledge-detail-content",
    attrs: { role: "status", "aria-live": "polite" },
  });
  const close = element("button", {
    className: "icon-button",
    text: "×",
    attrs: { type: "button", "aria-label": "关闭知识详情" },
  });
  const dialog = element("dialog", {
    className: "knowledge-detail",
    attrs: { "aria-labelledby": "knowledge-detail-title" },
  }, [
    element("header", { className: "knowledge-detail-header" }, [
      element("div", {}, [title, subtitle]),
      close,
    ]),
    content,
  ]);

  close.addEventListener("click", () => dialog.close());
  dialog.addEventListener("close", () => {
    returnFocus?.focus?.();
    returnFocus = null;
  });

  async function open(entityId, trigger) {
    returnFocus = trigger || document.activeElement;
    title.replaceChildren("知识详情");
    subtitle.replaceChildren("正在读取 Bot 保存的知识…");
    content.replaceChildren(element("div", { className: "knowledge-detail-loading", text: "加载中…" }));
    if (!dialog.open) dialog.showModal();
    try {
      const [detail, groupContext] = await Promise.all([
        query("knowledge/library/entity-detail", { entity_id: entityId }, { timeoutMs: 4_000 }),
        query("knowledge/group/entity-context", { entity_id: entityId }, { timeoutMs: 4_000 }),
      ]);
      const entity = detail?.entity || {};
      const releases = Array.isArray(detail?.release_states) ? detail.release_states : [];
      const aliases = Array.isArray(groupContext?.group_aliases) ? groupContext.group_aliases : [];
      const conventions = Array.isArray(groupContext?.group_conventions) ? groupContext.group_conventions : [];
      const claims = Array.isArray(detail?.claims) ? detail.claims : [];
      const related = Array.isArray(detail?.related_entities) ? detail.related_entities : [];
      title.replaceChildren(entity.canonical_name || "知识详情");
      const gameOwner = entity.canonical_game_name && entity.canonical_game_name !== entity.canonical_name
        ? ` · 属于 ${entity.canonical_game_name}`
        : "";
      subtitle.replaceChildren(`${entity.entity_type || "实体"}${gameOwner} · ${STATUS_LABELS[entity.status] || entity.status || "未知状态"}`);
      content.replaceChildren(
        element("p", {
          className: "knowledge-detail-privacy",
          text: "这里只展示公开来源和群级统计；原始群聊不会在这里展示。",
        }),
        detailSection("共享公开知识 · 版本状态", releases.length
          ? element("ul", { className: "knowledge-detail-releases" }, releases.map((item) =>
            element("li", {}, [
              element("div", {}, [
                element("strong", { text: item.official_label || "版本未命名" }),
                element("span", { text: OFFICIAL_LABELS[item.official_state] || item.official_state }),
              ]),
              detailDefinition([
                ["区域 / 平台", `${item.region} / ${item.platform}`],
                ["发布时间", formatDate(item.release_at)],
                ["有效至", formatDate(item.fresh_until)],
              ]),
              status(item.status),
            ]),
          ))
          : element("p", { className: "knowledge-detail-empty", text: "暂无版本状态。" })),
        detailSection("当前群语境 · 群内别名", aliases.length
          ? element("ul", { className: "knowledge-detail-aliases" }, aliases.map((item) =>
            element("li", {}, [
              element("strong", { text: item.expression }),
              element("span", { text: `${Math.round(Number(item.confidence || 0) * 100)}% 置信度` }),
              status(item.status),
            ]),
          ))
          : element("p", { className: "knowledge-detail-empty", text: "本群暂无专用别名。" }),
        ),
        detailSection("群内约定", conventions.length
          ? element("ul", { className: "knowledge-detail-conventions" }, conventions.map((item) =>
            element("li", {}, [
              element("div", {}, [element("strong", { text: item.expression }), element("p", { text: item.meaning_summary })]),
              element("small", { text: `${item.distinct_actor_count} 人 · ${item.distinct_scene_count} 场景` }),
              status(item.status),
            ]),
          ))
          : element("p", { className: "knowledge-detail-empty", text: "本群暂无相关约定。" }),
        ),
        detailSection("同游戏相关实体", related.length
          ? element("ul", { className: "knowledge-detail-related" }, related.map((item) =>
            element("li", {}, [
              element("div", {}, [
                element("strong", { text: item.canonical_name }),
                element("span", { text: item.entity_type }),
              ]),
              status(item.status),
              detailButton(item.entity_id, open),
            ]),
          ))
          : element("p", { className: "knowledge-detail-empty", text: "暂无已归类的相关实体。" }),
          `这些实体都归属于 ${entity.canonical_game_name || "同一游戏"}。`,
        ),
        detailSection("Bot 保存的具体知识", claims.length
          ? element("div", { className: "knowledge-detail-claims" }, claims.map((claim, index) => claimDetail(claim, index === 0)))
          : element("p", { className: "knowledge-detail-empty", text: "暂无可审查的具体知识。" }),
          "展开事实可检查其公开证据链、核验时间和历史关系。",
        ),
        detail?.claims_truncated
          ? element("p", { className: "knowledge-detail-limit", text: "仅显示最近的 100 条知识。" })
          : null,
      );
      content.setAttribute("role", "document");
    } catch (_error) {
      subtitle.replaceChildren("读取失败");
      content.replaceChildren(element("p", {
        className: "knowledge-detail-error",
        text: "暂时无法读取这项知识，请稍后重试。",
      }));
    }
  }

  return { dialog, open };
}

function pagedTable({
  title,
  description,
  endpoint,
  initialView,
  columns,
  statusOptions,
  rowFor,
  query,
}) {
  let current = initialView || { items: [], next_cursor: null };
  let items = [...(current.items || [])];
  const body = element("tbody");
  const feedback = element("p", {
    className: "knowledge-table-feedback",
    attrs: { role: "status", "aria-live": "polite" },
  });
  const more = element("button", {
    className: "button button-secondary knowledge-more",
    text: "加载更多",
    attrs: { type: "button" },
  });
  const statusFilter = element("select", {
    attrs: { "aria-label": `${title}状态筛选` },
  }, [element("option", { text: "全部状态", attrs: { value: "" } })]);
  for (const option of statusOptions) {
    statusFilter.append(element("option", {
      text: STATUS_LABELS[option] || option,
      attrs: { value: option },
    }));
  }

  function renderRows() {
    body.replaceChildren(...(
      items.length ? items.map(rowFor) : [emptyRow(columns.length)]
    ));
    more.hidden = !current.next_cursor;
    feedback.replaceChildren(items.length ? `${items.length} 条记录` : "暂无记录");
  }

  async function load({ append = false } = {}) {
    body.setAttribute("aria-busy", "true");
    feedback.replaceChildren("加载中…");
    more.disabled = true;
    try {
      const result = await query(endpoint, {
        status: statusFilter.value || undefined,
        cursor: append ? current.next_cursor : undefined,
      }, { timeoutMs: 4_000 });
      current = result || { items: [], next_cursor: null };
      items = append ? [...items, ...(current.items || [])] : [...(current.items || [])];
      renderRows();
    } catch (_error) {
      feedback.replaceChildren("暂时无法读取，请稍后重试。");
    } finally {
      body.setAttribute("aria-busy", "false");
      more.disabled = false;
    }
  }

  statusFilter.addEventListener("change", () => load());
  more.addEventListener("click", () => load({ append: true }));
  renderRows();

  return element("section", { className: "knowledge-panel" }, [
    sectionHeading(title, description, [statusFilter]),
    element("div", { className: "knowledge-table-wrap" }, [
      element("table", { className: "knowledge-table" }, [
        element("thead", {}, [
          element("tr", {}, columns.map((label) => element("th", {
            text: label,
            attrs: { scope: "col" },
          }))),
        ]),
        body,
      ]),
    ]),
    element("footer", { className: "knowledge-table-footer" }, [feedback, more]),
  ]);
}

function groupHealthPanel(overview, submitCommand, refresh) {
  const counts = overview?.counts || {};
  const enabled = overview?.ambient_canary_enabled === true;
  const canary = safeAction(
    enabled ? "关闭 AMBIENT 试运行" : "开启 AMBIENT 试运行",
    {
      type: "knowledge_ambient_canary_set",
      knowledge_scope: "group",
      expected_version: Number(overview?.revision || 0),
      payload: { enabled: !enabled },
    },
    submitCommand,
    refresh,
    {
      title: enabled ? "关闭本群 AMBIENT 试运行" : "开启本群 AMBIENT 试运行",
      submitLabel: enabled ? "确认关闭" : "确认开启",
    },
  );
  const metrics = [
    ["热门游戏", (overview?.popular_games || []).length],
    ["待复核约定", counts.review_conventions ?? 0],
    ["待重试任务", counts.retry_jobs ?? 0],
    ["近期知识使用", (overview?.recent_usage || []).length],
  ];
  return element("section", { className: "knowledge-health" }, [
    sectionHeading(
      "本群认知",
      "仅影响本群：热度、表达习惯和参与策略不会改写共享公共事实。",
      [canary],
    ),
    element("dl", { className: "knowledge-metrics" }, metrics.map(([label, value]) =>
      element("div", {}, [element("dt", { text: label }), element("dd", { text: value })]),
    )),
    element("p", {
      className: "knowledge-canary-state",
      text: enabled
        ? "AMBIENT 试运行：已对本群开启，仍受参与策略和搜索额度约束。"
        : "AMBIENT 试运行：关闭。DIRECT 与 CONTINUATION 的根据回复不受影响。",
    }),
  ]);
}

function libraryHealthPanel(overview) {
  const counts = overview?.counts || {};
  const metrics = [
    ["知识实体", counts.entities ?? 0],
    ["有效事实", counts.active_claims ?? 0],
    ["争议 / 过期", Number(counts.disputed_claims || 0) + Number(counts.stale_claims || 0)],
    ["全局刷新任务", counts.global_jobs ?? 0],
  ];
  return element("section", { className: "knowledge-health" }, [
    sectionHeading(
      "共享知识库",
      "所有群共享：公共事实、版本和来源不随当前群切换而改变。",
      [element("span", { className: "knowledge-scope-badge", text: "所有群共享" })],
    ),
    element("dl", { className: "knowledge-metrics" }, metrics.map(([label, value]) =>
      element("div", {}, [element("dt", { text: label }), element("dd", { text: value })]),
    )),
  ]);
}

function popularPanel(overview, submitCommand, refresh, openDetail) {
  const games = Array.isArray(overview?.popular_games) ? overview.popular_games : [];
  return element("section", { className: "knowledge-panel" }, [
    sectionHeading("群内热门游戏", "只统计当前群的合格提及，不与其他群共享热度。"),
    games.length
      ? element("ol", { className: "knowledge-popular-list" }, games.map((game) =>
        element("li", {}, [
          element("div", {}, [
            element("strong", { text: game.canonical_name }),
            element("span", { text: `${game.qualified_mention_count} 次合格提及 · ${game.distinct_actor_count} 人` }),
          ]),
          element("span", { text: `${Math.round(Number(game.salience || 0) * 100)}% 热度` }),
          element("div", { className: "knowledge-row-actions" }, [detailButton(game.entity_id, openDetail)]),
        ]),
      ))
      : element("p", { className: "knowledge-empty", text: "暂无群内热门游戏，达到学习门槛后会显示。" }),
  ]);
}

function freshnessPanel(overview, openDetail) {
  const states = Array.isArray(overview?.release_states) ? overview.release_states : [];
  return element("section", { className: "knowledge-panel" }, [
    sectionHeading("版本核验", "分别显示最近成功核验与最近尝试；过期不等于信息错误，但回复前必须重新核验。"),
    states.length
      ? element("ul", { className: "knowledge-freshness-list" }, states.map((item) =>
        element("li", {}, [
          element("div", {}, [
            element("strong", { text: `${item.canonical_name} · ${item.official_label || "版本未命名"}` }),
            element("span", { text: OFFICIAL_LABELS[item.official_state] || item.official_state }),
          ]),
          element("dl", {}, [
            element("div", {}, [element("dt", { text: "最近成功核验" }), element("dd", { text: formatDate(item.last_successful_check_at) })]),
            element("div", {}, [element("dt", { text: "最近尝试" }), element("dd", { text: formatDate(item.last_attempt_at) })]),
            element("div", {}, [element("dt", { text: "有效至" }), element("dd", { text: formatDate(item.fresh_until) })]),
          ]),
          element("div", { className: "knowledge-row-actions" }, [
            status(item.fresh ? "active" : "stale"),
            detailButton(item.entity_id, openDetail),
          ]),
        ]),
      ))
      : element("p", { className: "knowledge-empty", text: "暂无版本核验记录。" }),
  ]);
}

function conventionRow(item, submitCommand, refresh) {
  const actions = [];
  if (!["active", "rejected"].includes(item.status)) {
    actions.push(safeAction("确认", {
      type: "knowledge_convention_confirm",
      knowledge_scope: "group",
      expected_version: Number(item.control_revision ?? item.revision ?? 0),
      payload: { convention_id: item.convention_id },
    }, submitCommand, refresh, { title: `确认“${item.expression}”的群内含义`, submitLabel: "确认约定" }));
  }
  if (item.status !== "rejected") {
    actions.push(safeAction("驳回", {
      type: "knowledge_convention_reject",
      knowledge_scope: "group",
      expected_version: Number(item.control_revision ?? item.revision ?? 0),
      payload: { convention_id: item.convention_id },
    }, submitCommand, refresh, { danger: true, title: `驳回“${item.expression}”`, submitLabel: "确认驳回" }));
  }
  if (item.alias_id) {
    actions.push(safeAction("纠正指向", {
      type: "knowledge_alias_supersede",
      knowledge_scope: "group",
      expected_version: Number(item.control_revision ?? item.revision ?? 0),
      payload: { alias_id: item.alias_id },
    }, submitCommand, refresh, {
      title: `纠正“${item.expression}”的指向`,
      submitLabel: "确认替换",
      fields: [{ name: "replacement_entity_id", label: "新的实体 ID" }],
    }));
  }
  return element("tr", {}, [
    element("td", {}, [element("strong", { text: item.expression }), element("small", { text: item.meaning_summary })]),
    element("td", { text: item.canonical_name || "未解析" }),
    element("td", { text: `${item.distinct_actor_count} 人 · ${item.distinct_scene_count} 场景` }),
    element("td", {}, status(item.status)),
    element("td", { className: "knowledge-actions" }, actions),
  ]);
}

function aliasRow(item, submitCommand, refresh, controlRevision, openDetail) {
  return element("tr", {}, [
    element("td", {}, [
      element("strong", { text: item.expression }),
      element("small", { text: `${Math.round(Number(item.confidence || 0) * 100)}% 置信度` }),
    ]),
    element("td", { text: item.canonical_name }),
    element("td", { text: formatDate(item.last_used_at) }),
    element("td", {}, status(item.status)),
    element("td", { className: "knowledge-actions" }, [
      detailButton(item.entity_id, openDetail),
      safeAction("纠正指向", {
        type: "knowledge_alias_supersede",
        knowledge_scope: "group",
        expected_version: Number(controlRevision || 0),
        payload: { alias_id: item.alias_id },
      }, submitCommand, refresh, {
        title: `纠正“${item.expression}”的群内指向`,
        submitLabel: "确认替换",
        fields: [{ name: "replacement_entity_id", label: "新的实体 ID" }],
      }),
    ]),
  ]);
}

function entityRow(item, submitCommand, refresh, openDetail) {
  return element("tr", {}, [
    element("td", {}, [
      element("strong", { text: item.canonical_name }),
      element("small", { text: item.entity_id }),
    ]),
    element("td", { text: item.canonical_game_name || "待归类" }),
    element("td", { text: item.entity_type }),
    element("td", {}, status(item.status)),
    element("td", { text: formatDate(item.revision) }),
    element("td", { className: "knowledge-actions" }, [
      detailButton(item.entity_id, openDetail),
      safeAction("失效缓存", {
        type: "knowledge_cache_invalidate",
        knowledge_scope: "library",
        expected_version: Number(item.revision || 0),
        payload: { entity_id: item.entity_id },
      }, submitCommand, refresh, {
        danger: true,
        title: `使 ${item.canonical_name} 的共享查询缓存失效（影响所有群）`,
        submitLabel: "确认全局失效",
      }),
    ]),
  ]);
}

function claimRow(item, submitCommand, refresh, controlRevision, openDetail) {
  return element("tr", {}, [
    element("td", {}, [element("strong", { text: item.canonical_name }), element("small", { text: item.predicate })]),
    element("td", { text: item.safe_summary }),
    element("td", { text: (item.sources || []).map((source) => `${source.domain} · ${source.source_class}`).join("、") || "暂无公开来源" }),
    element("td", { text: formatDate(item.checked_at) }),
    element("td", {}, status(item.status)),
    element("td", { className: "knowledge-actions" }, [
      detailButton(item.entity_id, openDetail),
      ...(item.status === "disputed" ? [] : [safeAction("标记争议", {
        type: "knowledge_claim_dispute",
        knowledge_scope: "library",
        expected_version: Number(item.revision || controlRevision || 0),
        payload: { claim_id: item.claim_id },
      }, submitCommand, refresh, { danger: true, title: "标记知识事实为有争议", submitLabel: "确认标记" })]),
    ]),
  ]);
}

function jobRow(item, submitCommand, refresh, controlRevision, scopeKind) {
  return element("tr", {}, [
    element("td", {}, [element("strong", { text: item.job_kind }), element("small", { text: item.canonical_name || "通用任务" })]),
    element("td", {}, status(item.status)),
    element("td", { text: item.diagnostic || "暂无诊断" }),
    element("td", { text: formatDate(item.next_attempt_at) }),
    element("td", { className: "knowledge-actions" }, item.status === "retry" ? [
      safeAction("立即重试", {
        type: "knowledge_job_retry",
        knowledge_scope: scopeKind,
        expected_version: Number(scopeKind === "library" ? item.revision : controlRevision || 0),
        payload: { job_id: item.job_id },
      }, submitCommand, refresh, { title: "让知识任务进入当前到期队列", submitLabel: "确认重试" }),
    ] : []),
  ]);
}

function conflictPanel(claims, conventions) {
  const items = [
    ...(claims || []).filter((item) => ["disputed", "stale"].includes(item.status)).map((item) => `${item.canonical_name}：${item.safe_summary}`),
    ...(conventions || []).filter((item) => ["disputed", "stale"].includes(item.status)).map((item) => `群约定“${item.expression}”：${item.meaning_summary}`),
  ];
  return element("section", { className: "knowledge-panel knowledge-conflicts" }, [
    sectionHeading("冲突与过期", "这些内容不会作为确定事实直接注入回复。"),
    items.length
      ? element("ul", {}, items.map((item) => element("li", { text: item })))
      : element("p", { className: "knowledge-empty", text: "暂无冲突或过期内容。" }),
  ]);
}

function usagePanel(overview) {
  const items = Array.isArray(overview?.recent_usage) ? overview.recent_usage : [];
  return element("section", { className: "knowledge-panel" }, [
    sectionHeading("近期使用", "只显示结果类型、来源域名和诊断，不展示原始查询或群聊内容。"),
    items.length
      ? element("ul", { className: "knowledge-usage-list" }, items.map((item) =>
        element("li", {}, [
          element("time", { text: formatDate(item.recorded_at) }),
          element("strong", { text: item.result_kind }),
          element("span", { text: (item.source_domains || []).join("、") || "本地知识" }),
          element("small", { text: item.diagnostic || `${item.latency_ms} ms` }),
        ]),
      ))
      : element("p", { className: "knowledge-empty", text: "暂无近期使用记录。" }),
  ]);
}

let activeKnowledgeScope = "group";

export function renderKnowledge(selectView, submitCommand, refresh, query) {
  const libraryOverview = selectView("knowledge/library/overview") || {};
  const entities = selectView("knowledge/library/entities") || { items: [] };
  const claims = selectView("knowledge/library/claims") || { items: [] };
  const libraryJobs = selectView("knowledge/library/jobs") || { items: [] };
  const groupOverview = selectView("knowledge/group/overview") || {};
  const aliases = selectView("knowledge/group/aliases") || { items: [] };
  const conventions = selectView("knowledge/group/conventions") || { items: [] };
  const groupUsage = selectView("knowledge/group/usage") || { items: [] };
  const groupJobs = selectView("knowledge/group/jobs") || { items: [] };
  const groupRevision = Number(groupOverview.revision || 0);
  const conventionView = {
    ...conventions,
    items: (conventions.items || []).map((item) => ({ ...item, control_revision: groupRevision })),
  };
  const detail = createKnowledgeDetail(query);
  const groupPane = element("div", {
    className: "knowledge-scope-pane workspace-stack",
    attrs: { id: "knowledge-group-pane", role: "tabpanel", "aria-labelledby": "knowledge-group-tab" },
  }, [
    element("p", { className: "knowledge-scope-note", text: "仅影响本群：热门度、简称、群约定和近期使用不会写入共享公共事实。" }),
    groupHealthPanel(groupOverview, submitCommand, refresh),
    popularPanel(groupOverview, submitCommand, refresh, detail.open),
    conflictPanel([], conventions.items),
    pagedTable({
      title: "群内别名",
      description: "这些表达只用于当前群的实体识别，不会成为其他群的默认叫法。",
      endpoint: "knowledge/group/aliases",
      initialView: aliases,
      columns: ["表达", "指向", "最近使用", "状态", "操作"],
      statusOptions: ["candidate", "active", "disputed", "stale", "rejected"],
      rowFor: (item) => aliasRow(item, submitCommand, refresh, groupRevision, detail.open),
      query,
    }),
    pagedTable({
      title: "群内约定",
      description: "只影响当前群；确认不会创建全局别名。",
      endpoint: "knowledge/group/conventions",
      initialView: conventionView,
      columns: ["表达", "指向", "证据覆盖", "状态", "操作"],
      statusOptions: ["candidate", "active", "disputed", "stale", "rejected"],
      rowFor: (item) => conventionRow({ ...item, control_revision: groupRevision }, submitCommand, refresh),
      query,
    }),
    pagedTable({
      title: "群触发任务",
      description: "这里只展示由当前群触发的发现与学习任务，不包含共享库的定时刷新。",
      endpoint: "knowledge/group/jobs",
      initialView: groupJobs,
      columns: ["任务", "状态", "诊断", "下次尝试", "操作"],
      statusOptions: ["pending", "running", "retry", "completed", "discarded"],
      rowFor: (item) => jobRow(item, submitCommand, refresh, groupRevision, "group"),
      query,
    }),
    usagePanel({ recent_usage: groupUsage.items || groupOverview.recent_usage || [] }),
  ]);
  const libraryPane = element("div", {
    className: "knowledge-scope-pane workspace-stack",
    attrs: { id: "knowledge-library-pane", role: "tabpanel", "aria-labelledby": "knowledge-library-tab" },
  }, [
    element("p", { className: "knowledge-scope-note knowledge-scope-note-library", text: "所有群共享：这里的公共事实、版本和来源不受当前群选择影响。" }),
    libraryHealthPanel(libraryOverview),
    freshnessPanel(libraryOverview, detail.open),
    conflictPanel(claims.items, []),
    pagedTable({
      title: "知识实体",
      description: "游戏和相关实体是共享目录；群热度只决定检索优先级。",
      endpoint: "knowledge/library/entities",
      initialView: entities,
      columns: ["实体", "所属游戏", "类型", "状态", "更新时间", "操作"],
      statusOptions: ["candidate", "active", "stale", "superseded", "rejected"],
      rowFor: (item) => entityRow(item, submitCommand, refresh, detail.open),
      query,
    }),
    pagedTable({
      title: "公共事实",
      description: "所有群共享；标记争议后，任何群都不能再把它作为确定事实。",
      endpoint: "knowledge/library/claims",
      initialView: claims,
      columns: ["实体", "安全摘要", "来源", "核验时间", "状态", "操作"],
      statusOptions: ["active", "pending", "disputed", "stale", "rejected", "superseded"],
      rowFor: (item) => claimRow(item, submitCommand, refresh, Number(libraryOverview.revision || 0), detail.open),
      query,
    }),
    pagedTable({
      title: "共享刷新任务",
      description: "这些任务维护所有群共用的公开知识；重试不会在页面请求内直接访问网络。",
      endpoint: "knowledge/library/jobs",
      initialView: libraryJobs,
      columns: ["任务", "状态", "诊断", "下次尝试", "操作"],
      statusOptions: ["pending", "running", "retry", "completed", "discarded"],
      rowFor: (item) => jobRow(item, submitCommand, refresh, Number(libraryOverview.revision || 0), "library"),
      query,
    }),
  ]);
  const groupTab = element("button", {
    className: "knowledge-scope-tab",
    text: "本群认知",
    attrs: { id: "knowledge-group-tab", type: "button", role: "tab", "aria-controls": "knowledge-group-pane" },
  });
  const libraryTab = element("button", {
    className: "knowledge-scope-tab",
    text: "共享知识库",
    attrs: { id: "knowledge-library-tab", type: "button", role: "tab", "aria-controls": "knowledge-library-pane" },
  });
  function activate(scope) {
    activeKnowledgeScope = scope === "library" ? "library" : "group";
    const groupActive = activeKnowledgeScope === "group";
    groupPane.hidden = !groupActive;
    libraryPane.hidden = groupActive;
    groupTab.setAttribute("aria-selected", String(groupActive));
    libraryTab.setAttribute("aria-selected", String(!groupActive));
    groupTab.tabIndex = groupActive ? 0 : -1;
    libraryTab.tabIndex = groupActive ? -1 : 0;
  }
  groupTab.addEventListener("click", () => activate("group"));
  libraryTab.addEventListener("click", () => activate("library"));
  activate(activeKnowledgeScope);

  return element("div", { className: "knowledge-workspace workspace-stack" }, [
    element("div", { className: "knowledge-scope-switch", attrs: { role: "tablist", "aria-label": "知识管理范围" } }, [groupTab, libraryTab]),
    groupPane,
    libraryPane,
    detail.dialog,
  ]);
}
