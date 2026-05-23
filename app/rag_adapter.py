"""
Тонкая обертка над поисковой памятью Марины и schema overlay.

Дает оркестратору функции с кешем и обрезанием по бюджету токенов.
Marina `schema.json` остается источником таблиц и колонок, а
`deploy/schema_overlay.json` добавляет бизнес-описания, aliases,
PII-теги и read-only policy. Если overlay отсутствует, слой работает
как раньше и возвращает только контекст Марины.
"""

from __future__ import annotations

import csv
import json
import os
import re
import sys
import time
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

from jsonschema import Draft202012Validator

# Кладем код Марины в импортный путь без копирования. Это нужно когда
# скрипт запускается из репозитория напрямую, а не из собранного образа.
_MARINA_ROOT = Path(__file__).resolve().parent.parent / "TASK-3" / "marina-case3-rag"
_REPO_ROOT = Path(__file__).resolve().parent.parent
_SCHEMA_PATH = _MARINA_ROOT / "schema.json"
_OVERLAY_PATH = _REPO_ROOT / "deploy" / "schema_overlay.json"
_OVERLAY_SCHEMA_PATH = _REPO_ROOT / "deploy" / "schema_overlay.schema.json"
if str(_MARINA_ROOT) not in sys.path:
    sys.path.insert(0, str(_MARINA_ROOT))

# Импортируем уже после правки пути.
from rag_pipeline import rag_tools  # noqa: E402

# Сколько символов контекста показываем модели за один вызов. Для
# маленьких моделей даже несколько тысяч уже много, поэтому режем.
GENERATION_MAX_CHARS = 6000
SECURITY_MAX_CHARS = 4000
HIT_TEXT_MAX_CHARS = 5000


# RAG bridge-table filter ────────────────────────────────────────────
# Legacy FAISS-индекс Marina содержит 13 ms_* bridge-таблиц (один
# объект → один выбранный multiselect-вариант). На NL-запросы про
# сущность ("Выбери все заявки") FAISS вытаскивает такие bridge'и
# по совпадению "Заявка" в source_comment, и генератор пишет
# `SELECT id, obj_id FROM ms_<hash>` вместо настоящей таблицы заявок.
# Phase 6 / 2026-05-21: фильтруем bridge_multiselect по entity_role
# из `data/rag/v2/table_knowledge_index_v2.csv`. Канонические
# application_fact, contract_fact и т.д. остаются.

_BRIDGE_BLACKLIST_DEFAULT = ("bridge_multiselect",)
_TABLE_KNOWLEDGE_CSV_PATH = _REPO_ROOT / "data" / "rag" / "v2" / "table_knowledge_index_v2.csv"
_ENTITY_ROLE_MAP_CACHE: dict[str, str] | None = None


def _entity_role_map() -> dict[str, str]:
    """Lazy-loaded {table_name: entity_role} mapping from the v2 CSV index."""
    global _ENTITY_ROLE_MAP_CACHE
    if _ENTITY_ROLE_MAP_CACHE is not None:
        return _ENTITY_ROLE_MAP_CACHE
    mapping: dict[str, str] = {}
    if not _TABLE_KNOWLEDGE_CSV_PATH.exists():
        _ENTITY_ROLE_MAP_CACHE = mapping
        return mapping
    try:
        with _TABLE_KNOWLEDGE_CSV_PATH.open(encoding="utf-8", newline="") as fh:
            next(fh, None)  # row 1 is `Generated at:` metadata header
            reader = csv.DictReader(fh)
            for row in reader:
                name = (row.get("table_name") or "").strip()
                role = (row.get("entity_role") or "").strip()
                if name:
                    mapping[name] = role
    except OSError:
        mapping = {}
    _ENTITY_ROLE_MAP_CACHE = mapping
    return mapping


def _bridge_blacklist() -> set[str]:
    """Roles excluded from top-K retrieval. Overridable via env."""
    raw = os.environ.get("RAG_BRIDGE_BLACKLIST_ROLES", "").strip()
    if raw:
        return {item.strip() for item in raw.split(",") if item.strip()}
    return set(_BRIDGE_BLACKLIST_DEFAULT)


def is_bridge_table(table_name: str) -> bool:
    """True if the table's entity_role is in the bridge blacklist."""
    name = (table_name or "").strip()
    if not name:
        return False
    role = _entity_role_map().get(name, "")
    return role in _bridge_blacklist()


_BRIDGE_BLOCK_HEADER_RE = re.compile(r"(?m)^Таблица:\s*([^\s]+)")


def strip_bridge_table_blocks(raw: str) -> str:
    """Drop legacy FAISS text blocks that describe a bridge_multiselect table.

    Marina форматирует каждую таблицу как блок, начинающийся со строки
    `Таблица: <name>`. Ищем эти заголовки, для bridge-таблиц вырезаем
    блок целиком до следующего заголовка или конца строки. Если
    разметка не распознана — возвращаем raw без изменений.
    """
    if not raw or "Таблица:" not in raw:
        return raw
    headers = list(_BRIDGE_BLOCK_HEADER_RE.finditer(raw))
    if not headers:
        return raw
    out_parts: list[str] = []
    prefix_end = headers[0].start()
    if prefix_end > 0:
        out_parts.append(raw[:prefix_end])
    for idx, match in enumerate(headers):
        start = match.start()
        end = headers[idx + 1].start() if idx + 1 < len(headers) else len(raw)
        if is_bridge_table(match.group(1)):
            continue
        out_parts.append(raw[start:end])
    return "".join(out_parts)


