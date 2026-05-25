const report = readJson("report-json-data") || {};
const drawerData = readJson("report-drawer-data") || {};
const drawer = document.getElementById("stepDrawer");
const detailDrawer = document.getElementById("detailDrawer");
const overlay = document.getElementById("drawerOverlay");
const drawerTitle = document.getElementById("drawerTitle");
const drawerSubtitle = document.getElementById("drawerSubtitle");
const drawerTabs = document.getElementById("drawerTabs");
const drawerBody = document.getElementById("drawerBody");
const detailTitle = document.getElementById("detailTitle");
const detailSubtitle = document.getElementById("detailSubtitle");
const detailBody = document.getElementById("detailBody");

const checkSvg = '<svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"/></svg>';
const pinHtml = '<span class="tl-pin"></span>';
let activeStep = (report.timeline_steps || []).find((item) => item.active)?.key || (report.timeline_steps || [])[0]?.key || "";
let activeTab = "prompt";
let createJSONEditor = null;
let jsonLoadError = null;

const jsonReady = import("https://cdn.jsdelivr.net/npm/vanilla-jsoneditor@3.12.0/standalone.js")
  .then((module) => {
    createJSONEditor = module.createJSONEditor;
  })
  .catch((err) => {
    jsonLoadError = err;
  });

function readJson(id) {
  const node = document.getElementById(id);
  if (!node) return null;
  try {
    return JSON.parse(node.textContent || "null");
  } catch (err) {
    console.warn("JSON parse failed:", id, err.message);
    return null;
  }
}

function escapeHtml(value) {
  return String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function sqlEventSpec(label) {
  const key = String(label || "").trim().toUpperCase();
  const reportSpecs = report.sql_event_specs && report.sql_event_specs.events;
  if (reportSpecs && reportSpecs[key]) return reportSpecs[key];
  const globalSpecs = window.SQL_EVENT_SPECS && window.SQL_EVENT_SPECS.events;
  if (globalSpecs && globalSpecs[key]) return globalSpecs[key];
  if (typeof window.getSqlEventSpec === "function") return window.getSqlEventSpec(key);
  return null;
}

function riskLabel(item) {
  if (!item || typeof item !== "object") return "";
  return String(item.vuln_class || item.label || item.identifier || "").trim();
}

function formatSeconds(value) {
  const sec = Number(value || 0);
  if (!Number.isFinite(sec)) return "0.000s";
  return sec < 1 ? sec.toFixed(3) + "s" : sec.toFixed(2) + "s";
}

function shortSha(value) {
  return value ? String(value).slice(0, 10) : "no-sha";
}

function jsonText(value) {
  try {
    return JSON.stringify(value, null, 2);
  } catch (err) {
    return String(value);
  }
}

const sqlKeywords = [
  "SELECT", "FROM", "WHERE", "GROUP", "BY", "HAVING", "ORDER", "LIMIT",
  "JOIN", "LEFT", "RIGHT", "INNER", "FULL", "OUTER", "CROSS", "ON",
  "AS", "AND", "OR", "NOT", "NULL", "IS", "IN", "EXISTS", "UNION",
  "ALL", "DISTINCT", "CASE", "WHEN", "THEN", "ELSE", "END", "WITH",
  "OVER", "PARTITION", "OFFSET", "FETCH", "FIRST", "NEXT", "ROWS", "ONLY"
];
const sqlFunctions = [
  "COUNT", "SUM", "AVG", "MIN", "MAX", "COALESCE", "NULLIF", "DATE_TRUNC",
  "EXTRACT", "ROUND", "LOWER", "UPPER", "CAST"
];
const sqlTypes = ["TEXT", "INTEGER", "NUMERIC", "DATE", "TIMESTAMP", "BOOLEAN", "UUID"];

function formatSql(sql) {
  const raw = String(sql || "").replace(/\s+/g, " ").trim();
  if (!raw) return "";
  let out = raw.replace(/\bSELECT\s+/i, "SELECT\n  ");
  [
    "FROM",
    "WHERE",
    "GROUP BY",
    "HAVING",
    "ORDER BY",
    "LIMIT",
    "OFFSET",
    "UNION ALL",
    "UNION"
  ].forEach((phrase) => {
    const re = new RegExp("\\s+" + phrase.replace(" ", "\\s+") + "\\b", "gi");
    out = out.replace(re, "\n" + phrase);
  });
  out = out.replace(/\s*,\s*/g, ",\n  ");
  out = out.replace(/\n\s+(FROM|WHERE|GROUP BY|HAVING|ORDER BY|LIMIT|OFFSET|UNION ALL|UNION)\b/gi, "\n$1");
  out = out.replace(/\s*;\s*$/, ";");
  return out.split("\n").map((line) => line.trimEnd()).join("\n");
}

function protectStrings(line) {
  const values = [];
  const text = line.replace(/'([^']|'')*'/g, (match) => {
    const key = `__SQL_STR_${values.length}__`;
    values.push(match);
    return key;
  });
  return { text, values };
}

function restoreStrings(line, values) {
  return line.replace(/__SQL_STR_(\d+)__/g, (_match, index) => {
    return `<span class="sql-str">${escapeHtml(values[Number(index)] || "")}</span>`;
  });
}

function highlightSqlLine(line) {
  const { text, values } = protectStrings(line);
  let html = escapeHtml(text);
  const fnRe = new RegExp("\\b(" + sqlFunctions.join("|") + ")\\b(?=\\s*\\()", "gi");
  const kwRe = new RegExp("\\b(" + sqlKeywords.join("|") + ")\\b", "gi");
  const typeRe = new RegExp("\\b(" + sqlTypes.join("|") + ")\\b", "gi");
  html = html.replace(fnRe, '<span class="sql-fn">$1</span>');
  html = html.replace(typeRe, '<span class="sql-type">$1</span>');
  html = html.replace(kwRe, '<span class="sql-kw">$1</span>');
  html = html.replace(/\b(\d+(?:\.\d+)?)\b/g, '<span class="sql-num">$1</span>');
  return restoreStrings(html, values);
}

