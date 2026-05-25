/* Generated at: 2026-05-21 22:28:00 MSK */
(function(){
  const meta = (name) => document.querySelector(`meta[name="${name}"]`)?.content || "";
  const apiBase = meta("api-base-url") || "http://localhost:18081";
  const esc = (v) => String(v ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;","'":"&#39;"}[c]));
  fetch(apiBase + "/v1/tariffs").then(r => r.json()).then(data => {
    document.querySelector("#tariffsTable tbody").innerHTML = (data.items || []).map(x => `<tr><td class="mono">${esc(x.preset_key)}</td><td>${esc(x.backend)}</td><td>${esc(x.provider_model)}</td><td>${esc(x.price_per_1k_in)}</td><td>${esc(x.price_per_1k_out)}</td><td>${x.is_quota_equivalent ? "yes" : "no"}</td></tr>`).join("");
  }).catch(console.error);
})();
