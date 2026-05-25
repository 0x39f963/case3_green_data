from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts._oracle.ast_compare import ast_close, ast_equivalent, normalize_sql


def test_normalize_sql_basic() -> None:
    assert normalize_sql("SELECT * FROM t WHERE x=$1")


def test_normalize_sql_fallback_on_bad_sql() -> None:
    assert normalize_sql("SELECT FROM WHERE") == "select from where"


def test_ast_equivalent_same() -> None:
    passed, reason = ast_equivalent("SELECT id FROM t WHERE x = $1", "select id from t where x=$1;")
    assert passed, reason


def test_ast_equivalent_param_ignored() -> None:
    passed, reason = ast_equivalent("SELECT id FROM t WHERE x = $1", "SELECT id FROM t WHERE x = $2")
    assert passed, reason


def test_ast_equivalent_wrong_table_fails() -> None:
    passed, _ = ast_equivalent("SELECT id FROM t1", "SELECT id FROM t2")
    assert not passed


def test_ast_close_jaccard() -> None:
    passed, score, reason = ast_close(
        "SELECT id, name FROM clients WHERE company_id = $1 LIMIT 100",
        "SELECT id FROM clients WHERE company_id = $2 LIMIT 50",
        threshold=0.5,
    )
    assert passed, reason
    assert score >= 0.5
