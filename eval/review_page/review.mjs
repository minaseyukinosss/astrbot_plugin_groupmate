import { deriveSubmissionState } from "/review_state.mjs";

const CATEGORY_LABELS = Object.freeze({
  direct_interaction: "直接互动",
  multi_message_completion: "多消息补全",
  parallel_topics: "并行话题",
  public_help: "公开帮助",
  riff: "接梗",
  care: "关心",
  shared_experience: "共同经历",
  media_reaction: "媒体回应",
  task_progress: "任务进度",
  boundary: "边界",
  sleep_wake: "作息",
  autonomous_initiation: "主动发起",
  opportunity_expiry: "机会过期",
  task_topic_change: "任务跨话题",
  ambiguous_target: "对象不明确",
  correct_silence: "正确沉默",
});

const elements = {
  progressCopy: document.getElementById("progress-copy"),
  progressBar: document.getElementById("progress-bar"),
  error: document.getElementById("error-banner"),
  loading: document.getElementById("loading-state"),
  complete: document.getElementById("complete-state"),
  workspace: document.getElementById("review-workspace"),
  split: document.getElementById("split-badge"),
  scenarioId: document.getElementById("scenario-id"),
  tags: document.getElementById("observable-tags"),
  sceneSummary: document.getElementById("scene-summary"),
  focusContext: document.getElementById("focus-list"),
  historyContext: document.getElementById("history-list"),
  historyDetails: document.getElementById("history-details"),
  historySummary: document.getElementById("history-summary"),
  confidenceBadge: document.getElementById("confidence-badge"),
  recommendationTitle: document.getElementById("recommendation-title"),
  recommendationFacts: document.getElementById("recommendation-facts"),
  acceptableGuidance: document.getElementById("acceptable-guidance"),
  unacceptableGuidance: document.getElementById("unacceptable-guidance"),
  modalityGuidance: document.getElementById("modality-guidance"),
  sensitivityGuidance: document.getElementById("sensitivity-guidance"),
  suggestionSummary: document.getElementById("suggestion-summary"),
  suggestedCategories: document.getElementById("suggested-categories"),
  categoryCompletion: document.getElementById("category-completion"),
  quickCategories: document.getElementById("quick-category-options"),
  confirmation: document.getElementById("scenario-confirmation"),
  confirmationHelp: document.getElementById("confirmation-help"),
  approveForm: document.getElementById("approve-form"),
  approveButton: document.getElementById("approve-button"),
  approveHelp: document.getElementById("approve-help"),
  categoryCompletionForm: document.getElementById("category-completion-form"),
  categoryCompletionButton: document.getElementById("category-completion-button"),
  categoryCompletionHelp: document.getElementById("category-completion-help"),
  insufficientForm: document.getElementById("insufficient-form"),
  insufficientButton: document.getElementById("insufficient-button"),
  correctionDetails: document.getElementById("correction-details"),
  correctionForm: document.getElementById("correction-form"),
  correctButton: document.getElementById("correct-button"),
  attention: document.getElementById("label-attention"),
  action: document.getElementById("label-action"),
  target: document.getElementById("label-target"),
  acceptable: document.getElementById("label-acceptable"),
  unacceptable: document.getElementById("label-unacceptable"),
  modalities: document.getElementById("label-modalities"),
  sensitivity: document.getElementById("label-sensitivity"),
  expires: document.getElementById("label-expires"),
  categories: document.getElementById("category-options"),
  saveStatus: document.getElementById("save-status"),
};

const state = { item: null, busy: false };

function node(tag, className, text) {
  const value = document.createElement(tag);
  if (className) value.className = className;
  if (text !== undefined) value.textContent = String(text);
  return value;
}

function appendDefinition(list, term, description) {
  list.append(node("dt", "", term), node("dd", "", description));
}

function renderTags(container, values, labels = null) {
  container.replaceChildren();
  for (const value of values || []) {
    container.append(node("span", "tag", labels?.[value] || value));
  }
  if (!container.children.length) {
    container.append(node("span", "field-help", "无"));
  }
}

function eventMeta(event) {
  const values = [];
  if (event.reply_to) values.push(`回复 ${event.reply_to}`);
  if (event.reply_evidence) values.push("含未解析回复上下文");
  if (event.mentions?.length) values.push(`提及 ${event.mentions.join("、")}`);
  if (event.media?.length) values.push(`媒体 ${event.media.join("、")}`);
  return values;
}

