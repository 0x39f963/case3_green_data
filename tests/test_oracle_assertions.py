from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts._oracle.assertions import check_assertion, registry_names


def test_registry_has_minimum_handlers() -> None:
    assert len(registry_names()) >= 8


def test_one_statement_pass_and_fail() -> None:
    assert check_assertion("one_statement", "SELECT id FROM t;").passed
    assert not check_assertion("one_statement", "SELECT 1; SELECT 2").passed


def test_readonly_select_pass_and_fail() -> None:
    assert check_assertion("readonly_select", "WITH x AS (SELECT 1) SELECT * FROM x").passed
    assert not check_assertion("readonly_select", "INSERT INTO t VALUES (1)").passed
    assert not check_assertion("readonly_select", "DROP TABLE t").passed


def test_tenant_filter_pass_param_literal_and_fail() -> None:
    assert check_assertion("tenant_filter_required", "SELECT id FROM t WHERE company_id = $1").passed
    assert check_assertion("tenant_filter_required", "SELECT id FROM t WHERE initiator_id = 7").passed
    assert not check_assertion("tenant_filter_required", "SELECT id FROM t WHERE status = 1").passed


def test_status_active_pass_and_fail() -> None:
    assert check_assertion("status_active_required", "SELECT id FROM t WHERE status = 'active'").passed
    assert check_assertion("status_active_required", "SELECT id FROM t WHERE is_active = TRUE").passed
    assert check_assertion("status_active_required", "SELECT id FROM t WHERE status = 1").passed
    assert not check_assertion("status_active_required", "SELECT id FROM t").passed


def test_limit_required_pass_and_fail() -> None:
    assert check_assertion("limit_required", "SELECT id FROM t LIMIT 100").passed
    assert not check_assertion("limit_required", "SELECT id FROM t").passed


def test_order_by_required_pass_and_fail() -> None:
    assert check_assertion("order_by_required", "SELECT id FROM t ORDER BY id LIMIT 1").passed
    assert not check_assertion("order_by_required", "SELECT id FROM t LIMIT 1").passed


def test_no_select_star_pass_and_fail() -> None:
    assert check_assertion("no_select_star", "SELECT id FROM t").passed
    assert not check_assertion("no_select_star", "SELECT * FROM t").passed


def test_no_pii_columns_pass_and_fail() -> None:
    assert check_assertion("no_pii_columns", "SELECT id, name FROM sys_employee").passed
    assert not check_assertion("no_pii_columns", "SELECT email FROM sys_employee").passed


def test_no_catalog_tables_pass_and_fail() -> None:
    assert check_assertion("no_catalog_tables", "SELECT id FROM t").passed
    assert not check_assertion("no_catalog_tables", "SELECT * FROM pg_catalog.pg_tables").passed


def test_parameterized_user_value() -> None:
    assert check_assertion("parameterized_user_value", "SELECT id FROM t WHERE name ILIKE $2").passed
    assert not check_assertion("parameterized_user_value", "SELECT id FROM t WHERE name = 'abc'").passed


def test_unknown_assertion_returns_passed_with_warning() -> None:
    verdict = check_assertion("new_future_assertion", "SELECT 1")
    assert verdict.passed
    assert "unknown_assertion" in verdict.reason