def _filter_bridge_hits(hits: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Skip hits whose table_name is a known bridge_multiselect."""
    return [hit for hit in hits if not is_bridge_table(str(hit.get("table_name", "")))]


def _entity_role_map_reset_for_tests() -> None:
    """Invalidate the in-process cache (test-only helper)."""
    global _ENTITY_ROLE_MAP_CACHE
    _ENTITY_ROLE_MAP_CACHE = None


# Phase 0.4 — sub-timing для RAG. Кешированные функции ниже вызываются
# через эти thin-обёртки, которые меряют общий elapsed и сравнивают
# CacheInfo до и после, чтобы понять был ли это cache hit (горячий)
# или cache miss (cold start, реальный encode+FAISS). Orchestrator
# кладёт результат в trace.event.details.rag_timings, отчёт умеет это
# показать в блоке «Где ушло время».


def _timed_cached_call(fn: Callable[..., Any], *args: Any, **kwargs: Any) -> tuple[Any, dict[str, Any]]:
    """
    Замерить вызов lru_cache-функции. Возвращает (result, timing).
    timing.cache_hit определяется через CacheInfo.hits до/после.
    """
    cache_info = getattr(fn, "cache_info", None)
    hits_before = cache_info().hits if cache_info else 0
    t0 = time.perf_counter()
    result = fn(*args, **kwargs)
    elapsed = time.perf_counter() - t0
    cache_hit = False
    if cache_info:
        cache_hit = cache_info().hits > hits_before
    return result, {
        "elapsed_sec": round(elapsed, 4),
        "cache_hit": cache_hit,
        "fn": getattr(fn, "__name__", str(fn)),
    }


def _shrink(text: str, limit: int) -> str:
    """
    Аккуратно обрезать длинный контекст, чтобы он влез в окно маленькой
    модели. Режем по последнему абзацу - резкий обрыв на середине строки
    хуже воспринимается моделью, чем потеря последнего блока.
    """
    if limit <= 0:
        return ""
    if len(text) <= limit:
        return text
    marker = "\n\n[контекст сокращен для бюджета токенов]"
    if limit <= len(marker):
        return text[:limit]
    cut = text[: limit - len(marker)]
    last_break = cut.rfind("\n\n")
    if last_break > limit // 2:
        cut = cut[:last_break]
    return cut + marker


@lru_cache(maxsize=1)
def _load_schema() -> dict[str, Any]:
    """Прочитать schema.json Марины как источник таблиц и колонок."""
    try:
        return json.loads(_SCHEMA_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"tables": {}, "sensitive_fields_summary": {}}


@lru_cache(maxsize=1)
def _load_overlay() -> dict[str, Any]:
    """
    Прочитать business overlay и проверить JSON schema.

    Отсутствующий overlay не считается ошибкой: B2 smoke и старые
    окружения продолжают работать с Marina-only контекстом.
    """
    if not _OVERLAY_PATH.exists():
        return {}
    data = json.loads(_OVERLAY_PATH.read_text(encoding="utf-8"))
    if _OVERLAY_SCHEMA_PATH.exists():
        schema = json.loads(_OVERLAY_SCHEMA_PATH.read_text(encoding="utf-8"))
        Draft202012Validator(schema).validate(data)
    return data


def _join_generation_context(raw: str, overlay_text: str, v2_text: str) -> tuple[str, dict[str, int]]:
    """
    Собрать prompt context с защищенным v2-бюджетом.

    Порядок важен: table_knowledge_v2 и overlay идут раньше legacy FAISS.
    Если бюджет кончился, режется хвост, то есть старый FAISS, а не v2.
    """
    items: list[tuple[str, str]] = []
    if v2_text:
        items.append(("table_knowledge_v2", v2_text))
    if overlay_text:
        items.append(("schema_overlay", "=== BUSINESS OVERLAY ===\n" + overlay_text))
    if raw:
        items.append(("legacy_faiss", raw))

    parts: list[str] = []
    source_chars = {
        "table_knowledge_v2": 0,
        "schema_overlay": 0,
        "legacy_faiss": 0,
    }
    used = 0
    for key, text in items:
        sep = "\n\n" if parts else ""
        budget = GENERATION_MAX_CHARS - used - len(sep)
        if budget <= 0:
            continue
        chunk = _shrink(text, budget)
        if not chunk:
            continue
        if sep:
            parts.append(sep)
            used += len(sep)
        parts.append(chunk)
        used += len(chunk)
        source_chars[key] = len(chunk)
    return "".join(parts), source_chars


@lru_cache(maxsize=128)
def get_generation_context_bundle(task: str) -> dict[str, Any]:
    """
    Контекст для генератора + диагностика источников.

    Один и тот же текст задачи будет давать один и тот же результат -
    поэтому кеш по строке безопасен. Если оркестратор позовет нас второй
    раз внутри одной итерации, дорого работать не придется.
    """
    raw = rag_tools.get_generation_context(task)
    raw = strip_bridge_table_blocks(raw)
    v2_text, v2_meta = get_table_knowledge_v2_context_with_meta(task)
    link_seed = "\n\n".join(item for item in (v2_text, raw) if item)
    link = schema_link(task, link_seed)
    overlay_text = _format_overlay_blocks(link["allowed_tables"])
    context, source_chars = _join_generation_context(raw, overlay_text, v2_text)

    v2_enabled = bool(v2_meta.get("enabled"))
    v2_has_hits = int(v2_meta.get("hit_count") or 0) > 0
    fallback_used = bool(v2_enabled and not v2_has_hits and raw)
    if fallback_used:
        v2_meta["fallback_used"] = True
        if not v2_meta.get("fallback_reason"):
            v2_meta["fallback_reason"] = "no_v2_hits"
    v2_meta["context_chars"] = source_chars["table_knowledge_v2"]

    overlay_tables = [
        name
        for name in link["allowed_tables"]
        if (_load_overlay().get("tables") or {}).get(_base_name(name))
    ]
    return {
        "context": context,
        "rag_sources": {
            "table_knowledge_v2": v2_meta,
            "legacy_faiss": {
                "used": bool(raw),
                "hit_count": None,
                "role": "pg_patterns_docs_or_fallback" if fallback_used else "pg_patterns_docs",
                "context_chars": source_chars["legacy_faiss"],
                "fallback_for_table_knowledge_v2": fallback_used,
            },
            "schema_overlay": {
                "used": bool(overlay_text),
                "table_count": len(overlay_tables),
                "context_chars": source_chars["schema_overlay"],
            },
        },
        "source_context_chars": source_chars,
        "table_knowledge_v2_context": v2_text,
        "schema_overlay_context": overlay_text,
        "legacy_faiss_context": raw,
    }


def get_generation_context(task: str) -> str:
    """Контекст для генератора: v2 business tables, overlay и legacy FAISS."""
    return str(get_generation_context_bundle(task).get("context") or "")


get_generation_context.cache_clear = get_generation_context_bundle.cache_clear  # type: ignore[attr-defined]
get_generation_context.cache_info = get_generation_context_bundle.cache_info  # type: ignore[attr-defined]
get_generation_context.cache_parameters = get_generation_context_bundle.cache_parameters  # type: ignore[attr-defined]


@lru_cache(maxsize=128)
def get_generation_hits(task: str) -> list[dict[str, Any]]:
    """Вернуть документы, выбранные FAISS generation index для задачи."""
    index, metadata = rag_tools._load_generation_index()
    top_k = int(rag_tools.DEFAULT_TOP_K_GENERATION)
    # Запрашиваем с запасом, потому что bridge_multiselect-таблицы будут
    # отфильтрованы и могут оставить меньше top_k реальных хитов.
    results = rag_tools._search(task, index, metadata, max(top_k * 2, top_k + 8))
    hits = [_clean_hit(item) for item in results]
    return _filter_bridge_hits(hits)[:top_k]


def filter_generation_hits_by_scope(
    hits: list[dict[str, Any]],
    allowed_tables: list[str] | None,
    allowed_columns: dict[str, list[str]] | None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """TZ-7 phase 1: убрать RAG-паттерны, чьи schema_scope/pii_columns_used
    выходят за пределы allowed_tables/allowed_columns.

    Возвращает (отфильтрованные_хиты, статистика). Статистика
    {"dropped_by_table": N, "dropped_by_pii_column": N, "kept": N}
    идёт в trace как RAG meta — видно сколько кандидатов отвалилось и почему.
    """
    if not hits:
        return [], {"dropped_by_table": 0, "dropped_by_pii_column": 0, "kept": 0}
    allowed_t = {_base_name(str(t).lower()) for t in (allowed_tables or [])}
    allowed_c: set[str] = set()
    for cols in (allowed_columns or {}).values():
        for c in cols:
            allowed_c.add(str(c).lower())
    kept: list[dict[str, Any]] = []
    dropped_t = 0
    dropped_c = 0
    for hit in hits:
        md = hit.get("metadata") or {}
        scope = [str(t).lower() for t in (md.get("schema_scope") or [])]
        pii_used = [str(c).lower() for c in (md.get("pii_columns_used") or [])]
        if allowed_t and scope:
            out_of_scope = [t for t in scope if t not in allowed_t]
            if out_of_scope:
                dropped_t += 1
                continue
        if allowed_c and pii_used:
            out_of_columns = [c for c in pii_used if c not in allowed_c]
            if out_of_columns:
                dropped_c += 1
                continue
        kept.append(hit)
    return kept, {
        "dropped_by_table": dropped_t,
        "dropped_by_pii_column": dropped_c,
        "kept": len(kept),
    }


@lru_cache(maxsize=128)
def get_security_context(sql: str) -> str:
    """
    Контекст для аудитора: классы уязвимостей, релевантные конкретному SQL.

    Кешируем по тексту SQL. Это полезно если за одну итерацию аудитор
    зовется несколько раз - например, при гибридной проверке отдельно
    по правилам и отдельно языковой моделью.
    """
    raw = rag_tools.get_security_context(sql)
    return _shrink(raw, SECURITY_MAX_CHARS)


@lru_cache(maxsize=128)
def get_security_hits(sql: str) -> list[dict[str, Any]]:
    """Вернуть документы, выбранные FAISS security index для SQL."""
    index, metadata = rag_tools._load_security_index()
    results = rag_tools._search(
        sql,
        index,
        metadata,
        rag_tools.DEFAULT_TOP_K_SECURITY * 2,
    )
    return [_clean_hit(item) for item in results]


def get_table_context(table_names: list[str]) -> str:
    """
    Прямые описания таблиц по именам, без семантического поиска.
    Удобно когда аудитор уже видит имена в SQL и хочет проверить,
    что у этих таблиц действительно есть упомянутые колонки.
    """
    if not table_names:
        return ""
    base = rag_tools.get_table_context(table_names)
    overlay = _format_overlay_blocks(table_names)
    if overlay:
        base = base + "\n\n=== BUSINESS OVERLAY ===\n" + overlay
    return base.strip()


@lru_cache(maxsize=1)
def get_sensitive_fields() -> dict[str, list[str]]:
    """
    Словарь чувствительных полей по всей схеме (3 слоя union).

    Кешируем на процесс - его читает кросс-чек DIRECT_SENSITIVE в guard.

    Слои (в порядке наложения, последний выигрывает):
    1. marina rag_tools.get_sensitive_fields() — встроенная разметка
       Marina schema.json (схема.sensitive_fields_summary).
    2. deploy/schema_overlay.json.tables[t].pii_tags — ручная разметка
       бизнес-овнером.
    3. Phase 4.3 app/sensitive_detector — regex по именам колонок
       (email/phone/inn/passport/credit/sur_name/birth/sf_/address).
       Подключается флагом SENSITIVE_AUTO_DETECT (default 'true').

    Слой 3 добавляет автоматически найденные колонки, которые слои 1-2
    могли пропустить. Overlay при этом «побеждает» — если бизнес-овнер
    явно сказал что таблица НЕ sensitive (пустой pii_tags), auto-слой
    подавляется через sensitive_detector.merge_with_overlay(overlay_wins=True).

    Известное ограничение: проверка ловит прямой доступ типа SELECT email
    FROM sys_employee. Чувствительные данные, скрытые за view, CTE, alias
    или функцией, в текущем MVP не отслеживаются - это задача для B3 с
    разбором происхождения колонок через AST.
    """
    import os as _os
    data: dict[str, list[str]] = {
        str(table): [str(col) for col in cols]
        for table, cols in rag_tools.get_sensitive_fields().items()
    }
    overlay = _load_overlay()
    overlay_tables = overlay.get("tables") or {}
    for table, item in overlay_tables.items():
        tags = item.get("pii_tags") or {}
        if not tags:
            continue
        merged = set(data.get(str(table), []))
        merged.update(str(col) for col in tags)
        data[str(table)] = sorted(merged)

    # Phase 4.3 — auto-detect поверх. Регекс находит новые колонки в
    # marina schema.json которые слои 1-2 не упомянули. overlay_wins=True
    # значит: если overlay явно скинул pii_tags таблицы в {} —
    # auto-слой её не вернёт, но marina+explicit overlay остаются.
    if _os.environ.get("SENSITIVE_AUTO_DETECT", "true").strip().lower() in {"1", "true", "yes", "on"}:
        from app import sensitive_detector
        schema_tables = (_load_schema() or {}).get("tables") or {}
        auto_only = sensitive_detector.detect_from_schema(schema_tables)
        # Снимок состояния marina+explicit overlay ДО auto-слоя. Нужен,
        # чтобы для overlay с пустым pii_tags откатить только auto-колонки,
        # а явные marina/overlay поля оставить (иначе данные Marina теряются).
        pre_auto: dict[str, list[str]] = {table: list(cols) for table, cols in data.items()}
        for table, auto_cols in auto_only.items():
            current = set(data.get(table, []))
            current.update(auto_cols)
            data[table] = sorted(current)
        for table, item in overlay_tables.items():
            tags = item.get("pii_tags") or {}
            if tags:
                continue
            key = str(table)
            previous = pre_auto.get(key)
            if previous:
                data[key] = sorted(previous)
            else:
                data.pop(key, None)
    return data


def get_table_policy(table_name: str) -> dict[str, Any]:
    """
    Вернуть policy для таблицы: allowed_ops, denied_ops и pii_tags.

    Policy строится как глобальный default из overlay плюс per-table
    override. Если overlay отсутствует или таблица не описана,
    действует read-only default: allowed_ops=["SELECT"].
    """
    overlay = _load_overlay()
    global_cfg = overlay.get("global") or {}
    default_allowed = global_cfg.get("default_allowed_ops") or ["SELECT"]
    tables = overlay.get("tables") or {}
    item = tables.get(_base_name(table_name), {})
    return {
        "allowed_ops": [str(op).upper() for op in item.get("allowed_ops", default_allowed)],
        "denied_ops": [str(op).upper() for op in item.get("denied_ops", [])],
        "pii_tags": dict(item.get("pii_tags") or {}),
    }


def schema_link(task: str, context: str) -> dict[str, Any]:
    """
    Выделить allowed_tables и allowed_columns для генератора.

    Сначала берем таблицы, которые уже попали в RAG-context. Затем
    добавляем точные lexical hits по имени таблицы и aliases из overlay.
    Так запрос про сотрудников и подразделения получает `sys_employee`
    и `offices_psb`, даже если семантический индекс выбрал соседний
    офисный справочник.
    """
    schema = _load_schema()
    tables = schema.get("tables") or {}
    names = set(tables)

    selected: list[str] = []
    for name in _tables_from_context(context):
        if name in names and name not in selected:
            selected.append(name)

    lower_task = task.lower()
    overlay_tables = (_load_overlay().get("tables") or {})
    for name in names:
        item = overlay_tables.get(name) or {}
        aliases = [str(alias).lower() for alias in item.get("aliases", [])]
        tokens = [name.lower(), name.lower().replace("_", " ")] + aliases
        if any(token and token in lower_task for token in tokens):
            if name not in selected:
                selected.append(name)

    allowed_columns = {
        name: list((tables.get(name) or {}).get("columns") or {})
        for name in selected
    }
    return {
        "allowed_tables": selected,
        "allowed_columns": allowed_columns,
        "allowed_objects": format_allowed_objects(allowed_columns),
    }


def format_allowed_objects(allowed_columns: dict[str, list[str]]) -> str:
    """Собрать компактный текст разрешенных таблиц и колонок для prompt."""
    if not allowed_columns:
        return "Разрешенные таблицы и колонки не определены."
    lines = ["Разрешенные таблицы и колонки (используй только их):"]
    for table, cols in sorted(allowed_columns.items()):
        shown = cols[:40]
        suffix = "" if len(cols) <= 40 else ", ..."
        lines.append("- " + table + ": " + ", ".join(shown) + suffix)
    return "\n".join(lines)


def get_schema_tables() -> dict[str, list[str]]:
    """Вернуть все таблицы schema.json и их колонки."""
    tables = _load_schema().get("tables") or {}
    return {
        str(name): list((meta.get("columns") or {}).keys())
        for name, meta in tables.items()
        if isinstance(meta, dict)
    }


def format_sensitive_fields(data: dict[str, list[str]]) -> str:
    """
    Превратить словарь чувствительных полей в компактный текст.

    Используется промптом аудитора, чтобы передать модели сводку без
    раздувания токенов. Сортируем таблицы и колонки, пустые таблицы
    пропускаем. Если данных нет вовсе - возвращаем явный маркер.
    """
    if not data:
        return "Чувствительные поля не описаны."
    lines = []
    for table, cols in sorted(data.items()):
        if cols:
            lines.append(table + ": " + ", ".join(sorted(cols)))
    return "\n".join(lines)


def _format_overlay_blocks(table_names: list[str]) -> str:
    overlay = _load_overlay()
    tables = overlay.get("tables") or {}
    parts: list[str] = []
    for name in table_names:
        item = tables.get(_base_name(name))
        if not item:
            continue
        parts.append(
            "Таблица: " + _base_name(name) + "\n"
            + "Бизнес-описание: " + str(item.get("business_description", "")) + "\n"
            + "Aliases: " + ", ".join(item.get("aliases") or []) + "\n"
            + "Cardinality: " + str(item.get("cardinality_hint", "unknown")) + "\n"
            + "Allowed ops: " + ", ".join(item.get("allowed_ops") or ["SELECT"]) + "\n"
            + "PII tags: " + json.dumps(item.get("pii_tags") or {}, ensure_ascii=False)
        )
    return "\n\n".join(parts)


def _clean_hit(item: dict[str, Any]) -> dict[str, Any]:
    text = str(item.get("text", ""))
    out: dict[str, Any] = {
        "score": round(float(item.get("score", 0.0)), 4),
        "source": str(item.get("source", "")),
        "text": _shrink(text, HIT_TEXT_MAX_CHARS),
    }
    for key in (
        "table_name",
        "pattern_id",
        "pattern_type",
        "description",
        "section_key",
        "topic",
        "heading",
        "vuln_class",
        "name",
        "risk_score",
        "recommendation",
    ):
        if key in item:
            out[key] = item[key]
    return out


def _tables_from_context(context: str) -> list[str]:
    names: list[str] = []
    pattern = re.compile(r"(?m)^Таблица:\s*([A-Za-z_][\w]*)|^-\s*([A-Za-z_][\w]*)\s*\(")
    for match in pattern.finditer(context):
        name = match.group(1) or match.group(2)
        if name not in names:
            names.append(name)
    return names


def _base_name(name: str) -> str:
    return str(name).rsplit(".", 1)[-1].strip('"').lower()


# Phase 2 — solutions index ───────────────────────────────────────────────────
# Petля обучения: мета-аудитор разбирает прошлые прогоны и пишет урок в
# benchmark.rag_embeddings(index_name='solutions'). Здесь — клиент-сторона
# извлечения уроков под задачу. Без сети: прямой Postgres-запрос + numpy cosine.

SOLUTIONS_TOP_K = 3
SOLUTIONS_MIN_SIMILARITY = 0.55
SOLUTIONS_MAX_CHARS = 1800


def _bench_dsn_for_solutions() -> str:
    """DSN benchmark Postgres. Тот же путь, что в app/meta_auditor.py."""
    import os as _os
    dsn = _os.environ.get("BENCHMARK_DSN", "").strip()
    if dsn:
        return dsn
    user = _os.environ.get("BENCH_USER", "bench")
    password = _os.environ.get("BENCH_PASSWORD", "bench")
    host = _os.environ.get("BENCH_PG_HOST", "127.0.0.1")
    port = _os.environ.get("BENCH_PG_PORT", "15432")
    db = _os.environ.get("BENCH_DB", "bench")
    return "postgresql://" + user + ":" + password + "@" + host + ":" + port + "/" + db


def _bench_dsn_meta(index_name: str, enabled: bool = True) -> dict[str, Any]:
    dsn = _bench_dsn_for_solutions()
    try:
        parsed = urlparse(dsn)
        host = parsed.hostname or ""
        port = parsed.port
        db = parsed.path.lstrip("/")
        user = parsed.username or ""
    except ValueError:
        host = ""
        port = None
        db = ""
        user = ""
    return {
        "enabled": enabled,
        "dsn_configured": bool(os.environ.get("BENCHMARK_DSN", "").strip()),
        "dsn_host": host,
        "dsn_port": port,
        "dsn_database": db,
        "dsn_user": user,
        "index_name": index_name,
        "hit_count": 0,
        "context_chars": 0,
        "error": None,
        "fallback_used": False,
        "fallback_reason": None,
        "top_hits": [],
        "top_candidates": [],
    }


def _safe_error(exc: BaseException) -> str:
    text = exc.__class__.__name__ + ": " + str(exc)
    try:
        password = urlparse(_bench_dsn_for_solutions()).password
    except ValueError:
        password = None
    if password:
        text = text.replace(password, "***")
    return text[:700]


def _flag_true(name: str, default: str = "false") -> bool:
    return os.environ.get(name, default).strip().lower() in {"1", "true", "yes", "on"}


@lru_cache(maxsize=1)
def _solutions_embedder() -> Any:
    """multilingual-e5-small singleton под query-эмбеддинги уроков."""
    from sentence_transformers import SentenceTransformer
    return SentenceTransformer("intfloat/multilingual-e5-small")


def _solutions_search_with_meta(task: str, top_k: int = SOLUTIONS_TOP_K) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """
    Cosine-поиск по benchmark.rag_embeddings(index_name='solutions').

    Возвращает top_k записей с similarity >= SOLUTIONS_MIN_SIMILARITY.
    На пустой индекс / отсутствие БД — пустой список (без падения /run).
    """
    import numpy as np
    meta = _bench_dsn_meta("solutions", enabled=True)
    try:
        import psycopg2
    except ImportError as exc:
        meta["error"] = _safe_error(exc)
        meta["fallback_reason"] = "psycopg2_import_error"
        return [], meta

    try:
        with psycopg2.connect(_bench_dsn_for_solutions()) as conn:
            conn.set_session(readonly=True)
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id, text, metadata, embedding, source_trace_id, created_at
                    FROM benchmark.rag_embeddings
                    WHERE index_name = 'solutions'
                    """
                )
                rows = cur.fetchall()
    except psycopg2.Error as exc:
        meta["error"] = _safe_error(exc)
        meta["fallback_used"] = True
        meta["fallback_reason"] = "db_error"
        return [], meta
    meta["row_count"] = len(rows)
    if not rows:
        meta["fallback_reason"] = "empty_index"
        return [], meta

    try:
        embedder = _solutions_embedder()
        query_vec = embedder.encode(
            ["query: " + (task or "")],
            normalize_embeddings=True,
        )[0]
    except Exception as exc:
        meta["error"] = _safe_error(exc)
        meta["fallback_used"] = True
        meta["fallback_reason"] = "embedder_error"
        return [], meta

    try:
        matrix = np.array([row[3] for row in rows], dtype="float32")
        q = np.array(query_vec, dtype="float32")
        norm = np.linalg.norm(q)
        if norm < 1e-9:
            meta["fallback_reason"] = "empty_query_vector"
            return [], meta
        q = q / norm
        # embedding из БД уже normalize при INSERT через encode(normalize_embeddings=True)
        scores = matrix @ q
    except Exception as exc:
        meta["error"] = _safe_error(exc)
        meta["fallback_used"] = True
        meta["fallback_reason"] = "scoring_error"
        return [], meta

    order = np.argsort(scores)[::-1][: top_k * 2]
    results: list[dict[str, Any]] = []
    for idx in order:
        score = float(scores[idx])
        meta["top_candidates"].append({"id": int(rows[idx][0]), "score": round(score, 4)})
        if score < SOLUTIONS_MIN_SIMILARITY:
            continue
        row_meta = rows[idx][2] or {}
        results.append(
            {
                "id": int(rows[idx][0]),
                "text": rows[idx][1],
                "metadata": row_meta if isinstance(row_meta, dict) else {},
                "source_trace_id": rows[idx][4],
                "created_at": rows[idx][5],
                "score": round(score, 4),
            }
        )
        if len(results) >= top_k:
            break
    meta["hit_count"] = len(results)
    meta["top_hits"] = [
        {"id": int(hit["id"]), "score": hit["score"], "source_trace_id": hit.get("source_trace_id")}
        for hit in results[:top_k]
    ]
    if not results:
        meta["fallback_reason"] = "below_threshold"
    return results, meta