function renderSqlBoxes() {
  document.querySelectorAll("[data-sql-text]").forEach((box) => {
    const formatted = formatSql(box.dataset.sqlText || "");
    const lines = formatted ? formatted.split("\n") : [""];
    box.innerHTML = lines.map((line, index) => {
      return `<div class="sql-line"><span class="row-num">${index + 1}</span>${highlightSqlLine(line)}</div>`;
    }).join("");
    const counter = box.parentElement.querySelector("[data-sql-line-count]");
    if (counter) counter.textContent = `${lines.length} lines`;
  });
}

function stepByKey(key) {
  return (report.timeline_steps || []).find((item) => item.key === key || item.drawer_key === key);
}

function setActiveStep(key) {
  document.querySelectorAll(".timeline-step").forEach((button) => {
    const active = button.dataset.step === key;
    const dot = button.querySelector(".tl-dot");
    button.classList.toggle("is-active", active);
    button.setAttribute("aria-expanded", active ? "true" : "false");
    if (dot) {
      dot.classList.toggle("active", active);
      dot.classList.toggle("done", !active);
      dot.innerHTML = active ? pinHtml : checkSvg;
    }
  });
}

function stepMessages(step) {
  const events = step.events || [];
  if (!events.length) {
    return [
      {
        title: step.label,
        at: "",
        purpose: "No event payload was recorded for this step.",
        code: {},
        foot: step.duration,
      },
    ];
  }
  return events.map((event) => ({
    title: `${event.index}. ${event.node || step.label}`,
    at: event.started_at || "",
    purpose: event.outcome || "Pipeline event payload",
    code: event.outputs || event,
    foot: `${event.duration_sec || 0}s | ${event.level || "info"}`,
  }));
}

function renderMessages(step) {
  const entries = Array.isArray(step.prompt_entries) ? step.prompt_entries : [];
  if (entries.length) {
    return `
      <div class="drawer-meta">
        <span>${escapeHtml(entries.length)} prompt exchanges</span>
        <span class="drawer-chip">${escapeHtml(step.status || "step")}</span>
      </div>
      <div class="prompt-trace-stack">
        ${entries.map((item) => renderPromptAssembly(item)).join("")}
      </div>
    `;
  }
  const cards = stepMessages(step)
    .map((item) => `
      <div class="drawer-card">
        <div class="drawer-card-head">
          <div class="drawer-card-title">${escapeHtml(item.title)}</div>
          <div class="drawer-small mono">${escapeHtml(item.at)}</div>
        </div>
        <div class="drawer-purpose"><b>Purpose</b><span>${escapeHtml(item.purpose)}</span></div>
        <pre class="drawer-pre mono">${escapeHtml(jsonText(item.code))}</pre>
        <div class="drawer-foot"><b>Step</b><span>${escapeHtml(item.foot)}</span></div>
      </div>
    `)
    .join("");
  return `<div class="drawer-meta"><span>${escapeHtml((step.events || []).length)} events</span><span class="drawer-chip">${escapeHtml(step.status || "step")}</span></div>${cards}`;
}

function promptVersionLabel(item) {
  const meta = item.meta || item || {};
  const version = meta.prompt_version ?? item.prompt_version;
  if (version !== null && version !== undefined && version !== "") return `v${version}`;
  if (meta.prompt_id || item.prompt_id) return "legacy";
  return "unversioned";
}

function promptMetaChips(item) {
  const meta = item.meta || item || {};
  const chips = [
    meta.prompt_type || item.prompt_type || item.node || "prompt",
    promptVersionLabel(item),
    meta.prompt_source || item.prompt_source || "trace",
  ];
  if (meta.prompt_id || item.prompt_id) chips.push(meta.prompt_id || item.prompt_id);
  if (meta.prompt_sha256 || item.prompt_sha256) {
    chips.push(`sha ${String(meta.prompt_sha256 || item.prompt_sha256).slice(0, 12)}`);
  }
  return chips.map((chip) => `<span class="prompt-chip">${escapeHtml(chip)}</span>`).join("");
}

function renderPromptAssembly(item) {
  const parts = Array.isArray(item.parts) ? item.parts : [];
  const title = item.title || `${item.node || "prompt"} ${promptVersionLabel(item)}`;
  const cards = parts.map((part) => `
    <section class="prompt-part prompt-tone-${escapeHtml(part.tone || part.kind || "user")}" title="${escapeHtml(part.tooltip || part.source || "")}">
      <div class="prompt-part-head">
        <span>${escapeHtml(part.label || part.kind || "Prompt part")}</span>
        <span>${escapeHtml(part.source || "")}</span>
      </div>
      <pre class="prompt-text">${escapeHtml(part.text || "")}</pre>
    </section>
  `).join("");
  return `
    <article class="prompt-step-card">
      <div class="prompt-step-head">
        <div>
          <div class="prompt-step-title">${escapeHtml(title)}</div>
          <div class="prompt-step-sub">${escapeHtml(item.started_at || "")}${item.duration_sec ? " | " + escapeHtml(item.duration_sec + "s") : ""}</div>
        </div>
        <button class="btn !py-0.5 !px-1.5 text-[11px]" type="button" data-detail="${escapeHtml(item.key || item.event_key || "")}">Open</button>
      </div>
      <div class="prompt-meta-strip">${promptMetaChips(item)}</div>
      <div class="prompt-part-stack">${cards || '<div class="drawer-card">No prompt text was recorded for this step.</div>'}</div>
    </article>
  `;
}

function sourceHitName(hit) {
  return hit.table_name || hit.pattern_id || hit.vuln_class || hit.name || hit.source || hit.id || "retrieved chunk";
}

function sourceHitText(hit) {
  return hit.text || hit.description || hit.snippet || hit.content || JSON.stringify(hit).slice(0, 600);
}

