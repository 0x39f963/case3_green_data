const state = {
  config: null,
  chats: [],
  chat: null,
  prompts: [],
  promptTypes: [],
  selectedPrompt: null,
  promptTrace: null,
  promptTraceItem: null,
  roundDetail: null,
  promptDrawerTab: "prompt",
  timelineExpanded: {},
  // Phase 7: per-chat lock. Только текущий чат блокирует свой send-button,
  // в новой вкладке /chat/new есть свой state. busyChatId === null → не занят.
  busyChatId: null,
  progressTimer: null,
};

const PROGRESS_POLL_INTERVAL_MS = 800;

const el = {
  // header pills
  chatIdPill: document.getElementById("chat-id-pill"),
  threadStatePill: document.getElementById("thread-state-pill"),
  envPillValue: document.getElementById("env-pill-value"),
  modelPillValue: document.getElementById("model-pill-value"),
  envPillValueFooter: document.getElementById("env-pill-value-footer"),
  modelPillValueFooter: document.getElementById("model-pill-value-footer"),
  presetHint: document.getElementById("preset-hint"),
  providerField: document.getElementById("provider-field"),
  providerSelect: document.getElementById("provider-select"),
  providerHint: document.getElementById("provider-hint"),
  metaConfigBanner: document.getElementById("meta-config-banner"),
  metaBackend: document.getElementById("meta-backend"),
  metaAuditor: document.getElementById("meta-auditor"),
  // Pipeline timeline (phase 7)
  timelineVertical: document.getElementById("timelineVertical"),
  timelineTotal: document.getElementById("timelineTotal"),
  timelineDetailLink: document.getElementById("timelineDetailLink"),
  timelineHorizontal: document.getElementById("timelineHorizontal"),
  timelineHorizontalBar: document.getElementById("timelineHorizontalBar"),
  timelineHorizontalLegend: document.getElementById("timelineHorizontalLegend"),
  // header buttons + invisible helpers
  refreshBtn: document.getElementById("refresh-btn"),
  newChatBtn: document.getElementById("new-chat-btn"),
  traceLink: document.getElementById("trace-link"),
  pageTitle: document.getElementById("page-title"),
  modePill: document.getElementById("mode-pill"),
  // meta strip
  metaChatId: document.getElementById("meta-chat-id"),
  metaMode: document.getElementById("meta-mode"),
  metaModel: document.getElementById("meta-model"),
  metaIterations: document.getElementById("meta-iterations"),
  metaTrace: document.getElementById("meta-trace"),
  metaStatus: document.getElementById("meta-status"),
  metaUpdated: document.getElementById("meta-updated"),
  // request card
  requestBody: document.getElementById("requestBody"),
  requestEmpty: document.getElementById("requestEmpty"),
  // response card
  responseCard: document.getElementById("responseCard"),
  responseStatusPill: document.getElementById("responseStatusPill"),
  responseIter: document.getElementById("responseIter"),
  responseTime: document.getElementById("responseTime"),
  responseSqlPlaceholder: document.getElementById("responseSqlPlaceholder"),
  responseSqlPlaceholderTitle: document.getElementById("responseSqlPlaceholderTitle"),
  responseSqlPlaceholderSub: document.getElementById("responseSqlPlaceholderSub"),
  responseSqlBlock: document.getElementById("responseSqlBlock"),
  responseSql: document.getElementById("responseSql"),
  metricTokens: document.getElementById("metricTokens"),
  metricTokensSub: document.getElementById("metricTokensSub"),
  metricLatency: document.getElementById("metricLatency"),
  metricLatencySub: document.getElementById("metricLatencySub"),
  metricIterations: document.getElementById("metricIterations"),
  metricIterationsSub: document.getElementById("metricIterationsSub"),
  metricDecisionTile: document.getElementById("metricDecisionTile"),
  metricDecision: document.getElementById("metricDecision"),
  metricDecisionSub: document.getElementById("metricDecisionSub"),
  actionReport: document.getElementById("actionReport"),
  actionCopySql: document.getElementById("actionCopySql"),
  actionRerun: document.getElementById("actionRerun"),
  actionPrompts: document.getElementById("actionPrompts"),
  // composer
  composer: document.getElementById("composer"),
  taskInput: document.getElementById("task-input"),
  taskInputCount: document.getElementById("task-input-count"),
  sendBtn: document.getElementById("send-btn"),
  sendBtnLabel: document.getElementById("send-btn-label"),
  modelSelect: document.getElementById("model-select"),
  judgeSelect: document.getElementById("judge-select"),
  judgeHint: document.getElementById("judge-hint"),
  judgeProviderField: document.getElementById("judge-provider-field"),
  judgeProviderSelect: document.getElementById("judge-provider-select"),
  judgeProviderHint: document.getElementById("judge-provider-hint"),
  promptCheckEnabled: document.getElementById("prompt-check-enabled"),
  promptCheckEnabledHint: document.getElementById("prompt-check-enabled-hint"),
  promptCheckField: document.getElementById("prompt-check-field"),
  promptCheckSelect: document.getElementById("prompt-check-select"),
  promptCheckHint: document.getElementById("prompt-check-hint"),
  promptCheckProviderField: document.getElementById("prompt-check-provider-field"),
  promptCheckProviderSelect: document.getElementById("prompt-check-provider-select"),
  promptCheckProviderHint: document.getElementById("prompt-check-provider-hint"),
  modeSelect: document.getElementById("mode-select"),
  maxIterations: document.getElementById("max-iterations"),
  // inspector
  inspector: document.getElementById("inspector-content"),
  // views
  viewChat: document.getElementById("view-chat"),
  viewHistory: document.getElementById("view-history"),
  viewPrompts: document.getElementById("view-prompts"),
  // history
  historySearch: document.getElementById("history-search"),
  historyFilter: document.getElementById("history-filter"),
  historyList: document.getElementById("history-list"),
  // prompts
  promptsCount: document.getElementById("prompts-count"),
  promptsRefreshBtn: document.getElementById("prompts-refresh-btn"),
  promptNewBtn: document.getElementById("prompt-new-btn"),
  promptTypeFilter: document.getElementById("prompt-type-filter"),
  promptsList: document.getElementById("prompts-list"),
  promptEditorId: document.getElementById("prompt-editor-id"),
  promptEditorBadges: document.getElementById("prompt-editor-badges"),
  promptEditorType: document.getElementById("prompt-editor-type"),
  promptEditorDescription: document.getElementById("prompt-editor-description"),
  promptEditorName: document.getElementById("prompt-editor-name"),
  promptEditorNotes: document.getElementById("prompt-editor-notes"),
  promptEditorText: document.getElementById("prompt-editor-text"),
  promptEditorSha: document.getElementById("prompt-editor-sha"),
  promptEditorUpdated: document.getElementById("prompt-editor-updated"),
  promptSaveBtn: document.getElementById("prompt-save-btn"),
  promptCloneBtn: document.getElementById("prompt-clone-btn"),
  promptActivateBtn: document.getElementById("prompt-activate-btn"),
  promptDefaultBtn: document.getElementById("prompt-default-btn"),
  promptArchiveBtn: document.getElementById("prompt-archive-btn"),
  promptEditorStatus: document.getElementById("prompt-editor-status"),
  promptDrawer: document.getElementById("promptDrawer"),
  promptDrawerOverlay: document.getElementById("promptDrawerOverlay"),
  promptDrawerClose: document.getElementById("promptDrawerClose"),
  promptDrawerTitle: document.getElementById("promptDrawerTitle"),
  promptDrawerSubtitle: document.getElementById("promptDrawerSubtitle"),
  promptDrawerTabs: document.getElementById("promptDrawerTabs"),
  promptDrawerBody: document.getElementById("promptDrawerBody"),
};

const STATUS_PILL = {
  approved: { cls: "pill-green", text: "APPROVED" },
  needs_review: { cls: "pill-amber", text: "NEEDS REVIEW" },
  clarify: { cls: "pill-amber", text: "NEEDS REVIEW" },
  revise: { cls: "pill-blue", text: "REVISE" },
  refused: { cls: "pill-red", text: "BLOCKED" },
  blocked: { cls: "pill-red", text: "BLOCKED" },
  failed: { cls: "pill-red", text: "FAILED" },
  error: { cls: "pill-red", text: "FAILED" },
  running: { cls: "pill-blue", text: "RUNNING" },
  draft: { cls: "pill-slate", text: "DRAFT" },
};

const SVG = {
  status: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9"/><polyline points="12 7 12 12 15 14"/></svg>',
  trace: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M6 17v-2a4 4 0 0 1 4-4h4a4 4 0 0 0 4-4V5"/><circle cx="6" cy="17" r="2"/><circle cx="18" cy="5" r="2"/></svg>',
  model: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3l2.5 5.5L20 11l-5.5 2.5L12 19l-2.5-5.5L4 11l5.5-2.5z"/></svg>',
  risk: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/></svg>',
  iter: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="23 4 23 10 17 10"/><path d="M20.49 15A9 9 0 1 1 18.36 6.64L23 10"/></svg>',
  sql: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><ellipse cx="12" cy="5" rx="7" ry="3"/><path d="M5 5v10c0 1.7 3.1 3 7 3s7-1.3 7-3V5"/><path d="M5 10c0 1.7 3.1 3 7 3s7-1.3 7-3"/></svg>',
  external: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="15 3 21 3 21 9"/><line x1="10" y1="14" x2="21" y2="3"/><path d="M21 14v5a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5"/></svg>',
  shield: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/><polyline points="9 12 11 14 15 10"/></svg>',
};

const sqlKeywords = [
  "SELECT", "FROM", "WHERE", "GROUP", "BY", "HAVING", "ORDER", "LIMIT",
  "JOIN", "LEFT", "RIGHT", "INNER", "FULL", "OUTER", "CROSS", "ON",
  "AS", "AND", "OR", "NOT", "NULL", "IS", "IN", "EXISTS", "UNION",
  "ALL", "DISTINCT", "CASE", "WHEN", "THEN", "ELSE", "END", "WITH",
  "OVER", "PARTITION", "OFFSET", "FETCH", "FIRST", "NEXT", "ROWS", "ONLY",
  "CURRENT_DATE", "INTERVAL", "FILTER", "ILIKE", "BETWEEN"
];
const sqlFunctions = [
  "COUNT", "SUM", "AVG", "MIN", "MAX", "COALESCE", "NULLIF", "DATE_TRUNC",
  "EXTRACT", "ROUND", "LOWER", "UPPER", "CAST"
];
const sqlTypes = ["TEXT", "INTEGER", "NUMERIC", "DATE", "TIMESTAMP", "BOOLEAN", "UUID", "BIGINT"];
const jsonPayloads = [];
let createJSONEditor = null;
let jsonLoadError = null;
const jsonReady = import("https://cdn.jsdelivr.net/npm/vanilla-jsoneditor@3.12.0/standalone.js")
  .then((module) => {
    createJSONEditor = module.createJSONEditor;
  })
  .catch((error) => {
    jsonLoadError = error;
  });

const PROMPT_TYPE_HELP = {
  generator_system: {
    title: "Генератор SQL",
    text: "Главная инструкция для модели, которая пишет SQL. Здесь задаются правила: какие таблицы можно использовать, как сохранять фильтры пользователя, как избегать персональных данных и что делать при нехватке контекста.",
  },
  generator_tool_mode_system: {
    title: "Генератор SQL с инструментами",
    text: "Вариант инструкции для режима, где модель может просить дополнительные проверки через инструменты. Если выбранная модель не поддерживает tool-calling, pipeline переходит в обычную генерацию и продолжает работу.",
  },
  generator_tools_system: {
    title: "Описание инструментов генератора",
    text: "Объясняет модели, какие проверки и справочники доступны во время генерации. Нужен, чтобы модель не угадывала схему, а запрашивала проверяемые факты.",
  },
  auditor_system: {
    title: "Аудитор результата",
    text: "Проверяет готовый SQL перед выдачей пользователю. Ищет утечки персональных данных, обход прав, опасные операции, сломанный SQL и потерянные условия из запроса.",
  },
  semantic_judge_system: {
    title: "Судья безопасности на 4-м этапе",
    text: "Дополнительная модель для спорных случаев. Она смотрит на SQL и задачу пользователя и решает, можно ли пропустить результат или нужна правка/ручная проверка.",
  },
  quality_reviewer_system: {
    title: "Smart-judge качества кейса",
    text: "Оценивает один batch trace как эксперт по качеству: правильно ли понят запрос, использованы ли нужные таблицы, не потеряны ли фильтры, насколько полезна правка.",
  },
  bench_reviewer_system: {
    title: "Batch reviewer: системная инструкция",
    text: "Системная часть smart-judge для пакетных прогонов. Фиксирует шкалы оценок и формат ответа, чтобы результаты разных кейсов можно было сравнивать.",
  },
  bench_reviewer_user: {
    title: "Batch reviewer: вход кейса",
    text: "Шаблон пользовательской части smart-judge. В него подставляются задача, trace, найденные риски, SQL и служебные метрики конкретного кейса.",
  },
  case_quality_judge_system: {
    title: "Оценка качества одного кейса",
    text: "Рубрика для 9 оценок качества: корректность SQL, безопасность, соответствие намерению, использование схемы, работа с контекстом, объяснение решения, производительность, устойчивость и эффективность retry.",
  },
  judge_audit_hypothesis_system: {
    title: "Аудит судьи и гипотезы улучшений",
    text: "Читает smart-judge, Oracle и trace, затем объясняет человеческим языком, почему кейс провалился и какую проверяемую гипотезу улучшения стоит завести: prompt, guard, схема, поиск контекста, retry или runtime.",
  },
  classifier_judge_system: {
    title: "Классификатор рисков",
    text: "Помогает перевести найденные признаки риска в понятные категории: чувствительные данные, маскирование, неверный путь соединения таблиц, опасные операции.",
  },
  prompt_check_judge_system: {
    title: "Проверка исходного запроса",
    text: "Смотрит на текст пользователя до генерации SQL. Цель - заранее заметить просьбы, которые нельзя выполнять напрямую: персональные данные, обход доступа, системные таблицы или изменение данных.",
  },
};

document.addEventListener("DOMContentLoaded", init);

async function init() {
  bind();
  await loadConfig();
  await loadChats();
  await route(location.pathname, false);
}

