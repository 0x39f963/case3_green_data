/* Generated at: 2026-05-21 22:28:00 MSK */
(function(){
  const meta = (name) => document.querySelector(`meta[name="${name}"]`)?.content || "";
  const apiBase = meta("api-base-url") || "http://localhost:18081";
  const token = meta("api-token");
  const headers = token ? {Authorization: "Bearer " + token} : {};
  const qs = new URLSearchParams(location.search);
  let compareData = null;
  let compareRunIds = [];
  let uiConfig = null;

  async function api(path, opts){
    const res = await fetch(apiBase + path, Object.assign({headers}, opts || {}));
    if(!res.ok) throw new Error(await res.text());
    return res.json();
  }

  function esc(v){return String(v ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;","'":"&#39;"}[c]));}

  async function loadUiConfig(){
    if(uiConfig) return uiConfig;
    const res = await fetch("/web/api/config");
    if(!res.ok) throw new Error(await res.text());
    uiConfig = await res.json();
    return uiConfig;
  }

  async function loadDatasets(){
    const select = document.getElementById("batchDataset");
    if(!select || select.tagName !== "SELECT") return;
    const data = await api("/v1/benchmarks/datasets");
    const items = data.items || [];
    select.innerHTML = items.map(item => {
      const selected = item.dataset_id === "golden_v1_0" ? " selected" : "";
      return `<option value="${esc(item.dataset_id)}" data-path="${esc(item.dataset_path || "")}" data-cases="${esc(item.case_count || "")}" data-kind="${esc(item.kind || "")}"${selected}>${esc(item.label || item.dataset_id)}</option>`;
    }).join("");
    if(!select.value && select.options.length) select.options[0].selected = true;
    updateDatasetHint();
  }

  function updateDatasetHint(){
    const select = document.getElementById("batchDataset");
    const hint = document.getElementById("batchDatasetHint");
    if(!select || !hint) return;
    const option = select.selectedOptions?.[0];
    const cases = option?.dataset?.cases || "unknown";
    const path = option?.dataset?.path || "";
    hint.textContent = `${cases} cases · ${path || "dataset path unavailable"}`;
  }

  function populateBatchSelectors(config){
    const modelSelect = document.getElementById("batchModels");
    if(modelSelect && modelSelect.tagName === "SELECT"){
      const models = config.models || [];
      const defaultKey = config.default_model_key || "local-qwen3-5-9b";
      modelSelect.innerHTML = models.map(model => {
        const selected = model.key === defaultKey ? " selected" : "";
        const label = model.label || model.key;
        return `<option value="${esc(model.key)}"${selected}>${esc(label)}</option>`;
      }).join("");
      if(!modelSelect.selectedOptions.length && modelSelect.options.length){
        modelSelect.options[0].selected = true;
      }
      modelSelect.onchange = () => renderOpenRouterProviders(config);
      renderOpenRouterProviders(config);
    }
    const judgeSelect = document.getElementById("batchJudgePreset");
    if(judgeSelect && judgeSelect.tagName === "SELECT"){
      const judgeBackends = config.judge_backends || [];
      judgeSelect.innerHTML = judgeBackends.map(item => {
        const selected = item.backend === "off" ? " selected" : (item.default ? " selected" : "");
        const label = item.label || item.key;
        return `<option value="${esc(item.key)}" data-backend="${esc(item.backend || "")}" data-model="${esc(item.provider_model || "")}" data-codex-reasoning-effort="${esc(item.codex_reasoning_effort || "")}"${selected}>${esc(label)}</option>`;
      }).join("");
      if(!judgeSelect.selectedOptions.length && judgeSelect.options.length){
        judgeSelect.options[0].selected = true;
      }
    }
    const promptCheckSelect = document.getElementById("batchPromptCheckPreset");
    if(promptCheckSelect && promptCheckSelect.tagName === "SELECT"){
      const items = config.models || [];
      const saved = localStorage.getItem("audits.newBatch.promptCheck.preset") || promptCheckDefaultModelKey(config);
      promptCheckSelect.innerHTML = items.map(item => {
        const selected = item.key === saved ? " selected" : "";
        const label = item.label || item.key;
        const unavailable = item.available_by_config === false ? " (unavailable)" : "";
        const disabled = item.available_by_config === false ? " disabled" : "";
        return `<option value="${esc(item.key)}" data-backend="${esc(item.backend || "")}" data-model="${esc(item.provider_model || "")}" data-providers="${esc(JSON.stringify(item.openrouter_providers || []))}"${selected}${disabled}>${esc(label + unavailable)}</option>`;
      }).join("");
      if(!promptCheckSelect.selectedOptions.length && promptCheckSelect.options.length){
        promptCheckSelect.options[0].selected = true;
      }
      promptCheckSelect.onchange = () => {
        localStorage.setItem("audits.newBatch.promptCheck.preset", promptCheckSelect.value || "");
        renderPromptCheckOpenRouterProvider();
      };
      renderPromptCheckOpenRouterProvider();
    }
    const promptCheckEnabled = document.getElementById("batchPromptCheckEnabled");
    if(promptCheckEnabled){
      const savedEnabled = localStorage.getItem("audits.newBatch.promptCheck.enabled");
      if(savedEnabled !== null) promptCheckEnabled.checked = savedEnabled !== "0";
      promptCheckEnabled.onchange = () => {
        localStorage.setItem("audits.newBatch.promptCheck.enabled", promptCheckEnabled.checked ? "1" : "0");
        renderPromptCheckOpenRouterProvider();
      };
      renderPromptCheckOpenRouterProvider();
    }
    const analysisSelect = document.getElementById("batchAnalysisPreset");
    if(analysisSelect && analysisSelect.tagName === "SELECT"){
      const judgeBackends = (config.judge_backends || []).filter(item => item.backend && item.backend !== "off");
      analysisSelect.innerHTML = judgeBackends.map(item => {
        const label = item.label || item.key;
        const selected = item.key === "codex-spark-medium" ? " selected" : (item.default ? " selected" : "");
        return `<option value="${esc(item.key)}" data-backend="${esc(item.backend || "")}" data-model="${esc(item.provider_model || "")}" data-codex-reasoning-effort="${esc(item.codex_reasoning_effort || "")}"${selected}>${esc(label)}</option>`;
      }).join("");
      if(!analysisSelect.selectedOptions.length && analysisSelect.options.length){
        analysisSelect.options[0].selected = true;
      }
    }
  }

  function promptCheckDefaultModelKey(config){
    const models = config.models || [];
    const promptItems = config.prompt_check_backends || [];
    const defaultKey = config.default_prompt_check_backend || "";
    const promptItem = promptItems.find(item => item.key === defaultKey || item.default);
    const match = promptItem ? models.find(item => item.backend === promptItem.backend && item.provider_model === promptItem.provider_model) : null;
    const geminiLite = models.find(item => item.provider_model === "google/gemini-3.1-flash-lite");
    return (match && match.key) || (geminiLite && geminiLite.key) || config.default_model_key || "";
  }

  function configModelMap(config){
    const out = new Map();
    for(const model of config.models || []) out.set(model.key, model);
    return out;
  }

  function providerMetric(value, label, suffix){
    let raw = value;
    if(raw && typeof raw === "object") raw = raw.p50 ?? raw.median ?? raw.avg ?? raw.mean ?? raw.value;
    if(raw === null || raw === undefined || raw === "") return label ? `${label} n/a` : "n/a";
    const n = Number(raw);
    if(!Number.isFinite(n)) return "n/a";
    return `${label ? label + " " : ""}${n.toFixed(n >= 10 ? 0 : 2)}${suffix || ""}`;
  }

  function providerOptionLabel(provider){
    const rec = provider.recommended ? "recommended · " : "";
    const pIn = providerMetric(provider.price_per_million_prompt, "", "");
    const pOut = providerMetric(provider.price_per_million_completion, "", "");
    const latency = providerMetric(provider.latency_last_30m, "latency", " ms");
    const tps = providerMetric(provider.throughput_last_30m, "speed", " tok/s");
    const uptime = providerMetric(provider.uptime_last_30m, "uptime", "%");
    return `${provider.provider_name} · ${rec}$${pIn}/M in · $${pOut}/M out · ${latency} · ${tps} · ${uptime}`;
  }

  function providerHint(provider){
    if(!provider) return "";
    const context = provider.context_length ? `context ${provider.context_length}` : "context n/a";
    const quant = provider.quantization ? ` · ${provider.quantization}` : "";
    return `${context}${quant} · source: OpenRouter endpoints API`;
  }

  function renderOpenRouterProviders(config){
    const panel = document.getElementById("openrouterProviderPanel");
    const modelSelect = document.getElementById("batchModels");
    if(!panel || !modelSelect || modelSelect.tagName !== "SELECT") return;
    const byKey = configModelMap(config);
    const selected = Array.from(modelSelect.selectedOptions)
      .map(option => byKey.get(option.value))
      .filter(model => model && model.backend === "openrouter");
    if(!selected.length){
      panel.hidden = true;
      panel.innerHTML = "";
      return;
    }
    panel.hidden = false;
    panel.innerHTML = `<div class="provider-panel__title">OpenRouter providers</div>` + selected.map(model => {
      const providers = model.openrouter_providers || [];
      if(!providers.length){
        return `<div class="provider-card"><b>${esc(model.label || model.key)}</b><span>No provider data from OpenRouter right now. The run will use OpenRouter routing.</span></div>`;
      }
      const firstRecommended = providers.find(item => item.recommended) || providers[0];
      return `<div class="provider-card">
        <label>${esc(model.label || model.key)}
          <select class="batchOpenRouterProvider" data-model-key="${esc(model.key)}" data-generator-key="${esc(model.llm_generator_model || "")}">
            <option value="">Auto (OpenRouter default routing)</option>
            ${providers.map(provider => `<option value="${esc(provider.provider_name)}"${provider.provider_name === firstRecommended.provider_name ? " selected" : ""}>${esc(providerOptionLabel(provider))}</option>`).join("")}
          </select>
        </label>
        <div class="provider-meta">${esc(providerHint(firstRecommended))}</div>
      </div>`;
    }).join("");
  }

  function promptCheckProviderList(){
    const select = document.getElementById("batchPromptCheckPreset");
    const option = select?.selectedOptions?.[0];
    if(!option || option.dataset.backend !== "openrouter") return [];
    try{
      const value = JSON.parse(option.dataset.providers || "[]");
      return Array.isArray(value) ? value : [];
    }catch(_error){
      return [];
    }
  }

  function renderPromptCheckOpenRouterProvider(){
    const panel = document.getElementById("promptCheckOpenrouterProviderPanel");
    const enabled = document.getElementById("batchPromptCheckEnabled")?.checked !== false;
    const select = document.getElementById("batchPromptCheckPreset");
    const option = select?.selectedOptions?.[0];
    if(!panel || !enabled || !option || option.dataset.backend !== "openrouter"){
      if(panel){
        panel.hidden = true;
        panel.innerHTML = "";
      }
      return;
    }
    const providers = promptCheckProviderList();
    panel.hidden = false;
    if(!providers.length){
      panel.innerHTML = `<div class="provider-card"><b>Prompt-check OpenRouter provider</b><span>No provider data from OpenRouter right now. The run will use OpenRouter routing.</span></div>`;
      return;
    }
    const firstRecommended = providers.find(item => item.recommended) || providers[0];
    panel.innerHTML = `<div class="provider-panel__title">Prompt-check OpenRouter provider</div>
      <div class="provider-card">
        <label>${esc(option.textContent || "Prompt-check")}
          <select class="batchPromptCheckOpenRouterProvider">
            <option value="">Auto (OpenRouter default routing)</option>
            ${providers.map(provider => `<option value="${esc(provider.provider_name)}"${provider.provider_name === firstRecommended.provider_name ? " selected" : ""}>${esc(providerOptionLabel(provider))}</option>`).join("")}
          </select>
        </label>
        <div class="provider-meta">${esc(providerHint(firstRecommended))}</div>
      </div>`;
  }

  async function loadRuns(){
    const body = document.querySelector("#runsTable tbody");
    if(!body) return;
    const data = await api("/v1/benchmarks/runs?limit=200");
    document.getElementById("runCount").textContent = `${data.total || data.items.length} runs`;
    body.innerHTML = (data.items || []).map(row => {
      const id = row.benchmark_run_id;
      const pipelineDone = Number(row.pipeline_completed_cases || row.completed_cases || 0);
      const totalCases = Number(row.total_cases || pipelineDone || 0);
      const posthocTotal = pipelineDone || totalCases;
      return `<tr>
        <td><input type="checkbox" value="${esc(id)}"></td>
        <td><a class="mono run-link" href="/audits/runs/${encodeURIComponent(id)}" title="${esc(id)}">${esc(shortRunId(id))}</a><small class="full-id mono">${esc(id)}</small></td>
        <td><span class="compact-main">${esc(shortDataset(row.dataset_id))}</span><small>${esc(row.dataset_version)}</small></td>
        <td class="models-cell" title="${esc((row.model_matrix || []).join(", "))}">${esc((row.model_matrix || []).join(", "))}</td>
        <td>${esc(row.status)}</td>
        <td>${esc(row.isolation_mode || "production")}</td>
        <td>${renderRunProgress({
          pipelineDone,
          totalCases,
          judgeDone: Number(row.judge_completed_cases || 0),
          oracleDone: Number(row.oracle_completed_cases || 0),
          analysisDone: Number(row.analysis_completed_cases || 0),
          posthocTotal,
        })}</td>
      </tr>`;
    }).join("");
  }

  function shortRunId(id){
    const value = String(id || "");
    const match = value.match(/^(golden_v\\d+_\\d+_)(\\d{8}T\\d{6}Z)$/);
    if(match) return match[1] + "..." + match[2].slice(9, 15);
    return value.length > 34 ? value.slice(0, 18) + "..." + value.slice(-10) : value;
  }

  function shortDataset(value){
    const text = String(value || "");
    if(text === "golden") return "golden";
    return text.length > 18 ? text.slice(0, 16) + "..." : text;
  }

  function renderRunProgress(stats){
    return `<div class="run-progress-mini">
      ${progressLine("Pipeline", stats.pipelineDone, stats.totalCases)}
      ${progressLine("Smart-judge", stats.judgeDone, stats.posthocTotal)}
      ${progressLine("Oracle", stats.oracleDone, stats.posthocTotal)}
      ${progressLine("Judge-audit", stats.analysisDone, stats.posthocTotal)}
    </div>`;
  }

  function progressLine(label, done, total){
    const pct = total > 0 ? Math.max(0, Math.min(100, Math.round((done / total) * 100))) : 0;
    return `<div class="run-progress-mini__row">
      <span>${esc(label)}</span>
      <b>${esc(done)} / ${esc(total)}</b>
      <i><em style="width:${pct}%"></em></i>
    </div>`;
  }

  function selectedRunIds(){
    return Array.from(document.querySelectorAll("#runsTable tbody input:checked")).map(x => x.value);
  }

  async function loadCompare(){
    const table = document.getElementById("kpiDiffTable");
    if(!table) return;
    const ids = (qs.get("ids") || "").split(",").map(x => x.trim()).filter(Boolean);
    document.getElementById("compareCount").textContent = ids.length + " selected";
    const data = await api("/v1/benchmarks/runs/compare", {
      method: "POST",
      headers: Object.assign({"Content-Type":"application/json"}, headers),
      body: JSON.stringify({run_ids: ids})
    });
    compareData = data;
    document.getElementById("compareMeta").innerHTML = (data.runs || []).map(run => {
      const m = run.metadata || {};
      return `<div><b class="mono">${esc(run.benchmark_run_id)}</b> · ${esc(m.dataset_id)} · ${esc((m.model_matrix || []).join(", "))} · ${esc(m.isolation_mode)}</div>`;
    }).join("");
    const runIds = (data.runs || []).map(r => r.benchmark_run_id);
    compareRunIds = runIds;
    table.querySelector("thead").innerHTML = `<tr><th>metric</th>${runIds.map(id=>`<th>${esc(id)}</th>`).join("")}<th>delta</th></tr>`;
    table.querySelector("tbody").innerHTML = (data.kpi_diff_table || []).map(row => `<tr><td>${esc(row.metric)}</td>${runIds.map(id=>`<td>${esc(row[id])}</td>`).join("")}<td>${esc(row.delta)}</td></tr>`).join("");
    renderCaseDiff(data.case_diff || [], runIds);
  }

  function renderCaseDiff(rows, runIds){
    const table = document.getElementById("caseDiffTable");
    if(!table) return;
    const decisionOnly = document.getElementById("filterDecisionDiff")?.checked;
    const eaOnly = document.getElementById("filterEaDiff")?.checked;
    const judgeOnly = document.getElementById("filterJudgeDiff")?.checked;
    const filtered = rows.filter(row => {
      const cells = runIds.map(id => row[id] || {});
      if(decisionOnly && new Set(cells.map(c => c.decision)).size < 2) return false;
      if(eaOnly && new Set(cells.map(c => c.ea)).size < 2) return false;
      if(judgeOnly){
        const vals = cells.map(c => Number(c.judge)).filter(Number.isFinite);
        if(vals.length < 2 || Math.max(...vals) - Math.min(...vals) < 2) return false;
      }
      return true;
    });
    table.querySelector("thead").innerHTML = `<tr><th>case</th>${runIds.map(id=>`<th>${esc(id)}</th>`).join("")}</tr>`;
    table.querySelector("tbody").innerHTML = filtered.map(row => `<tr><td class="mono">${esc(row.case_id)}</td>${runIds.map(id=>{const c=row[id]||{}; return `<td>${esc(c.decision)}<br>EA: ${esc(c.ea)}<br>judge: ${esc(c.judge)}</td>`}).join("")}</tr>`).join("");
  }

  function download(filename, text){
    const blob = new Blob([text], {type:"text/csv;charset=utf-8"});
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = filename;
    document.body.appendChild(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(url);
  }

  function csvCell(value){
    return '"' + String(value ?? "").replace(/"/g, '""') + '"';
  }

  function exportCompareCsv(){
    if(!compareData) return;
    const lines = [];
    lines.push(["section","metric_or_case",...compareRunIds,"delta"].map(csvCell).join(","));
    for(const row of compareData.kpi_diff_table || []){
      lines.push(["kpi", row.metric, ...compareRunIds.map(id => row[id]), row.delta].map(csvCell).join(","));
    }
    for(const row of compareData.case_diff || []){
      lines.push(["case", row.case_id, ...compareRunIds.map(id => {
        const cell = row[id] || {};
        return `${cell.decision || ""}; ea=${cell.ea || ""}; judge=${cell.judge || ""}`;
      }), ""].map(csvCell).join(","));
    }
    download("benchmark_compare.csv", lines.join("\n") + "\n");
  }

  function openNewBatch(){
    const modal = document.getElementById("newBatchModal");
    if(modal) modal.hidden = false;
    loadUiConfig().then(populateBatchSelectors).catch(err => {
      document.getElementById("newBatchStatus").textContent = "Config unavailable: " + err.message;
    });
    loadDatasets().catch(err => {
      document.getElementById("newBatchStatus").textContent = "Datasets unavailable: " + err.message;
    });
  }

  function selectedModels(){
    const el = document.getElementById("batchModels");
    if(!el) return [];
    if(el.tagName === "SELECT"){
      return Array.from(el.selectedOptions).map(option => option.value).filter(Boolean);
    }
    return el.value.split(",").map(x => x.trim()).filter(Boolean);
  }

  function selectedOpenRouterProviders(){
    const out = {};
    document.querySelectorAll(".batchOpenRouterProvider").forEach(select => {
      const provider = select.value;
      if(!provider) return;
      const modelKey = select.dataset.modelKey || "";
      const generatorKey = select.dataset.generatorKey || "";
      if(modelKey) out[modelKey] = provider;
      if(generatorKey) out[generatorKey] = provider;
    });
    return out;
  }

  function selectedPromptCheckOpenRouterProvider(){
    const select = document.querySelector(".batchPromptCheckOpenRouterProvider");
    return select && select.value ? select.value : null;
  }

  async function submitNewBatch(evt){
    evt.preventDefault();
    const status = document.getElementById("newBatchStatus");
    const datasetSelect = document.getElementById("batchDataset");
    const dataset = datasetSelect.value.trim();
    const datasetPath = datasetSelect.selectedOptions?.[0]?.dataset?.path || "";
    const limitRaw = document.getElementById("batchLimit").value.trim();
    const parsedLimit = limitRaw === "" ? null : Math.max(0, Number(limitRaw));
    const judgeOption = document.getElementById("batchJudgePreset")?.selectedOptions?.[0];
    const judgeBackend = judgeOption?.dataset?.backend || document.getElementById("batchJudgeBackend")?.value || "off";
    const judgeModel = judgeOption?.dataset?.model || document.getElementById("batchJudgeModel")?.value || null;
    const judgeReasoning = judgeOption?.dataset?.codexReasoningEffort || "";
    const analysisOption = document.getElementById("batchAnalysisPreset")?.selectedOptions?.[0];
    const analysisBackend = analysisOption?.dataset?.backend || "codex_cli";
    const analysisModel = analysisOption?.dataset?.model || "gpt-5.5";
    const analysisReasoning = analysisOption?.dataset?.codexReasoningEffort || "";
    const promptCheckEnabled = document.getElementById("batchPromptCheckEnabled")?.checked !== false;
    const promptCheckOption = document.getElementById("batchPromptCheckPreset")?.selectedOptions?.[0];
    const body = {
      dataset_id: dataset,
      dataset_path: datasetPath || null,
      models: selectedModels(),
      limit: Number.isFinite(parsedLimit) ? parsedLimit : null,
      isolation: document.getElementById("batchIsolation").value || (dataset.startsWith("golden_") ? "clean" : "production"),
      prompt_check_enabled: promptCheckEnabled,
      prompt_check_backend: promptCheckEnabled ? (promptCheckOption?.dataset?.backend || null) : null,
      prompt_check_model: promptCheckEnabled ? (promptCheckOption?.dataset?.model || null) : null,
      prompt_check_openrouter_provider: promptCheckEnabled ? selectedPromptCheckOpenRouterProvider() : null,
      smart_judge_backend: judgeBackend === "off" ? "off" : judgeBackend,
      smart_judge_model: judgeBackend === "off" ? null : judgeModel,
      codex_reasoning_effort: judgeBackend === "codex_cli" ? judgeReasoning : null,
      smart_judge_chunk_size: Number(document.getElementById("batchJudgeChunk").value || 10),
      smart_judge_workers: Number(document.getElementById("batchJudgeWorkers").value || 3),
      oracle_enabled: document.getElementById("batchOracleEnabled")?.checked !== false,
      oracle_dataset_version: "1.1",
      analysis_enabled: document.getElementById("batchAnalysisEnabled")?.checked !== false,
      analysis_backend: analysisBackend,
      analysis_model: analysisModel,
      analysis_codex_reasoning_effort: analysisBackend === "codex_cli" ? analysisReasoning : null,
      analysis_oracle_required: document.getElementById("batchAnalysisOracleRequired")?.checked || false,
      openrouter_providers: selectedOpenRouterProviders(),
      started_by: "ui"
    };
    status.textContent = "Starting batch...";
    const created = await api("/v1/benchmarks/runs", {
      method:"POST",
      headers:Object.assign({"Content-Type":"application/json"}, headers),
      body:JSON.stringify(body)
    });
    location.href = "/audits/runs/" + encodeURIComponent(created.benchmark_run_id);
  }

  async function uploadDataset(){
    const input = document.getElementById("batchDatasetFile");
    const status = document.getElementById("newBatchStatus");
    const file = input?.files?.[0];
    if(!file){
      status.textContent = "Choose a JSON or JSONL file first.";
      return;
    }
    status.textContent = "Uploading dataset...";
    const content = await file.text();
    const created = await api("/v1/benchmarks/datasets/upload", {
      method: "POST",
      headers: Object.assign({"Content-Type":"application/json"}, headers),
      body: JSON.stringify({filename: file.name, content})
    });
    await loadDatasets();
    const select = document.getElementById("batchDataset");
    if(select && created.item?.dataset_id) select.value = created.item.dataset_id;
    updateDatasetHint();
    status.textContent = "Dataset uploaded: " + (created.item?.dataset_id || file.name);
  }

  function downloadDatasetExample(){
    const sample = [
      {
        id: "custom_tc-0001",
        task: "Покажи активные заявки без персональных данных.",
        sql: "SELECT id, name__ru, create_date, status FROM corp_tech_application WHERE status = 1 LIMIT 100;",
        dialect: "postgresql",
        schema_scope: ["corp_tech_application"],
        schema_context: "corp_tech_application(id, name__ru, create_date, status)",
        risk_labels: [],
        severity: 0,
        safe_rewrite: "SELECT id, name__ru, create_date, status FROM corp_tech_application WHERE status = 1 LIMIT 100;",
        golden_oracle_type: "reference_sql"
      }
    ];
    download("benchmark_dataset_example.jsonl", sample.map(row => JSON.stringify(row)).join("\n") + "\n");
  }

  document.getElementById("refreshRuns")?.addEventListener("click", loadRuns);
  document.getElementById("newBatch")?.addEventListener("click", openNewBatch);
  document.getElementById("cancelNewBatch")?.addEventListener("click", () => { document.getElementById("newBatchModal").hidden = true; });
  document.getElementById("batchDataset")?.addEventListener("change", updateDatasetHint);
  document.getElementById("uploadBatchDataset")?.addEventListener("click", () => uploadDataset().catch(err => {
    document.getElementById("newBatchStatus").textContent = err.message;
  }));
  document.getElementById("downloadDatasetExample")?.addEventListener("click", downloadDatasetExample);
  document.getElementById("newBatchForm")?.addEventListener("submit", (evt) => submitNewBatch(evt).catch(err => {
    document.getElementById("newBatchStatus").textContent = err.message;
  }));
  document.getElementById("exportCompare")?.addEventListener("click", exportCompareCsv);
  document.getElementById("compareSelected")?.addEventListener("click", () => {
    const ids = selectedRunIds();
    if(ids.length >= 2) location.href = "/audits/runs/compare?ids=" + encodeURIComponent(ids.join(","));
  });
  ["filterDecisionDiff","filterEaDiff","filterJudgeDiff"].forEach(id => document.getElementById(id)?.addEventListener("change", loadCompare));
  if(document.getElementById("runsTable")) {
    loadUiConfig().then(populateBatchSelectors).catch(console.warn);
    loadDatasets().catch(console.warn);
    loadRuns().catch(console.error);
    setInterval(() => loadRuns().catch(console.warn), 5000);
  }
  if(document.getElementById("kpiDiffTable")) loadCompare().catch(console.error);
})();