function renderPromptHits(title, hits) {
  const rows = (Array.isArray(hits) ? hits : []).slice(0, 8).map((hit, index) => {
    const score = hit.score ?? hit.similarity ?? hit.distance ?? "";
    return `
      <div class="prompt-source-hit">
        <span class="drawer-index">${index + 1}</span>
        <span class="prompt-source-main">
          <span class="prompt-source-title">${escapeHtml(sourceHitName(hit))}</span>
          <span class="prompt-source-text">${escapeHtml(sourceHitText(hit))}</span>
        </span>
        <span class="prompt-source-score">${escapeHtml(score)}</span>
      </div>
    `;
  }).join("");
  return `
    <section class="prompt-source-section">
      <div class="drawer-card-title">${escapeHtml(title)}</div>
      ${rows || '<div class="drawer-card">No hits were recorded for this source.</div>'}
    </section>
  `;
}

function renderPromptSources(item) {
  const sources = item.sources || {};
  const sourceMap = sources.rag_sources || {};
  const sourceCards = Object.entries(sourceMap).map(([name, source]) => `
    <div class="drawer-card">
      <div class="drawer-card-title">${escapeHtml(name)}</div>
      <div class="drawer-kv">
        <div><span>Status</span>${source.enabled === false ? "off" : "on"}</div>
        <div><span>Hits</span>${escapeHtml(source.hit_count ?? source.row_count ?? "unknown")}</div>
        <div><span>Context chars</span>${escapeHtml(source.context_chars ?? 0)}</div>
        <div><span>Host</span>${escapeHtml(source.dsn_host || "n/a")}</div>
      </div>
    </div>
  `).join("");
  return `
    <div class="prompt-source-grid">
      <div class="drawer-kv">
        <div><span>Retrieve event</span>${escapeHtml(sources.retrieve_event_index || "n/a")}</div>
        <div><span>Generated context chars</span>${escapeHtml(sources.generation_context_chars || 0)}</div>
        <div><span>Prompt request sha</span>${escapeHtml(item.prompt_request_sha256 || "n/a")}</div>
        <div><span>Iteration</span>${escapeHtml(item.iteration || "n/a")}</div>
      </div>
      ${sourceCards || '<div class="drawer-card">No RAG source metadata was recorded.</div>'}
      ${renderPromptHits("Generation RAG hits", sources.rag_generation_hits)}
      ${renderPromptHits("Security RAG hits", sources.security_hits)}
    </div>
  `;
}

function renderPromptDetailNode(item) {
  const wrap = document.createElement("div");
  let tab = "prompt";
  wrap.className = "prompt-detail-shell";
  wrap.innerHTML = `
    <div class="prompt-detail-tabs" role="tablist">
      <button class="drawer-tab is-active" type="button" data-prompt-tab="prompt">Prompt</button>
      <button class="drawer-tab" type="button" data-prompt-tab="sources">Sources</button>
      <button class="drawer-tab" type="button" data-prompt-tab="json">JSON</button>
    </div>
    <div class="prompt-detail-body"></div>
  `;
  const body = wrap.querySelector(".prompt-detail-body");
  const render = () => {
    wrap.querySelectorAll("[data-prompt-tab]").forEach((button) => {
      button.classList.toggle("is-active", button.dataset.promptTab === tab);
    });
    if (tab === "json") {
      body.replaceChildren(renderJsonBlock(item));
      return;
    }
    body.innerHTML = tab === "sources" ? renderPromptSources(item) : renderPromptAssembly(item);
  };
  wrap.addEventListener("click", (event) => {
    const button = event.target.closest("[data-prompt-tab]");
    if (!button) return;
    tab = button.dataset.promptTab;
    render();
  });
  render();
  return wrap;
}

function faissChunks() {
  const rows = [];
  (report.rag_blocks || []).forEach((block) => {
    (block.hits || []).forEach((hit) => {
      const score = Number(hit.score || 0);
      rows.push({
        key: `faiss-${rows.length}`,
        num: rows.length + 1,
        name: hit.table_name || hit.pattern_id || hit.vuln_class || hit.name || hit.source || "retrieved chunk",
        source: hit.source || block.title || "",
        score,
        pct: Math.max(0, Math.min(100, Math.round(score * 100))),
        tokens: hit.tokens || "unknown",
        text: hit.text || hit.description || "",
        raw: hit,
      });
    });
  });
  return rows.slice(0, 5);
}

function ragSources() {
  const out = {};
  (report.rag_blocks || []).forEach((block) => {
    Object.entries(block.sources || {}).forEach(([key, value]) => {
      out[key] = value || {};
    });
  });
  return out;
}

function renderRagSources() {
  const sources = ragSources();
  const rows = Object.entries(sources).map(([key, item]) => {
    const enabled = item.enabled === false ? "off" : "on";
    const error = item.error ? ` | ${item.error}` : "";
    const reason = item.fallback_reason ? ` | ${item.fallback_reason}` : "";
    const hits = item.hit_count ?? item.row_count ?? "unknown";
    const chars = item.context_chars ?? 0;
    return `
      <div class="drawer-card">
        <div class="drawer-card-title">${escapeHtml(key)}</div>
        <div class="drawer-kv">
          <div><span>Status</span>${escapeHtml(enabled)}${escapeHtml(reason)}${escapeHtml(error)}</div>
          <div><span>Hits</span>${escapeHtml(hits)}</div>
          <div><span>Context chars</span>${escapeHtml(chars)}</div>
          <div><span>Host</span>${escapeHtml(item.dsn_host || "n/a")}</div>
        </div>
      </div>
    `;
  }).join("");
  return rows || '<div class="drawer-card">No RAG source diagnostics in this report.</div>';
}