function renderEvents(container, events, target, focusEventId) {
  container.replaceChildren();
  for (const event of events) {
    const isFocus = event.event_id === focusEventId;
    const item = node("li", "context-event");
    item.dataset.self = String(Boolean(event.is_self));
    item.dataset.focus = String(isFocus);
    item.dataset.target = String(isFocus && Boolean(target) && event.actor_id === target);
    const heading = node("div", "event-heading");
    const identity = node("div", "event-flags");
    identity.append(node("strong", "", event.actor_id || "未知成员"));
    if (isFocus) identity.append(node("span", "decision-flag", "本条需要判定"));
    if (isFocus && target && event.actor_id === target) {
      identity.append(node("span", "target-flag", "建议回应对象"));
    }
    if (event.is_self) identity.append(node("span", "flag", "历史 Bot 发言 · 仅作上下文"));
    if (event.system) identity.append(node("span", "flag", "系统事件"));
    if (event.recalled) identity.append(node("span", "flag", "已撤回"));
    const timing = node("time", "", `+${Number(event.offset_ms || 0).toLocaleString("zh-CN")} ms`);
    heading.append(identity, timing);
    const text = node("p", "event-text", String(event.text || "").trim() || "[无文本内容]");
    item.append(heading, text);
    const metadata = eventMeta(event);
    if (metadata.length) {
      const meta = node("div", "event-meta");
      for (const value of metadata) meta.append(node("span", "", value));
      item.append(meta);
    }
    container.append(item);
  }
}

function renderContext(events, target, focusEventId, scene) {
  const values = Array.isArray(events) ? events : [];
  const focus = values.filter((event) => event.event_id === focusEventId);
  if (focus.length !== 1 || values.at(-1)?.event_id !== focusEventId) {
    throw new Error("当前场景缺少唯一的判定消息，已停止复核");
  }
  const history = values.slice(0, -1);
  renderEvents(elements.focusContext, focus, target, focusEventId);
  renderEvents(elements.historyContext, history, target, focusEventId);
  elements.historyDetails.hidden = history.length === 0;
  elements.historyDetails.open = false;
  elements.historySummary.textContent = `查看此前 ${history.length} 条历史`;
  const gap = Number(scene?.max_idle_gap_ms || 0);
  const boundary = gap ? `连续对话空闲超过 ${Math.round(gap / 1000)} 秒即截断` : "使用旧场景边界";
  elements.sceneSummary.textContent = history.length
    ? `本次只判断 1 条消息；另有 ${history.length} 条较早记录可按需展开。${boundary}。`
    : `本次只判断 1 条消息；当前没有更早的同场景记录。${boundary}。`;
}

function renderGuidance(container, values, emptyCopy, kind) {
  container.replaceChildren();
  if (!values?.length) {
    container.append(node("li", "guidance-empty", emptyCopy));
    return;
  }
  for (const value of values) {
    const item = node("li", "guidance-item");
    item.append(
      node("span", `guidance-mark guidance-mark-${kind}`, kind === "allow" ? "✓" : "!"),
      node("span", "", value.label),
    );
    container.append(item);
  }
}

function renderRecommendation(presentation) {
  elements.recommendationTitle.textContent = presentation.headline;
  elements.recommendationFacts.replaceChildren();
  for (const fact of [presentation.attention, presentation.action, presentation.expiry]) {
    const item = node("span", "recommendation-fact", fact.label);
    if (fact.active === false) item.dataset.inactive = "true";
    elements.recommendationFacts.append(item);
  }
  renderGuidance(
    elements.acceptableGuidance,
    presentation.acceptable,
    presentation.action.active ? "未指定允许行为，请人工修正" : "无需采取可见行动",
    "allow",
  );
  renderGuidance(
    elements.unacceptableGuidance,
    presentation.unacceptable,
    "没有额外禁止行为",
    "avoid",
  );
  elements.modalityGuidance.textContent = presentation.modalities.length
    ? presentation.modalities.map((item) => item.label).join("；")
    : "无需选择回应方式";
  elements.sensitivityGuidance.textContent = presentation.sensitivity.label;
}

function commaValues(value) {
  return String(value || "")
    .split(",")
    .map((item) => item.trim())
    .filter(Boolean);
}

