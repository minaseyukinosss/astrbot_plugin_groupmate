import { governedAction } from "../components/command-dialog.js";
import { element, textValue } from "../components/dom.js";
import { controlVersion, projectionList, statusNarrative, workspaceSection } from "../components/projection.js";

const GOVERNANCE_AREAS = [
  "待复核",
  "纠正",
  "遗忘",
  "身份关联",
  "配置版本",
  "校准",
  "导出",
  "保留策略",
  "目标效果评估",
];

export function renderGovernance(select, command) {
  const governance = select("governance");
  const evaluation = select("evaluation");
  const expectedVersion = controlVersion(governance);
  const governanceActions = (item) => (
    item.kind === "calibration.shadow_candidate_evaluated"
    && item.summary?.status === "PENDING_APPROVAL"
      ? [governedAction("批准校准", {
        type: "approve_calibration",
        expected_version: expectedVersion,
        payload: { entity_ref: item.entity_ref },
      }, command, { danger: true })]
      : []
  );
  const reset = governedAction("重置状态", {
    type: "reset",
    expected_version: expectedVersion,
    payload: { target: "group-runtime" },
  }, command, { danger: true });
  const shadowReviews = renderShadowReviews(evaluation, expectedVersion, command);

  return element("div", { className: "workspace-stack" }, [
    statusNarrative([governance, evaluation]),
    workspaceSection("治理范围", "高影响动作必须有 Expected Version、原因和二次确认。", element("p", { text: GOVERNANCE_AREAS.join(" · ") }), [reset]),
    workspaceSection("实群 SHADOW 复核", "逐条判断一个真实决策点；分类建议仅在缺失或错误时补充。页面没有发送或立即执行入口。", shadowReviews),
    workspaceSection("待复核与版本", "治理事件、配置版本、校准和保留策略的审计摘要。", projectionList(governance, { actions: governanceActions })),
    workspaceSection("目标效果评估", "只展示标注与效果 Projection，不展示模型思维链。", projectionList(evaluation)),
  ]);
}

function renderShadowReviews(view, expectedVersion, command) {
  const items = (view?.items || []).filter((item) =>
    item.kind === "evaluation.shadow_decision_captured"
    && Array.isArray(item.summary?.focus)
    && item.summary.focus.length === 1,
  );
  if (!items.length) {
    return element("p", {
      className: "empty-state",
      text: "尚无待复核的 installed-live SHADOW 决策；历史 bootstrap 不计入这里。",
    });
  }
  const list = element("ol", { className: "shadow-review-list" });
  for (const item of items) list.append(renderShadowDecision(item, expectedVersion, command));
  return list;
}

function renderShadowDecision(item, expectedVersion, command) {
  const summary = item.summary || {};
  const focus = summary.focus[0] || {};
  const suggested = summary.suggested_categories || [];
  const base = {
    type: "shadow_review",
    expected_version: expectedVersion,
    payload: { entity_ref: item.entity_ref },
  };
  const categoryField = suggested.length ? [] : [{
    name: "categories",
    label: "补充分类（逗号分隔）",
    format: "csv",
  }];
  const actions = summary.status === "pending" ? [
    governedAction("合理", {
      ...base,
      payload: { ...base.payload, decision: "reasonable", categories: suggested },
    }, command, { fields: categoryField, title: "确认当前决策合理" }),
    governedAction("不合理", {
      ...base,
      payload: { ...base.payload, decision: "unreasonable" },
    }, command, {
      danger: true,
      title: "纠正当前决策",
      fields: [
        {
          name: "categories",
          label: "正确分类（仅在建议缺失或错误时修改）",
          format: "csv",
          defaultValue: suggested.join(","),
        },
        {
          name: "correction",
          label: "完整结构化标签纠正（JSON）",
          format: "json",
        },
      ],
    }),
    governedAction("证据不足", {
      ...base,
      payload: { ...base.payload, decision: "insufficient", categories: [] },
    }, command, { title: "标记证据不足" }),
  ] : [];
  const history = element("ol", { className: "shadow-history" });
  for (const event of summary.history || []) {
    history.append(element("li", {}, [
      element("span", { className: "shadow-actor", text: event.actor_ref || "群成员" }),
      element("span", { text: event.summary || "—" }),
    ]));
  }
  if (!history.children.length) {
    history.append(element("li", { className: "empty-state", text: "这个判断点没有更早的可见历史。" }));
  }
  return element("li", { className: "shadow-review-item" }, [
    element("div", { className: "projection-item-heading" }, [
      element("strong", { text: "唯一 Focus" }),
      element("span", { className: "version-tag", text: summary.runtime_mode || "SHADOW" }),
    ]),
    element("div", { className: "shadow-focus" }, [
      element("span", { className: "shadow-actor", text: focus.actor_ref || "群成员" }),
      element("p", { text: focus.summary || "—" }),
    ]),
    element("details", {}, [
      element("summary", { text: `历史摘要（${(summary.history || []).length}）` }),
      history,
    ]),
    element("dl", { className: "shadow-decision-facts" }, [
      fact("Attention", summary.attention),
      fact("对象", summary.target),
      fact("候选响应与动作", {
        response: summary.candidate_response,
        actions: summary.candidate_actions,
      }),
      fact("Governor outcome", summary.governor?.outcome),
      fact("结构化理由", summary.governor?.reason_codes || summary.reason_codes),
      fact("有效期", summary.expires_at),
      fact("系统分类建议", suggested),
    ]),
    element("div", { className: "item-actions" }, actions),
  ]);
}

function fact(label, value) {
  return element("div", {}, [
    element("dt", { text: label }),
    element("dd", { text: textValue(value) }),
  ]);
}
