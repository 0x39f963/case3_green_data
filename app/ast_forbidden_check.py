"""Раннее AST-сито на запрещённые SQL-конструкции (ранний барьер до LLM).

marina-03. Если сгенерированный SQL содержит ``DROP``, ``TRUNCATE``,
``COPY … FROM/TO PROGRAM``, ``dblink``, ``pg_read_file`` и т.п. — pipeline должен
отказать сразу, не тратя LLM-аудитора. Один pglast-парс — миллисекунды; один
вызов аудитора — секунды и деньги. AST «не договаривается» с креативной
формулировкой: он видит узел ``ast.DropStmt`` и блокирует.

Каталог правил — data-driven. По умолчанию подхватывает
``data/sql_guard/forbidden_constructs.yaml`` (его готовит ksenia-01) и домешивает
fallback-floor (:data:`FALLBACK_RULES`), который гарантирует обязательный минимум
из ТЗ; если файла нет — работает только на fallback. Путь к каталогу можно
переопределить env-переменной ``FORBIDDEN_CONSTRUCTS_PATH``.

Защитный guard (:data:`_HARD_FAIL_NODE_TYPES`): как hard-fail через ``node_type``
применяются только statement-узлы из allowlist. Широкие узлы (``A_Expr``,
``SelectStmt``, ``RawStmt`` …) отсекаются — иначе барьер ложно срабатывал бы на
каждом SELECT. Семантические классы (TAUTOLOGY, UNION_EXFIL, MULTI_STATEMENT,
DYNAMIC_EXECUTE), которые ksenia-01 повесила на такие узлы, корректно ловит
semantic-слой ``sql_guard`` дальше по пайплайну, а не этот ранний барьер.
Загрузчик терпим к схеме ksenia-01 (``func_call_with_arg``, ``func_name``).

Это «hard fail» правила: при срабатывании ivan-02 направляет pipeline сразу к
``decision=abstain``. Поэтому правила точные, без широких regex — цена ложного
срабатывания высокая.

Контракт findings: :class:`Finding` (label/severity/evidence). При интеграции
(ivan-02) маппится в ``sql_guard.make_vulnerability``.

Пример использования::

    from app.ast_forbidden_check import check_forbidden_commands

    check_forbidden_commands("DROP TABLE sys_employee")
    # → [Finding('DDL_FORBIDDEN', 9, 'DROP — удаление объекта: sys_employee')]
    check_forbidden_commands("SELECT id FROM corp_tech_application LIMIT 100")
    # → []   (safe SELECT — барьер не срабатывает)
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pglast import parse_sql
from pglast.parser import ParseError
from pglast.visitors import Visitor

__all__ = ["Finding", "ForbiddenRule", "FALLBACK_RULES", "check_forbidden_commands"]

_DEFAULT_CATALOG = Path(__file__).resolve().parents[1] / "data" / "sql_guard" / "forbidden_constructs.yaml"

# Allowlist узлов, которые допустимо трактовать как hard-fail через node_type.
# Только statement-уровень с гарантированно опасной семантикой. Вездесущие узлы
# (SelectStmt, RawStmt, A_Expr, BoolExpr, ColumnRef, …) сюда НЕ входят: правило на
# них в раннем барьере давало бы ложные срабатывания на каждом SELECT. Каталог
# ksenia-01 вешает TAUTOLOGY/UNION/MULTI_STATEMENT/DYNAMIC_EXECUTE именно на такие
# широкие узлы — их корректно ловит semantic-слой sql_guard, не этот барьер.
_HARD_FAIL_NODE_TYPES: frozenset[str] = frozenset(
    {
        "DropStmt",
        "TruncateStmt",
        "AlterTableStmt",
        "CreateStmt",
        "CreateTableAsStmt",
        "DeleteStmt",
        "UpdateStmt",
        "InsertStmt",
        "GrantStmt",
        "GrantRoleStmt",
        "VariableSetStmt",
        "CreateRoleStmt",
        "AlterRoleStmt",
        "DropRoleStmt",
        "CreatedbStmt",
        "DropdbStmt",
    }
)

# Известные функции, для которых порог аргумента подразумевается семантикой
# (в YAML ksenia-01 порог не закодирован — только в примерах). pg_sleep > 1 c.
_DEFAULT_ARG_GT: dict[str, float] = {"pg_sleep": 1.0}


@dataclass
class Finding:
    """Сработавшее запрещённое правило."""

    label: str
    severity: int
    evidence: str


@dataclass
class ForbiddenRule:
    """Одно правило запрета.

    ast_check определяет способ проверки:
        node_type     — совпадение по типу узла (``ast_node``, напр. DropStmt).
        func_call     — вызов функции из ``funcs``; ``arg_gt`` — числовой порог
                        первого аргумента (для pg_sleep).
        schema_access — обращение к схеме из ``schemas`` (RangeVar.schemaname).
        copy_program  — CopyStmt с флагом is_program (COPY … FROM/TO PROGRAM).
    """

    id: str
    label: str
    severity: int
    ast_check: str
    ast_node: str = ""
    funcs: tuple[str, ...] = ()
    schemas: tuple[str, ...] = ()
    arg_gt: float | None = None
    description: str = ""


# Встроенный fallback-каталог (обязательный набор из ТЗ marina-03).
FALLBACK_RULES: list[ForbiddenRule] = [
    ForbiddenRule("drop_any", "DDL_FORBIDDEN", 9, "node_type", ast_node="DropStmt",
                  description="DROP — удаление объекта"),
    ForbiddenRule("truncate", "TRUNCATE", 8, "node_type", ast_node="TruncateStmt",
                  description="TRUNCATE — очистка таблицы"),
    ForbiddenRule("alter_table", "DDL_FORBIDDEN", 9, "node_type", ast_node="AlterTableStmt",
                  description="ALTER TABLE — изменение схемы"),
    ForbiddenRule("create", "DDL_FORBIDDEN", 9, "node_type", ast_node="CreateStmt",
                  description="CREATE TABLE — создание объекта"),
    ForbiddenRule("delete", "DML_NO_WHERE", 8, "node_type", ast_node="DeleteStmt",
                  description="DELETE — удаление строк (разрешён только SELECT)"),
    ForbiddenRule("update", "DML_NO_WHERE", 8, "node_type", ast_node="UpdateStmt",
                  description="UPDATE — изменение строк (разрешён только SELECT)"),
    ForbiddenRule("insert", "INSERT_UNSAFE", 7, "node_type", ast_node="InsertStmt",
                  description="INSERT — вставка строк (разрешён только SELECT)"),
    ForbiddenRule("copy_program", "COPY_EXPORT", 9, "copy_program",
                  description="COPY … FROM/TO PROGRAM — исполнение команды ОС"),
    ForbiddenRule("pg_read_file", "PROMPT_FS_READ", 9, "func_call",
                  funcs=("pg_read_file",), description="pg_read_file — чтение файла ФС"),
    ForbiddenRule("pg_ls_dir", "PROMPT_FS_READ", 9, "func_call",
                  funcs=("pg_ls_dir",), description="pg_ls_dir — листинг каталога ФС"),
    ForbiddenRule("dblink", "PROMPT_SCHEMA_EXFIL", 9, "func_call",
                  funcs=("dblink", "dblink_connect"), description="dblink — внешнее соединение"),
    ForbiddenRule("large_objects", "COPY_EXPORT", 9, "func_call",
                  funcs=("lo_import", "lo_export"), description="lo_import/lo_export — экспорт файла"),
    ForbiddenRule("pg_catalog", "SCHEMA_LEAK", 7, "schema_access",
                  schemas=("pg_catalog",), description="доступ к pg_catalog"),
    ForbiddenRule("information_schema", "SCHEMA_LEAK", 7, "schema_access",
                  schemas=("information_schema",), description="доступ к information_schema"),
    ForbiddenRule("pg_sleep", "TIME_DELAY", 7, "func_call",
                  funcs=("pg_sleep",), arg_gt=1.0, description="pg_sleep > 1 c — искусственная задержка"),
]


def check_forbidden_commands(
    sql: str,
    rules: list[ForbiddenRule] | None = None,
) -> list[Finding]:
    """Раннее AST-сито на запрещённые конструкции.

    Если ``rules`` не передан — берёт каталог по умолчанию из
    ``data/sql_guard/forbidden_constructs.yaml`` (ksenia-01), а при его
    отсутствии — встроенный :data:`FALLBACK_RULES`.

    Возвращает список всех найденных нарушений за один проход AST (по одному
    finding на сработавшее правило). Сломанный SQL → один finding
    ``SYNTAX_BROKEN`` (severity 7) и выход.
    """
    if not sql or not sql.strip():
        return [Finding("SYNTAX_BROKEN", 7, "пустой SQL")]
    try:
        tree = parse_sql(sql)
    except (ParseError, Exception) as exc:  # noqa: BLE001 — любой сбой парсера = broken
        return [Finding("SYNTAX_BROKEN", 7, f"SQL не парсится: {exc}")]
    if not tree:
        return [Finding("SYNTAX_BROKEN", 7, "пустой AST")]

    active_rules = rules if rules is not None else _default_rules()

    collector = _Collector()
    collector(tree)
    by_type = collector.by_type

    findings: list[Finding] = []
    for rule in active_rules:
        evidence = _eval_rule(rule, by_type)
        if evidence is not None:
            findings.append(Finding(rule.label, rule.severity, evidence))
    return findings


class _Collector(Visitor):
    """Собирает все узлы AST, сгруппированные по имени класса, за один проход."""

    def __init__(self) -> None:
        self.by_type: dict[str, list[Any]] = {}

    def visit(self, ancestors: Any, node: Any) -> None:  # noqa: D401
        self.by_type.setdefault(type(node).__name__, []).append(node)


def _eval_rule(rule: ForbiddenRule, by_type: dict[str, list[Any]]) -> str | None:
    """Вернуть evidence-строку, если правило сработало, иначе None."""
    if rule.ast_check == "node_type":
        # Защитный guard: hard-fail только для statement-узлов из allowlist.
        # Широкий/неизвестный узел (A_Expr, SelectStmt, RawStmt, …) — пропускаем,
        # иначе барьер отклонял бы безобидные SELECT.
        if rule.ast_node not in _HARD_FAIL_NODE_TYPES:
            return None
        nodes = by_type.get(rule.ast_node, [])
        if not nodes:
            return None
        targets = [t for t in (_target_name(n) for n in nodes) if t]
        suffix = f": {', '.join(sorted(set(targets)))}" if targets else ""
        return f"{_headline(rule.description)}{suffix}"

    if rule.ast_check == "func_call":
        hits = []
        for node in by_type.get("FuncCall", []):
            if _func_base(node) not in rule.funcs:
                continue
            if rule.arg_gt is not None:
                value = _first_numeric_arg(node)
                if value is None or value <= rule.arg_gt:
                    continue
                hits.append(f"{_func_base(node)}({value:g})")
            else:
                hits.append(f"{_func_base(node)}()")
        if hits:
            return f"{_headline(rule.description)}: {', '.join(sorted(set(hits)))}"
        return None

    if rule.ast_check == "schema_access":
        if not rule.schemas:  # без списка схем правило бессодержательно (см. ksenia-01)
            return None
        hits = []
        for node in by_type.get("RangeVar", []):
            schema = (getattr(node, "schemaname", None) or "").lower()
            if schema in rule.schemas:
                hits.append(f"{schema}.{getattr(node, 'relname', '') or ''}")
        if hits:
            return f"{_headline(rule.description)}: {', '.join(sorted(set(hits)))}"
        return None

    if rule.ast_check == "copy_program":
        for node in by_type.get("CopyStmt", []):
            if getattr(node, "is_program", False):
                return _headline(rule.description)
        return None

    return None


def _target_name(node: Any) -> str:
    """Лучшее имя объекта для evidence (таблица DROP/TRUNCATE/DELETE/…)."""
    cls = type(node).__name__
    if cls == "DropStmt":
        names = []
        for obj in getattr(node, "objects", None) or ():
            parts = [p.sval for p in obj if getattr(p, "sval", None)] if isinstance(obj, (tuple, list)) else []
            if parts:
                names.append(".".join(parts))
        return ", ".join(names)
    if cls == "TruncateStmt":
        return ", ".join(r.relname for r in (getattr(node, "relations", None) or ()) if getattr(r, "relname", None))
    rel = getattr(node, "relation", None)
    if rel is not None:
        return getattr(rel, "relname", "") or ""
    return ""


def _func_base(node: Any) -> str:
    parts = [getattr(p, "sval", "") or "" for p in getattr(node, "funcname", ()) or ()]
    return (parts[-1] if parts else "").lower()


def _first_numeric_arg(node: Any) -> float | None:
    args = getattr(node, "args", None) or ()
    if not args:
        return None
    val = getattr(args[0], "val", None)
    ival = getattr(val, "ival", None)
    if isinstance(ival, int):
        return float(ival)
    fval = getattr(val, "fval", None)
    if fval:
        try:
            return float(fval)
        except (TypeError, ValueError):
            return None
    return None


def _catalog_path() -> Path:
    """Путь к YAML-каталогу. Переопределяется env FORBIDDEN_CONSTRUCTS_PATH (для тестов)."""
    override = os.environ.get("FORBIDDEN_CONSTRUCTS_PATH")
    return Path(override) if override else _DEFAULT_CATALOG


def _default_rules() -> list[ForbiddenRule]:
    """Каталог по умолчанию: YAML ksenia-01 (если есть) + fallback-floor; иначе fallback.

    Каталог Ксении используется как источник истины, но fallback-floor
    гарантирует обязательный минимум из ТЗ: если её каталог чего-то не покрывает
    (напр. plain INSERT, или schema_access без списка схем), недостающее правило
    добавляется из :data:`FALLBACK_RULES`. Широкие/семантические правила её
    каталога отсекаются guard'ом в :func:`_eval_rule`.
    """
    loaded = _load_yaml_rules(_catalog_path())
    if not loaded:
        return FALLBACK_RULES
    return loaded + [fb for fb in FALLBACK_RULES if not _covered_by(fb, loaded)]


def _covered_by(fallback: ForbiddenRule, loaded: list[ForbiddenRule]) -> bool:
    """Покрывает ли загруженный каталог это fallback-правило (тем же механизмом)."""
    for rule in loaded:
        if rule.ast_check != fallback.ast_check:
            continue
        if fallback.ast_check == "node_type":
            if rule.ast_node == fallback.ast_node and rule.ast_node in _HARD_FAIL_NODE_TYPES:
                return True
        elif fallback.ast_check == "func_call":
            if set(rule.funcs) & set(fallback.funcs):
                return True
        elif fallback.ast_check == "schema_access":
            if set(rule.schemas) & set(fallback.schemas):  # пустой schemas не покрывает
                return True
        elif fallback.ast_check == "copy_program":
            return True
    return False


def _load_yaml_rules(path: Path) -> list[ForbiddenRule] | None:
    """Загрузить каталог из YAML (ksenia-01). None — файла нет/пуст/нечитаем.

    Терпим к схеме Ксении: ``ast_check: func_call_with_arg`` → ``func_call``,
    имя функции из ``func_name`` (или ``func``/``funcs``). Для функций с известным
    семантическим порогом (pg_sleep) подставляется ``arg_gt`` из :data:`_DEFAULT_ARG_GT`,
    если в YAML он не задан.
    """
    if not path.exists():
        return None
    try:
        import yaml  # noqa: PLC0415 — ленивый импорт, модуль не зависит от pyyaml жёстко

        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 — битый YAML не должен ронять барьер; идём на fallback
        return None
    if not data:
        return None
    raw = data["rules"] if isinstance(data, dict) and "rules" in data else data
    if not isinstance(raw, list):
        return None
    rules: list[ForbiddenRule] = []
    for item in raw:
        if not isinstance(item, dict) or "label" not in item:
            continue
        ast_check = str(item.get("ast_check", "node_type"))
        if ast_check == "func_call_with_arg":  # синоним схемы ksenia-01
            ast_check = "func_call"
        funcs = tuple(
            s.lower()
            for s in _as_list(item.get("func") or item.get("funcs") or item.get("func_name"))
        )
        arg_gt = item.get("arg_gt")
        if arg_gt is None:
            for fn in funcs:
                if fn in _DEFAULT_ARG_GT:
                    arg_gt = _DEFAULT_ARG_GT[fn]
                    break
        rules.append(
            ForbiddenRule(
                id=str(item.get("id", "")),
                label=str(item["label"]),
                severity=int(float(item.get("severity", 5))),
                ast_check=ast_check,
                ast_node=_strip_ast_prefix(str(item.get("ast_node", ""))),
                funcs=funcs,
                schemas=tuple(s.lower() for s in _as_list(item.get("schemas"))),
                arg_gt=arg_gt,
                description=str(item.get("description", "")),
            )
        )
    return rules or None


def _headline(text: str) -> str:
    """Первая непустая строка описания (описания ksenia-01 многострочные)."""
    for line in (text or "").splitlines():
        if line.strip():
            return line.strip()
    return ""


def _strip_ast_prefix(name: str) -> str:
    return name.split(".")[-1].strip()


def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return [str(v) for v in value]
    return [str(value)]