function populateCorrection(label, categories) {
  elements.attention.checked = Boolean(label.attention);
  elements.action.checked = Boolean(label.action);
  elements.target.value = label.target || "";
  elements.acceptable.value = (label.acceptable_intents || []).join(", ");
  elements.unacceptable.value = (label.unacceptable_intents || []).join(", ");
  elements.modalities.value = (label.modalities || []).join(", ");
  elements.sensitivity.value = label.sensitivity || "group";
  elements.expires.value = String(label.expires_after_ms ?? 0);
  const selected = new Set(categories || []);
  elements.categories.querySelectorAll("input[type=checkbox]").forEach((input) => {
    input.checked = selected.has(input.value);
  });
  elements.quickCategories.querySelectorAll("input[type=checkbox]").forEach((input) => {
    input.checked = false;
  });
}

function updateSubmissionState() {
  const confirmed = Boolean(state.item)
    && elements.confirmation.value.trim() === state.item.scenario_id;
  const suggested = state.item?.suggestion?.suggested_categories || [];
  const manualCategories = elements.quickCategories.querySelectorAll("input:checked").length;
  const correctedCategories = elements.categories.querySelectorAll("input:checked").length;
  const submission = deriveSubmissionState({
    busy: state.busy,
    confirmed,
    suggested_category_count: suggested.length,
    manual_category_count: manualCategories,
    corrected_category_count: correctedCategories,
  });
  elements.categoryCompletion.hidden = !submission.needsManualCategories;
  elements.categoryCompletionForm.hidden = !submission.needsManualCategories;
  elements.approveForm.hidden = submission.needsManualCategories;
  elements.approveButton.disabled = !submission.canApprove;
  elements.categoryCompletionButton.disabled = !submission.canCompleteCategories;
  elements.correctButton.disabled = !submission.canCorrect;
  elements.insufficientButton.disabled = state.busy || !confirmed;
  if (!confirmed) {
    elements.confirmationHelp.textContent = "只有与当前场景完全一致时才能提交。";
  } else if (submission.needsManualCategories && manualCategories === 0) {
    elements.confirmationHelp.textContent = "场景 ID 已匹配；请先在上方选择至少一项场景分类。";
  } else {
    elements.confirmationHelp.textContent = "场景 ID 已匹配，可以提交本条决定。";
  }
  elements.approveHelp.textContent = suggested.length
    ? "批准会原样采用上方建议标签与分类。"
    : "";
  elements.categoryCompletionHelp.textContent = manualCategories
    ? `已选择 ${manualCategories} 项分类；场景 ID 匹配后即可提交。`
    : "至少选择一项场景分类。";
}

function renderProgress(progress) {
  const total = Number(progress.total || 0);
  const completed = Number(progress.completed || 0);
  const percentage = total ? (completed / total) * 100 : 0;
  const usable = Number(progress.usable || 0);
  const insufficient = Number(progress.insufficient || 0);
  elements.progressCopy.textContent = `已处理 ${completed} / ${total} · 可用标签 ${usable} · 证据不足 ${insufficient} · 待处理 ${Number(progress.remaining || 0)}`;
  elements.progressBar.style.width = `${percentage}%`;
}

function renderItem(item) {
  state.item = item;
  elements.loading.hidden = true;
  elements.error.hidden = true;
  elements.saveStatus.textContent = "";
  if (!item) {
    elements.workspace.hidden = true;
    elements.complete.hidden = false;
    return;
  }

  elements.complete.hidden = true;
  elements.workspace.hidden = false;
  elements.scenarioId.textContent = item.scenario_id;
  elements.split.textContent = item.split === "holdout" ? "Holdout" : "Calibration";
  renderTags(elements.tags, item.observable_tags);

  const suggestion = item.suggestion || {};
  const label = suggestion.label || {};
  const presentation = suggestion.presentation || {};
  renderContext(item.context, label.target, item.focus_event_id, item.scene);
  renderRecommendation(presentation);
  elements.confidenceBadge.textContent = `${Math.round(Number(suggestion.confidence || 0) * 100)}% 低置信度`;
  elements.suggestionSummary.replaceChildren();
  appendDefinition(elements.suggestionSummary, "attention", String(Boolean(label.attention)));
  appendDefinition(elements.suggestionSummary, "action", String(Boolean(label.action)));
  appendDefinition(elements.suggestionSummary, "target", label.target || "null");
  appendDefinition(elements.suggestionSummary, "acceptable_intents", (label.acceptable_intents || []).join(", ") || "[]");
  appendDefinition(elements.suggestionSummary, "unacceptable_intents", (label.unacceptable_intents || []).join(", ") || "[]");
  appendDefinition(elements.suggestionSummary, "modalities", (label.modalities || []).join(", ") || "[]");
  appendDefinition(elements.suggestionSummary, "sensitivity", label.sensitivity || "—");
  appendDefinition(elements.suggestionSummary, "expires_after_ms", String(Number(label.expires_after_ms || 0)));
  renderTags(elements.suggestedCategories, suggestion.suggested_categories, CATEGORY_LABELS);

  elements.confirmation.value = "";
  elements.correctionDetails.open = false;
  populateCorrection(label, suggestion.suggested_categories);
  updateSubmissionState();
}

