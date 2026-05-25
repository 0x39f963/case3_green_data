#!/usr/bin/env python3
"""One-off injector: добавляет сквозную навигацию (sidebar + sticky TOC)
во все HTML-файлы команды раунда 2026-05-23 в `.cursor/!tmp/!TZ/2026-05-23/team_tz/`.

Идемпотентно: если файл уже содержит маркер TEAM-TZ-NAV-INJECTED, пропускается.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TEAM_DIR = ROOT / ".cursor" / "!tmp" / "!TZ" / "2026-05-23" / "team_tz"
MARKER = "TEAM-TZ-NAV-INJECTED"

NAV_LINKS: list[tuple[str, str, str]] = [
    ("__group__", "Обзор", ""),
    ("00-team_handoff_map.html", "Карта команды", "обзор"),
    ("__group__", "Марина", ""),
    ("marina-01-ast-compare-golden.html", "01. Сравнение SQL с эталоном", "AST"),
    ("marina-02-ast-pii-masking.html", "02. PII и маскировка", "AST"),
    ("marina-03-ast-forbidden-commands.html", "03. Запрещённые команды", "AST"),
    ("marina-04-golden-safe-rewrite-format.html", "04. Формат эталона Golden", "правила"),
    ("__group__", "Ксения", ""),
    ("ksenia-01-early-algo-filter.html", "01. Каталог запрещенки", "yaml"),
    ("ksenia-02-prompt-injection-research.html", "02. Prompt injection research", "2025-2026"),
    ("ksenia-03-49-classes-decomposition.html", "03. Декомпозиция 49 классов", "группы"),
    ("ksenia-04-security-rag-data.html", "04. RAG-данные security", "FAISS"),
    ("__group__", "Екатерина", ""),
    ("ekaterina-01-tables-upgrade.html", "01. Апгрейд описаний таблиц", "60 таблиц"),
    ("ekaterina-02-golden-dataset-v2.html", "02. Golden Dataset v2", "1000 записей"),
    ("__group__", "Иван", ""),
    ("ivan-01-auditor-decomposition.html", "01. Декомпозиция аудитора", "ансамбль"),
    ("ivan-02-integrate-marina-pglast.html", "02. Интеграция pglast Марины", "pipeline"),
    ("ivan-03-integrate-ksenia-rag-judge.html", "03. RAG Ксении + judge", "v5"),
    ("ivan-04-golden-csv-to-jsonl-converter.html", "04. CSV → JSONL конвертер", "eval"),
]


def build_sidebar_html(current_file: str) -> str:
    rows: list[str] = []
    for href, label, hint in NAV_LINKS:
        if href == "__group__":
            rows.append(f'<div class="tz-group">{label}</div>')
            continue
        cls = "tz-link active" if href == current_file else "tz-link"
        hint_html = f'<span class="tz-hint">{hint}</span>' if hint else ""
        rows.append(
            f'<a class="{cls}" href="{href}" data-id="{href}">'
            f'<span class="tz-label">{label}</span>{hint_html}</a>'
        )
    body = "\n  ".join(rows)
    return (
        f'<aside id="tz-sidebar" aria-label="Все ТЗ команды">\n'
        f'  <div class="tz-side-head">Раунд 23 мая 2026</div>\n'
        f'  <div class="tz-side-sub">SQL Security Pipeline</div>\n'
        f'  {body}\n'
        f'</aside>'
    )


CSS_BLOCK = """
<style id="team-tz-nav-css">
/* --- TEAM-TZ-NAV-INJECTED --- */
body { max-width: none !important; margin: 0 !important; padding: 0 !important;
  display: grid; grid-template-columns: 240px minmax(0, 1fr) 230px; gap: 0;
  min-height: 100vh; background: #f4f6fa; }
#tz-sidebar { position: sticky; top: 0; align-self: start;
  height: 100vh; overflow-y: auto; box-sizing: border-box;
  padding: 18px 14px; background: #fff; border-right: 1px solid #d8dde6;
  font-size: 13px; }