function bind() {
  document.body.addEventListener("click", async (event) => {
    const action = event.target.closest("[data-action]");
    if (action) {
      if (action.hasAttribute("disabled") || action.getAttribute("aria-disabled") === "true") return;
      if (action.dataset.action === "copy-sql") {
        const sql = currentSql();
        if (sql) await copyText(sql);
        flashLabel(action, "Copied", "Copy SQL");
        return;
      }
      if (action.dataset.action === "re-run") {
        const task = currentTask();
        if (task) {
          el.taskInput.value = task;
          updateCharCount();
          await sendMessage();
        }
        return;
      }
    }

    const link = event.target.closest("[data-link]");
    if (!link) return;
    event.preventDefault();
    await route(new URL(link.href).pathname, true);
  });

  document.querySelectorAll(".chat-example-chip").forEach((button) => {
    button.addEventListener("click", () => {
      el.taskInput.value = button.textContent.trim();
      el.taskInput.focus();
      updateCharCount();
    });
  });

  el.modelSelect.addEventListener("change", () => {
    const item = selectedModel();
    if (item && item.llm_mode) el.modeSelect.value = item.llm_mode;
    updatePresetHint();
    updateProviderSelect();
    updateMeta(lastAssistant((state.chat && state.chat.messages) || []));
    updateHeaderPills();
  });
  if (el.providerSelect) {
    el.providerSelect.addEventListener("change", updateProviderHint);
  }
  if (el.judgeSelect) {
    el.judgeSelect.addEventListener("change", () => {
      updateJudgeHint();
      updateJudgeProviderSelect();
      updateMeta(lastAssistant((state.chat && state.chat.messages) || []));
    });
  }
  if (el.judgeProviderSelect) {
    el.judgeProviderSelect.addEventListener("change", updateJudgeProviderHint);
  }
  if (el.promptCheckEnabled) {
    el.promptCheckEnabled.addEventListener("change", () => {
      localStorage.setItem(promptCheckStorageKey("enabled"), el.promptCheckEnabled.checked ? "1" : "0");
      updatePromptCheckProviderSelect();
      updatePromptCheckHint();
    });
  }
  if (el.promptCheckSelect) {
    el.promptCheckSelect.addEventListener("change", () => {
      localStorage.setItem(promptCheckStorageKey("preset"), el.promptCheckSelect.value || "");
      updatePromptCheckProviderSelect();
      updatePromptCheckHint();
    });
  }
  if (el.promptCheckProviderSelect) {
    el.promptCheckProviderSelect.addEventListener("change", () => {
      updatePromptCheckProviderHint();
      updatePromptCheckHint();
    });
  }

  el.composer.addEventListener("submit", async (event) => {
    event.preventDefault();
    await sendMessage();
  });

  // Enter to submit, Shift+Enter for newline
  el.taskInput.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey && !event.ctrlKey && !event.metaKey && !event.altKey) {
      event.preventDefault();
      if (!state.busy) el.composer.requestSubmit();
    }
  });
  el.taskInput.addEventListener("input", updateCharCount);

  // New chat: anchor с target=_blank — браузер сам открывает новую вкладку.
  // JS-обработчик не нужен, оставляем default behaviour.

  el.refreshBtn.addEventListener("click", async () => {
    await loadChats();
    await route(location.pathname, false);
  });

  el.historySearch.addEventListener("input", renderHistory);
  el.historyFilter.addEventListener("change", renderHistory);

  if (el.promptsRefreshBtn) el.promptsRefreshBtn.addEventListener("click", loadAndRenderPrompts);
  if (el.promptTypeFilter) el.promptTypeFilter.addEventListener("change", renderPromptsList);
  if (el.promptEditorType) el.promptEditorType.addEventListener("change", () => renderPromptTypeDescription(el.promptEditorType.value));
  if (el.promptNewBtn) el.promptNewBtn.addEventListener("click", newPromptDraft);
  if (el.promptSaveBtn) el.promptSaveBtn.addEventListener("click", savePromptDraft);
  if (el.promptCloneBtn) el.promptCloneBtn.addEventListener("click", clonePrompt);
  if (el.promptActivateBtn) el.promptActivateBtn.addEventListener("click", activatePrompt);
  if (el.promptDefaultBtn) el.promptDefaultBtn.addEventListener("click", makePromptDefault);
  if (el.promptArchiveBtn) el.promptArchiveBtn.addEventListener("click", archivePrompt);
  if (el.actionPrompts) el.actionPrompts.addEventListener("click", () => openPromptTraceDrawer().catch(appendError));
  if (el.metricDecisionTile) {
    el.metricDecisionTile.addEventListener("click", () => {
      const href = el.metricDecisionTile.dataset.href || "";
      if (href) window.open(href, "_blank", "noopener");
    });
  }
  if (el.promptDrawerClose) el.promptDrawerClose.addEventListener("click", closePromptTraceDrawer);
  if (el.promptDrawerOverlay) el.promptDrawerOverlay.addEventListener("click", closePromptTraceDrawer);
  if (el.promptDrawerTabs) {
    el.promptDrawerTabs.addEventListener("click", (event) => {
      const tab = event.target.closest("[data-prompt-tab]");
      if (!tab) return;
      state.promptDrawerTab = tab.dataset.promptTab || "prompt";
      renderPromptTraceDrawer();
    });
  }

  window.addEventListener("popstate", () => {
    route(location.pathname, false);
  });
  window.addEventListener("keydown", (event) => {
    if (event.key === "Escape") closePromptTraceDrawer();
  }, true);
}

function updateCharCount() {
  el.taskInputCount.textContent = String((el.taskInput.value || "").length);
}

function protectSqlStrings(line) {
  const values = [];
  const text = line.replace(/'([^']|'')*'/g, (match) => {
    const key = "__SQL_STR_" + values.length + "__";
    values.push(match);
    return key;
  });
  return { text, values };
}

function restoreSqlStrings(line, values) {
  return line.replace(/__SQL_STR_(\d+)__/g, (_match, index) => {
    return '<span class="sql-str">' + escapeHtml(values[Number(index)] || "") + '</span>';
  });
}

function highlightSqlLine(line) {
  const protectedLine = protectSqlStrings(line);
  let html = escapeHtml(protectedLine.text);
  const fnRe = new RegExp("\\b(" + sqlFunctions.join("|") + ")\\b(?=\\s*\\()", "gi");
  const kwRe = new RegExp("\\b(" + sqlKeywords.join("|") + ")\\b", "gi");
  const typeRe = new RegExp("\\b(" + sqlTypes.join("|") + ")\\b", "gi");
  html = html.replace(fnRe, '<span class="sql-fn">$1</span>');
  html = html.replace(typeRe, '<span class="sql-type">$1</span>');
  html = html.replace(kwRe, '<span class="sql-kw">$1</span>');
  html = html.replace(/\b(\d+(?:\.\d+)?)\b/g, '<span class="sql-num">$1</span>');
  return restoreSqlStrings(html, protectedLine.values);
}

function renderHighlightedSql(sql) {
  const lines = formatSqlForDisplay(sql).split("\n");
  return (lines.length ? lines : [""]).map((line, index) => {
    return '<div class="sql-line"><span class="row-num">' + (index + 1) + '</span>' + highlightSqlLine(line) + '</div>';
  }).join("");
}

function formatSqlForDisplay(sql) {
  const raw = String(sql || "").trim();
  if (!raw) return "";
  if (raw.includes("\n")) return raw;
  return raw
    .replace(/\s+/g, " ")
    .replace(/\s+(FROM|WHERE|GROUP\s+BY|HAVING|ORDER\s+BY|LIMIT|OFFSET|FETCH|UNION|RETURNING)\b/gi, "\n$1")
    .replace(/\s+((?:LEFT|RIGHT|INNER|FULL|CROSS)\s+JOIN|JOIN)\b/gi, "\n$1")
    .replace(/\s+(AND|OR)\b/gi, "\n  $1")
    .replace(/,\s*/g, ",\n  ");
}

function flashLabel(target, flashText, originalText) {
  const original = target.dataset.original || target.textContent.trim() || originalText;
  target.dataset.original = original;
  target.textContent = flashText;
  setTimeout(() => { target.textContent = original; }, 900);
}

const BACKEND_GROUPS = [
  { backend: "openrouter", label: "OpenRouter (cloud)" },
  { backend: "local_openai", label: "Local Ollama / OpenAI-compatible" },
  { backend: "anthropic_cli", label: "Claude CLI" },
  { backend: "codex_cli", label: "Codex CLI" },
];

async function loadConfig() {
  state.config = await api("/web/api/config");
  const models = state.config.models || [];
  el.modelSelect.innerHTML = "";
  BACKEND_GROUPS.forEach((group) => {
    const groupItems = models.filter((m) => m.backend === group.backend);
    if (!groupItems.length) return;
    const optgroup = document.createElement("optgroup");
    optgroup.label = group.label;
    groupItems.forEach((model) => {
      const option = document.createElement("option");
      option.value = model.key;
      option.textContent = (model.label || model.key)
        + (model.available_by_config === false ? " (unavailable)" : "")
        + (model.supports_tool_mode === "unsupported" ? " (no tools)" : "");
      option.disabled = model.available_by_config === false;
      option.dataset.mode = model.llm_mode || "";
      option.dataset.generator = model.llm_generator_model || "";
      option.dataset.backend = model.backend || "";
      option.dataset.providerModel = model.provider_model || "";
      option.dataset.codexReasoningEffort = model.codex_reasoning_effort || "";
      option.dataset.configHint = model.config_hint || "";
      option.dataset.available = model.available_by_config ? "1" : "0";
      option.dataset.supportsToolMode = model.supports_tool_mode || "unknown";
      option.dataset.openrouterProviders = JSON.stringify(model.openrouter_providers || []);
      optgroup.appendChild(option);
    });
    el.modelSelect.appendChild(optgroup);
  });
  if (!el.modelSelect.options.length) {
    const option = document.createElement("option");
    option.value = "";
    option.textContent = "default";
    el.modelSelect.appendChild(option);
  }
  const defaultKey = state.config.default_model_key || "";
  if (defaultKey && Array.from(el.modelSelect.options).some((o) => o.value === defaultKey)) {
    el.modelSelect.value = defaultKey;
  }
  const item = selectedModel();
  if (item && item.llm_mode) el.modeSelect.value = item.llm_mode;
  fillJudgeSelect();
  fillPromptCheckSelect();
  updatePresetHint();
  updateProviderSelect();
  updateJudgeHint();
  updateJudgeProviderSelect();
  updatePromptCheckHint();
  updateMeta(null);
  updateHeaderPills();
}

function fillJudgeSelect() {
  if (!el.judgeSelect) return;
  const items = (state.config && state.config.judge_backends) || [];
  el.judgeSelect.innerHTML = "";
  items.forEach((item) => {
    const option = document.createElement("option");
    option.value = item.key;
    option.textContent = item.label || item.key;
    option.dataset.backend = item.backend || "";
    option.dataset.providerModel = item.provider_model || "";
    option.dataset.codexReasoningEffort = item.codex_reasoning_effort || "";
    option.dataset.configHint = item.config_hint || "";
    option.dataset.available = item.available_by_config ? "1" : "0";
    option.dataset.openrouterProviders = JSON.stringify(item.openrouter_providers || []);
    option.disabled = item.available_by_config === false;
    el.judgeSelect.appendChild(option);
  });
  const defaultKey = (state.config && state.config.default_judge_backend) || "";
  const options = Array.from(el.judgeSelect.options);
  if (defaultKey && options.some((item) => item.value === defaultKey && !item.disabled)) {
    el.judgeSelect.value = defaultKey;
  } else {
    const firstAvailable = options.find((item) => !item.disabled);
    if (firstAvailable) el.judgeSelect.value = firstAvailable.value;
  }
}

function updatePresetHint() {
  const item = selectedModel();
  if (!el.presetHint) return;
  if (!item) {
    el.presetHint.textContent = "";
    el.presetHint.classList.remove("hint-bad");
    return;
  }
  const parts = [
    "Contour: " + (item.llm_mode || "—"),
    "Backend: " + (item.backend || "—"),
  ];
  if (item.provider_model) parts.push("Model: " + item.provider_model);
  if (item.codex_reasoning_effort) parts.push("Reasoning: " + item.codex_reasoning_effort);
  if (!item.available_by_config && item.config_hint) {
    parts.push("Needs: " + item.config_hint);
    el.presetHint.classList.add("hint-bad");
  } else if (item.config_hint) {
    parts.push("Configured via: " + item.config_hint);
    el.presetHint.classList.remove("hint-bad");
  } else {
    el.presetHint.classList.remove("hint-bad");
  }
  if (state.config && state.config.generator_tool_mode_enabled && item.supports_tool_mode !== "verified") {
    parts.push("Tool-mode warning: selected preset has no verified tool-calling support");
    el.presetHint.classList.add("hint-bad");
  }
  el.presetHint.textContent = parts.join(" · ");
}

function metricSourceValue(value) {
  if (value && typeof value === "object") {
    return value.p50 ?? value.median ?? value.avg ?? value.mean ?? value.value;
  }
  return value;
}

function providerMetric(value, label, suffix) {
  const n = Number(metricSourceValue(value));
  if (!Number.isFinite(n)) return label + " n/a";
  return label + " " + n.toFixed(n >= 10 ? 0 : 2) + (suffix || "");
}

function providerPrice(value) {
  const n = Number(metricSourceValue(value));
  return Number.isFinite(n) ? n.toFixed(n >= 10 ? 0 : 2) : "n/a";
}

function providerOptionLabel(provider) {
  const rec = provider.recommended ? "recommended · " : "";
  return [
    provider.provider_name || "unknown",
    rec + "$" + providerPrice(provider.price_per_million_prompt) + "/M in",
    "$" + providerPrice(provider.price_per_million_completion) + "/M out",
    providerMetric(provider.latency_last_30m, "latency", " ms"),
    providerMetric(provider.throughput_last_30m, "speed", " tok/s"),
    providerMetric(provider.uptime_last_30m, "uptime", "%"),
  ].join(" · ");
}

function selectedOpenRouterProviders() {
  const option = el.modelSelect.selectedOptions[0];
  if (!option || option.dataset.backend !== "openrouter") return [];
  try {
    const value = JSON.parse(option.dataset.openrouterProviders || "[]");
    return Array.isArray(value) ? value : [];
  } catch (_error) {
    return [];
  }
}

function updateProviderSelect() {
  if (!el.providerField || !el.providerSelect) return;
  const option = el.modelSelect.selectedOptions[0];
  const visible = !!option && option.dataset.backend === "openrouter";
  const providers = selectedOpenRouterProviders();
  el.providerField.classList.toggle("hidden", !visible);
  if (!visible) {
    el.providerSelect.innerHTML = "";
    if (el.providerHint) el.providerHint.textContent = "";
    return;
  }
  if (!providers.length) {
    el.providerSelect.innerHTML = '<option value="">Auto (OpenRouter default routing)</option>';
    if (el.providerHint) el.providerHint.textContent = "Provider metadata is unavailable from OpenRouter right now.";
    return;
  }
  const firstRecommended = providers.find((item) => item.recommended) || providers[0];
  el.providerSelect.innerHTML = '<option value="">Auto (OpenRouter default routing)</option>' + providers.map((provider) => {
    const selected = provider.provider_name === firstRecommended.provider_name ? " selected" : "";
    return '<option value="' + escapeHtml(provider.provider_name || "") + '"' + selected + '>' + escapeHtml(providerOptionLabel(provider)) + '</option>';
  }).join("");
  updateProviderHint();
}

