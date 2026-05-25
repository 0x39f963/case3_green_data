/* Generated at: 2026-05-21 23:55:00 MSK */
(function(){
  const meta = (name) => document.querySelector(`meta[name="${name}"]`)?.content || "";
  const apiBase = meta("api-base-url") || "http://localhost:18081";
  const token = meta("api-token");
  const runId = meta("benchmark-run-id");
  const headers = token ? {Authorization: "Bearer " + token} : {};
  let detailData = null;
  let failedCaseIds = [];
  let refreshTimer = null;
  let uiConfig = null;

  const esc = (v) => String(v ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;","'":"&#39;"}[c]));
  const money = (v) => "$" + (Number.isFinite(Number(v)) ? Number(v).toFixed(4) : "0.0000");
  const fmt = (v, suffix) => Number.isFinite(Number(v)) ? Number(v).toFixed(2) + (suffix || "") : "n/a";
  const pct = (v) => Number.isFinite(Number(v)) ? (Number(v) * 100).toFixed(2) + "%" : "N/A";

  const metricCatalog = {
    approve_rate: {
      label: "Approve Rate",
      icon: "shield",
      tone: "green",
      summary: "Доля запросов, которые pipeline смог одобрить без ручной доработки. Для бизнеса это быстрый показатель конверсии: сколько кейсов система довела до рабочего SQL.",
      formula: "approved cases / all processed cases",
      seriesKey: "approve_rate"
    },
    first_try_success_rate: {
      label: "First-try Success",
      icon: "rocket",
      tone: "blue",
      summary: "Доля кейсов, где правильное решение получилось с первой попытки. Метрика показывает не только итоговый результат, но и качество первого ответа без дополнительных итераций.",
      formula: "approved cases with iterations_used = 1 / all processed cases",
      seriesKey: "first_try_success_rate"
    },
    ea_pass_rate: {
      label: "EA Pass Rate",
      icon: "clipboard",
      tone: "violet",
      summary: "Доля кейсов, которые прошли oracle/execution-accuracy проверку. Это самая строгая бизнесовая проверка: SQL не просто одобрен pipeline, а совпал с эталонной логикой.",
      formula: "oracle verdict = pass / cases with oracle evaluation",
      seriesKey: "ea_pass_rate"
    },
    smart_judge_avg_score: {
      label: "Smart-judge Avg",
      icon: "scale",
      tone: "blue",
      summary: "Средняя оценка smart-judge по 9 параметрам качества. Пока судья не запущен по batch, эта метрика остаётся пустой.",
      formula: "weighted average of 9 case_quality_scores sub-scores",
      seriesKey: null
    },
    avg_latency_ms: {
      label: "Avg Latency",
      icon: "clock",
      tone: "blue",
      summary: "Среднее время обработки одного кейса pipeline. Помогает увидеть, насколько быстро система проходит датасет.",
      formula: "average pipeline_runs.duration_sec * 1000",
      seriesKey: null
    },
    total_cost_usd: {
      label: "Total Cost",
      icon: "dollar",
      tone: "green",
      summary: "Суммарная стоимость LLM-вызовов по batch. Для CLI-бэкендов это может быть quota-equivalent USD, а не прямой биллинг.",
      formula: "sum(llm_calls.cost_usd)",
      seriesKey: null
    },
    stage4_judge_call_rate: {
      label: "Stage 4 Call Rate",
      icon: "phone",
      tone: "orange",
      summary: "Доля кейсов, которым понадобился дополнительный judge/check stage. Чем ниже показатель при хорошем approve rate, тем дешевле и стабильнее pipeline.",
      formula: "stage-4 judge calls / all processed cases",
      seriesKey: null
    },
    avg_iterations: {
      label: "Avg Iterations",
      icon: "refresh",
      tone: "violet",
      summary: "Среднее количество итераций генерации/проверки до финального решения. Высокое значение обычно указывает на сложные запросы или слабые места промпта.",
      formula: "average pipeline_runs.iterations_used",
      seriesKey: null
    },
    max_iter_hit_rate: {
      label: "Max-iter Hit",
      icon: "target",
      tone: "pink",
      summary: "Доля кейсов, которые дошли до лимита итераций и всё равно не были одобрены. Это очередь кандидатов для улучшения промпта/RAG/schema overlay.",
      formula: "not approved cases with iterations_used >= 5 / all processed cases",
      seriesKey: null
    }
  };

  async function api(path, opts){
    const options = Object.assign({headers}, opts || {});
    if(options.body && !options.headers?.["Content-Type"]){
      options.headers = Object.assign({"Content-Type":"application/json"}, options.headers || {});
    }
    const res = await fetch(apiBase + path, options);
    if(!res.ok) throw new Error(await res.text());
    return res.json();
  }

  async function loadUiConfig(){
    if(uiConfig) return uiConfig;
    const res = await fetch("/web/api/config");
    if(!res.ok) throw new Error(await res.text());
    uiConfig = await res.json();
    return uiConfig;
  }

  function populatePromptCheckRerun(config){
    const select = document.getElementById("rerunPromptCheckPreset");
    if(!select) return;
    const items = (config.prompt_check_backends || []).filter(item => item.backend && item.backend !== "off");
    const def = config.default_prompt_check_backend || "";
    select.innerHTML = items.map(item => {
      const selected = item.key === def || (!def && item.default) ? " selected" : "";
      return `<option value="${esc(item.key)}" data-backend="${esc(item.backend || "")}" data-model="${esc(item.provider_model || "")}"${selected}>${esc(item.label || item.key)}</option>`;
    }).join("");
    if(!select.selectedOptions.length && select.options.length) select.options[0].selected = true;
  }

  function iconSvg(name){
    const common = `fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"`;
    const paths = {
      shield: `<path d="M12 3l7 3v5c0 5-3.4 8.5-7 10-3.6-1.5-7-5-7-10V6l7-3z"/><path d="M9 12l2 2 4-5"/>`,
      rocket: `<path d="M14 4c3.2.2 5.8 2.8 6 6-2.7.8-5.1 2.2-7 4l-3-3c1.8-1.9 3.2-4.3 4-7z"/><path d="M9 15l-3 3"/><path d="M10 11l-4 1-2 4 4-2 1-4"/><circle cx="15" cy="9" r="1"/>`,
      clipboard: `<path d="M9 4h6l1 2h2v15H6V6h2l1-2z"/><path d="M9 10h6"/><path d="M9 14h4"/><path d="M10 4h4"/>`,
      scale: `<path d="M12 3v18"/><path d="M5 6h14"/><path d="M6 6l-3 7h6L6 6z"/><path d="M18 6l-3 7h6l-3-7z"/><path d="M8 21h8"/>`,
      clock: `<circle cx="12" cy="12" r="9"/><path d="M12 7v6l4 2"/>`,
      dollar: `<path d="M12 3v18"/><path d="M16 7.5c-1.2-1-6-1.4-6 1.5 0 3 6 2 6 5 0 2.8-4.8 2.5-7 1.2"/>`,
      phone: `<path d="M7 4l3 3-2 2c1.2 2.4 2.7 3.9 5 5l2-2 3 3-2 4c-7-1-11-5-13-12l4-3z"/>`,
      refresh: `<path d="M20 12a8 8 0 1 1-2.3-5.7"/><path d="M20 4v6h-6"/>`,
      target: `<circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="5"/><circle cx="12" cy="12" r="1"/>`,
      rows: `<path d="M4 6h16"/><path d="M4 12h16"/><path d="M4 18h16"/><path d="M8 4v16"/>`
    };
    return `<svg viewBox="0 0 24 24" aria-hidden="true" ${common}>${paths[name] || paths.rows}</svg>`;
  }

  function tile(metricKey, value, hint){
    const meta = metricCatalog[metricKey] || {label: metricKey, icon:"rows", tone:"blue"};
    const clickable = ["approve_rate", "first_try_success_rate", "ea_pass_rate"].includes(metricKey);
    return `<button class="kpi-card${clickable ? " kpi-card-clickable" : ""}" type="button" data-metric="${esc(metricKey)}"${clickable ? "" : " tabindex=\"-1\""}>
      <span class="metric-icon metric-icon-${esc(meta.tone || "blue")}">${iconSvg(meta.icon)}</span>
      <span class="kpi-card__text"><span class="kpi-card__label">${esc(meta.label)}</span><b>${esc(value)}</b><span class="kpi-card__hint">${esc(hint || "")}</span></span>
    </button>`;
  }

  function renderLineage(data){
    const el = document.getElementById("lineageRow");
    if(!el) return;
    const metaData = data.metadata || {};
    const links = [];
    if(metaData.parent_run_id){
      links.push(`Parent: <a class="mono" href="/audits/runs/${encodeURIComponent(metaData.parent_run_id)}">${esc(metaData.parent_run_id)}</a>`);
    }
    const children = data.children || [];
    if(children.length){
      links.push("Children: " + children.map(x => `<a class="mono" href="/audits/runs/${encodeURIComponent(x.benchmark_run_id)}">${esc(x.benchmark_run_id)}</a>`).join(", "));
    }
    el.innerHTML = links.join(" · ");
  }

  function providerList(raw){
    return String(raw || "")
      .replace(/["']/g, "")
      .split(",")
      .map(x => x.trim())
      .filter(Boolean)
      .join(", ");
  }

  function humanizeRunnerError(text){
    const raw = String(text || "").trim();
    if(!raw) return "";
    const noProvider = raw.includes("No allowed providers are available for the selected model");
    if(noProvider){
      const available = raw.match(/available_providers['"]?\s*:\s*\[([^\]]+)\]/i);
      const requested = raw.match(/requested_providers['"]?\s*:\s*\[([^\]]+)\]/i);
      if(!available && !requested) return raw.length > 900 ? raw.slice(0, 900) + "..." : raw;
      const bits = ["No allowed providers are available for the selected model."];
      if(available) bits.push("Available: " + providerList(available[1]) + ".");
      if(requested) bits.push("Requested: " + providerList(requested[1]) + ".");
      return bits.join(" ");
    }
    return raw.length > 900 ? raw.slice(0, 900) + "..." : raw;
  }

  function renderPipelineError(progress, metaData){
    const box = document.getElementById("pipelineError");
    if(!box) return;
    const runner = progress.runner || {};
    const pipeline = progress.pipeline || {};
    const runnerStatus = runner.status || metaData.status || "";
    const errorText = runner.error_text || pipeline.error_text || metaData.config_jsonb?.runner_error || "";
    if(runnerStatus !== "failed" || !errorText){
      box.hidden = true;
      box.innerHTML = "";
      return;
    }
    const logPath = runner.log_path || metaData.config_jsonb?.runner_log_path || "";
    box.hidden = false;
    box.innerHTML = `<b>Pipeline failed</b><span>${esc(humanizeRunnerError(errorText))}</span>${logPath ? `<code>${esc(logPath)}</code>` : ""}`;
  }

  function renderModelSummary(data){
    const rows = data.model_summary || [];
    const tbody = document.querySelector("#modelSummaryTable tbody");
    const summary = document.getElementById("modelSummaryText");
    const analyzedLink = document.getElementById("openAnalyzedCases");
    if(analyzedLink) analyzedLink.href = "/audits/batch-cases?run_id=" + encodeURIComponent(runId) + "&analysis_status=analyzed";
    if(!tbody || !summary) return;
    if(!rows.length){
      summary.textContent = "No model metadata was ingested for this run.";
      tbody.innerHTML = `<tr><td colspan="4"><div class="case-empty">No model rows.</div></td></tr>`;
      return;
    }
    const total = rows.reduce((acc, row) => acc + Number(row.cases || 0), 0);
    const generatorSet = new Set(rows.map(row => [row.generator_backend, row.generator_provider, row.generator_model].filter(Boolean).join(" / ")).filter(Boolean));
    const auditorSet = new Set(rows.map(row => [row.auditor_backend, row.auditor_model].filter(Boolean).join(" / ")).filter(Boolean));
    summary.textContent = `${total} processed cases · ${generatorSet.size || 0} generator model(s) · ${auditorSet.size || 0} evaluation/auditor model(s)`;
    tbody.innerHTML = rows.map(row => {
      const generator = [row.generator_backend, row.generator_provider, row.generator_model].filter(Boolean).join(" / ");
      const auditor = [row.auditor_backend, row.auditor_model].filter(Boolean).join(" / ");
      return `<tr>
        <td><code>${esc(row.model_key || "")}</code></td>
        <td>${esc(generator || "n/a")}</td>
        <td>${esc(auditor || "n/a")}</td>
        <td>${esc(row.cases || 0)}</td>
      </tr>`;
    }).join("");
  }

  function jobStatusClass(status){
    const s = String(status || "").toLowerCase();
    if(["running", "queued", "waiting"].includes(s)) return "is-running";
    if(["completed", "done"].includes(s)) return "is-done";
    if(["failed", "error"].includes(s)) return "is-failed";
    if(["aborted", "stopped_by_user"].includes(s)) return "is-stopped";
    return "";
  }

  function jobLine(label, job, totalBase){
    const status = job.status || "not_started";
    const completed = Number(job.completed_cases || 0);
    const total = Number(totalBase || 0);
    const missing = job.total_missing !== undefined ? Number(job.total_missing || 0) : Math.max(total - completed, 0);
    const logPath = job.log_path || "";
    const jobId = job.job_id || "";
    const bits = [];
    if(total) bits.push(`${completed}/${total} cases`);
    else bits.push(`${completed} cases`);
    if(job.pass_cases !== undefined || job.fail_cases !== undefined || job.error_cases !== undefined){
      bits.push(`${job.pass_cases || 0} pass`);
      bits.push(`${job.fail_cases || 0} fail`);
      if(job.error_cases) bits.push(`${job.error_cases} error`);
    }
    if(missing) bits.push(`${missing} missing`);
    return `<article class="job-card ${jobStatusClass(status)}">
      <div class="job-card__head">
        <b>${esc(label)}</b>
        <span>${esc(status)}</span>
      </div>
      <p>${esc(bits.join(" · "))}</p>
      ${job.error_text ? `<div class="job-card__error">${esc(String(job.error_text).slice(0, 220))}</div>` : ""}
      <div class="job-card__meta">
        ${jobId ? `<code title="${esc(jobId)}">${esc(jobId)}</code>` : ""}
        ${logPath ? `<code title="${esc(logPath)}">${esc(logPath)}</code>` : ""}
      </div>
    </article>`;
  }

  function renderJobTimeline(progress){
    const target = document.getElementById("jobTimeline");
    const hint = document.getElementById("jobTimelineHint");
    if(!target) return;
    const pipeline = progress.pipeline || {};
    const processed = Number(pipeline.completed_cases || 0);
    const items = [
      ["Pipeline", progress.runner || {status: "not_started", completed_cases: processed, total_missing: 0}, Number(pipeline.total_cases || 0)],
      ["Smart-judge", progress.judge || {}, processed],
      ["Oracle", progress.oracle || {}, processed],
      ["Judge-audit", progress.analysis || {}, processed],
    ];
    target.innerHTML = items.map(([label, job, total]) => jobLine(label, job, total)).join("");
    const running = items.filter(([, job]) => ["queued", "running", "waiting"].includes(String(job.status || ""))).map(([label]) => label);
    if(hint) hint.textContent = running.length ? "Сейчас выполняется: " + running.join(", ") : "Активных пересчётов сейчас нет";
  }

  function renderDetail(data){
    const m = data.metrics || {};
    const metaData = data.metadata || {};
    document.getElementById("isolationChip").textContent = metaData.isolation_mode === "clean" ? "Clean isolation" : (metaData.isolation_mode || "production");
    renderLineage(data);
    renderModelSummary(data);

    const p = data.progress || {};
    const pipeline = p.pipeline || {};
    const judge = p.judge || {};
    const oracle = p.oracle || {};
    const analysis = p.analysis || {};
    const pPct = pipeline.total_cases ? Math.round(100 * (pipeline.completed_cases || 0) / pipeline.total_cases) : 0;
    const jBase = pipeline.completed_cases || 0;
    const jPct = jBase ? Math.round(100 * (judge.completed_cases || 0) / jBase) : 0;
    const oPct = jBase ? Math.min(100, Math.round(100 * (oracle.completed_cases || 0) / jBase)) : 0;
    const aPct = jBase ? Math.min(100, Math.round(100 * (analysis.completed_cases || 0) / jBase)) : 0;
    const remainingPlanned = Math.max((pipeline.total_cases || 0) - (pipeline.completed_cases || 0), 0);
    const runnerStatus = p.runner?.status || metaData.status || "";
    document.getElementById("pipelineProgress").value = pPct;
    document.getElementById("judgeProgress").value = jPct;
    document.getElementById("oracleProgress").value = oPct;
    document.getElementById("pipelineText").textContent = `${pipeline.completed_cases || 0} processed / ${pipeline.total_cases || 0} planned${runnerStatus === "failed" && remainingPlanned ? ` (${remainingPlanned} not ingested)` : ""}`;
    renderPipelineError(p, metaData);
    renderJobTimeline(p);
    const judgeBits = [`${judge.completed_cases || 0} scored / ${jBase} processed`];
    if(judge.status && judge.status !== "disabled") judgeBits.push(judge.status);
    if(judge.error_text) judgeBits.push(String(judge.error_text).slice(0, 120));
    document.getElementById("judgeText").textContent = judgeBits.join(" · ");
    renderJudgeAction(pipeline, judge);
    const oracleBits = [(oracle.completed_cases || 0) ? `${oracle.completed_cases || 0} evaluated / ${jBase} processed` : `N/A · 0 evaluated / ${jBase} processed`];
    if(oracle.status) oracleBits.push(oracle.status);
    if(oracle.completed_cases){
      oracleBits.push(`${oracle.pass_cases || 0} pass`);
      oracleBits.push(`${oracle.fail_cases || 0} fail`);
      if(oracle.error_cases) oracleBits.push(`${oracle.error_cases} error`);
    }
    if(oracle.error_text) oracleBits.push(String(oracle.error_text).slice(0, 120));
    document.getElementById("oracleText").textContent = oracleBits.join(" · ");
    renderOracleAction(pipeline, oracle);
    document.getElementById("analysisProgress").value = aPct;
    const analysisBits = [(analysis.completed_cases || 0) ? `${analysis.completed_cases || 0} analyzed / ${jBase} processed` : `0 analyzed / ${jBase} processed`];
    if(analysis.status) analysisBits.push(analysis.status);
    if(analysis.total_missing !== undefined) analysisBits.push(`${analysis.total_missing || 0} missing`);
    if(analysis.error_text) analysisBits.push(String(analysis.error_text).slice(0, 120));
    document.getElementById("analysisText").textContent = analysisBits.join(" · ");
    renderAnalysisAction(pipeline, judge, analysis);

    const eaEvaluated = Number(m.ea_evaluated_cases || 0);
    const eaTotal = Number(m.ea_total_cases || m.total || 0);
    const eaValue = eaEvaluated ? pct(m.ea_pass_rate) : "N/A";
    const eaHint = eaEvaluated ? `${eaEvaluated}/${eaTotal} ${m.ea_status || ""}`.trim() : "not evaluated";

    document.getElementById("kpiGrid").innerHTML = [
      tile("approve_rate", pct(m.approve_rate)),
      tile("first_try_success_rate", pct(m.first_try_success_rate)),
      tile("ea_pass_rate", eaValue, eaHint),
      tile("smart_judge_avg_score", fmt(m.smart_judge_avg_score), "/ 10"),
      tile("avg_latency_ms", fmt(m.avg_latency_ms, " ms"), "p95 " + fmt(m.p95_latency_ms, " ms")),
      tile("total_cost_usd", money(m.total_cost_usd), "quota-eq " + money(m.total_cost_quota_equivalent_usd)),
      tile("stage4_judge_call_rate", pct(m.stage4_judge_call_rate)),
      tile("avg_iterations", fmt(m.avg_iterations)),
      tile("max_iter_hit_rate", pct(m.max_iter_hit_rate))
    ].join("");

    const stageBody = document.querySelector("#stageTable tbody");
    stageBody.innerHTML = Object.entries(m.per_stage || {}).map(([stage,row]) => `<tr>
      <td>${esc(stage)}</td>
      <td>${fmt(row.avg_ms)}</td>
      <td>${fmt(row.p95_ms)}</td>
      <td>${fmt(row.avg_tokens_in)}</td>
      <td>${money(row.avg_cost_usd)}</td>
      <td>${fmt((row.fail_rate || 0) * 100, "%")}</td>
    </tr>`).join("");

    const dist = m.iterations_distribution || {};
    if(window.Plotly){
      Plotly.newPlot("iterationsChart", [{type:"bar", x:Object.keys(dist), y:Object.values(dist), marker:{color:"#2563eb", line:{color:"#1d4ed8", width:1}}, hovertemplate:"%{x} iterations<br>%{y} cases<extra></extra>"}], {margin:{t:26,l:40,r:14,b:38}, title:{text:"Iterations", font:{size:14, color:"#0f172a"}}, paper_bgcolor:"rgba(0,0,0,0)", plot_bgcolor:"rgba(0,0,0,0)", font:{family:"Inter, system-ui, sans-serif", size:11, color:"#334155"}, yaxis:{gridcolor:"#e8eef8", tickfont:{size:11}}, xaxis:{title:{text:"iterations", font:{size:11}}, tickfont:{size:11}}}, {displayModeBar:false, responsive:true});
    }
    renderRadar(m.smart_judge_avg_subscores || {}, judge, jBase);
    loadHypotheses().catch(console.warn);
  }

  function renderRadar(subscores, judge, scoredBase){
    const axes = [
      ["sql_correctness", "sql"],
      ["security", "security"],
      ["intent_fidelity", "intent"],
      ["schema_usage", "schema"],
      ["rag_facts_used", "rag"],
      ["decision_rationale", "decision"],
      ["performance", "perf"],
      ["robustness", "robust"],
      ["retry_efficiency", "retry"]
    ];
    const values = axes.map(([key]) => Number(subscores[key]));
    const hasData = values.some(Number.isFinite);
    const size = 380;
    const cx = size / 2;
    const cy = size / 2;
    const radius = 125;
    const points = values.map((raw, index) => {
      const angle = -Math.PI / 2 + index * Math.PI * 2 / axes.length;
      const value = Number.isFinite(raw) ? Math.max(0, Math.min(10, raw)) : 0;
      const r = radius * value / 10;
      return {x: cx + Math.cos(angle) * r, y: cy + Math.sin(angle) * r, angle, value};
    });
    const polygon = points.map(p => `${p.x.toFixed(1)},${p.y.toFixed(1)}`).join(" ");
    const rings = [2,4,6,8,10].map(level => `<circle cx="${cx}" cy="${cy}" r="${radius * level / 10}" class="radar-ring"/><text x="${cx + 4}" y="${cy - radius * level / 10 + 4}" class="radar-tick">${level}</text>`).join("");
    const spokes = axes.map(([,label], index) => {
      const angle = -Math.PI / 2 + index * Math.PI * 2 / axes.length;
      const x = cx + Math.cos(angle) * radius;
      const y = cy + Math.sin(angle) * radius;
      const lx = cx + Math.cos(angle) * (radius + 34);
      const ly = cy + Math.sin(angle) * (radius + 34);
      return `<line x1="${cx}" y1="${cy}" x2="${x.toFixed(1)}" y2="${y.toFixed(1)}" class="radar-spoke"/><text x="${lx.toFixed(1)}" y="${ly.toFixed(1)}" text-anchor="middle" dominant-baseline="middle" class="radar-label">${esc(label)}</text>`;
    }).join("");
    const dots = points.map(p => `<circle cx="${p.x.toFixed(1)}" cy="${p.y.toFixed(1)}" r="3.5" class="radar-dot"><title>${p.value.toFixed(1)} / 10</title></circle>`).join("");
    const pendingPoints = axes.map((_, index) => {
      const angle = -Math.PI / 2 + index * Math.PI * 2 / axes.length;
      const r = radius * 0.62;
      return `${(cx + Math.cos(angle) * r).toFixed(1)},${(cy + Math.sin(angle) * r).toFixed(1)}`;
    }).join(" ");
    const scored = Number(judge?.completed_cases || 0);
    const base = Number(scoredBase || 0);
    const empty = hasData ? "" : `<polygon points="${pendingPoints}" class="radar-pending-area"/><text x="${cx}" y="${cy - 12}" text-anchor="middle" class="radar-empty-main">Awaiting judge</text><text x="${cx}" y="${cy + 10}" text-anchor="middle" class="radar-empty-sub">${scored} / ${base} cases scored</text><text x="${cx}" y="${cy + 30}" text-anchor="middle" class="radar-empty-note">Scores will fill this area after posthoc smart-judge</text>`;
    const fill = hasData ? `<polygon points="${polygon}" class="radar-area"/>${dots}` : "";
    const el = document.getElementById("radarChart");
    if(!el) return;
    el.innerHTML = `<svg class="smart-radar-svg" viewBox="0 0 ${size} ${size}" role="img" aria-label="Smart-judge radar chart">
      ${rings}
      ${spokes}
      ${fill}
      <circle cx="${cx}" cy="${cy}" r="3" class="radar-center"/>
      ${empty}
    </svg>`;
  }

  function openMetricDrawer(metricKey){
    const drawer = document.getElementById("metricDrawer");
    const backdrop = document.getElementById("metricDrawerBackdrop");
    const data = detailData?.metrics || {};
    const meta = metricCatalog[metricKey];
    if(!drawer || !backdrop || !meta) return;
    document.getElementById("metricDrawerTitle").textContent = meta.label;
    document.getElementById("metricDrawerSummary").textContent = meta.summary;
    document.getElementById("metricDrawerFormula").textContent = meta.formula;
    const evaluated = Number(data.ea_evaluated_cases || 0);
    const value = metricKey === "ea_pass_rate" && !evaluated ? "N/A" : pct(data[metricKey]);
    const series = (data.metric_series || []).filter(row => row[meta.seriesKey] !== null && row[meta.seriesKey] !== undefined);
    document.getElementById("metricDrawerStats").innerHTML = [
      `<div><span>Current</span><b>${esc(value)}</b></div>`,
      `<div><span>Cases in chart</span><b>${esc(series.length)}</b></div>`,
      `<div><span>Processed</span><b>${esc(data.total || 0)}</b></div>`
    ].join("");
    backdrop.hidden = false;
    drawer.classList.add("is-open");
    drawer.setAttribute("aria-hidden", "false");
    renderMetricDrawerChart(meta, series);
  }

  function renderMetricDrawerChart(meta, series){
    const el = document.getElementById("metricDrawerChart");
    if(!window.Plotly || !el) return;
    if(!meta.seriesKey || !series.length){
      Plotly.newPlot(el, [], {
        margin:{t:20,l:35,r:20,b:30},
        annotations:[{text:"No series data for this metric yet", x:0.5, y:0.5, xref:"paper", yref:"paper", showarrow:false}]
      }, {displayModeBar:false, responsive:true});
      return;
    }
    const x = series.map(row => row.idx);
    const y = series.map(row => Number(row[meta.seriesKey] || 0) * 100);
    Plotly.newPlot(el, [{
      type:"scatter",
      mode:"lines",
      x,
      y,
      line:{color:"#2563eb", width:3, shape:"spline"},
      fill:"tozeroy",
      fillcolor:"rgba(37, 99, 235, 0.10)",
      hovertemplate:"case #%{x}<br>%{y:.2f}%<extra></extra>"
    }], {
      margin:{t:18,l:46,r:20,b:42},
      paper_bgcolor:"rgba(0,0,0,0)",
      plot_bgcolor:"rgba(0,0,0,0)",
      font:{family:"Inter, system-ui, sans-serif", color:"#0f172a"},
      yaxis:{title:"rate", ticksuffix:"%", range:[0, Math.max(100, Math.ceil(Math.max(...y, 1) / 10) * 10)], gridcolor:"#e8eef8"},
      xaxis:{title:"dataset order", gridcolor:"#f1f5f9"}
    }, {displayModeBar:false, responsive:true});
  }

  function closeMetricDrawer(){
    const drawer = document.getElementById("metricDrawer");
    const backdrop = document.getElementById("metricDrawerBackdrop");
    if(!drawer || !backdrop) return;
    drawer.classList.remove("is-open");
    drawer.setAttribute("aria-hidden", "true");
    backdrop.hidden = true;
  }

  async function loadPromptVersions(){
    const select = document.getElementById("promptVersionOverride");
    if(!select) return;
    if(select.dataset.loaded === "1") return;
    try{
      const res = await fetch("/web/api/system-prompts");
      if(!res.ok) return;
      const data = await res.json();
      const prompts = data.prompts || data.items || [];
      const options = prompts.flatMap(p => {
        const versions = p.versions || [];
        if(!versions.length && p.prompt_id) return [{id:p.prompt_id, version:p.version || p.prompt_version}];
        return versions.map(v => ({id:p.prompt_id, version:v.version || v.prompt_version}));
      }).filter(x => x.id && x.version);
      select.insertAdjacentHTML("beforeend", options.map(x => `<option value="${esc(x.id)}@${esc(x.version)}">${esc(x.id)}@${esc(x.version)}</option>`).join(""));
      select.dataset.loaded = "1";
    }catch(err){
      console.warn("prompt versions unavailable", err);
    }
  }

  async function openRerunModal(){
    const modal = document.getElementById("rerunModal");
    const status = document.getElementById("rerunStatus");
    status.textContent = "Loading failed cases...";
    modal.hidden = false;
    loadUiConfig().then(populatePromptCheckRerun).catch(console.warn);
    const data = await api("/v1/benchmarks/runs/" + encodeURIComponent(runId) + "/failed-cases");
    failedCaseIds = data.case_ids || [];
    document.getElementById("rerunCases").value = failedCaseIds.join("\n");
    status.textContent = failedCaseIds.length ? `${failedCaseIds.length} cases ready` : "No failed cases in this run";
  }

  async function submitRerun(evt){
    evt.preventDefault();
    const status = document.getElementById("rerunStatus");
    if(!failedCaseIds.length){
      status.textContent = "No failed cases to re-run.";
      return;
    }
    const metaData = detailData?.metadata || {};
    const promptCheckEnabled = document.getElementById("rerunPromptCheckEnabled")?.checked !== false;
    const promptCheckOption = document.getElementById("rerunPromptCheckPreset")?.selectedOptions?.[0];
    const body = {
      parent_run_id: runId,
      dataset_id: metaData.dataset_id,
      models: metaData.model_matrix || [],
      case_ids_filter: failedCaseIds,
      isolation: metaData.isolation_mode || "clean",
      prompt_check_enabled: promptCheckEnabled,
      prompt_check_backend: promptCheckEnabled ? (promptCheckOption?.dataset?.backend || null) : null,
      prompt_check_model: promptCheckEnabled ? (promptCheckOption?.dataset?.model || null) : null,
      prompt_version_override: document.getElementById("promptVersionOverride").value || null,
      smart_judge_backend: document.getElementById("rerunJudgeBackend").value || null,
      smart_judge_model: document.getElementById("rerunJudgeModel").value || null,
      smart_judge_chunk_size: 10,
      smart_judge_workers: 3,
      started_by: "ui-rerun"
    };
    status.textContent = "Starting child batch...";
    const created = await api("/v1/benchmarks/runs", {method:"POST", body:JSON.stringify(body)});
    location.href = "/audits/runs/" + encodeURIComponent(created.benchmark_run_id);
  }

  function renderJudgeAction(pipeline, judge){
    const btn = document.getElementById("startJudge");
    if(!btn) return;
    const processed = Number(pipeline.completed_cases || 0);
    const scored = Number(judge.completed_cases || 0);
    const canRun = processed > scored;
    btn.hidden = !canRun;
    btn.disabled = ["queued", "running", "waiting"].includes(String(judge.status || ""));
    btn.textContent = scored ? "Resume smart-judge" : "Run smart-judge";
  }

  function renderOracleAction(pipeline, oracle){
    const btn = document.getElementById("startOracle");
    if(!btn) return;
    const processed = Number(pipeline.completed_cases || 0);
    const evaluated = Number(oracle.completed_cases || 0);
    const canRun = processed > evaluated;
    btn.hidden = !canRun;
    btn.disabled = ["queued", "running", "waiting"].includes(String(oracle.status || ""));
    btn.textContent = evaluated ? "Resume Oracle" : "Run Oracle";
  }

  function renderAnalysisAction(pipeline, judge, analysis){
    const btn = document.getElementById("startAnalysis");
    if(!btn) return;
    const processed = Number(pipeline.completed_cases || 0);
    const scored = Number(judge.completed_cases || 0);
    const analyzed = Number(analysis.completed_cases || 0);
    const running = ["queued", "running", "waiting"].includes(String(analysis.status || ""));
    btn.hidden = processed === 0 || scored === 0;
    btn.disabled = running;
    btn.textContent = analyzed ? "Resume audit analysis" : "Run audit analysis";
  }

  function openJudgeModal(){
    const modal = document.getElementById("judgeModal");
    const status = document.getElementById("judgeStatus");
    const progress = detailData?.progress || {};
    if(progress.smart_judge_backend && progress.smart_judge_backend !== "off"){
      document.getElementById("judgeBackend").value = progress.smart_judge_backend;
    }
    if(progress.smart_judge_model){
      document.getElementById("judgeModel").value = progress.smart_judge_model;
    }
    status.textContent = "";
    modal.hidden = false;
  }

  async function submitJudge(evt){
    evt.preventDefault();
    const status = document.getElementById("judgeStatus");
    const body = {
      backend: document.getElementById("judgeBackend").value || "codex_cli",
      model: document.getElementById("judgeModel").value || "gpt-5.5",
      workers: Number(document.getElementById("judgeWorkers").value || 1),
      chunk_size: 10,
      limit: Number(document.getElementById("judgeLimit").value || 0),
      missing_only: document.getElementById("judgeMissingOnly").checked,
      codex_reasoning_effort: document.getElementById("judgeCodexReasoning")?.value || null,
      status_on_error: "runtime_error"
    };
    status.textContent = "Starting smart-judge...";
    const started = await api("/v1/benchmarks/runs/" + encodeURIComponent(runId) + "/judge/start", {method:"POST", body:JSON.stringify(body)});
    status.textContent = `Started ${started.job_id || started.status}`;
    document.getElementById("judgeModal").hidden = true;
    await load();
  }

  function openOracleModal(){
    const modal = document.getElementById("oracleModal");
    const status = document.getElementById("oracleStatus");
    if(!modal || !status) return;
    status.textContent = "";
    modal.hidden = false;
  }

  async function submitOracle(evt){
    evt.preventDefault();
    const status = document.getElementById("oracleStatus");
    const rawTypes = String(document.getElementById("oracleTypes").value || "").trim();
    const body = {
      oracle_types: rawTypes ? rawTypes.split(",").map(x => x.trim()).filter(Boolean) : [],
      limit: Number(document.getElementById("oracleLimit").value || 0),
      missing_only: document.getElementById("oracleMissingOnly").checked,
      dataset_version: document.getElementById("oracleDatasetVersion").value || "1.1",
      workers: 1
    };
    status.textContent = "Starting Oracle...";
    const started = await api("/v1/benchmarks/runs/" + encodeURIComponent(runId) + "/oracle/start", {method:"POST", body:JSON.stringify(body)});
    status.textContent = `Started ${started.job_id || started.status}`;
    document.getElementById("oracleModal").hidden = true;
    await load();
  }

  function openAnalysisModal(){
    const modal = document.getElementById("analysisModal");
    const status = document.getElementById("analysisStatus");
    if(!modal || !status) return;
    status.textContent = "";
    modal.hidden = false;
  }

  async function submitAnalysis(evt){
    evt.preventDefault();
    const status = document.getElementById("analysisStatus");
    const body = {
      backend: document.getElementById("analysisBackend").value || "codex_cli",
      model: document.getElementById("analysisModel").value || "gpt-5.5",
      limit: Number(document.getElementById("analysisLimit").value || 0),
      missing_only: document.getElementById("analysisMissingOnly").checked,
      oracle_required: document.getElementById("analysisOracleRequired").checked,
      codex_reasoning_effort: document.getElementById("analysisCodexReasoning")?.value || null,
      status_on_error: "runtime_error"
    };
    status.textContent = "Starting judge-audit analysis...";
    const started = await api("/v1/benchmarks/runs/" + encodeURIComponent(runId) + "/analysis/start", {method:"POST", body:JSON.stringify(body)});
    status.textContent = `Started ${started.job_id || started.status}`;
    document.getElementById("analysisModal").hidden = true;
    await load();
  }

  async function loadHypotheses(){
    const table = document.querySelector("#hypothesisTable tbody");
    const summary = document.getElementById("hypothesisSummary");
    if(!table || !summary) return;
    const data = await api("/v1/benchmarks/runs/" + encodeURIComponent(runId) + "/hypotheses?limit=50");
    const items = data.items || [];
    const counts = (data.report_status_counts || []).map(row => `${row.status}: ${row.count}`).join(" · ");
    summary.textContent = items.length
      ? `${items.length} hypotheses · ${counts || "analysis reports ready"}`
      : (counts ? `No hypotheses emitted yet · ${counts}` : "No judge-audit analysis reports yet. Start audit analysis to generate hypotheses.");
    table.innerHTML = items.length ? items.map(item => {
      const traceIds = item.trace_ids || [];
      const firstTrace = traceIds[0] || "";
      const evidenceLink = firstTrace
        ? `<a class="mono" href="/audits/batch-cases?run_id=${encodeURIComponent(runId)}&q=${encodeURIComponent(firstTrace)}">${esc(firstTrace)}</a>`
        : "";
      return `<tr>
      <td>${esc(item.severity || "")}</td>
      <td>${esc(item.target_area || "")}</td>
      <td><b>${esc(item.title || "")}</b><br><span class="case-muted">${esc(item.description || item.patch_hint || "")}</span></td>
      <td>${esc(item.run_evidence_count || item.evidence_count || 0)} reports<br><span class="case-muted">${evidenceLink}</span></td>
      <td>${esc(item.status || "")}</td>
    </tr>`;
    }).join("") : `<tr><td colspan="5"><div class="case-empty">No hypotheses yet.</div></td></tr>`;
  }

  async function load(){
    detailData = await api("/v1/benchmarks/runs/" + encodeURIComponent(runId));
    renderDetail(detailData);
    await loadPromptVersions();
    updateAutoRefresh(detailData);
  }

  function shouldAutoRefresh(data){
    const progress = data?.progress || {};
    const judge = progress.judge || {};
    const oracle = progress.oracle || {};
    const analysis = progress.analysis || {};
    if(["queued", "running", "waiting"].includes(String(oracle.status || ""))) return true;
    if(["queued", "running", "waiting"].includes(String(judge.status || ""))) return true;
    if(["queued", "running", "waiting"].includes(String(analysis.status || ""))) return true;
    const runnerStatus = progress.runner?.status || data?.metadata?.status || "";
    if(["completed", "failed", "aborted"].includes(String(runnerStatus))) return false;
    const pipeline = progress.pipeline || {};
    if((pipeline.total_cases || 0) && (pipeline.completed_cases || 0) < pipeline.total_cases) return true;
    return judge.status === "running";
  }

  function updateAutoRefresh(data){
    const shouldRefresh = shouldAutoRefresh(data);
    if(shouldRefresh && !refreshTimer){
      refreshTimer = setInterval(() => load().catch(console.warn), 5000);
    }
    if(!shouldRefresh && refreshTimer){
      clearInterval(refreshTimer);
      refreshTimer = null;
    }
  }

  document.getElementById("rerunFailed")?.addEventListener("click", () => openRerunModal().catch(err => {
    document.getElementById("rerunStatus").textContent = err.message;
  }));
  document.getElementById("startJudge")?.addEventListener("click", openJudgeModal);
  document.getElementById("startOracle")?.addEventListener("click", openOracleModal);
  document.getElementById("startAnalysis")?.addEventListener("click", openAnalysisModal);
  document.getElementById("cancelJudge")?.addEventListener("click", () => { document.getElementById("judgeModal").hidden = true; });
  document.getElementById("cancelOracle")?.addEventListener("click", () => { document.getElementById("oracleModal").hidden = true; });
  document.getElementById("cancelAnalysis")?.addEventListener("click", () => { document.getElementById("analysisModal").hidden = true; });
  document.getElementById("judgeForm")?.addEventListener("submit", (evt) => submitJudge(evt).catch(err => {
    document.getElementById("judgeStatus").textContent = err.message;
  }));
  document.getElementById("oracleForm")?.addEventListener("submit", (evt) => submitOracle(evt).catch(err => {
    document.getElementById("oracleStatus").textContent = err.message;
  }));
  document.getElementById("analysisForm")?.addEventListener("submit", (evt) => submitAnalysis(evt).catch(err => {
    document.getElementById("analysisStatus").textContent = err.message;
  }));
  document.getElementById("refreshHypotheses")?.addEventListener("click", () => loadHypotheses().catch(console.warn));
  document.getElementById("cancelRerun")?.addEventListener("click", () => { document.getElementById("rerunModal").hidden = true; });
  document.getElementById("rerunForm")?.addEventListener("submit", (evt) => submitRerun(evt).catch(err => {
    document.getElementById("rerunStatus").textContent = err.message;
  }));
  document.getElementById("kpiGrid")?.addEventListener("click", evt => {
    const card = evt.target.closest("[data-metric]");
    if(card?.classList.contains("kpi-card-clickable")) openMetricDrawer(card.dataset.metric);
  });
  document.getElementById("metricDrawerClose")?.addEventListener("click", closeMetricDrawer);
  document.getElementById("metricDrawerBackdrop")?.addEventListener("click", closeMetricDrawer);
  document.addEventListener("keydown", evt => {
    if(evt.key === "Escape") closeMetricDrawer();
  });
  if(runId) load().catch(console.error);
})();
