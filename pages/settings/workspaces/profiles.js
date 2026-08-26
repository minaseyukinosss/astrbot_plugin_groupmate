import { element } from "../components/dom.js";
import { governedAction } from "../components/command-dialog.js";

const MATURITY_LABELS = Object.freeze({
  new: "刚开始了解",
  forming: "逐步形成",
  established: "相对稳定",
});

const RELATION_LABELS = Object.freeze({
  technical_peer: "技术同伴",
  close_friend: "亲近朋友",
  frequent_partner: "常互动",
  playful_rival: "玩笑对手",
  mentor: "经验引导",
});

function avatar(member, large = false) {
  const name = String(member?.display_name || "群成员");
  return element("span", {
    className: `participant-avatar${large ? " participant-avatar-large" : ""}`,
    text: name.slice(0, 1),
    dataset: { avatarRef: member?.avatar_ref || "" },
    attrs: { "aria-hidden": "true" },
  });
}

function list(items, emptyText, className = "profile-value-list") {
  const values = Array.isArray(items) ? items.filter(Boolean) : [];
  if (!values.length) {
    return element("p", { className: "profile-empty-copy", text: emptyText });
  }
  return element("ul", { className }, values.map((value) =>
    element("li", { text: value }),
  ));
}

function section(title, body, className = "") {
  return element("section", { className: `profile-section ${className}`.trim() }, [
    element("h3", { text: title }),
    body,
  ]);
}

function formatDate(value) {
  const timestamp = Number(value || 0);
  if (!timestamp) return "—";
  const milliseconds = timestamp > 10_000_000_000 ? timestamp : timestamp * 1_000;
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false,
  }).format(new Date(milliseconds));
}

function relationRows(relations) {
  if (!relations.length) return element("p", {
    className: "profile-empty-copy",
    text: "还没有形成足够可靠的群友关系。",
  });
  return element("div", { className: "profile-relation-list" }, relations.map((relation) => {
    const other = {
      display_name: relation.other_display_name,
      avatar_ref: relation.other_avatar_ref,
    };
    return element("div", { className: "profile-relation-row" }, [
      avatar(other),
      element("div", {}, [
        element("strong", { text: relation.other_display_name }),
        element("span", { text: RELATION_LABELS[relation.relation_type] || relation.relation_type }),
      ]),
      element("span", {
        className: "profile-strength",
        text: `${Math.round(Number(relation.strength || 0) * 100)}%`,
        attrs: { title: "关系强度" },
      }),
    ]);
  }));
}

function episodeRows(episodes) {
  if (!episodes.length) return element("p", {
    className: "profile-empty-copy",
    text: "共同经历还在积累，需要多次可靠证据才会留下。",
  });
  return element("ol", { className: "profile-episode-list" }, episodes.map((episode) =>
    element("li", {}, [
      element("span", { text: formatDate(episode.occurred_at) }),
      element("div", {}, [
        element("strong", { text: episode.title }),
        element("p", { text: episode.summary }),
      ]),
    ]),
  ));
}

function auditDetails(summary, member, submitCommand, refresh) {
  const facts = Array.isArray(summary.facts) ? summary.facts : [];
  const audit = Array.isArray(summary.audit) ? summary.audit : [];
  const content = element("div", { className: "profile-audit-content" }, [
    element("p", {
      text: `当前有 ${facts.length} 条认知事实，其中 ${facts.filter((fact) => fact.status === "confirmed").length} 条已确认。`,
    }),
    ...facts.map((fact) => element("div", { className: "profile-evidence-row" }, [
      element("span", { text: fact.category }),
      element("p", { text: fact.summary }),
      element("div", { className: "profile-evidence-meta" }, [
        element("small", { text: `${fact.evidence_count} 条证据 · 置信度 ${Math.round(Number(fact.confidence || 0) * 100)}%` }),
        ...(fact.status === "confirmed" ? [
          governedAction("纠正", {
            type: "profile_fact_correct",
            expected_version: summary.profile_revision,
            payload: { member_ref: member.member_ref, fact_ref: fact.fact_ref },
          }, async (spec) => {
            await submitCommand(spec);
            await refresh();
          }, {
            title: "纠正这条画像事实",
            submitLabel: "确认纠正",
            fields: [{
              name: "new_summary",
              label: "正确内容",
              multiline: true,
              defaultValue: fact.summary,
            }],
          }),
          governedAction("标记失效", {
            type: "profile_fact_invalidate",
            expected_version: summary.profile_revision,
            payload: { member_ref: member.member_ref, fact_ref: fact.fact_ref },
          }, async (spec) => {
            await submitCommand(spec);
            await refresh();
          }, { danger: true, submitLabel: "确认失效" }),
        ] : []),
      ]),
    ])),
    ...audit.map((item) => element("p", {
      className: "profile-audit-line",
      text: `${formatDate(item.created_at)} · ${item.action_type}`,
    })),
  ]);
  return element("details", { className: "profile-audit" }, [
    element("summary", { text: AUDIT_LABEL }),
    content,
  ]);
}