function selectedOpenRouterProviderName() {
  if (!el.providerField || el.providerField.classList.contains("hidden") || !el.providerSelect) return null;
  return el.providerSelect.value || null;
}

function updateProviderHint() {
  if (!el.providerHint || !el.providerSelect) return;
  const provider = selectedOpenRouterProviders().find((item) => item.provider_name === el.providerSelect.value);
  if (!provider) {
    el.providerHint.textContent = "Auto lets OpenRouter choose. Select a provider for reproducible latency/cost comparison.";
    return;
  }
  const context = provider.context_length ? "context " + provider.context_length : "context n/a";
  const quant = provider.quantization ? " · " + provider.quantization : "";
  el.providerHint.textContent = context + quant + " · " + providerMetric(provider.latency_last_30m, "latency", " ms") + " · " + providerMetric(provider.throughput_last_30m, "speed", " tok/s");
}

function selectedJudgeOpenRouterProviders() {
  const option = el.judgeSelect ? el.judgeSelect.selectedOptions[0] : null;
  if (!option || option.dataset.backend !== "openrouter") return [];
  try {
    const value = JSON.parse(option.dataset.openrouterProviders || "[]");
    return Array.isArray(value) ? value : [];
  } catch (_error) {
    return [];
  }
}

function updateJudgeProviderSelect() {
  if (!el.judgeProviderField || !el.judgeProviderSelect) return;
  const option = el.judgeSelect ? el.judgeSelect.selectedOptions[0] : null;
  const visible = !!option && option.dataset.backend === "openrouter";
  const providers = selectedJudgeOpenRouterProviders();
  el.judgeProviderField.classList.toggle("hidden", !visible);
  if (!visible) {
    el.judgeProviderSelect.innerHTML = "";
    if (el.judgeProviderHint) el.judgeProviderHint.textContent = "";
    return;
  }
  if (!providers.length) {
    el.judgeProviderSelect.innerHTML = '<option value="">Auto (OpenRouter default routing)</option>';
    if (el.judgeProviderHint) el.judgeProviderHint.textContent = "Provider metadata is unavailable from OpenRouter right now.";
    return;
  }
  const firstRecommended = providers.find((item) => item.recommended) || providers[0];
  el.judgeProviderSelect.innerHTML = '<option value="">Auto (OpenRouter default routing)</option>' + providers.map((provider) => {
    const selected = provider.provider_name === firstRecommended.provider_name ? " selected" : "";
    return '<option value="' + escapeHtml(provider.provider_name || "") + '"' + selected + '>' + escapeHtml(providerOptionLabel(provider)) + '</option>';
  }).join("");
  updateJudgeProviderHint();
}

function selectedJudgeOpenRouterProviderName() {
  if (!el.judgeProviderField || el.judgeProviderField.classList.contains("hidden") || !el.judgeProviderSelect) return null;
  return el.judgeProviderSelect.value || null;
}

function updateJudgeProviderHint() {
  if (!el.judgeProviderHint || !el.judgeProviderSelect) return;
  const provider = selectedJudgeOpenRouterProviders().find((item) => item.provider_name === el.judgeProviderSelect.value);
  if (!provider) {
    el.judgeProviderHint.textContent = "Auto lets OpenRouter choose for Stage 4 only.";
    return;
  }
  const context = provider.context_length ? "context " + provider.context_length : "context n/a";
  const quant = provider.quantization ? " · " + provider.quantization : "";
  el.judgeProviderHint.textContent = context + quant + " · " + providerMetric(provider.latency_last_30m, "latency", " ms") + " · " + providerMetric(provider.throughput_last_30m, "speed", " tok/s");
}

function updateJudgeHint() {
  if (!el.judgeHint || !el.judgeSelect) return;
  const option = el.judgeSelect.selectedOptions[0];
  if (!option) {
    el.judgeHint.textContent = "";
    el.judgeHint.classList.remove("hint-bad");
    return;
  }
  const parts = [
    "Backend: " + (option.dataset.backend || "-"),
  ];
  if (option.dataset.providerModel) parts.push("Model: " + option.dataset.providerModel);
  if (option.dataset.configHint) parts.push(option.dataset.configHint);
  el.judgeHint.classList.toggle("hint-bad", option.dataset.available !== "1");
  el.judgeHint.textContent = parts.join(" · ");
}

function fillPromptCheckSelect() {
  if (!el.promptCheckSelect) return;
  const items = (state.config && state.config.models) || [];
  el.promptCheckSelect.innerHTML = "";
  BACKEND_GROUPS.forEach((group) => {
    const groupItems = items.filter((item) => item.backend === group.backend);
    if (!groupItems.length) return;
    const optgroup = document.createElement("optgroup");
    optgroup.label = group.label;
    groupItems.forEach((item) => {
      const option = document.createElement("option");
      option.value = item.key;
      option.textContent = (item.label || item.key)
        + (item.available_by_config === false ? " (unavailable)" : "")
        + (item.supports_tool_mode === "unsupported" ? " (no tools)" : "");
      option.disabled = item.available_by_config === false;
      option.dataset.backend = item.backend || "";
      option.dataset.providerModel = item.provider_model || "";
      option.dataset.available = item.available_by_config ? "1" : "0";
      option.dataset.configHint = item.config_hint || "";
      option.dataset.openrouterProviders = JSON.stringify(item.openrouter_providers || []);
      optgroup.appendChild(option);
    });
    el.promptCheckSelect.appendChild(optgroup);
  });
  const savedEnabled = localStorage.getItem(promptCheckStorageKey("enabled"));
  if (el.promptCheckEnabled && savedEnabled !== null) {
    el.promptCheckEnabled.checked = savedEnabled !== "0";
  }
  const savedPreset = localStorage.getItem(promptCheckStorageKey("preset"));
  const defaultKey = savedPreset || promptCheckDefaultModelKey();
  if (defaultKey && Array.from(el.promptCheckSelect.options).some((item) => item.value === defaultKey)) {
    el.promptCheckSelect.value = defaultKey;
  } else if (el.promptCheckSelect.options.length) {
    el.promptCheckSelect.options[0].selected = true;
  }
  updatePromptCheckProviderSelect();
}

function promptCheckStorageKey(name) {
  const chatId = (state.chat && state.chat.chat_id) || "global";
  return "webChat." + chatId + ".promptCheck." + name;
}

function promptCheckDefaultModelKey() {
  const options = (state.config && state.config.models) || [];
  const defaultPromptKey = (state.config && state.config.default_prompt_check_backend) || "";
  const promptItems = (state.config && state.config.prompt_check_backends) || [];
  const promptItem = promptItems.find((item) => item.key === defaultPromptKey || item.default);
  const promptBackend = promptItem && promptItem.backend;
  const promptModel = promptItem && promptItem.provider_model;
  const match = options.find((item) => item.backend === promptBackend && item.provider_model === promptModel);
  if (match) return match.key;
  const geminiLite = options.find((item) => item.provider_model === "google/gemini-3.1-flash-lite");
  if (geminiLite) return geminiLite.key;
  return (state.config && state.config.default_model_key) || "";
}

function selectedPromptCheckOpenRouterProviders() {
  const option = el.promptCheckSelect ? el.promptCheckSelect.selectedOptions[0] : null;
  if (!option || option.dataset.backend !== "openrouter") return [];
  try {
    const value = JSON.parse(option.dataset.openrouterProviders || "[]");
    return Array.isArray(value) ? value : [];
  } catch (_error) {
    return [];
  }
}

function updatePromptCheckProviderSelect() {
  if (!el.promptCheckProviderField || !el.promptCheckProviderSelect) return;
  const enabled = el.promptCheckEnabled ? el.promptCheckEnabled.checked !== false : true;
  const option = el.promptCheckSelect ? el.promptCheckSelect.selectedOptions[0] : null;
  const visible = enabled && !!option && option.dataset.backend === "openrouter";
  const providers = selectedPromptCheckOpenRouterProviders();
  el.promptCheckProviderField.classList.toggle("hidden", !visible);
  if (!visible) {
    el.promptCheckProviderSelect.innerHTML = "";
    if (el.promptCheckProviderHint) el.promptCheckProviderHint.textContent = "";
    return;
  }
  if (!providers.length) {
    el.promptCheckProviderSelect.innerHTML = '<option value="">Auto (OpenRouter default routing)</option>';
    if (el.promptCheckProviderHint) el.promptCheckProviderHint.textContent = "Provider metadata is unavailable from OpenRouter right now.";
    return;
  }
  const firstRecommended = providers.find((item) => item.recommended) || providers[0];
  el.promptCheckProviderSelect.innerHTML = '<option value="">Auto (OpenRouter default routing)</option>' + providers.map((provider) => {
    const selected = provider.provider_name === firstRecommended.provider_name ? " selected" : "";
    return '<option value="' + escapeHtml(provider.provider_name || "") + '"' + selected + '>' + escapeHtml(providerOptionLabel(provider)) + '</option>';
  }).join("");
  updatePromptCheckProviderHint();
}

function selectedPromptCheckOpenRouterProviderName() {
  if (!el.promptCheckProviderField || el.promptCheckProviderField.classList.contains("hidden") || !el.promptCheckProviderSelect) return null;
  return el.promptCheckProviderSelect.value || null;
}

function updatePromptCheckProviderHint() {
  if (!el.promptCheckProviderHint || !el.promptCheckProviderSelect) return;
  const provider = selectedPromptCheckOpenRouterProviders().find((item) => item.provider_name === el.promptCheckProviderSelect.value);
  if (!provider) {
    el.promptCheckProviderHint.textContent = "Auto lets OpenRouter choose for request-ingestion only.";
    return;
  }
  const context = provider.context_length ? "context " + provider.context_length : "context n/a";
  const quant = provider.quantization ? " · " + provider.quantization : "";
  el.promptCheckProviderHint.textContent = context + quant + " · " + providerMetric(provider.latency_last_30m, "latency", " ms") + " · " + providerMetric(provider.throughput_last_30m, "speed", " tok/s");
}

function updatePromptCheckHint() {
  if (!el.promptCheckSelect) return;
  const enabled = el.promptCheckEnabled ? el.promptCheckEnabled.checked !== false : true;
  if (el.promptCheckField) el.promptCheckField.classList.toggle("is-disabled", !enabled);
  el.promptCheckSelect.disabled = !enabled;
  if (el.promptCheckEnabledHint) {
    el.promptCheckEnabledHint.textContent = enabled
      ? "Включено для safety-метрик."
      : "Injection check disabled для этого запуска.";
  }
  const option = el.promptCheckSelect.selectedOptions[0];
  if (!el.promptCheckHint || !option) return;
  const parts = ["Backend: " + (option.dataset.backend || "-")];
  if (option.dataset.providerModel) parts.push("Model: " + option.dataset.providerModel);
  if (option.dataset.backend === "openrouter") parts.push("Provider: " + (selectedPromptCheckOpenRouterProviderName() || "auto"));
  if (option.dataset.configHint) parts.push(option.dataset.configHint);
  el.promptCheckHint.classList.toggle("hint-bad", enabled && option.dataset.available !== "1");
  el.promptCheckHint.textContent = parts.join(" · ");
}

async function loadChats() {
  const data = await api("/web/api/chats?limit=500");
  state.chats = data.items || [];
}

async function route(path, push) {
  if (push) history.pushState({}, "", path);
  setActiveNav(path);
  if (path === "/history") {
    showView("history");
    syncHistoryControlsFromUrl();
    renderHistory();
    return;
  }
  if (path === "/settings/prompts" || path === "/prompts/system") {
    showView("prompts");
    await loadAndRenderPrompts();
    return;
  }
  showView("chat");
  const match = path.match(/^\/chat\/([A-Za-z0-9_-]{8,64})$/);
  if (match) {
    await openChat(match[1]);
  } else {
    state.chat = null;
    renderChat();
  }
}

async function openChat(chatId) {
  const data = await api("/web/api/chats/" + encodeURIComponent(chatId));
  state.chat = data.chat;
  attachPromptSummary(data.summary);
  renderChat();
}

async function createChat() {
  const data = await api("/web/api/chats", {
    method: "POST",
    body: JSON.stringify({ source: "web" }),
  });
  state.chat = data.chat;
  await loadChats();
  return data.chat;
}

async function sendMessage() {
  const task = el.taskInput.value.trim();
  if (!task) return;
  const currentChatId = state.chat && state.chat.chat_id;
  if (state.busyChatId && state.busyChatId === currentChatId) return;
  state.busyChatId = currentChatId || "__new__";
  el.sendBtn.disabled = true;
  el.sendBtnLabel.textContent = "В работе";
  try {
    if (!state.chat) {
      await createChat();
      history.replaceState({}, "", "/chat/" + state.chat.chat_id);
      state.busyChatId = state.chat.chat_id;
    }
    const model = selectedModel();
    const judge = selectedJudge();
    const llmMode = (model && model.llm_mode) || el.modeSelect.value || null;
    const isGenericCli = model && (model.backend === "anthropic_cli" || model.backend === "codex_cli") && !model.llm_generator_model;
    const llmGeneratorModel = isGenericCli ? null : ((model && model.llm_generator_model) || null);
    const judgeBackend = el.judgeSelect ? (el.judgeSelect.value || null) : null;
    const promptCheckEnabled = el.promptCheckEnabled ? el.promptCheckEnabled.checked !== false : true;
    const promptCheckOption = el.promptCheckSelect ? el.promptCheckSelect.selectedOptions[0] : null;
    appendPending(task);
    startProgressPolling(state.chat.chat_id);
    const data = await api("/web/api/chats/" + state.chat.chat_id + "/messages", {
      method: "POST",
      body: JSON.stringify({
        task,
        llm_mode: llmMode,
        llm_generator_model: llmGeneratorModel,
        openrouter_provider: selectedOpenRouterProviderName(),
        judge_openrouter_provider: selectedJudgeOpenRouterProviderName(),
        codex_reasoning_effort: model && model.codex_reasoning_effort ? model.codex_reasoning_effort : (judge && judge.codex_reasoning_effort ? judge.codex_reasoning_effort : null),
        judge_backend: judgeBackend,
        prompt_check_enabled: promptCheckEnabled,
        prompt_check_backend: promptCheckEnabled ? (promptCheckOption && promptCheckOption.dataset.backend ? promptCheckOption.dataset.backend : null) : null,
        prompt_check_model: promptCheckEnabled ? (promptCheckOption && promptCheckOption.dataset.providerModel ? promptCheckOption.dataset.providerModel : null) : null,
        prompt_check_openrouter_provider: promptCheckEnabled ? selectedPromptCheckOpenRouterProviderName() : null,
        max_iterations: Number(el.maxIterations.value || 5),
      }),
    });
    state.chat = data.chat;
    attachPromptSummary(data.summary);
    el.taskInput.value = "";
    updateCharCount();
    await loadChats();
    renderChat();
  } catch (error) {
    appendError(error);
  } finally {
    state.busyChatId = null;
    el.sendBtn.disabled = false;
    el.sendBtnLabel.textContent = "Отправить";
    stopProgressPolling();
    // Финальный pull прогресса для отрисовки полного timeline после run'а.
    if (state.chat && state.chat.chat_id) {
      fetchProgress(state.chat.chat_id).catch(() => {});
    }
  }
}