def _solutions_search(task: str, top_k: int = SOLUTIONS_TOP_K) -> list[dict[str, Any]]:
    hits, _meta = _solutions_search_with_meta(task, top_k=top_k)
    return hits


def get_solutions_context_with_meta(
    task: str,
    top_k: int = SOLUTIONS_TOP_K,
    allowed_tables: list[str] | None = None,
    allowed_columns: dict[str, list[str]] | None = None,
) -> tuple[str, dict[str, Any]]:
    """
    Подготовить блок «УРОКИ ИЗ ПОХОЖИХ ЗАДАЧ» для шапки промпта генератора.

    Если индекс пуст / similarity ниже порога / БД недоступна — возвращает
    пустую строку. Это безопасный default: блок просто не выводится в
    prompt, остальная цепочка работает как обычно.
    """
    hits, meta = _solutions_search_with_meta(task, top_k=top_k)
    if not hits:
        return "", meta

    lines: list[str] = ["=== УРОКИ ИЗ ПОХОЖИХ ЗАДАЧ (анализ Claude Opus) ==="]
    used = 0
    seen_lessons: set[str] = set()
    allowed = {_base_name(item.lower()) for item in (allowed_tables or [])}
    filtered_count = 0
    filtered_by_columns = 0
    for hit in hits:
        hit_meta = hit["metadata"] or {}
        lesson = (hit_meta.get("lesson_for_generator") or "").strip()
        if not lesson or lesson in seen_lessons:
            continue
        seen_lessons.add(lesson)
        approach = (hit_meta.get("correct_sql_approach") or "").strip()
        task_type = (hit_meta.get("task_type") or "").strip()
        errors = hit_meta.get("generator_errors") or []
        block = []
        if task_type:
            block.append("[Тип: " + task_type + " · sim=" + str(hit["score"]) + "]")
        if isinstance(errors, list) and errors:
            top_errors = "; ".join(str(e) for e in errors[:3])
            block.append("Типичные ошибки: " + top_errors)
        if approach:
            block.append("Правильный подход: " + approach)
        block.append("Урок: " + lesson)
        chunk = "\n".join(block)
        if allowed and _mentions_disallowed_table(chunk, allowed):
            filtered_count += 1
            continue
        if allowed and _mentions_disallowed_column(chunk, allowed, allowed_columns):
            filtered_by_columns += 1
            continue
        if used + len(chunk) + 2 > SOLUTIONS_MAX_CHARS:
            break
        lines.append(chunk)
        lines.append("")
        used += len(chunk) + 2

    if len(lines) == 1:
        meta["fallback_reason"] = "no_lesson_text"
        return "", meta
    text = "\n".join(lines).strip()
    meta["context_chars"] = len(text)
    meta["filtered_by_allowed_tables"] = filtered_count
    meta["filtered_by_allowed_columns"] = filtered_by_columns
    return text, meta