function renderFaissPanel() {
  const chunks = faissChunks();
  const cards = chunks.map((item) => `
    <button class="faiss-card" type="button" data-faiss-index="${item.num - 1}">
      <span class="drawer-index">${item.num}</span>
      <span class="faiss-main">
        <span class="faiss-head">
          <span class="faiss-name">${escapeHtml(item.name)}</span>
          <span class="faiss-score">Score ${escapeHtml(item.score.toFixed ? item.score.toFixed(3) : item.score)}<span class="faiss-bar"><span style="width:${item.pct}%"></span></span>${item.pct}%</span>
        </span>
        <span class="faiss-text">${escapeHtml(item.text).slice(0, 420)}</span>
      </span>
      <span class="faiss-tokens">Tokens: ${escapeHtml(item.tokens)}</span>
    </button>
  `).join("");
  return `
    <div class="drawer-meta"><span class="drawer-chip">Top-K: ${chunks.length}</span><span>Total Retrieved: <b>${chunks.length}</b></span></div>
    <div class="drawer-card-title">Context Sources</div>
    ${renderRagSources()}
    <div class="drawer-card-title">Retrieved Chunks</div>
    <div class="faiss-list">${cards || '<div class="drawer-card">No retrieved chunks in this report.</div>'}</div>
    <div class="drawer-card-title">Retrieval Metadata</div>
    <div class="drawer-kv">
      <div><span>Blocks</span>${escapeHtml((report.rag_blocks || []).length)}</div>
      <div><span>Search Type</span>RAG</div>
      <div><span>Best Score</span>${escapeHtml(chunks[0]?.score || "unknown")}</div>
      <div><span>Pipeline Step</span>Retrieval</div>
    </div>
  `;
}

function renderDetails(step) {
  const isFaiss = /faiss|retrieval|retrieve/i.test(step.key || step.label || "");
  if (isFaiss) return renderFaissPanel();
  const promptLinks = (Array.isArray(step.prompt_entries) ? step.prompt_entries : []).map((item, index) => ({
    key: item.key,
    title: item.title || "Prompt exchange",
    text: `${item.prompt_type || item.node || "prompt"} ${promptVersionLabel(item)} · ${item.prompt_source || "trace"}`,
    index,
  }));
  const key = step.drawer_key || `timeline-${step.key}`;
  const links = [
    ...promptLinks,
    { key, title: "Step event summary", text: "Open grouped event inputs, outputs, and durations." },
    { key: "report-json", title: "Full report JSON", text: "Open normalized report payload used by this HTML." },
  ].map((item, index) => `
    <button class="drawer-link" type="button" data-detail="${item.key}">
      <span class="drawer-index">${index + 1}</span>
      <span class="drawer-link-main">
        <span class="drawer-link-title">${escapeHtml(item.title)}</span>
        <span class="drawer-link-text">${escapeHtml(item.text)}</span>
      </span>
    </button>
  `).join("");
  return `<div class="drawer-meta"><span>${escapeHtml(step.label)}</span><span class="drawer-chip">${escapeHtml(step.duration)}</span></div><div class="drawer-list">${links}</div>`;
}

function openStep(key, tab) {
  const step = stepByKey(key);
  if (!step) return;
  activeStep = step.key;
  activeTab = tab || activeTab || "prompt";
  drawerTitle.textContent = step.label;
  drawerSubtitle.textContent = `${step.duration} | ${step.status || "step"}`;
  drawerTabs.innerHTML = `
    <button class="drawer-tab ${activeTab === "prompt" ? "is-active" : ""}" type="button" data-tab="prompt">Prompt Exchange</button>
    <button class="drawer-tab ${activeTab === "details" ? "is-active" : ""}" type="button" data-tab="details">Details</button>
  `;
  drawerBody.innerHTML = activeTab === "details" ? renderDetails(step) : renderMessages(step);
  drawer.classList.add("is-open");
  drawer.setAttribute("aria-hidden", "false");
  overlay.classList.add("is-open");
  setActiveStep(step.key);
  closeDetail();
}

function setActiveMode(block, mode) {
  block.querySelectorAll(".json-mode").forEach((button) => {
    button.classList.toggle("active", button.dataset.jsonMode === mode);
  });
}

function collapseToFirstLevel(editor) {
  window.setTimeout(() => {
    try {
      editor.collapse([], true);
      editor.expand([], (path) => path.length < 1);
    } catch (err) {
      console.warn("JSON collapse skipped:", err.message);
    }
  }, 0);
}

function jsonScalarHtml(value) {
  if (value === null) return '<span class="json-null">null</span>';
  if (typeof value === "string") return `<span class="json-string">${escapeHtml(JSON.stringify(value))}</span>`;
  if (typeof value === "number") return `<span class="json-number">${escapeHtml(value)}</span>`;
  if (typeof value === "boolean") return `<span class="json-bool">${escapeHtml(value)}</span>`;
  return `<span>${escapeHtml(value)}</span>`;
}

function jsonTreeHtml(value, depth = 0) {
  if (value === null || typeof value !== "object") return jsonScalarHtml(value);
  const isArray = Array.isArray(value);
  const entries = isArray ? value.map((item, index) => [String(index), item]) : Object.entries(value);
  const label = isArray ? `Array(${entries.length})` : `Object(${entries.length})`;
  if (!entries.length) return isArray ? "[]" : "{}";
  const open = depth < 2 ? " open" : "";
  const rows = entries.map(([key, child]) => {
    return `<li><span class="json-key">${escapeHtml(key)}</span>: ${jsonTreeHtml(child, depth + 1)}</li>`;
  }).join("");
  return `<details${open}><summary>${label}</summary><ul>${rows}</ul></details>`;
}

function renderJsonBlock(value) {
  const block = document.createElement("div");
  block.className = "json-block";
  block.dataset.jsonBlock = "1";
  block.innerHTML = `
    <div class="json-tools" role="group" aria-label="JSON view">
      <span class="json-title">JSON</span>
      <button type="button" class="json-mode active" data-json-mode="tree">Tree</button>
      <button type="button" class="json-mode" data-json-mode="text">Text</button>
    </div>
    <div class="json-viewer"></div>
  `;
  const viewer = block.querySelector(".json-viewer");
  viewer.__jsonData = value;
  bindJsonBlock(block);
  jsonReady.finally(() => initViewer(viewer));
  return block;
}