function renderDetail(item, hydrateAvatars, submitCommand, refresh) {
  const summary = item?.summary || {};
  const member = summary.member || {};
  const snapshot = summary.snapshot || {};
  const episodes = Array.isArray(summary.episodes) ? summary.episodes : [];
  const relations = Array.isArray(summary.relations) ? summary.relations : [];
  const detail = element("article", { className: "profile-detail" }, [
    element("header", { className: "profile-identity" }, [
      avatar(member, true),
      element("div", {}, [
        element("h2", { text: member.display_name || "群成员" }),
        element("p", { text: snapshot.relationship_summary || "关系认知正在积累" }),
        element("div", { className: "profile-role-list" }, (snapshot.group_roles || []).map((role) =>
          element("span", { text: role }),
        )),
      ]),
      element("span", {
        className: "profile-maturity",
        text: MATURITY_LABELS[snapshot.maturity] || "逐步形成",
      }),
    ]),
    section("一句话画像", element("p", {
      className: "profile-portrait-quote",
      text: snapshot.one_line_portrait || "画像正在形成",
    }), "profile-lead"),
    section("个体特征", list(
      snapshot.individual_fingerprints,
      "还没有足够稳定的个体特征。",
    )),
    section("偏好与边界", list(
      snapshot.preferences_and_boundaries,
      "偏好和边界仍在观察中。",
    )),
    section("与 Groupmate 的关系", element("p", {
      className: "profile-relationship-copy",
      text: snapshot.relationship_summary || "关系认知正在积累",
    })),
    section("代表经历", episodeRows(episodes)),
    section("群友关系", relationRows(relations)),
    auditDetails(summary, member, submitCommand, refresh),
  ]);
  queueMicrotask(() => hydrateAvatars?.(detail));
  return detail;
}

const AUDIT_LABEL = "证据与审计";

function teachingState() {
  return element("div", { className: "profile-detail profile-teaching-state" }, [
    element("img", { attrs: { src: "./assets/icons/user-cog.svg", alt: "" } }),
    element("h2", { text: "选择左侧成员" }),
    element("p", { text: "查看 Groupmate 对这位群友的独立了解。画像会在群聊中逐步形成，并随新证据更新。" }),
  ]);
}

function portraitHeader(view) {
  const portrait = view?.items?.[0]?.summary;
  const topics = Array.isArray(portrait?.common_topics) ? portrait.common_topics : [];
  return element("section", { className: "profile-group-summary" }, [
    element("div", {}, [
      element("span", { text: "群体认知" }),
      element("strong", { text: portrait?.summary || "群体画像正在形成" }),
    ]),
    element("dl", {}, [
      element("div", {}, [element("dt", { text: "已形成画像" }), element("dd", { text: portrait?.member_count ?? 0 })]),
      element("div", {}, [element("dt", { text: "互动节奏" }), element("dd", { text: portrait?.activity_rhythm || "尚未形成" })]),
    ]),
    element("div", { className: "profile-topic-list" }, topics.map((topic) =>
      element("span", { text: topic }),
    )),
  ]);
}