def get_solutions_context(
    task: str,
    top_k: int = SOLUTIONS_TOP_K,
    allowed_tables: list[str] | None = None,
    allowed_columns: dict[str, list[str]] | None = None,
) -> str:
    text, _meta = get_solutions_context_with_meta(
        task,
        top_k=top_k,
        allowed_tables=allowed_tables,
        allowed_columns=allowed_columns,
    )
    return text


def get_solutions_context_timed(
    task: str,
    allowed_tables: list[str] | None = None,
    allowed_columns: dict[str, list[str]] | None = None,
) -> tuple[str, dict[str, Any]]:
    """Wrapper с тайминг-замером для Phase 0 sub-timings в orchestrator."""
    t0 = time.perf_counter()
    text, meta = get_solutions_context_with_meta(
        task,
        allowed_tables=allowed_tables,
        allowed_columns=allowed_columns,
    )
    elapsed = time.perf_counter() - t0
    return text, {
        "elapsed_sec": round(elapsed, 4),
        "cache_hit": False,  # solutions не кешируем — индекс мутирует от cron
        "fn": "get_solutions_context",
        "had_lessons": bool(text),
        "source_meta": meta,
    }


def _mentions_disallowed_table(text: str, allowed_tables: set[str]) -> bool:
    schema_tables = set((_load_schema().get("tables") or {}).keys())
    low = text.lower()
    for table in schema_tables:
        base = _base_name(table.lower())
        if base in allowed_tables:
            continue
        pattern = r"(?<![a-z0-9_])" + re.escape(table.lower()) + r"(?![a-z0-9_])"
        if re.search(pattern, low):
            return True
    return False