function startProgressPolling(chatId) {
  stopProgressPolling();
  if (!chatId) return;
  fetchProgress(chatId).catch(() => {});
  state.progressTimer = setInterval(() => {
    fetchProgress(chatId).catch(() => {});
  }, PROGRESS_POLL_INTERVAL_MS);
}

function stopProgressPolling() {
  if (state.progressTimer) {
    clearInterval(state.progressTimer);
    state.progressTimer = null;
  }
}

async function fetchProgress(chatId) {
  if (!chatId) return;
  let data;
  try {
    data = await api("/web/api/chats/" + encodeURIComponent(chatId) + "/progress");
  } catch (error) {
    return;
  }
  renderTimeline(data);
  if (data && data.complete && state.progressTimer && state.busyChatId !== chatId) {
    stopProgressPolling();
  }
}

function renderTimeline(progress) {
  if (!el.timelineVertical) return;
  state.lastProgress = progress || {};
  const steps = (progress && Array.isArray(progress.steps)) ? progress.steps : [];
  const isActive = !!(progress && progress.complete === false && progress.trace_id);
  // Vertical timeline
  if (!steps.length) {
    el.timelineVertical.innerHTML = '<div class="chat-timeline__empty">' +
      (isActive ? "Pipeline запускается…" : "Pipeline ещё не выполнялся.") +
      "</div>";
    if (el.timelineTotal) el.timelineTotal.textContent = "—";
  } else {
    el.timelineVertical.innerHTML = "";
    let totalSec = 0;
    const iterationStages = buildTimelineIterationStages(progress || {});
    steps.forEach((step) => {
      totalSec += Number(step.sec || 0);
      el.timelineVertical.appendChild(renderTimelineStep(step, progress.complete, iterationStages[step.key]));
    });
    if (el.timelineTotal) el.timelineTotal.textContent = totalSec ? totalSec.toFixed(2) + "s" : "—";
  }

  // Detail report link
  if (el.timelineDetailLink) {
    const traceId = progress && progress.trace_id;
    if (traceId && progress && progress.complete) {
      el.timelineDetailLink.href = "/runs/" + encodeURIComponent(traceId);
      el.timelineDetailLink.classList.remove("hidden");
    } else {
      el.timelineDetailLink.classList.add("hidden");
    }
  }

  // Horizontal timeline (только пока pipeline идёт ИЛИ есть результат)
  if (el.timelineHorizontal) {
    if (!steps.length) {
      el.timelineHorizontal.classList.add("hidden");
      return;
    }
    el.timelineHorizontal.classList.toggle("hidden", !isActive && progress && progress.complete && !steps.length);
    if (!isActive && (!progress || progress.complete)) {
      // После завершения горизонтальный бар можно прятать через 2s,
      // оставляем legend как итог.
      el.timelineHorizontal.classList.remove("hidden");
    } else {
      el.timelineHorizontal.classList.remove("hidden");
    }
    renderHorizontalTimeline(steps, progress);
  }
}

function buildTimelineIterationStages(progress) {
  const iterations = Array.isArray(progress.iterations) ? progress.iterations : [];
  const complete = progress.complete !== false;
  const activeStep = progress.active_step || "";
  const stages = {
    generate: { key: "generate", label: "SQL Generation", rounds: [] },
    audit: { key: "audit", label: "Validation", rounds: [] },
  };
  iterations.forEach((iteration) => {
    const number = iteration.iteration || stages.generate.rounds.length + 1;
    (iteration.steps || []).forEach((step) => {
      if (step.node !== "generate" && step.node !== "audit") return;
      const detail = {
        number,
        sec: Number(step.sec || 0),
        duration: step.duration || formatSeconds(step.sec || 0),
        status: "completed",
        subtitle: timelineRoundSubtitle(step),
        node: step.node,
        detail: step.detail || null,
      };
      stages[step.node].rounds.push(detail);
    });
  });

  Object.keys(stages).forEach((key) => {
    const stage = stages[key];
    if (!stage.rounds.length) return;
    if (!complete && activeStep === key) {
      stage.rounds[stage.rounds.length - 1].status = "running";
    }
    stage.totalSec = stage.rounds.reduce((sum, round) => sum + Number(round.sec || 0), 0);
    stage.completedCount = stage.rounds.filter((round) => round.status === "completed").length;
    stage.runningRound = stage.rounds.find((round) => round.status === "running");
  });
  return stages;
}

function timelineRoundSubtitle(step) {
  if (step.node === "generate") {
    const count = Number(step.candidate_count || 0);
    const pieces = [];
    if (count) pieces.push(count + " SQL-кандидата");
    if (Array.isArray(step.candidate_seconds) && step.candidate_seconds.length) {
      pieces.push(step.candidate_seconds.map(formatSeconds).join(" + "));
    }
    return pieces.join(" · ") || "генерация SQL";
  }
  if (step.node === "audit") return "проверка ответа";
  return timelineNodeLabel(step.node);
}

function timelineNodeLabel(node) {
  return {
    retrieve: "контекст",
    generate: "генерация",
    sql_guard: "проверки",
    explain_sandbox: "EXPLAIN",
    audit: "аудит",
    decide: "решение",
    revise: "повтор",
  }[node] || node || "шаг";
}

function formatSeconds(value) {
  const sec = Number(value || 0);
  if (!Number.isFinite(sec)) return "0.000s";
  return sec < 1 ? sec.toFixed(3) + "s" : sec.toFixed(2) + "s";
}

function renderTimelineStep(step, complete, stageInfo) {
  const wrap = document.createElement("div");
  wrap.className = "chat-timeline-node" + (stageInfo && stageInfo.rounds && stageInfo.rounds.length ? " has-rounds" : "");
  const btn = document.createElement("button");
  btn.type = "button";
  btn.className = "chat-timeline-step" + (step.active ? " is-active" : "");
  const dot = document.createElement("span");
  const status = String(step.status || "").toLowerCase();
  let dotCls = "chat-timeline-step__dot";
  let dotHtml = "";
  if (status === "error") {
    dotCls += " error";
    dotHtml = '<svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><line x1="6" y1="6" x2="18" y2="18"/><line x1="18" y1="6" x2="6" y2="18"/></svg>';
  } else if (!complete && step.active) {
    dotCls += " active";
    dotHtml = '<span class="chat-timeline-step__pulse"></span>';
  } else if (status === "done" || complete) {
    dotCls += " done";
    dotHtml = '<svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"/></svg>';
  }
  dot.className = dotCls;
  dot.innerHTML = dotHtml;
  btn.appendChild(dot);

  const label = document.createElement("span");
  label.className = "chat-timeline-step__label";
  label.textContent = step.label || step.key || "step";
  btn.appendChild(label);

  const duration = document.createElement("span");
  duration.className = "chat-timeline-step__duration";
  duration.textContent = step.duration || "";
  btn.appendChild(duration);

  if (stageInfo && stageInfo.rounds && stageInfo.rounds.length) {
    const toggle = document.createElement("span");
    const expanded = timelineStageExpanded(step.key, step.active);
    toggle.className = "chat-timeline-step__chevron" + (expanded ? " expanded" : "");
    toggle.innerHTML = '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><polyline points="6 9 12 15 18 9"/></svg>';
    duration.before(toggle);
    btn.setAttribute("aria-expanded", expanded ? "true" : "false");
  }

  btn.addEventListener("click", () => {
    if (stageInfo && stageInfo.rounds && stageInfo.rounds.length) {
      state.timelineExpanded[step.key] = !timelineStageExpanded(step.key, step.active);
      renderTimeline(state.lastProgress || {});
      return;
    }
    const traceId = state.chat && state.chat.last_result && state.chat.last_result.trace_id;
    const pendingTraceId = state.chat && state.chat.pending_trace_id;
    const target = traceId || pendingTraceId;
    if (target) {
      window.open("/runs/" + encodeURIComponent(target) + "#" + (step.drawer_key || step.key || ""), "_blank", "noopener");
    }
  });
  wrap.appendChild(btn);
  if (stageInfo && stageInfo.rounds && stageInfo.rounds.length) {
    const expanded = timelineStageExpanded(step.key, step.active);
    wrap.appendChild(expanded ? renderTimelineRoundList(stageInfo) : renderTimelineRoundSummary(stageInfo));
  }
  return wrap;
}

function timelineStageExpanded(key, active) {
  if (Object.prototype.hasOwnProperty.call(state.timelineExpanded, key)) {
    return !!state.timelineExpanded[key];
  }
  return !!active;
}

function renderTimelineRoundSummary(stageInfo) {
  const summary = document.createElement("button");
  summary.type = "button";
  summary.className = "chat-timeline-rounds-summary";
  const completed = Number(stageInfo.completedCount || stageInfo.rounds.length || 0);
  const running = stageInfo.runningRound ? " · сейчас круг " + stageInfo.runningRound.number : "";
  summary.textContent = completed + " " + pluralRu(completed, "круг", "круга", "кругов") + " готово" + running;
  summary.addEventListener("click", (event) => {
    event.stopPropagation();
    state.timelineExpanded[stageInfo.key] = true;
    renderTimeline(state.lastProgress || {});
  });
  return summary;
}

function renderTimelineRoundList(stageInfo) {
  const list = document.createElement("div");
  list.className = "chat-timeline-rounds";
  stageInfo.rounds.forEach((round) => {
    const row = document.createElement(round.detail ? "button" : "div");
    if (round.detail) {
      row.type = "button";
      row.addEventListener("click", (event) => {
        event.stopPropagation();
        openRoundDetailDrawer(stageInfo, round);
      });
    }
    row.className = "chat-timeline-round is-" + round.status;
    const marker = document.createElement("span");
    marker.className = "chat-timeline-round__marker";
    marker.innerHTML = round.status === "completed"
      ? '<svg width="9" height="9" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"/></svg>'
      : '<span class="chat-timeline-round__spinner"></span>';
    row.appendChild(marker);

    const body = document.createElement("span");
    body.className = "chat-timeline-round__body";
    const title = document.createElement("span");
    title.className = "chat-timeline-round__title";
    title.textContent = "Круг " + round.number;
    const status = document.createElement("span");
    status.className = "chat-timeline-round__status";
    status.textContent = round.status === "running" ? "выполняется" : "готово";
    const subtitle = document.createElement("span");
    subtitle.className = "chat-timeline-round__subtitle";
    subtitle.textContent = round.subtitle || "";
    body.appendChild(title);
    body.appendChild(status);
    if (round.subtitle) body.appendChild(subtitle);
    row.appendChild(body);

    const time = document.createElement("span");
    time.className = "chat-timeline-round__duration";
    time.textContent = round.duration || "";
    row.appendChild(time);
    list.appendChild(row);
  });
  return list;
}

function pluralRu(value, one, few, many) {
  const n = Math.abs(Number(value || 0)) % 100;
  const n1 = n % 10;
  if (n > 10 && n < 20) return many;
  if (n1 > 1 && n1 < 5) return few;
  if (n1 === 1) return one;
  return many;
}

function renderHorizontalTimeline(steps, progress) {
  if (!el.timelineHorizontalBar || !el.timelineHorizontalLegend) return;
  const total = steps.reduce((acc, step) => acc + Number(step.sec || 0), 0);
  el.timelineHorizontalBar.innerHTML = "";
  el.timelineHorizontalLegend.innerHTML = "";
  steps.forEach((step) => {
    const sec = Number(step.sec || 0);
    const pct = total > 0 ? Math.max(3, Math.round((sec / total) * 100)) : Math.round(100 / steps.length);
    const seg = document.createElement("span");
    seg.className = "chat-timeline-horizontal__seg";
    if (step.active && progress && progress.complete === false) seg.classList.add("active");
    else if (String(step.status || "").toLowerCase() === "done" || (progress && progress.complete)) seg.classList.add("done");
    if (String(step.status || "").toLowerCase() === "error") seg.classList.add("error");
    seg.style.flex = String(pct);
    seg.title = (step.label || step.key) + " · " + (step.duration || "—");
    seg.textContent = step.duration || "";
    el.timelineHorizontalBar.appendChild(seg);

    const chip = document.createElement("span");
    chip.className = "chat-timeline-horizontal__chip";
    if (step.active && progress && progress.complete === false) chip.classList.add("active");
    else if (String(step.status || "").toLowerCase() === "done" || (progress && progress.complete)) chip.classList.add("done");
    if (String(step.status || "").toLowerCase() === "error") chip.classList.add("error");
    chip.textContent = (step.label || step.key) + " · " + (step.duration || "—");
    el.timelineHorizontalLegend.appendChild(chip);
  });
}

function appendPending(task) {
  const chat = state.chat || { messages: [] };
  chat.messages = chat.messages || [];
  chat.messages.push({ role: "user", text: task, created_at: new Date().toISOString() });
  chat.messages.push({ role: "assistant", status: "running", pending: true, summary: { status: "running" } });
  state.chat = chat;
  renderChat();
}

function appendError(error) {
  const chat = state.chat || { messages: [] };
  chat.messages = chat.messages || [];
  // drop the pending placeholder, if any
  if (chat.messages.length && chat.messages[chat.messages.length - 1].pending) chat.messages.pop();
  chat.messages.push({
    role: "assistant",
    status: "failed",
    error: { code: "send_failed", message: String(error.message || error) },
    summary: { status: "failed" },
  });
  state.chat = chat;
  renderChat();
}

async function loadAndRenderPrompts() {
  if (!el.promptsList) return;
  try {
    const data = await api("/web/api/system-prompts");
    state.prompts = data.items || [];
    state.promptTypes = data.prompt_types || [];
    fillPromptTypeSelects();
    if (el.promptsCount) el.promptsCount.textContent = String(data.total || state.prompts.length);
    if (!state.selectedPrompt && state.prompts.length) {
      await openPrompt(state.prompts[0].id);
    } else {
      renderPromptsList();
      renderPromptEditor();
    }
  } catch (error) {
    state.prompts = [];
    state.selectedPrompt = null;
    fillPromptTypeSelects();
    el.promptsList.innerHTML = '<div class="chat-history__empty">Registry unavailable: ' + escapeHtml(error.message || error) + "</div>";
    renderPromptEditor();
  }
}