function fallbackJson(container, data, mode = "tree") {
  const note = jsonLoadError ? `JSON renderer is unavailable: ${jsonLoadError.message}\n\n` : "";
  if (mode === "text") {
    const pre = document.createElement("pre");
    pre.className = "json-fallback";
    pre.setAttribute("aria-readonly", "true");
    pre.textContent = note + jsonText(data);
    container.replaceChildren(pre);
  } else {
    const tree = document.createElement("div");
    tree.className = "json-tree scrollbar-thin";
    tree.setAttribute("aria-readonly", "true");
    tree.innerHTML = jsonTreeHtml(data);
    container.replaceChildren(tree);
  }
  container.dataset.fallback = "1";
  container.dataset.rendered = "1";
}

function initViewer(container) {
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
    collapseToFirstLevel(editor);
    return editor;
  } catch (err) {
    jsonLoadError = err;
    fallbackJson(container, data);
    return null;
  }
}

function switchMode(block, mode) {
  const container = block.querySelector(".json-viewer");
  const editor = initViewer(container);
  if (editor) {
    editor.updateProps({
      mode,
      readOnly: true,
      mainMenuBar: false,
      navigationBar: false,
      statusBar: false,
      indentation: 2,
    });
    if (mode === "tree") collapseToFirstLevel(editor);
  } else {
    fallbackJson(container, container.__jsonData, mode);
  }
  setActiveMode(block, mode);
}

function bindJsonBlock(block) {
  block.querySelectorAll(".json-mode").forEach((button) => {
    button.addEventListener("click", () => switchMode(block, button.dataset.jsonMode));
  });
}

const riskCatalog = {
  DIRECT_SENSITIVE: {
    title: "Прямой вывод чувствительных данных",
    meaning: "Запрос показывает персональные или служебно чувствительные поля напрямую: телефон, email, ФИО, внутренние идентификаторы или похожие данные.",
    impact: "Такой отчёт может нарушить внутреннюю политику доступа и раскрыть данные сотрудникам, которым нужна только агрегированная картина.",
    action: "Заменить прямые поля на агрегаты, маскированные значения или безопасные признаки. Проверить, кому и зачем нужен каждый столбец."
  },
  MASKING_REQUIRED: {
    title: "Нужно маскирование",
    meaning: "Данные можно использовать в отчёте, но показывать их нужно в защищённом виде: частично скрыть, сгруппировать или заменить техническим маркером.",
    impact: "Без маскирования отчёт выглядит рабочим, но создаёт риск утечки при выгрузке, пересылке или демонстрации.",
    action: "Добавить маскирующие функции, исключить лишние поля из SELECT и оставить только бизнес-необходимый минимум."
  },
  WRONG_JOIN_PATH: {
    title: "Сомнительная связь между таблицами",
    meaning: "Пайплайн выбрал путь соединения таблиц, который не подтверждён схемой или бизнес-правилами.",
    impact: "Отчёт может смешать данные разных сущностей: цифры выглядят правдоподобно, но отвечают не на тот бизнес-вопрос.",
    action: "Проверить join по approved_joins/schema overlay и заменить связь на разрешённый путь."
  },
  BROKEN_SQL: {
    title: "SQL технически сломан",
    meaning: "Запрос не выполняется или не проходит базовую проверку синтаксиса.",
    impact: "Пользователь получает не отчёт, а ошибку выполнения; дальнейшая бизнес-проверка невозможна.",
    action: "Исправить синтаксис, параметры и совместимость с целевой SQL-средой перед одобрением."
  },
  SYNTAX_BROKEN: {
    title: "Ошибка синтаксиса",
    meaning: "В тексте SQL есть конструкция, которую база не сможет разобрать.",
    impact: "Даже правильная идея отчёта не попадёт в эксплуатацию, потому что запрос упадёт при запуске.",
    action: "Проверить плейсхолдеры, кавычки, алиасы, GROUP BY и LIMIT/OFFSET."
  },
  MISSING_REQUIRED_FILTER: {
    title: "Потерян обязательный фильтр",
    meaning: "В задаче был важный ограничитель: период, клиент, tenant, статус или другой критерий, но SQL его не сохранил.",
    impact: "Отчёт может вернуть слишком широкий набор данных и исказить выводы или раскрыть лишнюю информацию.",
    action: "Вернуть фильтр из пользовательского запроса и добавить guard на потерю ключевых predicates при retry."
  },
  HALLUCINATED_COLUMN: {
    title: "Несуществующее поле",
    meaning: "Модель сослалась на колонку, которой нет в подтверждённой схеме.",
    impact: "Запрос не выполнится или будет чиниться вручную, а доверие к автогенерации падает.",
    action: "Опора на schema overlay/RAG должна быть обязательной для выбора колонок."
  },
  SELECT_STAR: {
    title: "Слишком широкий SELECT",
    meaning: "Запрос пытается взять все поля, вместо осознанного списка бизнес-нужных колонок.",
    impact: "В отчёт могут случайно попасть чувствительные или технические поля.",
    action: "Заменить SELECT * на явный перечень безопасных столбцов."
  },
  NO_LIMIT: {
    title: "Нет ограничения объёма",
    meaning: "Запрос может вернуть слишком много строк без LIMIT, периода или другого ограничителя.",
    impact: "Это повышает нагрузку и усложняет ручную проверку результата.",
    action: "Добавить разумный LIMIT или обязательные фильтры объёма."
  },
  EXCESSIVE_SCOPE: {
    title: "Слишком широкий охват",
    meaning: "Запрос берёт больше объектов или периодов, чем требовалось по задаче.",
    impact: "Метрика может стать нерелевантной: бизнес видит общий шум вместо нужного среза.",
    action: "Сузить область запроса до точного сегмента из формулировки пользователя."
  }
};