def _mentions_disallowed_column(
    text: str,
    allowed_tables: set[str],
    allowed_columns: dict[str, list[str]] | None,
) -> bool:
    """H8: RAG-урок не должен предлагать колонки вне schema_scope.

    Берём колонки всех allowed_tables из глобальной схемы и сравниваем с
    allowed_columns. Если в RAG-уроке встречается известная колонка
    разрешённой таблицы, но её НЕТ в allowed_columns — это утечка scope,
    урок нужно отфильтровать как «похожий, но вне области».
    """
    if not allowed_columns or not allowed_tables:
        return False
    schema = _load_schema().get("tables") or {}
    out_of_scope: set[str] = set()
    for table in allowed_tables:
        base = _base_name(str(table).lower())
        meta = schema.get(base) or schema.get(table) or {}
        all_cols = {str(c).lower() for c in (meta.get("columns") or {}).keys()}
        allowed_cols = {str(c).lower() for c in (allowed_columns.get(base) or allowed_columns.get(table) or [])}
        if not allowed_cols:
            continue
        out_of_scope |= (all_cols - allowed_cols)
    if not out_of_scope:
        return False
    low = text.lower()
    for col in out_of_scope:
        if len(col) < 4:
            # Слишком короткие имена дают шум.
            continue
        pattern = r"(?<![a-z0-9_])" + re.escape(col) + r"(?![a-z0-9_])"
        if re.search(pattern, low):
            return True
    return False