export function renderProfiles(selectView, submitCommand, refresh, query, hydrateAvatars) {
  const view = selectView("profiles");
  const members = Array.isArray(view?.items) ? view.items : [];
  const detailHost = element("div", { className: "profile-detail-host" }, teachingState());
  let activeButton = null;

  const memberButtons = members.map((item) => {
    const member = item.summary || {};
    const row = element("button", {
      className: "profile-member-row",
      attrs: { type: "button" },
      dataset: {
        memberRef: member.member_ref,
        search: `${member.display_name || ""} ${member.one_line_portrait || ""} ${(member.group_roles || []).join(" ")}`.toLowerCase(),
      },
    }, [
      avatar(member),
      element("span", { className: "profile-member-copy" }, [
        element("strong", { text: member.display_name || "群成员" }),
        element("small", { text: member.one_line_portrait || "画像正在形成" }),
      ]),
      element("span", { className: "profile-member-meta" }, [
        element("b", { text: MATURITY_LABELS[member.maturity] || "刚开始了解" }),
        element("small", { text: `${member.fact_count || 0} 条认知` }),
      ]),
    ]);
    row.addEventListener("click", async () => {
      detailHost.scrollTop = 0;
      activeButton?.classList.remove("is-active");
      row.classList.add("is-active");
      activeButton = row;
      detailHost.replaceChildren(element("div", { className: "profile-detail profile-detail-loading" }, [
        element("div", { className: "skeleton skeleton-heading" }),
        element("div", { className: "skeleton skeleton-line" }),
        element("div", { className: "skeleton skeleton-line skeleton-short" }),
      ]));
      try {
        const result = await query("profile", { member_ref: member.member_ref }, { timeoutMs: 4_000 });
        const detail = result?.items?.[0];
        if (!row.isConnected || activeButton !== row) return;
        detailHost.replaceChildren(renderDetail(
          detail, hydrateAvatars, submitCommand, refresh,
        ));
      } catch (_error) {
        if (!row.isConnected || activeButton !== row) return;
        detailHost.replaceChildren(element("div", { className: "profile-detail profile-teaching-state" }, [
          element("h2", { text: "画像暂时无法读取" }),
          element("p", { text: "当前列表仍可使用，请稍后刷新后重试。" }),
        ]));
      }
    });
    return row;
  });

  const empty = element("p", {
    className: "profile-list-empty",
    text: members.length ? "没有匹配的成员" : "还没有成员画像，群聊后会逐步出现。",
  });
  empty.hidden = members.length > 0;
  const memberList = element("div", { className: "profile-member-list" }, [...memberButtons, empty]);
  const search = element("input", {
    attrs: { type: "search", placeholder: "搜索成员或画像关键词", "aria-label": "搜索成员画像" },
  });
  search.addEventListener("input", () => {
    const term = search.value.trim().toLowerCase();
    let visible = 0;
    memberButtons.forEach((row) => {
      row.hidden = Boolean(term) && !row.dataset.search.includes(term);
      if (!row.hidden) visible += 1;
    });
    empty.hidden = visible > 0;
  });

  const refreshButton = element("button", {
    className: "runtime-refresh",
    text: "刷新画像",
    attrs: { type: "button" },
  });
  refreshButton.addEventListener("click", async () => {
    refreshButton.disabled = true;
    refreshButton.textContent = "正在刷新…";
    try { await refresh(); } finally {
      if (refreshButton.isConnected) {
        refreshButton.disabled = false;
        refreshButton.textContent = "刷新画像";
      }
    }
  });

  const root = element("div", { className: "profile-workspace workspace-stack" }, [
    portraitHeader(selectView("group-portrait")),
    element("section", { className: "profile-browser" }, [
      element("aside", { className: "profile-directory", attrs: { "aria-label": "群成员列表" } }, [
        element("header", {}, [
          element("div", {}, [element("h2", { text: "群成员" }), element("span", { text: `${members.length} 人` })]),
          refreshButton,
        ]),
        element("label", { className: "profile-search" }, [
          element("img", { attrs: { src: "./assets/icons/search.svg", alt: "" } }),
          search,
        ]),
        memberList,
      ]),
      detailHost,
    ]),
  ]);
  queueMicrotask(() => hydrateAvatars?.(root));
  return root;
}