function riskInfo(label) {
  const key = String(label || "").trim().toUpperCase();
  const spec = sqlEventSpec(key);
  if (spec) {
    return {
      label: spec.identifier || key,
      title: spec.business_meaning || key,
      meaning: spec.business_meaning || "Описание риска пока не задано.",
      impact: spec.trigger || "Технический триггер не задан.",
      action: "Проверить evidence и SQL-кандидат, затем подтвердить или исправить запрос.",
      trigger: spec.trigger || "Технический триггер не задан.",
    };
  }
  const fallback = riskCatalog[key];
  if (fallback) return { label: key, ...fallback, trigger: fallback.action };
  return {
    label: key || "UNKNOWN_RISK",
    title: String(label || "Unknown risk"),
    meaning: "Обнаружен риск, для которого пока нет отдельного бизнес-описания.",
    impact: "Нужно открыть raw JSON и посмотреть технические признаки, по которым guard поднял этот label.",
    action: "Добавить описание риска в каталог и проверить SQL вручную."
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
          <p>${escapeHtml(info.trigger || info.impact)}</p>
        </article>
      </div>
    </section>
  `;
  modal.classList.add("is-open");
  modal.querySelectorAll("[data-risk-close]").forEach((node) => {
    node.addEventListener("click", () => modal.classList.remove("is-open"));
  });
}

function renderRiskEvidenceNode(value) {
  const root = document.createElement("div");
  root.className = "risk-evidence";
  const items = Array.isArray(value?.risk_items) ? value.risk_items : [];
  const score = value?.score ?? "n/a";
  const business = document.createElement("div");
  business.className = "risk-evidence__tab-body";
  business.innerHTML = `
    <div class="risk-summary-card">
      <div>
        <b>Бизнес-оценка риска</b>
        <p>Сначала показаны понятные причины риска. Raw JSON оставлен отдельно для отладки и сверки с техническими признаками.</p>
      </div>
      <span>${escapeHtml(score)}/10</span>
    </div>
    <div class="risk-card-list">
      ${items.length ? items.map((item) => {
        const info = riskInfo(item.label);
        const maxRisk = Number(item.max_risk);
        const tone = Number.isFinite(maxRisk) && maxRisk >= 6 ? "risk-card--bad" : "risk-card--warn";
        return `<article class="risk-card ${tone}">
          <div class="risk-card__head">
            <div><strong>${escapeHtml(info.title)}</strong><code>${escapeHtml(item.label || "")}</code></div>
            <span>${escapeHtml(item.count || 0)} found · risk ${escapeHtml(item.max_risk ?? "n/a")}</span>
          </div>
          <p><b>Что это значит:</b> ${escapeHtml(info.meaning)}</p>
          <p><b>Почему это важно:</b> ${escapeHtml(info.impact)}</p>
          <p><b>Что проверить:</b> ${escapeHtml(info.action)}</p>
        </article>`;
      }).join("") : `<div class="drawer-card">Рисковые признаки не найдены.</div>`}
    </div>`;
  const raw = document.createElement("div");
  raw.className = "risk-evidence__tab-body hidden";
  raw.appendChild(renderJsonBlock(value));
  const tabs = document.createElement("div");
  tabs.className = "prompt-detail-tabs";
  tabs.innerHTML = `
    <button class="drawer-tab is-active" type="button" data-risk-tab="business">Бизнес-объяснение</button>
    <button class="drawer-tab" type="button" data-risk-tab="json">Raw JSON</button>`;
  tabs.querySelectorAll("[data-risk-tab]").forEach((button) => {
    button.addEventListener("click", () => {
      tabs.querySelectorAll("[data-risk-tab]").forEach((item) => item.classList.toggle("is-active", item === button));
      business.classList.toggle("hidden", button.dataset.riskTab !== "business");
      raw.classList.toggle("hidden", button.dataset.riskTab !== "json");
    });
  });
  root.appendChild(tabs);
  root.appendChild(business);
  root.appendChild(raw);
  return root;
}

function activeCandidateIndex(detail) {
  const candidates = Array.isArray(detail && detail.candidates) ? detail.candidates : [];
  const selected = candidates.find((item) => item && item.selected);
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

function winnerCheckHtml(title) {
  return '<span class="winner-check" title="' + escapeHtml(title || "Selected candidate") + '">' + checkSvg + "</span>";
}

function winnerBadgeHtml(item) {
  return item && item.selected
    ? '<span class="winner-badge">' + checkSvg + " selected</span>"
    : "";
}

function hasSelectedCandidate(detail) {
  const candidates = Array.isArray(detail && detail.candidates) ? detail.candidates : [];
  return candidates.some((item) => item && item.selected);
}

function roundPromptPartHtml(index, label, source, text, tone) {
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

function renderCandidatePrompt(item) {
  if (!item) return '<div class="prompt-drawer__empty">No candidate selected.</div>';
  const meta = item.prompt_system_meta || {};
  const chips = `
    <div class="prompt-meta-strip">
      <span>Candidate ${escapeHtml(item.index)}</span>
      ${winnerBadgeHtml(item)}
      <span>${escapeHtml(tempLabel(item))}</span>
      <span>${escapeHtml(item.model || item.backend || "model n/a")}</span>
      ${meta.prompt_id ? '<span>' + escapeHtml(meta.prompt_id) + '</span>' : ""}
      ${meta.prompt_version != null ? '<span>v' + escapeHtml(meta.prompt_version) + '</span>' : ""}
      ${meta.prompt_sha256 ? '<span>' + escapeHtml(shortSha(meta.prompt_sha256)) + '</span>' : ""}
    </div>
  `;
  const system = item.prompt_system
    ? roundPromptPartHtml("S", "System prompt", meta.prompt_source || "trace", item.prompt_system, "system")
    : "";
  const user = item.prompt_user
    ? roundPromptPartHtml("U", "User prompt", "assembled for this candidate", item.prompt_user, "task")
    : "";
  return chips + (system || user ? system + user : '<div class="prompt-drawer__empty">Prompt text was not recorded for this candidate.</div>');
}

function renderCandidateCard(item) {
  return `
    <article class="prompt-part">
      <header class="prompt-part__head">
        <span class="prompt-part__index">${escapeHtml(item.index)}</span>
        <span class="prompt-part__label">${item.selected ? winnerBadgeHtml(item) : "candidate"}</span>
        <span class="prompt-part__source">${escapeHtml(candidateMetaLine(item))}</span>
      </header>
      <pre class="prompt-part__text mono">${escapeHtml(item.sql || item.response_raw || "")}</pre>
    </article>
  `;
}

function renderGenerateRoundHtml(detail, tab, activeIndex) {
  const candidates = Array.isArray(detail.candidates) ? detail.candidates : [];
  const active = candidates.find((item) => Number(item.index) === activeIndex) || candidates[0] || {};
  const list = '<div class="prompt-trace-list">' + candidates.map((item) => `
    <button class="prompt-trace-item ${Number(item.index) === activeIndex ? "is-active" : ""}" type="button" data-candidate-index="${escapeHtml(item.index)}">
      <span class="prompt-trace-item__title">Candidate ${escapeHtml(item.index)} ${winnerBadgeHtml(item)}</span>
      <span class="prompt-trace-item__meta">${escapeHtml(candidateMetaLine(item))}</span>
    </button>
  `).join("") + "</div>";
  const body = tab === "sources"
    ? candidates.map(renderCandidateCard).join("") || '<div class="prompt-drawer__empty">No candidates recorded.</div>'
    : renderCandidatePrompt(active);
  return list + '<div class="prompt-trace-detail">' + body + "</div>";
}

function renderAuditRoundHtml(detail, tab) {
  const findings = Array.isArray(detail.merged_findings) ? detail.merged_findings : [];
  const body = tab === "sources"
    ? findings.map((item, index) => `
      <article class="prompt-hit">
        <div class="prompt-hit__head"><b>${index + 1}. ${escapeHtml(riskLabel(item) || "finding")}</b><span>${escapeHtml(item.risk_score || item.severity || "")}</span></div>
        <div class="prompt-hit__sub">${escapeHtml(item.layer || item.detector || "")}</div>
        ${renderRiskBadges([item])}
        <div class="prompt-hit__business">${escapeHtml(riskInfo(riskLabel(item)).meaning)}</div>
        <div class="prompt-hit__text">${escapeHtml(item.description || item.evidence_span || "")}</div>
      </article>
    `).join("") || '<div class="prompt-drawer__empty">No findings recorded.</div>'
    : renderRiskBadges(findings) + '<pre class="prompt-json mono">' + escapeHtml(detail.prompt_user || "") + "</pre>";
  return '<div class="prompt-trace-list"><div class="prompt-trace-item is-active"><span class="prompt-trace-item__title">Audit call</span><span class="prompt-trace-item__meta">' + escapeHtml(detail.approved ? "approved" : "blocked") + " · risk " + escapeHtml(detail.overall_risk_score || "0") + '</span></div></div><div class="prompt-trace-detail">' + body + "</div>";
}

function renderRoundDetailNode(detail) {
  const wrap = document.createElement("div");
  const kind = detail.kind || "round";
  let tab = "prompt";
  let activeIndex = activeCandidateIndex(detail);
  const tabWinner = kind === "generate" && hasSelectedCandidate(detail) ? winnerCheckHtml("Selected candidate in this round") : "";
  wrap.className = "round-detail-shell";
  wrap.innerHTML = `
    <div class="prompt-detail-tabs" role="tablist">
      <button class="drawer-tab is-active" type="button" data-round-tab="prompt">Prompt${tabWinner}</button>
      <button class="drawer-tab" type="button" data-round-tab="sources">${kind === "audit" ? "Findings" : "Candidates" + tabWinner}</button>
      <button class="drawer-tab" type="button" data-round-tab="json">JSON</button>
    </div>
    <div class="round-detail-body"></div>
  `;
  const body = wrap.querySelector(".round-detail-body");
  const bindBody = () => {
    body.querySelectorAll("[data-candidate-index]").forEach((button) => {
      button.addEventListener("click", () => {
        activeIndex = Number(button.dataset.candidateIndex || 0);
        render();
      });
    });
    body.querySelectorAll("[data-risk-label]").forEach((button) => {
      button.addEventListener("click", (event) => {
        event.stopPropagation();
        openRiskModal(button.dataset.riskLabel);
      });
    });
  };
  const render = () => {
    wrap.querySelectorAll("[data-round-tab]").forEach((button) => {
      button.classList.toggle("is-active", button.dataset.roundTab === tab);
    });
    if (tab === "json") {
      body.replaceChildren(renderJsonBlock(detail));
      return;
    }
    body.innerHTML = kind === "audit"
      ? renderAuditRoundHtml(detail, tab)
      : renderGenerateRoundHtml(detail, tab, activeIndex);
    bindBody();
  };
  wrap.addEventListener("click", (event) => {
    const button = event.target.closest("[data-round-tab]");
    if (!button) return;
    tab = button.dataset.roundTab;
    render();
  });
  render();
  return wrap;
}

function openDetail(key, titleOverride, valueOverride) {
  const item = drawerData[key] || { title: titleOverride || "Detail", kind: "json", value: valueOverride };
  detailTitle.textContent = titleOverride || item.title || "Detail";
  detailSubtitle.textContent = item.subtitle || key;
  detailBody.replaceChildren();
  if (item.kind === "text") {
    const card = document.createElement("div");
    card.className = "drawer-card";
    card.innerHTML = `<pre class="drawer-pre mono">${escapeHtml(item.value || "")}</pre>`;
    detailBody.appendChild(card);
  } else if (item.kind === "html") {
    // Phase 0.7-v2 — pre-rendered HTML контент из Python.
    // Эскейп уже сделан в test_report.py, здесь только вставка.
    const wrap = document.createElement("div");
    wrap.innerHTML = item.value || "";
    while (wrap.firstChild) detailBody.appendChild(wrap.firstChild);
  } else if (item.kind === "prompt") {
    detailBody.appendChild(renderPromptDetailNode(item.value || {}));
  } else if (item.kind === "round" || ["generate", "audit"].includes(String((item.value || {}).kind || ""))) {
    detailBody.appendChild(renderRoundDetailNode(item.value || {}));
  } else if (key === "metric-risk" || item.subtitle === "metric-risk") {
    detailBody.appendChild(renderRiskEvidenceNode(item.value || {}));
  } else {
    detailBody.appendChild(renderJsonBlock(item.value));
  }
  detailDrawer.classList.add("is-open");
  detailDrawer.setAttribute("aria-hidden", "false");
  drawer.classList.add("is-shifted");
  overlay.classList.add("is-open");
}

function openFaissDetail(index) {
  const item = faissChunks()[index];
  if (!item) return;
  detailTitle.textContent = item.name;
  detailSubtitle.textContent = `Score ${item.score} | ${item.pct}% | Tokens ${item.tokens}`;
  detailBody.innerHTML = `
    <div class="detail-score"><b>Score ${escapeHtml(item.score)}</b><div class="drawer-score"><span style="width:${item.pct}%"></span></div><span>${item.pct}%</span><span>Tokens: ${escapeHtml(item.tokens)}</span></div>
    <div class="drawer-card"><div class="drawer-card-title">${escapeHtml(item.source)}</div><div class="drawer-link-text">${escapeHtml(item.text)}</div></div>
  `;
  detailBody.appendChild(renderJsonBlock(item.raw));
  detailDrawer.classList.add("is-open");
  detailDrawer.setAttribute("aria-hidden", "false");
  drawer.classList.add("is-shifted");
  overlay.classList.add("is-open");
}

function closeDetail() {
  detailDrawer.classList.remove("is-open");
  drawer.classList.remove("is-shifted");
  detailDrawer.setAttribute("aria-hidden", "true");
}

function closeAll() {
  drawer.classList.remove("is-open", "is-shifted");
  detailDrawer.classList.remove("is-open");
  overlay.classList.remove("is-open");
  drawer.setAttribute("aria-hidden", "true");
  detailDrawer.setAttribute("aria-hidden", "true");
}

function mountInlineJson() {
  document.querySelectorAll("[data-json-inline]").forEach((target) => {
    const key = target.dataset.jsonInline;
    const value = key === "ast" ? report.ast_data : report.explain_json;
    target.replaceChildren(renderJsonBlock(value || { status: "unavailable" }));
  });
}

function setEventFilter(level) {
  document.querySelectorAll("[data-event-filter]").forEach((button) => {
    button.classList.toggle("active", button.dataset.eventFilter === level);
  });
  document.querySelectorAll(".event-row").forEach((row) => {
    row.hidden = level !== "all" && row.dataset.level !== level;
  });
}

async function copySql(button) {
  const text = report.final_sql || "";
  try {
    await navigator.clipboard.writeText(text);
  } catch (err) {
    const area = document.createElement("textarea");
    area.value = text;
    document.body.appendChild(area);
    area.select();
    document.execCommand("copy");
    area.remove();
  }
  const old = button.textContent;
  button.lastChild.textContent = "Copied";
  setTimeout(() => {
    button.lastChild.textContent = old.includes("Copy") ? "Copy" : old;
  }, 1000);
}

function exportReport() {
  const html = "<!doctype html>\n" + document.documentElement.outerHTML;
  const blob = new Blob([html], { type: "text/html;charset=utf-8" });
  const link = document.createElement("a");
  link.href = URL.createObjectURL(blob);
  link.download = `${report.report_id || "telegram-report"}.html`;
  document.body.appendChild(link);
  link.click();
  URL.revokeObjectURL(link.href);
  link.remove();
}

document.querySelectorAll(".timeline-step").forEach((button) => {
  button.addEventListener("click", (event) => {
    const node = button.closest(".timeline-node.has-rounds");
    if (node && (event.target.closest(".timeline-chevron") || event.altKey)) {
      node.classList.toggle("is-expanded");
      return;
    }
    openStep(button.dataset.step);
  });
});
document.querySelectorAll(".timeline-round-toggle").forEach((button) => {
  button.addEventListener("click", (event) => {
    event.stopPropagation();
    button.closest(".timeline-node")?.classList.toggle("is-expanded");
  });
});
renderSqlBoxes();
mountInlineJson();
document.querySelectorAll("[data-step-key]").forEach((button) => {
  button.addEventListener("click", () => openStep(button.dataset.stepKey));
});
drawerTabs.addEventListener("click", (event) => {
  const tab = event.target.closest("[data-tab]");
  if (!tab) return;
  activeTab = tab.dataset.tab;
  openStep(activeStep, activeTab);
});
document.addEventListener("click", (event) => {
  const risk = event.target.closest("[data-risk-label]");
  if (risk) {
    event.stopPropagation();
    openRiskModal(risk.dataset.riskLabel);
    return;
  }
  const faiss = event.target.closest("[data-faiss-index]");
  if (faiss) {
    openFaissDetail(Number(faiss.dataset.faissIndex));
    return;
  }
  const detail = event.target.closest("[data-detail]");
  if (detail) {
    openDetail(detail.dataset.detail);
    return;
  }
});
document.querySelectorAll("[data-event-filter]").forEach((button) => {
  button.addEventListener("click", () => setEventFilter(button.dataset.eventFilter));
});
document.querySelector("[data-copy-sql]").addEventListener("click", (event) => copySql(event.currentTarget));
document.querySelector("[data-export-report]").addEventListener("click", exportReport);
document.querySelector("[data-open-json]").addEventListener("click", () => openDetail("report-json"));
document.getElementById("drawerClose").addEventListener("click", closeAll);
document.getElementById("detailClose").addEventListener("click", closeDetail);
overlay.addEventListener("click", closeAll);
window.addEventListener("keydown", (event) => {
  if (event.key === "Escape") {
    document.getElementById("riskInfoModal")?.classList.remove("is-open");
    closeAll();
  }
}, true);