# Phase 2.1 — table_knowledge_v2 index ───────────────────────────────────────
# Бизнес-знания о 60 таблицах GreenData. Хранение и поиск повторяют solutions:
# Postgres FLOAT4[] + numpy cosine, без pgvector. Включается env-флагом для A/B.

TABLE_KNOWLEDGE_V2_TOP_K = 6
TABLE_KNOWLEDGE_V2_MIN_SIMILARITY = 0.45
TABLE_KNOWLEDGE_V2_MAX_CHARS = 1500


def _table_knowledge_v2_search_with_meta(
    task: str,
    top_k: int = TABLE_KNOWLEDGE_V2_TOP_K,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """
    Cosine-поиск по benchmark.rag_embeddings(index_name='table_knowledge_v2').

    Возвращает top_k записей с similarity >= TABLE_KNOWLEDGE_V2_MIN_SIMILARITY.
    На пустой индекс / отсутствие БД — пустой список и явную meta.
    """
    enabled = _flag_true("TABLE_KNOWLEDGE_V2_ENABLED", default="true")
    meta = _bench_dsn_meta("table_knowledge_v2", enabled=enabled)
    if not enabled:
        meta["fallback_reason"] = "disabled"
        return [], meta

    import numpy as np
    try:
        import psycopg2
    except ImportError as exc:
        meta["error"] = _safe_error(exc)
        meta["fallback_used"] = True
        meta["fallback_reason"] = "psycopg2_import_error"
        return [], meta

    try:
        with psycopg2.connect(_bench_dsn_for_solutions()) as conn:
            conn.set_session(readonly=True)
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id, text, metadata, embedding
                    FROM benchmark.rag_embeddings
                    WHERE index_name = 'table_knowledge_v2'
                    """
                )
                rows = cur.fetchall()
    except psycopg2.Error as exc:
        meta["error"] = _safe_error(exc)
        meta["fallback_used"] = True
        meta["fallback_reason"] = "db_error"
        return [], meta
    meta["row_count"] = len(rows)
    if not rows:
        meta["fallback_reason"] = "empty_index"
        return [], meta

    try:
        embedder = _solutions_embedder()
        query_vec = embedder.encode(
            ["query: " + (task or "")],
            normalize_embeddings=True,
        )[0]
    except Exception as exc:
        meta["error"] = _safe_error(exc)
        meta["fallback_used"] = True
        meta["fallback_reason"] = "embedder_error"
        return [], meta

    try:
        matrix = np.array([row[3] for row in rows], dtype="float32")
        q = np.array(query_vec, dtype="float32")
        norm = np.linalg.norm(q)
        if norm < 1e-9:
            meta["fallback_reason"] = "empty_query_vector"
            return [], meta
        q = q / norm
        scores = matrix @ q
    except Exception as exc:
        meta["error"] = _safe_error(exc)
        meta["fallback_used"] = True
        meta["fallback_reason"] = "scoring_error"
        return [], meta

    blacklist = _bridge_blacklist()
    order = np.argsort(scores)[::-1][: top_k * 3]
    results: list[dict[str, Any]] = []
    filtered_count = 0
    for idx in order:
        score = float(scores[idx])
        row_meta_raw = rows[idx][2] or {}
        row_meta = row_meta_raw if isinstance(row_meta_raw, dict) else {}
        table_name = str(row_meta.get("table_name") or "")
        entity_role = str(row_meta.get("entity_role") or "")
        meta_hit = {
            "table_name": table_name,
            "entity_role": entity_role,
            "score": round(score, 4),
        }
        meta["top_candidates"].append(meta_hit)
        if score < TABLE_KNOWLEDGE_V2_MIN_SIMILARITY:
            continue
        if entity_role in blacklist:
            filtered_count += 1
            continue
        if table_name and is_bridge_table(table_name):
            filtered_count += 1
            continue
        results.append({
            "id": int(rows[idx][0]),
            "text": rows[idx][1],
            "metadata": row_meta,
            "score": round(score, 4),
        })
        if len(results) >= top_k:
            break
    meta["hit_count"] = len(results)
    meta["filtered_count"] = filtered_count
    meta["top_hits"] = [
        {
            "table_name": str((hit.get("metadata") or {}).get("table_name") or ""),
            "entity_role": str((hit.get("metadata") or {}).get("entity_role") or ""),
            "score": hit.get("score"),
        }
        for hit in results[:top_k]
    ]
    if not results:
        top_score = float(meta["top_candidates"][0]["score"]) if meta["top_candidates"] else 0.0
        if top_score < TABLE_KNOWLEDGE_V2_MIN_SIMILARITY:
            meta["fallback_reason"] = "below_threshold"
        elif filtered_count:
            meta["fallback_reason"] = "filtered_by_role"
        else:
            meta["fallback_reason"] = "no_hits"
    return results, meta


def _table_knowledge_v2_search(task: str, top_k: int = TABLE_KNOWLEDGE_V2_TOP_K) -> list[dict[str, Any]]:
    hits, _meta = _table_knowledge_v2_search_with_meta(task, top_k=top_k)
    return hits


def get_table_knowledge_v2_context_with_meta(
    task: str,
    top_k: int = TABLE_KNOWLEDGE_V2_TOP_K,
) -> tuple[str, dict[str, Any]]:
    """
    Подготовить блок BUSINESS TABLES (v2) для шапки промпта генератора.

    Возвращает пустую строку если флаг выключен, индекс пуст, БД недоступна
    или similarity ниже порога. Смена флага требует cache_clear/restart,
    потому что get_generation_context кешируется по task.
    """
    hits, meta = _table_knowledge_v2_search_with_meta(task, top_k=top_k)
    if not hits:
        return "", meta

    lines: list[str] = ["=== BUSINESS TABLES (v2) ==="]
    used = 0
    for hit in hits:
        hit_meta = hit["metadata"] or {}
        table_name = hit_meta.get("table_name", "?")
        domain = hit_meta.get("business_domain", "")
        role = hit_meta.get("entity_role", "")
        related = hit_meta.get("related_tables", "")
        joins = hit_meta.get("approved_joins", "")
        sensitive = hit_meta.get("sensitive_columns", "")

        block = f"\n- {table_name} ({domain}, role={role})"
        if related:
            block += f"\n  related: {related}"
        if joins:
            block += f"\n  approved_joins: {joins}"
        if sensitive:
            block += f"\n  sensitive: {sensitive}"
        block += f"\n  (similarity={hit['score']})"

        if used + len(block) > TABLE_KNOWLEDGE_V2_MAX_CHARS:
            meta["truncated"] = True
            break
        lines.append(block)
        used += len(block)

    if len(lines) == 1:
        meta["fallback_reason"] = "budget_empty"
        return "", meta
    text = "\n".join(lines)
    meta["context_chars"] = len(text)
    meta["truncated"] = bool(meta.get("truncated", False))
    return text, meta


def get_table_knowledge_v2_context(task: str, top_k: int = TABLE_KNOWLEDGE_V2_TOP_K) -> str:
    text, _meta = get_table_knowledge_v2_context_with_meta(task, top_k=top_k)
    return text


def get_rag_diagnostics() -> dict[str, Any]:
    """
    Lightweight health diagnostics for benchmark-backed RAG indices.

    It checks row counts only; no embedding model is loaded here.
    """
    indices = {
        "table_knowledge_v2": _bench_dsn_meta(
            "table_knowledge_v2",
            enabled=_flag_true("TABLE_KNOWLEDGE_V2_ENABLED", default="true"),
        ),
        "solutions": _bench_dsn_meta("solutions", enabled=True),
    }
    try:
        import psycopg2
    except ImportError as exc:
        for item in indices.values():
            item["error"] = _safe_error(exc)
            item["fallback_reason"] = "psycopg2_import_error"
        return indices

    try:
        with psycopg2.connect(_bench_dsn_for_solutions(), connect_timeout=2) as conn:
            conn.set_session(readonly=True)
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT index_name, count(*)
                    FROM benchmark.rag_embeddings
                    WHERE index_name IN ('table_knowledge_v2', 'solutions')
                    GROUP BY index_name
                    """
                )
                rows = dict(cur.fetchall())
    except psycopg2.Error as exc:
        for item in indices.values():
            item["error"] = _safe_error(exc)
            item["fallback_used"] = bool(item.get("enabled"))
            item["fallback_reason"] = "db_error"
        return indices

    for name, item in indices.items():
        item["row_count"] = int(rows.get(name, 0))
        if item["row_count"] == 0:
            item["fallback_reason"] = "empty_index"
    return indices


