/* Generated at: 2026-05-21 23:55:00 MSK */
(function(){
  const meta = (name) => document.querySelector(`meta[name="${name}"]`)?.content || "";
  const apiBase = meta("api-base-url") || "http://localhost:18081";
  const token = meta("api-token");
  const runId = meta("benchmark-run-id");
  const headers = token ? {Authorization: "Bearer " + token} : {};
  const esc = (v) => String(v ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;","'":"&#39;"}[c]));
  const listText = (value) => Array.isArray(value) ? value.join(", ") : String(value ?? "");

  async function load(){
    if(!runId) return;
    const res = await fetch(apiBase + "/v1/benchmarks/runs/" + encodeURIComponent(runId) + "/insights", {headers});
    if(!res.ok) throw new Error(await res.text());
    const data = await res.json();
    renderTopProblems(data.top_prompt_problems || []);
    renderTaxonomy(data.failure_taxonomy || {});
    renderRagGaps(data.rag_gaps || []);
    renderHotspots(data.stage_hotspots || {});
  }

  function renderTopProblems(rows){
    const el = document.getElementById("topProblems");
    if(!el) return;
    if(!rows.length){
      el.textContent = "No prompt problems";
      return;
    }
    el.innerHTML = `<table class="audit-table"><thead><tr><th>area</th><th>severity</th><th>count</th><th>examples</th></tr></thead><tbody>${rows.slice(0,10).map(row => `
      <tr>
        <td>${esc(row.target_area)}</td>
        <td>${esc(row.severity)}</td>
        <td>${esc(row.count)}</td>
        <td>${esc((row.case_id_samples || []).join(", "))}<br><small>${esc((row.representative_titles || []).slice(0,2).join("; "))}</small></td>
      </tr>`).join("")}</tbody></table>`;
  }

  function renderTaxonomy(taxonomy){
    const rows = Object.entries(taxonomy);
    const list = document.getElementById("taxonomyList");
    if(list){
      list.innerHTML = rows.map(([key,val]) => `${esc(key)}: ${esc(val.count)} (${Math.round((val.pct || 0) * 100)}%)`).join("<br>") || "No failures";
    }
    if(window.Plotly && document.getElementById("taxonomyChart")){
      Plotly.newPlot("taxonomyChart", [{
        type:"pie",
        labels:rows.map(x=>x[0]),
        values:rows.map(x=>x[1].count || 0),
        hole:.46,
        textinfo:"percent",
        textfont:{size:11, color:"#0f172a"},
        marker:{line:{color:"#fff", width:1}},
        hovertemplate:"%{label}<br>%{value} cases<br>%{percent}<extra></extra>",
        showlegend:false
      }], {
        height:240,
        margin:{t:8,l:8,r:8,b:8},
        paper_bgcolor:"rgba(0,0,0,0)",
        font:{family:"Inter, system-ui, sans-serif", size:11, color:"#334155"}
      }, {displayModeBar:false, responsive:true});
    }
  }

  function renderRagGaps(rows){
    const el = document.getElementById("ragGaps");
    if(!el) return;
    if(!rows.length){
      el.textContent = "No RAG gaps detected yet";
      return;
    }
    el.innerHTML = `<table class="audit-table"><thead><tr><th>case</th><th>task</th><th>expected/missing</th><th>retrieved</th><th>score</th><th>reason</th></tr></thead><tbody>${rows.slice(0,20).map(row => `
      <tr>
        <td class="mono">${esc(row.case_id)}</td>
        <td>${esc(row.task_text)}</td>
        <td>${esc(listText(row.expected_tables))}<br><small>missing: ${esc(listText(row.missing_tables))}</small></td>
        <td>${esc(listText(row.actually_retrieved))}</td>
        <td>${esc(row.smart_judge_rag_facts_used)}</td>
        <td>${esc(row.gap_reason)}</td>
      </tr>`).join("")}</tbody></table>`;
  }

  function renderHotspots(data){
    const rows = data.by_avg_duration || [];
    const list = document.getElementById("hotspotsList");
    if(list){
      const outliers = data.outliers || [];
      list.innerHTML = rows.slice(0,5).map(row => `${esc(row.stage)}: ${Number(row.avg_ms || 0).toFixed(1)} ms avg`).join("<br>") +
        (outliers.length ? `<br><br><b>Outliers</b><br>${outliers.slice(0,5).map(x => `${esc(x.stage)} ${Number(x.duration_ms || 0).toFixed(1)} ms`).join("<br>")}` : "");
    }
    if(window.Plotly && document.getElementById("hotspotsChart")){
      Plotly.newPlot("hotspotsChart", [{
        type:"bar",
        orientation:"h",
        x:rows.map(x=>x.avg_ms || 0),
        y:rows.map(x=>x.stage),
        marker:{color:"#0f766e"},
        hovertemplate:"%{y}<br>%{x:.1f} ms avg<extra></extra>"
      }], {
        height:240,
        margin:{t:8,l:96,r:12,b:34},
        paper_bgcolor:"rgba(0,0,0,0)",
        plot_bgcolor:"rgba(0,0,0,0)",
        font:{family:"Inter, system-ui, sans-serif", size:11, color:"#334155"},
        xaxis:{gridcolor:"#e8eef8", tickfont:{size:10}},
        yaxis:{tickfont:{size:11}}
      }, {displayModeBar:false, responsive:true});
    }
  }

  load().catch(console.error);
})();