function fillPromptTypeSelects() {
  const types = state.promptTypes.length ? state.promptTypes : [
    "generator_system",
    "generator_tool_mode_system",
    "generator_tools_system",
    "auditor_system",
    "semantic_judge_system",
    "quality_reviewer_system",
    "bench_reviewer_system",
    "bench_reviewer_user",
    "case_quality_judge_system",
    "judge_audit_hypothesis_system",
    "classifier_judge_system",
    "prompt_check_judge_system",
  ];
  [el.promptTypeFilter, el.promptEditorType].forEach((select) => {
    if (!select) return;
    const current = select.value;
    select.innerHTML = "";
    if (select === el.promptTypeFilter) {
      const all = document.createElement("option");
      all.value = "";
      all.textContent = "All prompt types";
      select.appendChild(all);
    }
    types.forEach((type) => {
      const option = document.createElement("option");
      option.value = type;
      option.textContent = type;
      select.appendChild(option);
    });
    if (current && Array.from(select.options).some((item) => item.value === current)) {
      select.value = current;
    }
  });
}

function renderPromptsList() {
  if (!el.promptsList) return;
  const filter = el.promptTypeFilter ? el.promptTypeFilter.value : "";
  const items = state.prompts.filter((item) => !filter || item.prompt_type === filter);
  el.promptsList.innerHTML = "";
  if (!items.length) {
    const empty = document.createElement("div");
    empty.className = "chat-history__empty";
    empty.textContent = "No prompts found.";
    el.promptsList.appendChild(empty);
    return;
  }
  items.forEach((item) => {
    const row = document.createElement("button");
    row.type = "button";
    row.className = "prompt-row" + (state.selectedPrompt && state.selectedPrompt.id === item.id ? " is-active" : "");
    row.addEventListener("click", () => openPrompt(item.id).catch(showPromptError));
    const main = document.createElement("span");
    main.className = "prompt-row__main";
    const title = document.createElement("span");
    title.className = "prompt-row__title";
    title.textContent = item.name || item.id;
    const meta = document.createElement("span");
    meta.className = "prompt-row__meta mono";
    meta.textContent = item.prompt_type + " · v" + item.version + " · " + shortSha(item.text_sha256);
    main.appendChild(title);
    main.appendChild(meta);
    row.appendChild(main);
    const badges = document.createElement("span");
    badges.className = "prompt-row__badges";
    badges.appendChild(makePill(item.status || "draft", promptStatusClass(item.status)));
    if (item.is_default) badges.appendChild(makePill("default", "pill-green"));
    row.appendChild(badges);
    el.promptsList.appendChild(row);
  });
}

async function openPrompt(promptId) {
  const data = await api("/web/api/system-prompts/" + encodeURIComponent(promptId));
  state.selectedPrompt = data.prompt;
  renderPromptsList();
  renderPromptEditor();
}

function newPromptDraft() {
  const type = (el.promptTypeFilter && el.promptTypeFilter.value) || state.promptTypes[0] || "generator_system";
  state.selectedPrompt = {
    id: null,
    prompt_type: type,
    version: null,
    name: type + " draft",
    text: "",
    notes: "",
    status: "draft",
    is_default: false,
    text_sha256: "",
    is_new: true,
  };
  renderPromptsList();
  renderPromptEditor();
  if (el.promptEditorName) el.promptEditorName.focus();
}

function renderPromptEditor() {
  const item = state.selectedPrompt;
  const hasItem = !!item;
  const isDraft = hasItem && item.status === "draft";
  const isNew = hasItem && item.is_new;
  const canEdit = hasItem && (isNew || item.status !== "archived");
  const saveLabel = isNew ? "Create draft" : (isDraft ? "Save draft" : "Save as default version");
  if (el.promptEditorId) el.promptEditorId.textContent = hasItem ? (item.id || "new draft") : "—";
  if (el.promptEditorType) {
    el.promptEditorType.value = hasItem ? item.prompt_type : "";
    el.promptEditorType.disabled = !isNew;
    renderPromptTypeDescription(el.promptEditorType.value);
  }
  if (el.promptEditorName) {
    el.promptEditorName.value = hasItem ? (item.name || "") : "";
    el.promptEditorName.disabled = !canEdit;
  }
  if (el.promptEditorNotes) {
    el.promptEditorNotes.value = hasItem ? (item.notes || "") : "";
    el.promptEditorNotes.disabled = !canEdit;
  }
  if (el.promptEditorText) {
    el.promptEditorText.value = hasItem ? (item.text || "") : "";
    el.promptEditorText.disabled = !canEdit;
  }
  if (el.promptEditorSha) el.promptEditorSha.textContent = "sha256: " + (hasItem ? (item.text_sha256 || "pending") : "—");
  if (el.promptEditorUpdated) el.promptEditorUpdated.textContent = "updated: " + (hasItem ? (item.updated_at || "—") : "—");
  if (el.promptEditorBadges) {
    el.promptEditorBadges.innerHTML = "";
    if (hasItem) {
      el.promptEditorBadges.appendChild(makePill(item.status || "draft", promptStatusClass(item.status)));
      if (item.is_default) el.promptEditorBadges.appendChild(makePill("default", "pill-green"));
    }
  }
  if (el.promptSaveBtn) el.promptSaveBtn.textContent = saveLabel;
  setDisabled(el.promptSaveBtn, !canEdit);
  setDisabled(el.promptCloneBtn, !hasItem || isNew);
  setDisabled(el.promptActivateBtn, !hasItem || isNew || item.status !== "draft");
  setDisabled(el.promptDefaultBtn, !hasItem || isNew || item.status !== "active" || item.is_default);
  setDisabled(el.promptArchiveBtn, !hasItem || isNew || item.is_default);
}

function renderPromptTypeDescription(type) {
  if (!el.promptEditorDescription) return;
  const help = PROMPT_TYPE_HELP[type] || {
    title: type || "Prompt type",
    text: "Служебная инструкция pipeline. Если этот тип появился недавно, добавьте в notes назначение и этап, где он применяется.",
  };
  el.promptEditorDescription.innerHTML = '<b>' + escapeHtml(help.title) + '</b><span>' + escapeHtml(help.text) + '</span>';
}

async function savePromptDraft() {
  const item = state.selectedPrompt;
  if (!item) return;
  const payload = {
    prompt_type: el.promptEditorType.value,
    name: el.promptEditorName.value.trim() || el.promptEditorType.value,
    text: el.promptEditorText.value,
    notes: el.promptEditorNotes.value,
  };
  try {
    let data;
    let message;
    if (item.is_new) {
      data = await api("/web/api/system-prompts", { method: "POST", body: JSON.stringify(payload) });
      message = "Draft created";
    } else if (item.status === "draft") {
      data = await api("/web/api/system-prompts/" + encodeURIComponent(item.id), {
        method: "PATCH",
        body: JSON.stringify({ name: payload.name, text: payload.text, notes: payload.notes }),
      });
      message = "Draft saved";
    } else {
      if (!window.confirm("This will create a new active default prompt version. All new runs for this prompt type will use it.")) {
        return;
      }
      data = await api("/web/api/system-prompts/" + encodeURIComponent(item.id) + "/save-as-default", {
        method: "POST",
        body: JSON.stringify({ name: payload.name, text: payload.text, notes: payload.notes }),
      });
      message = "New default version saved";
    }
    state.selectedPrompt = data.prompt;
    await refreshPromptsAfterAction(message);
  } catch (error) {
    showPromptError(error);
  }
}

async function clonePrompt() {
  const item = state.selectedPrompt;
  if (!item || !item.id) return;
  try {
    const data = await api("/web/api/system-prompts/" + encodeURIComponent(item.id) + "/clone", {
      method: "POST",
      body: JSON.stringify({}),
    });
    state.selectedPrompt = data.prompt;
    await refreshPromptsAfterAction("Draft cloned");
  } catch (error) {
    showPromptError(error);
  }
}

async function activatePrompt() {
  const item = state.selectedPrompt;
  if (!item || !item.id) return;
  try {
    const data = await api("/web/api/system-prompts/" + encodeURIComponent(item.id) + "/activate", { method: "POST" });
    state.selectedPrompt = data.prompt;
    await refreshPromptsAfterAction("Activated");
  } catch (error) {
    showPromptError(error);
  }
}

async function makePromptDefault() {
  const item = state.selectedPrompt;
  if (!item || !item.id) return;
  if (!window.confirm("All new runs for this prompt type will use this version.")) return;
  try {
    const data = await api("/web/api/system-prompts/" + encodeURIComponent(item.id) + "/make-default", { method: "POST" });
    state.selectedPrompt = data.prompt;
    await refreshPromptsAfterAction("Default changed");
  } catch (error) {
    showPromptError(error);
  }
}

async function archivePrompt() {
  const item = state.selectedPrompt;
  if (!item || !item.id) return;
  try {
    const data = await api("/web/api/system-prompts/" + encodeURIComponent(item.id) + "/archive", { method: "POST" });
    state.selectedPrompt = data.prompt;
    await refreshPromptsAfterAction("Archived");
  } catch (error) {
    showPromptError(error);
  }
}

async function refreshPromptsAfterAction(message) {
  const selectedId = state.selectedPrompt && state.selectedPrompt.id;
  const data = await api("/web/api/system-prompts");
  state.prompts = data.items || [];
  if (el.promptsCount) el.promptsCount.textContent = String(data.total || state.prompts.length);
  if (selectedId) {
    const detail = await api("/web/api/system-prompts/" + encodeURIComponent(selectedId));
    state.selectedPrompt = detail.prompt;
  }
  renderPromptsList();
  renderPromptEditor();
  showPromptStatus(message, "ok");
}

function showPromptError(error) {
  showPromptStatus("Error: " + (error.message || error), "bad");
}

function showPromptStatus(text, tone) {
  if (!el.promptEditorStatus) return;
  el.promptEditorStatus.textContent = text;
  el.promptEditorStatus.classList.remove("ok", "bad");
  if (tone) el.promptEditorStatus.classList.add(tone);
}

function promptStatusClass(status) {
  if (status === "active") return "pill-blue";
  if (status === "archived") return "pill-slate";
  return "pill-amber";
}

function shortSha(value) {
  return value ? String(value).slice(0, 10) : "no-sha";
}

function setDisabled(button, disabled) {
  if (!button) return;
  button.disabled = !!disabled;
}