.tz-side-head { font-weight: 700; color: #2e3a59; font-size: 14px; }
.tz-side-sub { color: #666; font-size: 12px; margin-bottom: 16px; }
.tz-group { color: #6FB1FF; font-weight: 600; text-transform: uppercase;
  letter-spacing: 0.05em; font-size: 11px; margin: 14px 0 6px 4px; }
.tz-link { display: block; padding: 6px 10px; color: #1a1a1a; border-radius: 4px;
  text-decoration: none; margin-bottom: 2px; }
.tz-link:hover { background: #eef2f8; }
.tz-link.active { background: #2e3a59; color: #fff; }
.tz-link.active:hover { background: #2e3a59; }
.tz-label { display: block; }
.tz-hint { display: block; font-size: 11px; color: #888; margin-top: 2px; }
.tz-link.active .tz-hint { color: #c5d0e6; }
main#tz-content { background: #fff; box-sizing: border-box;
  padding: 32px 40px 80px; max-width: 980px; margin: 0 auto; width: 100%; }
#tz-toc { position: sticky; top: 0; align-self: start;
  height: 100vh; overflow-y: auto; box-sizing: border-box;
  padding: 18px 14px; font-size: 13px; border-left: 1px solid #d8dde6;
  background: #f9fafd; }
.tz-toc-title { font-weight: 700; color: #2e3a59; font-size: 12px;
  text-transform: uppercase; letter-spacing: 0.05em; margin-bottom: 8px; }
#tz-toc ul { list-style: none; padding-left: 0; margin: 0; }
#tz-toc li { margin-bottom: 4px; }
#tz-toc li.lvl-3 { padding-left: 14px; }
#tz-toc a { color: #444; text-decoration: none; display: block;
  padding: 4px 6px; border-left: 2px solid transparent; border-radius: 0;
  font-size: 12px; line-height: 1.35; }
#tz-toc a:hover { color: #2862c4; border-left-color: #6FB1FF; background: #eef2f8; }
#tz-toc a.tz-active { color: #2e3a59; border-left-color: #2e3a59;
  background: #eef2f8; font-weight: 600; }
main#tz-content h1, main#tz-content h2, main#tz-content h3 { scroll-margin-top: 18px; }
@media (max-width: 1200px) {
  body { display: block; }
  #tz-sidebar, #tz-toc { position: static; height: auto; max-height: 240px;
    border: 0; border-bottom: 1px solid #d8dde6; }
  main#tz-content { max-width: none; padding: 24px 20px 60px; }
}
</style>
""".strip()

TOC_HTML = """
<aside id="tz-toc" aria-label="Оглавление страницы">
  <div class="tz-toc-title">На этой странице</div>
  <ul></ul>
</aside>
""".strip()

JS_BLOCK = """
<script>
(function(){
  function slugify(s){return s.toLowerCase().normalize('NFKD').replace(/[^\\w\\s-]/g,'').trim().replace(/\\s+/g,'-').slice(0,80);}
  var content = document.getElementById('tz-content'); if (!content) return;
  var hs = content.querySelectorAll('h2, h3');
  if (hs.length === 0) return;
  var toc = document.querySelector('#tz-toc ul'); if (!toc) return;
  var seen = {};
  var items = [];
  hs.forEach(function(h){
    if (!h.id){
      var s = slugify(h.textContent) || 'sec';
      var base = s; var i = 1; while (seen[s]) { s = base + '-' + (++i); } seen[s] = 1; h.id = s;
    }
    var li = document.createElement('li');
    li.className = 'lvl-' + (h.tagName === 'H2' ? '2' : '3');
    var a = document.createElement('a');
    a.href = '#' + h.id; a.textContent = h.textContent; li.appendChild(a);
    toc.appendChild(li);
    items.push({h: h, a: a});
  });
  if ('IntersectionObserver' in window) {
    var io = new IntersectionObserver(function(entries){
      entries.forEach(function(e){
        var match = items.find(function(it){return it.h === e.target;});
        if (!match) return;
        if (e.isIntersecting) {
          items.forEach(function(it){ it.a.classList.remove('tz-active'); });
          match.a.classList.add('tz-active');
        }
      });
    }, {rootMargin: '0px 0px -70% 0px', threshold: 0});
    items.forEach(function(it){io.observe(it.h);});
  }
})();
</script>
""".strip()


def inject_one(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    if MARKER in text:
        return "skip (already injected)"

    # 1. Add CSS block before </head>
    if "</head>" not in text:
        return "skip (no </head>)"
    text = text.replace("</head>", CSS_BLOCK + "\n</head>", 1)

    # 2. Build sidebar for current file
    sidebar = build_sidebar_html(path.name)

    # 3. Wrap existing body content in main + add sidebar + toc
    body_open = re.search(r"<body[^>]*>", text)
    body_close = "</body>"
    if not body_open or body_close not in text:
        return "skip (no body tags)"

    before_body = text[: body_open.end()]
    inner = text[body_open.end():text.index(body_close)]
    after_body = text[text.index(body_close):]

    new_inner = (
        f"\n{sidebar}\n"
        f'<main id="tz-content">\n'
        f"{inner.strip()}\n"
        f"</main>\n"
        f"{TOC_HTML}\n"
        f"{JS_BLOCK}\n"
    )
    return _write(path, before_body + new_inner + after_body)


def _write(path: Path, text: str) -> str:
    path.write_text(text, encoding="utf-8")
    return f"injected ({len(text)} bytes)"


def main() -> int:
    if not TEAM_DIR.is_dir():
        print(f"team_tz dir not found: {TEAM_DIR}", file=sys.stderr)
        return 1
    files = sorted(TEAM_DIR.glob("*.html"))
    if not files:
        print("no html files", file=sys.stderr)
        return 1
    for f in files:
        result = inject_one(f)
        print(f"  {f.name:55s} -- {result}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