def get_generation_context_timed(task: str) -> tuple[str, dict[str, Any]]:
    """
    Получить generation-контекст и тайминги вызова.

    Возвращает (text, timing) где timing содержит elapsed_sec и
    cache_hit. Cold start (первый запрос с уникальной задачей) — реальные
    миллисекунды encode + FAISS. Hot (повторный с тем же task) — около
    нуля. Используется orchestrator-узлом retrieve.

    Lookup через sys.modules[__name__] сохраняет совместимость с
    monkey-patching в smoke-тестах (rag_adapter.get_generation_context = mock).
    """
    fn = getattr(sys.modules[__name__], "get_generation_context")
    return _timed_cached_call(fn, task)


def get_generation_context_bundle_timed(task: str) -> tuple[dict[str, Any], dict[str, Any]]:
    """Вернуть generation bundle и cache timing для retrieve trace."""
    fn = getattr(sys.modules[__name__], "get_generation_context_bundle")
    return _timed_cached_call(fn, task)


def get_security_context_timed(sql: str) -> tuple[str, dict[str, Any]]:
    """Аналогично get_generation_context_timed для security-индекса."""
    fn = getattr(sys.modules[__name__], "get_security_context")
    return _timed_cached_call(fn, sql)


def get_generation_hits_timed(task: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Аналогично для generation FAISS-хитов."""
    fn = getattr(sys.modules[__name__], "get_generation_hits")
    return _timed_cached_call(fn, task)


def get_security_hits_timed(sql: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Аналогично для security FAISS-хитов."""
    fn = getattr(sys.modules[__name__], "get_security_hits")
    return _timed_cached_call(fn, sql)