function escapeHtml(value) {
  return String(value || "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function sqlEventSpec(label) {
  const key = String(label || "").trim().toUpperCase();
  const specs = window.SQL_EVENT_SPECS && window.SQL_EVENT_SPECS.events;
  if (specs && specs[key]) return specs[key];
  if (typeof window.getSqlEventSpec === "function") return window.getSqlEventSpec(key);
  return null;
}

function riskLabel(item) {
  if (!item || typeof item !== "object") return "";
  return String(item.vuln_class || item.label || item.identifier || "").trim();
}

function riskInfo(label) {
  const spec = sqlEventSpec(label);
  const key = String(label || "UNKNOWN_RISK").trim().toUpperCase();
  if (spec) {
    return {
      label: spec.identifier || key,
      title: spec.business_meaning || key,
      meaning: spec.business_meaning || "Описание риска пока не задано.",
      trigger: spec.trigger || "Технический триггер не задан.",
    };
  }
  return {
    label: key,
    title: key,
    meaning: "Для этого риска пока нет бизнес-описания в sql_event_specs.",
    trigger: "Откройте raw JSON и проверьте detector/evidence для этого label.",
  };
}

function renderRiskBadges(items) {
  const labels = [];
  (Array.isArray(items) ? items : []).forEach((item) => {
    const label = riskLabel(item);
    if (label && !labels.includes(label)) labels.push(label);
  });
  if (!labels.length) return "";
  return '<div class="risk-badge-row">' + labels.map((label) => {
    const info = riskInfo(label);
    return '<button class="risk-badge" type="button" data-risk-label="' + escapeHtml(info.label) + '">' +
      '<span>' + escapeHtml(info.label) + '</span>' +
      '<small>' + escapeHtml(info.meaning) + '</small>' +
    "</button>";
  }).join("") + "</div>";
}

function openRiskModal(label) {
  const info = riskInfo(label);
  let modal = document.getElementById("riskInfoModal");
  if (!modal) {
    modal = document.createElement("div");
    modal.id = "riskInfoModal";
    modal.className = "risk-modal";
    document.body.appendChild(modal);
  }
  modal.innerHTML = `
    <div class="risk-modal__backdrop" data-risk-close="1"></div>
    <section class="risk-modal__panel" role="dialog" aria-modal="true" aria-labelledby="riskModalTitle">
      <header class="risk-modal__head">
        <div>
          <div class="risk-modal__code">${escapeHtml(info.label)}</div>
          <h2 id="riskModalTitle">${escapeHtml(info.title)}</h2>
        </div>
        <button class="risk-modal__close" type="button" data-risk-close="1" aria-label="Close risk details">x</button>
      </header>
      <div class="risk-modal__body">
        <article>
          <b>Бизнес-смысл</b>
          <p>${escapeHtml(info.meaning)}</p>
        </article>
        <article>
          <b>Когда срабатывает</b>
          <p>${escapeHtml(info.trigger)}</p>
        </article>
      </div>
    </section>
  `;
  modal.classList.add("is-open");
  modal.querySelectorAll("[data-risk-close]").forEach((node) => {
    node.addEventListener("click", () => modal.classList.remove("is-open"));
  });
}

function jsonText(value) {
  return JSON.stringify(value, null, 2);
}

function jsonScalarHtml(value) {
  if (value === null) return '<span class="json-null">null</span>';
  if (typeof value === "string") return '<span class="json-string">"' + escapeHtml(value) + '"</span>';
  if (typeof value === "number") return '<span class="json-number">' + escapeHtml(value) + "</span>";
  if (typeof value === "boolean") return '<span class="json-bool">' + escapeHtml(value) + "</span>";
  return '<span>' + escapeHtml(String(value)) + "</span>";
}

function jsonTreeHtml(value, depth = 0) {
  if (value === null || typeof value !== "object") return jsonScalarHtml(value);
  const isArray = Array.isArray(value);
  const entries = isArray ? value.map((item, index) => [String(index), item]) : Object.entries(value);
  const label = isArray ? "Array(" + entries.length + ")" : "Object(" + entries.length + ")";
  if (!entries.length) return isArray ? "[]" : "{}";
  const open = depth < 2 ? " open" : "";
  const rows = entries.map(([key, child]) => {
    return '<li><span class="json-key">' + escapeHtml(key) + '</span>: ' + jsonTreeHtml(child, depth + 1) + "</li>";
  }).join("");
  return "<details" + open + "><summary>" + label + "</summary><ul>" + rows + "</ul></details>";
}

function renderJsonBlockHtml(value) {
  const key = jsonPayloads.length;
  jsonPayloads.push(value);
  return `
    <div class="json-block" data-json-block="1" data-json-key="${key}">
      <div class="json-tools" role="group" aria-label="JSON view">
        <span class="json-title">JSON</span>
        <button type="button" class="json-mode active" data-json-mode="tree">Tree</button>
        <button type="button" class="json-mode" data-json-mode="text">Text</button>
      </div>
      <div class="json-viewer"></div>
    </div>
  `;
}

function fallbackJson(container, data, mode = "tree") {
  const note = jsonLoadError ? "JSON renderer is unavailable: " + jsonLoadError.message + "\n\n" : "";
  if (mode === "text") {
    const pre = document.createElement("pre");
    pre.className = "json-fallback mono";
    pre.setAttribute("aria-readonly", "true");
    pre.textContent = note + jsonText(data);
    container.replaceChildren(pre);
  } else {
    const tree = document.createElement("div");
    tree.className = "json-tree";
    tree.setAttribute("aria-readonly", "true");
    tree.innerHTML = jsonTreeHtml(data);
    container.replaceChildren(tree);
  }
  container.dataset.fallback = "1";
  container.dataset.rendered = "1";
}

function initJsonViewer(container) {
  if (!container || container.dataset.rendered === "1") return container ? container.__jsonEditor || null : null;
  const data = container.__jsonData;
  if (!createJSONEditor) {
    fallbackJson(container, data);
    return null;
  }
  try {
    const editor = createJSONEditor({
      target: container,
      props: {
        content: { json: data },
        mode: "tree",
        readOnly: true,
        mainMenuBar: false,
        navigationBar: false,
        statusBar: false,
        indentation: 2,
      },
    });
    container.__jsonEditor = editor;
    container.dataset.rendered = "1";
    return editor;
  } catch (error) {
    jsonLoadError = error;
    fallbackJson(container, data);
    return null;
  }
}

function switchJsonMode(block, mode) {
  const container = block.querySelector(".json-viewer");
  const editor = initJsonViewer(container);
  if (editor) {
    editor.updateProps({
      mode,
      readOnly: true,
      mainMenuBar: false,
      navigationBar: false,
      statusBar: false,
      indentation: 2,
    });
  } else {
    fallbackJson(container, container.__jsonData, mode);
  }
  block.querySelectorAll(".json-mode").forEach((button) => {
    button.classList.toggle("active", button.dataset.jsonMode === mode);
  });
}

function initJsonBlocks(root) {
  (root || document).querySelectorAll("[data-json-block='1']").forEach((block) => {
    if (block.dataset.bound === "1") return;
    block.dataset.bound = "1";
    const container = block.querySelector(".json-viewer");
    const key = Number(block.dataset.jsonKey || -1);
    container.__jsonData = jsonPayloads[key];
    block.querySelectorAll(".json-mode").forEach((button) => {
      button.addEventListener("click", () => switchJsonMode(block, button.dataset.jsonMode || "tree"));
    });
    jsonReady.finally(() => initJsonViewer(container));
  });
}

async function openPromptTraceDrawer() {
  state.roundDetail = null;
  const msg = lastAssistant((state.chat && state.chat.messages) || []);
  const traceId = msg && (msg.trace_id || (msg.result && msg.result.metadata && msg.result.metadata.trace_id));
  if (!traceId) return;
  if (!state.promptTrace || state.promptTrace.trace_id !== traceId) {
    const data = await api("/web/api/traces/" + encodeURIComponent(traceId) + "/prompts");
    state.promptTrace = data.prompt_trace || { items: [], summary: {} };
  }
  state.promptTraceItem = (state.promptTrace.items || [])[0] || null;
  state.promptDrawerTab = "prompt";
  renderPromptTraceDrawer();
  el.promptDrawer.classList.add("is-open");
  el.promptDrawer.setAttribute("aria-hidden", "false");
  el.promptDrawerOverlay.classList.add("is-open");
}

function closePromptTraceDrawer() {
  if (!el.promptDrawer) return;
  state.roundDetail = null;
  el.promptDrawer.classList.remove("is-open");
  el.promptDrawer.setAttribute("aria-hidden", "true");
  el.promptDrawerOverlay.classList.remove("is-open");
}

function openRoundDetailDrawer(stageInfo, round) {
  if (!round || !round.detail) return;
  state.roundDetail = { stageInfo, round };
  state.roundCandidateIndex = activeCandidateIndex(round.detail);
  state.promptDrawerTab = "prompt";
  renderPromptTraceDrawer();
  el.promptDrawer.classList.add("is-open");
  el.promptDrawer.setAttribute("aria-hidden", "false");
  el.promptDrawerOverlay.classList.add("is-open");
}

function renderPromptTraceDrawer() {
  if (state.roundDetail) {
    renderRoundDetailDrawer();
    return;
  }
  if (!el.promptDrawerBody || !state.promptTrace) return;
  const items = state.promptTrace.items || [];
  if (!state.promptTraceItem && items.length) state.promptTraceItem = items[0];
  const item = state.promptTraceItem;
  el.promptDrawerTitle.textContent = item ? item.title : "Runtime prompts";
  el.promptDrawerSubtitle.textContent = promptTraceSubtitle(item, state.promptTrace.summary);
  const defaultTabLabels = {prompt: "Prompt", sources: "Sources", json: "JSON"};
  el.promptDrawerTabs.querySelectorAll("[data-prompt-tab]").forEach((tab) => {
    tab.textContent = defaultTabLabels[tab.dataset.promptTab] || tab.textContent;
    tab.classList.toggle("is-active", tab.dataset.promptTab === state.promptDrawerTab);
  });
  if (!items.length) {
    el.promptDrawerBody.innerHTML = '<div class="prompt-drawer__empty">Prompt trace is not available for this run. New runs record prompt metadata and assembled prompt text.</div>';
    return;
  }
  const list = '<div class="prompt-trace-list">' + items.map((row) => `
    <button class="prompt-trace-item ${item && item.key === row.key ? "is-active" : ""}" type="button" data-prompt-key="${escapeHtml(row.key)}">
      <span class="prompt-trace-item__title">${escapeHtml(row.node)} · ${escapeHtml(row.meta && row.meta.prompt_type)}</span>
      <span class="prompt-trace-item__meta">${escapeHtml(promptVersionLabel(row))}</span>
    </button>
  `).join("") + "</div>";
  const body = state.promptDrawerTab === "sources"
    ? renderPromptSources(item)
    : state.promptDrawerTab === "json"
      ? renderJsonBlockHtml(item)
      : renderPromptAssembly(item);
  el.promptDrawerBody.innerHTML = list + '<div class="prompt-trace-detail">' + body + "</div>";
  initJsonBlocks(el.promptDrawerBody);
  el.promptDrawerBody.querySelectorAll("[data-prompt-key]").forEach((button) => {
    button.addEventListener("click", () => {
      state.promptTraceItem = items.find((row) => row.key === button.dataset.promptKey) || item;
      renderPromptTraceDrawer();
    });
  });
  el.promptDrawerBody.querySelectorAll("[data-source-json]").forEach((button) => {
    button.addEventListener("click", () => {
      const key = button.dataset.sourceJson;
      const source = (item && item.sources && item.sources[key]) || {};
      state.promptDrawerTab = "json";
      state.promptTraceItem = { ...item, source_focus: { key, source } };
      renderPromptTraceDrawer();
    });
  });
}

function renderRoundDetailDrawer() {
  const data = state.roundDetail || {};
  const round = data.round || {};
  const detail = round.detail || {};
  const kind = detail.kind || round.node || "round";
  el.promptDrawerTitle.textContent = (kind === "audit" ? "Validation" : "SQL Generation") + " · круг " + round.number;
  el.promptDrawerSubtitle.textContent = [round.duration, detail.model || detail.backend || "", detail.summary || ""].filter(Boolean).join(" · ");
  el.promptDrawerTabs.querySelectorAll("[data-prompt-tab]").forEach((tab) => {
    const map = {prompt: "Prompt", sources: kind === "audit" ? "Findings" : "Candidates", json: "JSON"};
    tab.textContent = map[tab.dataset.promptTab] || tab.textContent;
    tab.classList.toggle("is-active", tab.dataset.promptTab === state.promptDrawerTab);
  });
  if (kind === "audit") {
    el.promptDrawerBody.innerHTML = renderAuditRoundDetail(detail);
  } else {
    el.promptDrawerBody.innerHTML = renderGenerateRoundDetail(detail);
  }
  initJsonBlocks(el.promptDrawerBody);
  el.promptDrawerBody.querySelectorAll("[data-candidate-index]").forEach((button) => {
    button.addEventListener("click", () => {
      state.roundCandidateIndex = Number(button.dataset.candidateIndex || 0);
      renderRoundDetailDrawer();
    });
  });
  el.promptDrawerBody.querySelectorAll("[data-risk-label]").forEach((button) => {
    button.addEventListener("click", (event) => {
      event.stopPropagation();
      openRiskModal(button.dataset.riskLabel);
    });
  });
}

function renderGenerateRoundDetail(detail) {
  const candidates = Array.isArray(detail.candidates) ? detail.candidates : [];
  const activeIndex = activeCandidateIndex(detail);
  const active = candidates.find((item) => Number(item.index) === activeIndex) || candidates[0] || {};
  const list = '<div class="prompt-trace-list">' + candidates.map((item) => `
    <button class="prompt-trace-item ${Number(item.index) === activeIndex ? "is-active" : ""}" type="button" data-candidate-index="${escapeHtml(item.index)}">
      <span class="prompt-trace-item__title">Candidate ${escapeHtml(item.index)}${item.selected ? " · selected" : ""}</span>
      <span class="prompt-trace-item__meta">${escapeHtml(candidateMetaLine(item))}</span>
    </button>
  `).join("") + "</div>";
  const body = state.promptDrawerTab === "json"
    ? renderJsonBlockHtml(detail)
    : state.promptDrawerTab === "sources"
      ? candidates.map(renderCandidateCard).join("") || '<div class="prompt-drawer__empty">No candidates recorded.</div>'
      : renderCandidatePrompt(active);
  return list + '<div class="prompt-trace-detail">' + body + "</div>";
}

function renderCandidateCard(item) {
  return `
    <article class="prompt-part">
      <header class="prompt-part__head">
        <span class="prompt-part__index">${escapeHtml(item.index)}</span>
        <span class="prompt-part__label">${item.selected ? "selected" : "candidate"}</span>
        <span class="prompt-part__source">${escapeHtml(candidateMetaLine(item))}</span>
      </header>
      <pre class="prompt-part__text mono">${escapeHtml(item.sql || item.response_raw || "")}</pre>
    </article>
  `;
}

function activeCandidateIndex(detail) {
  const candidates = Array.isArray(detail && detail.candidates) ? detail.candidates : [];
  if (Number.isFinite(state.roundCandidateIndex) && candidates.some((item) => Number(item.index) === state.roundCandidateIndex)) {
    return state.roundCandidateIndex;
  }
  const selected = candidates.find((item) => item.selected);
  if (selected) return Number(selected.index);
  const raw = Number(detail && detail.selected_index);
  if (Number.isFinite(raw)) return raw;
  return Number(candidates[0] && candidates[0].index) || 0;
}

function tempLabel(item) {
  if (!item || item.temperature === null || item.temperature === undefined || item.temperature === "") return "temp n/a";
  const value = Number(item.temperature);
  const text = Number.isFinite(value) ? value.toFixed(2).replace(/0+$/, "").replace(/\.$/, "") : String(item.temperature);
  return "temp " + text + (item.temperature_applied === false ? " (ignored)" : "");
}

function candidateMetaLine(item) {
  return [
    item.model || item.backend || "",
    tempLabel(item),
    formatSeconds(item.walltime_sec || 0),
    item.temperature_note || "",
  ].filter(Boolean).join(" · ");
}

function renderCandidatePrompt(item) {
  if (!item) return '<div class="prompt-drawer__empty">No candidate selected.</div>';
  const meta = item.prompt_system_meta || {};
  const chips = `
    <div class="prompt-meta-strip">
      <span>Candidate ${escapeHtml(item.index)}</span>
      <span>${escapeHtml(tempLabel(item))}</span>
      <span>${escapeHtml(item.model || item.backend || "model n/a")}</span>
      ${meta.prompt_id ? '<span>' + escapeHtml(meta.prompt_id) + '</span>' : ""}
      ${meta.prompt_version != null ? '<span>v' + escapeHtml(meta.prompt_version) + '</span>' : ""}
      ${meta.prompt_sha256 ? '<span>' + escapeHtml(shortSha(meta.prompt_sha256)) + '</span>' : ""}
    </div>
  `;
  const system = item.prompt_system
    ? promptPartHtml("S", "System prompt", meta.prompt_source || "trace", item.prompt_system, "system")
    : "";
  const user = item.prompt_user
    ? promptPartHtml("U", "User prompt", "assembled for this candidate", item.prompt_user, "task")
    : "";
  return chips + (system || user ? system + user : '<div class="prompt-drawer__empty">Prompt text was not recorded for this candidate.</div>');
}

function promptPartHtml(index, label, source, text, tone) {
  return `
    <article class="prompt-part tone-${escapeHtml(tone || "user")}">
      <header class="prompt-part__head">
        <span class="prompt-part__index">${escapeHtml(index)}</span>
        <span class="prompt-part__label">${escapeHtml(label)}</span>
        <span class="prompt-part__source">${escapeHtml(source || "")}</span>
      </header>
      <pre class="prompt-part__text mono">${escapeHtml(text || "")}</pre>
    </article>
  `;
}

function renderAuditRoundDetail(detail) {
  const findings = Array.isArray(detail.merged_findings) ? detail.merged_findings : [];
  const badges = renderRiskBadges(findings);
  const body = state.promptDrawerTab === "json"
    ? renderJsonBlockHtml(detail)
    : state.promptDrawerTab === "sources"
      ? findings.map((item, index) => `
        <article class="prompt-hit">
          <div class="prompt-hit__head"><b>${index + 1}. ${escapeHtml(riskLabel(item) || "finding")}</b><span>${escapeHtml(item.risk_score || item.severity || "")}</span></div>
          <div class="prompt-hit__sub">${escapeHtml(item.layer || item.detector || "")}</div>
          ${renderRiskBadges([item])}
          <div class="prompt-hit__business">${escapeHtml(riskInfo(riskLabel(item)).meaning)}</div>
          <div class="prompt-hit__text">${escapeHtml(item.description || item.evidence_span || "")}</div>
        </article>
      `).join("") || '<div class="prompt-drawer__empty">No findings recorded.</div>'
      : badges + '<pre class="prompt-json mono">' + escapeHtml(detail.prompt_user || "") + "</pre>";
  return '<div class="prompt-trace-list"><div class="prompt-trace-item is-active"><span class="prompt-trace-item__title">Audit call</span><span class="prompt-trace-item__meta">' + escapeHtml(detail.approved ? "approved" : "blocked") + " · risk " + escapeHtml(detail.overall_risk_score || "0") + '</span></div></div><div class="prompt-trace-detail">' + body + "</div>";
}

function promptTraceSubtitle(item, summary) {
  if (!item) return promptSummaryLabel(summary) || "No prompt metadata";
  const meta = item.meta || {};
  return [
    meta.prompt_id,
    meta.prompt_version != null ? "v" + meta.prompt_version : null,
    meta.prompt_source,
    item.started_at,
  ].filter(Boolean).join(" · ");
}

function promptVersionLabel(item) {
  const meta = (item && item.meta) || {};
  const version = meta.prompt_version != null ? "v" + meta.prompt_version : "legacy";
  return [meta.prompt_id || meta.prompt_type || "prompt", version, meta.prompt_source].filter(Boolean).join(" · ");
}

function renderPromptAssembly(item) {
  if (!item) return "";
  const meta = item.meta || {};
  const parts = item.parts || [];
  const chips = `
    <div class="prompt-meta-strip">
      <span>${escapeHtml(meta.prompt_type || "prompt")}</span>
      <span>${escapeHtml(meta.prompt_id || "unknown")}</span>
      <span>${escapeHtml(meta.prompt_version != null ? "v" + meta.prompt_version : "legacy")}</span>
      <span>${escapeHtml(shortSha(meta.prompt_sha256 || ""))}</span>
    </div>
  `;
  const cards = parts.map((part, index) => `
    <article class="prompt-part tone-${escapeHtml(part.tone || "user")}" title="${escapeHtml(part.tooltip || part.source || "")}">
      <header class="prompt-part__head">
        <span class="prompt-part__index">${index + 1}</span>
        <span class="prompt-part__label">${escapeHtml(part.label || part.kind || "prompt part")}</span>
        <span class="prompt-part__source">${escapeHtml(part.source || "")}</span>
      </header>
      <pre class="prompt-part__text mono">${escapeHtml(part.text || "")}</pre>
    </article>
  `).join("");
  return chips + (cards || '<div class="prompt-drawer__empty">No prompt parts recorded.</div>');
}

function renderPromptSources(item) {
  if (!item) return "";
  const sources = item.sources || {};
  const hits = Array.isArray(sources.rag_generation_hits) ? sources.rag_generation_hits : [];
  const securityHits = Array.isArray(sources.security_hits) ? sources.security_hits : [];
  const sourceCards = Object.entries(sources.rag_sources || {}).map(([key, value]) => {
    const hitCount = value && (value.hit_count || value.row_count || value.table_count || "0");
    const chars = value && (value.context_chars || "0");
    const status = value && value.error ? "error: " + value.error : (value && value.fallback_used ? "fallback" : "ok");
    return `
      <article class="prompt-source-card">
        <div class="prompt-source-card__title">${escapeHtml(key)}</div>
        <div class="prompt-source-grid">
          <span>Status</span><b>${escapeHtml(status)}</b>
          <span>Hits</span><b>${escapeHtml(hitCount)}</b>
          <span>Context chars</span><b>${escapeHtml(chars)}</b>
          <span>Index</span><b>${escapeHtml((value && value.index_name) || "n/a")}</b>
        </div>
      </article>
    `;
  }).join("");
  const hitCards = hits.slice(0, 8).map((hit, index) => `
    <article class="prompt-hit">
      <div class="prompt-hit__head"><b>${index + 1}. ${escapeHtml(hit.pattern_id || hit.table_name || hit.source || "retrieved chunk")}</b><span>${escapeHtml(hit.score != null ? Number(hit.score).toFixed(4) : "")}</span></div>
      <div class="prompt-hit__sub">${escapeHtml(hit.pattern_type || hit.entity_role || hit.description || "")}</div>
      <div class="prompt-hit__text">${escapeHtml(hit.text || hit.description || "")}</div>
    </article>
  `).join("");
  const securityCards = securityHits.slice(0, 8).map((hit, index) => `
    <article class="prompt-hit">
      <div class="prompt-hit__head"><b>${index + 1}. ${escapeHtml(hit.vuln_class || hit.source || "security hit")}</b><span>${escapeHtml(hit.score || "")}</span></div>
      <div class="prompt-hit__text">${escapeHtml(hit.text || hit.description || "")}</div>
    </article>
  `).join("");
  return `
    <div class="prompt-source-section">
      <h3>Retrieval sources</h3>
      ${sourceCards || '<div class="prompt-drawer__empty">No RAG source metadata recorded.</div>'}
    </div>
    <div class="prompt-source-section">
      <h3>Generation hits</h3>
      ${hitCards || '<div class="prompt-drawer__empty">No generation hits for this prompt.</div>'}
    </div>
    <div class="prompt-source-section">
      <h3>Security hits</h3>
      ${securityCards || '<div class="prompt-drawer__empty">No security hits for this prompt.</div>'}
    </div>
  `;
}

function renderChat() {
  const chat = state.chat;
  const messages = (chat && chat.messages) || [];
  const lastUser = [...messages].reverse().find((item) => item.role === "user");
  const lastAssistantMsg = lastAssistant(messages);
  renderRequestCard(lastUser);
  renderResponseCard(lastAssistantMsg);
  renderInspector(lastAssistantMsg);
  updateTraceLink(lastAssistantMsg);
  updateMeta(lastAssistantMsg);
  updateHeaderPills();
  // Подтянуть timeline текущего/последнего run'а сразу после рендера чата.
  if (chat && chat.chat_id) {
    fetchProgress(chat.chat_id).catch(() => {});
  } else {
    renderTimeline({ steps: [], complete: true, trace_id: "" });
  }
}

function renderRequestCard(message) {
  if (!message) {
    if (el.requestEmpty) el.requestEmpty.classList.remove("hidden");
    el.requestBody.querySelectorAll(".chat-request__quote").forEach((node) => node.remove());
    return;
  }
  if (el.requestEmpty) el.requestEmpty.classList.add("hidden");
  let quote = el.requestBody.querySelector(".chat-request__quote");
  if (!quote) {
    quote = document.createElement("div");
    quote.className = "chat-request__quote";
    el.requestBody.appendChild(quote);
  }
  quote.textContent = message.text || "";
}

function renderResponseCard(message) {
  if (!message) {
    el.responseCard.classList.add("hidden");
    return;
  }
  el.responseCard.classList.remove("hidden");

  const result = message.result || {};
  const meta = result.metadata || {};
  const summary = message.summary || {};
  const maxIter = meta.max_iterations || Number(el.maxIterations.value || 5);
  const iterUsed = result.iterations_used != null ? result.iterations_used : (message.pending ? 1 : "—");

  const statusKey = (() => {
    if (message.pending) return "running";
    if (message.error) return "failed";
    return String(message.status || summary.status || "draft").toLowerCase();
  })();
  setPill(el.responseStatusPill, statusKey);

  el.responseIter.textContent = "Iter " + iterUsed + "/" + maxIter;
  el.responseTime.textContent = formatTime(message.created_at || new Date().toISOString());

  const sql = result.final_sql || "";
  if (sql) {
    el.responseSqlPlaceholder.classList.add("hidden");
    el.responseSqlPlaceholder.classList.remove("is-static", "is-refusal", "is-error");
    el.responseSqlBlock.classList.remove("hidden");
    el.responseSql.innerHTML = renderHighlightedSql(sql);
  } else {
    el.responseSqlBlock.classList.add("hidden");
    el.responseSqlPlaceholder.classList.remove("hidden");
    el.responseSqlPlaceholder.classList.toggle("is-static", !message.pending);
    el.responseSqlPlaceholder.classList.toggle("is-error", !!message.error);
    el.responseSqlPlaceholder.classList.toggle("is-refusal", !!meta.refusal_message);
    if (message.pending) {
      if (el.responseSqlPlaceholderTitle) el.responseSqlPlaceholderTitle.textContent = "SQL ещё не сгенерирован";
      el.responseSqlPlaceholderSub.textContent = "Пайплайн выполняет генерацию и валидацию SQL.";
    } else if (message.error) {
      if (el.responseSqlPlaceholderTitle) el.responseSqlPlaceholderTitle.textContent = "Pipeline failed";
      el.responseSqlPlaceholderSub.textContent = (message.error.code || "error") + ": " + (message.error.message || "");
    } else if (meta.refusal_message) {
      if (el.responseSqlPlaceholderTitle) el.responseSqlPlaceholderTitle.textContent = humanPolicyLabel(meta.policy_label || meta.decision || "refuse");
      el.responseSqlPlaceholderSub.textContent = meta.refusal_message;
    } else {
      if (el.responseSqlPlaceholderTitle) el.responseSqlPlaceholderTitle.textContent = humanPolicyLabel(meta.policy_label || meta.decision || "abstain");
      el.responseSqlPlaceholderSub.textContent = meta.human_reason || meta.policy_message || meta.rationale || "Pipeline завершился без публичного SQL.";
    }
  }

  setMetric(el.metricTokens, el.metricTokensSub, formatTokens(extractTokensTotal(result)), message.pending ? "In progress" : "Generated", "neutral");
  setMetric(el.metricLatency, el.metricLatencySub, formatDuration(meta.duration_sec), message.pending ? "Elapsed" : "Elapsed", "neutral");
  setMetric(el.metricIterations, el.metricIterationsSub, iterUsed + "/" + maxIter, "Current / Max", "neutral");
  const decisionLabel = meta.decision || statusLabel(statusKey);
  setMetric(el.metricDecision, el.metricDecisionSub, decisionLabel, message.pending ? "Awaiting checks" : "Pipeline decision", decisionTone(statusKey));

  const traceId = message.trace_id || meta.trace_id || "";
  if (traceId && !message.pending) {
    el.actionReport.href = "/runs/" + encodeURIComponent(traceId);
    el.actionReport.removeAttribute("aria-disabled");
    el.actionReport.target = "_blank";
    if (el.metricDecisionTile) {
      el.metricDecisionTile.disabled = false;
      el.metricDecisionTile.dataset.href = el.actionReport.href;
    }
  } else {
    el.actionReport.href = "#";
    el.actionReport.setAttribute("aria-disabled", "true");
    if (el.metricDecisionTile) {
      el.metricDecisionTile.disabled = true;
      delete el.metricDecisionTile.dataset.href;
    }
  }
  el.actionCopySql.disabled = !sql;
  el.actionRerun.disabled = !!message.pending;
  if (el.actionPrompts) el.actionPrompts.disabled = !traceId || !!message.pending;
}

function renderInspector(message) {
  el.inspector.innerHTML = "";

  const result = (message && message.result) || {};
  const meta = result.metadata || {};
  const summary = (message && message.summary) || {};
  const maxIter = meta.max_iterations || Number(el.maxIterations.value || 5);
  const iterUsed = !message ? "—" : (result.iterations_used != null ? result.iterations_used : (message.pending ? 1 : "—"));

  const statusKey = !message
    ? "draft"
    : (message.pending ? "running" : (message.error ? "failed" : (message.status || summary.status || "draft")));
  const isPending = !!(message && message.pending);
  const isError = !!(message && message.error);
  const hasResult = !!(message && message.result && !message.pending);
  const selectedModelInfo = selectedModel();
  const modelLabel = summary.model || meta.generator_model || (selectedModelInfo && (selectedModelInfo.label || selectedModelInfo.key)) || el.modelSelect.value || "—";

  el.inspector.appendChild(inspectorRow({
    icon: SVG.status,
    label: "Status",
    value: statusLabel(statusKey).toUpperCase(),
    sub: subForStatus(statusKey),
  }));

  const traceId = (message && (message.trace_id || meta.trace_id)) || "";
  el.inspector.appendChild(inspectorRow({
    icon: SVG.trace,
    label: "Trace ID",
    value: traceId || "—",
    valueClass: "mono",
    sub: traceId ? "Dynamic execution trace" : "Not assigned yet",
    actionIcon: traceId ? SVG.external : null,
    href: traceId ? "/runs/" + encodeURIComponent(traceId) : null,
  }));

  el.inspector.appendChild(inspectorRow({
    icon: SVG.model,
    label: "Model",
    value: modelLabel,
    valueClass: "mono",
    sub: "Active model for this run",
  }));

  const promptLabel = promptSummaryLabel((message && message.summary && message.summary.prompt_summary) || null);
  el.inspector.appendChild(inspectorRow({
    icon: SVG.sql,
    label: "Prompts",
    value: promptLabel || "—",
    valueClass: "mono",
    sub: promptLabel ? "System prompt versions used by this run" : "Recorded for new prompt-registry runs",
  }));

  const riskValue = hasResult ? value(summary.risk != null ? summary.risk : meta.overall_risk_score) : (isPending ? "pending" : "—");
  el.inspector.appendChild(inspectorRow({
    icon: SVG.risk,
    label: "Risk Score",
    value: riskValue,
    sub: hasResult ? "Pipeline risk score" : (isError ? "Pipeline failed" : "Will be available after checks"),
  }));

  el.inspector.appendChild(inspectorRow({
    icon: SVG.iter,
    label: "Iterations",
    value: iterUsed + "/" + maxIter,
    sub: "Current / Max iterations",
  }));

  const sql = result.final_sql || "";
  const md = result.metadata || {};
  const policyLabel = String(md.policy_label || "");
  const refusalMessage = String(md.refusal_message || "");
  let emptyExplain = "<pre>SQL ещё не сгенерирован</pre>";
  let emptyValue = "—";
  let emptySub = "Not available yet";
  if (!sql && refusalMessage) {
    emptyValue = humanPolicyLabel(policyLabel);
    emptySub = "Policy label: " + (policyLabel || "—");
    emptyExplain = '<div class="refusal-message">' + escapeHtml(refusalMessage) + "</div>";
  } else if (!sql && policyLabel) {
    emptyValue = humanPolicyLabel(policyLabel);
    emptySub = "Policy label: " + policyLabel;
  }
  el.inspector.appendChild(inspectorRow({
    icon: SVG.sql,
    label: "Final SQL",
    value: sql ? "Available" : emptyValue,
    sub: sql ? "Latest pipeline SQL output" : emptySub,
    extra: sql ? null : emptyExplain,
    sqlRow: !sql,
  }));

  // Insight 7: split security/quality risk + temperature stats.
  const security = Number(md.security_risk_score);
  const quality = Number(md.quality_risk_score);
  if (Number.isFinite(security) || Number.isFinite(quality)) {
    el.inspector.appendChild(inspectorRow({
      icon: SVG.shield,
      label: "Risk split",
      value:
        "Security " + (Number.isFinite(security) ? security.toFixed(1) : "—")
        + " / Quality " + (Number.isFinite(quality) ? quality.toFixed(1) : "—"),
      sub: "Security blocks approval, quality is advisory",
    }));
  }
  const candidates = collectCandidateStats(message);
  if (candidates.length) {
    const lines = candidates.map((c) => (
      "iter " + c.iteration + " · cand " + c.candidate_index
      + (c.selected ? " ✓" : "")
      + " · t=" + (c.temperature != null ? Number(c.temperature).toFixed(2) : "—")
      + " · " + (c.broken ? "broken" : (c.finding_count + " findings"))
    ));
    el.inspector.appendChild(inspectorRow({
      icon: SVG.iter,
      label: "Candidates × temperature",
      value: candidates.length + " trial(s)",
      sub: "selected (✓) vs alternates by temperature",
      extra: '<pre class="candidate-stats">' + escapeHtml(lines.join("\n")) + "</pre>",
      sqlRow: true,
    }));
  }
}

function humanPolicyLabel(label) {
  const map = {
    approve: "Approved",
    approve_with_advisory: "Approved with advisory",
    refusal_required: "Refusal required",
    insufficient_context: "Insufficient context",
    revise_needed: "Needs revision",
    prompt_blocked: "Prompt blocked",
    audit_uncertain: "Audit uncertain",
    max_iterations_exceeded: "Max iterations",
    repeat_stop: "Repeat stop",
    hard_fail: "Hard fail",
  };
  return map[String(label || "").toLowerCase()] || (label || "—");
}

function escapeHtml(value) {
  return String(value == null ? "" : value)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function collectCandidateStats(msg) {
  const stats = (msg && msg.result && msg.result.candidate_metrics) || [];
  if (Array.isArray(stats) && stats.length) return stats;
  // Fallback: try to extract from trace events if available client-side.
  const events = (msg && msg.trace && msg.trace.events) || [];
  const collected = [];
  for (const ev of events) {
    if (ev.node !== "generate") continue;
    const detailsCandidates = ((ev.details || {}).candidates) || [];
    for (const c of detailsCandidates) {
      collected.push({
        iteration: ev.outputs && ev.outputs.iteration,
        candidate_index: c.candidate_index,
        selected: !!c.selected_by_selector,
        temperature: c.temperature,
        finding_count: (c.selector_score || {}).finding_count,
        broken: (c.selector_score || {}).broken,
      });
    }
  }
  return collected;
}

function inspectorRow(spec) {
  const tag = spec.href ? "a" : "div";
  const row = document.createElement(tag);
  row.className = "inspector-row" + (spec.sqlRow ? " inspector-row--sql" : "");
  if (spec.href) {
    row.href = spec.href;
    row.target = "_blank";
    row.rel = "noreferrer";
  }
  const icon = document.createElement("span");
  icon.className = "inspector-row__icon";
  icon.innerHTML = spec.icon;
  row.appendChild(icon);

  const main = document.createElement("div");
  main.className = "inspector-row__main";
  const label = document.createElement("div");
  label.className = "inspector-row__label";
  label.textContent = spec.label;
  main.appendChild(label);
  const val = document.createElement("div");
  val.className = "inspector-row__value" + (spec.valueClass ? " " + spec.valueClass : "");
  val.textContent = String(spec.value);
  main.appendChild(val);
  if (spec.sub) {
    const sub = document.createElement("div");
    sub.className = "inspector-row__sub";
    sub.textContent = spec.sub;
    main.appendChild(sub);
  }
  if (spec.extra) {
    const extra = document.createElement("div");
    extra.innerHTML = spec.extra;
    main.appendChild(extra);
  }
  row.appendChild(main);

  if (spec.actionIcon) {
    const action = document.createElement("span");
    action.className = "inspector-row__action";
    action.innerHTML = spec.actionIcon;
    row.appendChild(action);
  } else {
    const spacer = document.createElement("span");
    spacer.className = "inspector-row__action";
    row.appendChild(spacer);
  }
  return row;
}

function updateMeta(message) {
  const chat = state.chat;
  const model = selectedModel();
  const judge = selectedJudge();
  const cfgMode = (state.config && state.config.mode) || {};
  const cfgError = state.config && state.config.mode_error;
  const result = (message && message.result) || {};
  const meta = result.metadata || {};
  const summary = (message && message.summary) || {};

  const statusKey = message ? (message.pending ? "running" : (message.error ? "failed" : (message.status || summary.status || "draft"))) : "draft";

  el.metaChatId.textContent = (chat && chat.chat_id) || "—";
  el.metaMode.textContent = String((model && model.llm_mode) || cfgMode.mode || "—").toUpperCase();
  el.metaBackend.textContent = (model && model.backend) || cfgMode.generator_backend || "—";
  el.metaModel.textContent = (model && model.provider_model) || cfgMode.generator_model || "—";
  el.metaAuditor.textContent = auditorForBackend(model && model.backend) || cfgMode.auditor_backend || "—";
  el.metaTrace.textContent = (message && (message.trace_id || meta.trace_id)) || "—";
  el.metaStatus.textContent = statusLabel(statusKey).toUpperCase();
  if (el.metaIterations) el.metaIterations.value = el.maxIterations.value || "5";

  if (el.metaConfigBanner) {
    const hints = [];
    if (cfgError) hints.push(cfgError);
    if (model && !model.available_by_config && model.config_hint) {
      hints.push("Preset «" + (model.label || model.key) + "» требует " + model.config_hint + ".");
    }
    if (judge && !judge.available_by_config && judge.config_hint) {
      hints.push("Judge «" + (judge.label || judge.key) + "» требует " + judge.config_hint + ".");
    }
    if (state.config && state.config.generator_tool_mode_enabled && model && model.supports_tool_mode !== "verified") {
      hints.push("GENERATOR_TOOL_MODE включен, но выбранный preset не имеет verified tool-calling support.");
    }
    if (hints.length) {
      el.metaConfigBanner.textContent = hints.join(" ");
      el.metaConfigBanner.classList.remove("hidden");
    } else {
      el.metaConfigBanner.textContent = "";
      el.metaConfigBanner.classList.add("hidden");
    }
  }

  if (el.threadStatePill) setPill(el.threadStatePill, statusKey);
  if (el.chatIdPill) el.chatIdPill.textContent = (chat && chat.chat_id) ? chat.chat_id : "chat_new";
}

function auditorForBackend(backend) {
  // CONTOURS mapping mirror (read-only client copy).
  const map = {
    openrouter: "openrouter",
    local_openai: "local_openai",
    anthropic_cli: "anthropic_cli",
    codex_cli: "codex_cli",
  };
  return backend ? (map[backend] || "—") : "";
}

function updateHeaderPills() {
  const model = selectedModel();
  const modelLabel = (model && (model.label || model.key)) || el.modelSelect.value || "—";
  if (el.modelPillValue) el.modelPillValue.textContent = modelLabel;
  if (el.modelPillValueFooter) el.modelPillValueFooter.textContent = modelLabel;
}

function renderHistory() {
  const q = (el.historySearch.value || "").toLowerCase();
  const status = el.historyFilter.value || "all";
  if (location.pathname === "/history") {
    const params = new URLSearchParams();
    if (q) params.set("q", q);
    if (status !== "all") params.set("status", status);
    const next = "/history" + (params.toString() ? "?" + params.toString() : "");
    history.replaceState({}, "", next);
  }
  const rows = state.chats.filter((item) => {
    const hay = [item.title, item.task, item.trace_id, item.model].join(" ").toLowerCase();
    const statusOk = status === "all" || item.status === status;
    return statusOk && (!q || hay.includes(q));
  });
  el.historyList.innerHTML = "";
  if (!rows.length) {
    const empty = document.createElement("div");
    empty.className = "chat-history__empty";
    empty.textContent = "Запросы не найдены.";
    el.historyList.appendChild(empty);
    return;
  }
  rows.forEach((item) => el.historyList.appendChild(historyRow(item)));
}

function syncHistoryControlsFromUrl() {
  const url = new URL(location.href);
  el.historySearch.value = url.searchParams.get("q") || "";
  const status = url.searchParams.get("status") || "all";
  const option = Array.from(el.historyFilter.options).find((item) => item.value === status);
  el.historyFilter.value = option ? status : "all";
}

function historyRow(item) {
  const link = document.createElement("a");
  link.href = "/chat/" + item.chat_id;
  link.dataset.link = "";
  link.className = "chat-history__row";
  const left = document.createElement("div");
  const title = document.createElement("div");
  title.className = "chat-history__row-title";
  title.textContent = item.title || item.task || item.chat_id;
  const meta = document.createElement("div");
  meta.className = "chat-history__row-meta";
  meta.textContent = [item.updated_at, item.trace_id, item.model, item.prompt_summary_label].filter(Boolean).join(" · ");
  left.appendChild(title);
  left.appendChild(meta);
  link.appendChild(left);
  link.appendChild(makePill(statusLabel(item.status), statusPillClass(item.status)));
  return link;
}

function updateTraceLink(message) {
  const traceId = message && message.trace_id;
  if (!traceId || !state.config) {
    if (el.traceLink) el.traceLink.classList.add("hidden");
    return;
  }
  const base = (state.config.trace_viewer_url || "").replace(/\/$/, "");
  if (el.traceLink) {
    el.traceLink.href = base + "/trace/" + encodeURIComponent(traceId);
    el.traceLink.classList.remove("hidden");
  }
}

function selectedModel() {
  const key = el.modelSelect.value;
  return (state.config && (state.config.models || []).find((item) => item.key === key)) || null;
}

function selectedJudge() {
  const key = el.judgeSelect ? el.judgeSelect.value : "";
  return (state.config && (state.config.judge_backends || []).find((item) => item.key === key)) || null;
}

function lastAssistant(messages) {
  return [...messages].reverse().find((item) => item.role === "assistant");
}

function attachPromptSummary(summary) {
  if (!summary || !summary.prompt_summary || !state.chat) return;
  const msg = lastAssistant(state.chat.messages || []);
  if (!msg) return;
  msg.summary = msg.summary || {};
  msg.summary.prompt_summary = summary.prompt_summary;
  msg.summary.prompt_summary_label = summary.prompt_summary_label || summary.prompt_summary.label || "";
}

function promptSummaryLabel(summary) {
  if (!summary) return "";
  if (summary.label) return summary.label;
  const unique = Array.isArray(summary.unique) ? summary.unique : [];
  return unique.map((item) => item.label || item.prompt_id || item.prompt_type).filter(Boolean).join(", ");
}

function setPill(element, statusKey) {
  if (!element) return;
  const entry = STATUS_PILL[String(statusKey || "draft").toLowerCase()] || STATUS_PILL.draft;
  element.className = "pill " + entry.cls;
  element.textContent = entry.text;
}

function statusPillClass(status) {
  const entry = STATUS_PILL[String(status || "draft").toLowerCase()];
  return entry ? entry.cls : "pill-slate";
}

function setMetric(valueEl, subEl, value, sub, tone) {
  valueEl.textContent = value;
  valueEl.classList.remove("tone-ok", "tone-warn", "tone-bad", "tone-neutral");
  valueEl.classList.add("tone-" + (tone || "neutral"));
  subEl.textContent = sub;
}

function decisionTone(statusKey) {
  if (statusKey === "approved") return "ok";
  if (statusKey === "needs_review" || statusKey === "clarify" || statusKey === "revise") return "warn";
  if (statusKey === "failed" || statusKey === "error" || statusKey === "blocked" || statusKey === "refused") return "bad";
  return "neutral";
}

function subForStatus(statusKey) {
  switch (String(statusKey || "").toLowerCase()) {
    case "running": return "Pipeline execution in progress";
    case "approved": return "SQL passed all checks";
    case "needs_review": return "Needs human review";
    case "failed": return "Pipeline failed";
    case "blocked": return "Blocked by guardrails";
    default: return "Awaiting pipeline run";
  }
}

function extractTokensTotal(result) {
  const meta = result.metadata || {};
  if (typeof meta.tokens_total === "number") return meta.tokens_total;
  let sum = 0;
  (result.iterations_log || []).forEach((iter) => {
    (iter.llm_calls || []).forEach((call) => {
      sum += (call.usage && call.usage.total_tokens) || call.tokens_total || 0;
    });
  });
  return sum || null;
}

function formatTokens(n) {
  return n == null ? "—" : Number(n).toLocaleString("ru-RU");
}

function formatDuration(sec) {
  return sec === undefined || sec === null ? "—" : Number(sec).toFixed(1) + "s";
}

function formatTime(iso) {
  return iso ? new Date(iso).toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit", second: "2-digit" }) : "—";
}

function formatDateTime(iso) {
  return iso ? new Date(iso).toLocaleString("ru-RU", { day: "2-digit", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit" }) : "—";
}

async function copyText(text) {
  if (navigator.clipboard && window.isSecureContext) {
    await navigator.clipboard.writeText(text);
    return;
  }
  const area = document.createElement("textarea");
  area.value = text;
  area.style.position = "fixed";
  area.style.opacity = "0";
  document.body.appendChild(area);
  area.select();
  document.execCommand("copy");
  area.remove();
}

function currentSql() {
  const msg = lastAssistant((state.chat && state.chat.messages) || []);
  return (msg && msg.result && msg.result.final_sql) || "";
}

function currentTask() {
  const messages = (state.chat && state.chat.messages) || [];
  for (let i = messages.length - 1; i >= 0; i -= 1) {
    if (messages[i].role === "user") return messages[i].text || "";
  }
  return "";
}

function makePill(text, cls) {
  const span = document.createElement("span");
  span.className = "pill " + (cls || "pill-slate");
  span.textContent = text || "unknown";
  return span;
}

function statusLabel(status) {
  const key = String(status || "").toLowerCase();
  const entry = STATUS_PILL[key];
  if (entry) {
    const txt = entry.text.toLowerCase();
    return txt.replace(/\b\w/g, (m) => m.toUpperCase());
  }
  if (!status) return "Draft";
  return String(status).replace(/_/g, " ");
}

function value(raw) {
  if (raw === undefined || raw === null || raw === "") return "—";
  if (typeof raw === "number") return String(raw);
  return String(raw);
}

function setActiveNav(path) {
  const key = path.startsWith("/history")
    ? "/history"
    : (path.startsWith("/settings/prompts") || path.startsWith("/prompts/system"))
        ? "/settings/prompts"
        : "/chat";
  document.querySelectorAll(".tool-btn").forEach((link) => {
    const href = link.getAttribute("href") || "";
    link.classList.toggle("is-active", href === key);
  });
}

function showView(name) {
  el.viewChat.classList.toggle("hidden", name !== "chat");
  el.viewHistory.classList.toggle("hidden", name !== "history");
  if (el.viewPrompts) el.viewPrompts.classList.toggle("hidden", name !== "prompts");
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  });
  const data = await response.json();
  if (!response.ok) {
    throw new Error(data.detail || data.error || response.statusText);
  }
  return data;
}
