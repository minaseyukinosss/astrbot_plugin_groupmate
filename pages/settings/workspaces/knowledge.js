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

function healthPanel(overview, submitCommand, refresh) {
  const counts = overview?.counts || {};
  const enabled = overview?.ambient_canary_enabled === true;
  const canary = safeAction(
    enabled ? "关闭 AMBIENT 试运行" : "开启 AMBIENT 试运行",
    {
      type: "knowledge_ambient_canary_set",
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
    ["知识实体", counts.entities ?? 0],
    ["有效事实", counts.active_claims ?? 0],
    ["待复核约定", counts.review_conventions ?? 0],
    ["待重试任务", counts.retry_jobs ?? 0],
  ];
  return element("section", { className: "knowledge-health" }, [
    sectionHeading(
      "知识健康",
      "这里展示当前群可用的知识状态；群内热度与公共事实彼此独立。",
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

function popularPanel(overview, submitCommand, refresh) {
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
          safeAction("失效缓存", {
            type: "knowledge_cache_invalidate",
            expected_version: Number(overview?.revision || 0),
            payload: { entity_id: game.entity_id },
          }, submitCommand, refresh, {
            danger: true,
            title: `使 ${game.canonical_name} 的查询缓存失效`,
            submitLabel: "确认失效",
          }),
        ]),
      ))
      : element("p", { className: "knowledge-empty", text: "暂无群内热门游戏，达到学习门槛后会显示。" }),
  ]);
}

function freshnessPanel(overview) {
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
          status(item.fresh ? "active" : "stale"),
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
      expected_version: Number(item.control_revision ?? item.revision ?? 0),
      payload: { convention_id: item.convention_id },
    }, submitCommand, refresh, { title: `确认“${item.expression}”的群内含义`, submitLabel: "确认约定" }));
  }
  if (item.status !== "rejected") {
    actions.push(safeAction("驳回", {
      type: "knowledge_convention_reject",
      expected_version: Number(item.control_revision ?? item.revision ?? 0),
      payload: { convention_id: item.convention_id },
    }, submitCommand, refresh, { danger: true, title: `驳回“${item.expression}”`, submitLabel: "确认驳回" }));
  }
  if (item.alias_id) {
    actions.push(safeAction("纠正指向", {
      type: "knowledge_alias_supersede",
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

function claimRow(item, submitCommand, refresh, controlRevision) {
  return element("tr", {}, [
    element("td", {}, [element("strong", { text: item.canonical_name }), element("small", { text: item.predicate })]),
    element("td", { text: item.safe_summary }),
    element("td", { text: (item.sources || []).map((source) => `${source.domain} · ${source.source_class}`).join("、") || "暂无公开来源" }),
    element("td", { text: formatDate(item.checked_at) }),
    element("td", {}, status(item.status)),
    element("td", { className: "knowledge-actions" }, item.status === "disputed" ? [] : [
      safeAction("标记争议", {
        type: "knowledge_claim_dispute",
        expected_version: Number(controlRevision || 0),
        payload: { claim_id: item.claim_id },
      }, submitCommand, refresh, { danger: true, title: "标记知识事实为有争议", submitLabel: "确认标记" }),
    ]),
  ]);
}

function jobRow(item, submitCommand, refresh, controlRevision) {
  return element("tr", {}, [
    element("td", {}, [element("strong", { text: item.job_kind }), element("small", { text: item.canonical_name || "通用任务" })]),
    element("td", {}, status(item.status)),
    element("td", { text: item.diagnostic || "暂无诊断" }),
    element("td", { text: formatDate(item.next_attempt_at) }),
    element("td", { className: "knowledge-actions" }, item.status === "retry" ? [
      safeAction("立即重试", {
        type: "knowledge_job_retry",
        expected_version: Number(controlRevision || 0),
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

export function renderKnowledge(selectView, submitCommand, refresh, query) {
  const overview = selectView("knowledge/overview") || {};
  const conventions = selectView("knowledge/conventions") || { items: [] };
  const claims = selectView("knowledge/claims") || { items: [] };
  const jobs = selectView("knowledge/jobs") || { items: [] };
  const revision = Number(overview.revision || 0);
  const conventionView = {
    ...conventions,
    items: (conventions.items || []).map((item) => ({ ...item, control_revision: revision })),
  };

  return element("div", { className: "knowledge-workspace workspace-stack" }, [
    healthPanel(overview, submitCommand, refresh),
    element("div", { className: "knowledge-overview-grid" }, [
      popularPanel(overview, submitCommand, refresh),
      freshnessPanel(overview),
    ]),
    conflictPanel(claims.items, conventions.items),
    pagedTable({
      title: "群内约定",
      description: "只影响当前群；确认不会创建全局别名。",
      endpoint: "knowledge/conventions",
      initialView: conventionView,
      columns: ["表达", "指向", "证据覆盖", "状态", "操作"],
      statusOptions: ["candidate", "active", "disputed", "stale", "rejected"],
      rowFor: (item) => conventionRow({ ...item, control_revision: revision }, submitCommand, refresh),
      query,
    }),
    pagedTable({
      title: "公共事实",
      description: "来源与核验时间明确；有争议或过期内容不会直接作为确定事实。",
      endpoint: "knowledge/claims",
      initialView: claims,
      columns: ["实体", "安全摘要", "来源", "核验时间", "状态", "操作"],
      statusOptions: ["active", "pending", "disputed", "stale", "rejected", "superseded"],
      rowFor: (item) => claimRow(item, submitCommand, refresh, revision),
      query,
    }),
    pagedTable({
      title: "后台任务",
      description: "手动重试只调整任务到期时间，不会在页面请求内直接访问网络。",
      endpoint: "knowledge/jobs",
      initialView: jobs,
      columns: ["任务", "状态", "诊断", "下次尝试", "操作"],
      statusOptions: ["pending", "running", "retry", "completed", "discarded"],
      rowFor: (item) => jobRow(item, submitCommand, refresh, revision),
      query,
    }),
    usagePanel(overview),
  ]);
}