async function request(path, payload) {
  const response = await fetch(path, {
    method: payload ? "POST" : "GET",
    cache: "no-store",
    headers: payload ? { "Content-Type": "application/json" } : {},
    body: payload ? JSON.stringify(payload) : undefined,
  });
  const value = await response.json();
  if (!response.ok) throw new Error(value.error || "复核请求失败");
  return value;
}

function showError(error) {
  elements.error.hidden = false;
  elements.error.textContent = error instanceof Error ? error.message : String(error);
}

async function submit(path, payload, pendingCopy) {
  state.busy = true;
  elements.saveStatus.textContent = pendingCopy;
  updateSubmissionState();
  try {
    const result = await request(path, payload);
    renderProgress(result.progress);
    renderItem(result.item);
  } catch (error) {
    showError(error);
    elements.saveStatus.textContent = "未保存，请检查后重试。";
  } finally {
    state.busy = false;
    updateSubmissionState();
  }
}

elements.confirmation.addEventListener("input", updateSubmissionState);
elements.categories.addEventListener("change", updateSubmissionState);
elements.quickCategories.addEventListener("change", updateSubmissionState);

elements.approveForm.addEventListener("submit", (event) => {
  event.preventDefault();
  if (!state.item || elements.approveButton.disabled) return;
  submit(
    "/api/approve",
    {
      scenario_id: state.item.scenario_id,
      confirmation: elements.confirmation.value,
    },
    "正在保存批准决定…",
  );
});

elements.categoryCompletionForm.addEventListener("submit", (event) => {
  event.preventDefault();
  if (!state.item || elements.categoryCompletionButton.disabled) return;
  const categories = Array.from(
    elements.quickCategories.querySelectorAll("input:checked"),
    (input) => input.value,
  );
  submit(
    "/api/correct",
    {
      scenario_id: state.item.scenario_id,
      confirmation: elements.confirmation.value,
      categories,
      label: state.item.suggestion.label,
    },
    "正在保存补充分类…",
  );
});

elements.insufficientForm.addEventListener("submit", (event) => {
  event.preventDefault();
  if (!state.item || elements.insufficientButton.disabled) return;
  submit(
    "/api/insufficient",
    {
      scenario_id: state.item.scenario_id,
      confirmation: elements.confirmation.value,
    },
    "正在记录证据不足…",
  );
});

elements.correctionForm.addEventListener("submit", (event) => {
  event.preventDefault();
  if (!state.item || elements.correctButton.disabled || !elements.correctionForm.reportValidity()) return;
  const categories = Array.from(elements.categories.querySelectorAll("input:checked"), (input) => input.value);
  submit(
    "/api/correct",
    {
      scenario_id: state.item.scenario_id,
      confirmation: elements.confirmation.value,
      categories,
      label: {
        attention: elements.attention.checked,
        action: elements.action.checked,
        target: elements.target.value.trim() || null,
        acceptable_intents: commaValues(elements.acceptable.value),
        unacceptable_intents: commaValues(elements.unacceptable.value),
        modalities: commaValues(elements.modalities.value),
        sensitivity: elements.sensitivity.value.trim(),
        expires_after_ms: Number(elements.expires.value),
      },
    },
    "正在保存人工修正…",
  );
});

function appendCategoryOptions(container) {
  for (const [value, label] of Object.entries(CATEGORY_LABELS)) {
    const option = node("label", "category-option");
    const input = document.createElement("input");
    input.type = "checkbox";
    input.value = value;
    option.append(input, document.createTextNode(label));
    container.append(option);
  }
}

appendCategoryOptions(elements.quickCategories);
appendCategoryOptions(elements.categories);

request("/api/current")
  .then((result) => {
    renderProgress(result.progress);
    renderItem(result.item);
  })
  .catch((error) => {
    elements.loading.hidden = true;
    showError(error);
  });
