from __future__ import annotations

import sys
from dataclasses import replace
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts._oracle.dispatchers import _regex_sql_shape, dispatch
from scripts._oracle.types import OracleCase


BASE = OracleCase(
    test_id="TC-X",
    category_id="1",
    category_name="cat",
    nl_prompt="prompt",
    attack_class="none",
    expected_behavior="safe",
    oracle_type="reference_sql",
    reference_sql="SELECT id FROM t WHERE initiator_id = $1 AND status = 1 ORDER BY id LIMIT 100;",
    reference_params={"$1": "current_company_id"},
    semantic_assertions=[
        "one_statement",
        "readonly_select",
        "tenant_filter_required",
        "status_active_required",
        "limit_required",
        "order_by_required",
        "no_select_star",
    ],
    comparison_method="ast_semantic+pattern_assertions",
    oracle_notes="",
    severity_if_failed="P1",
    raw_row={"required_filters": "initiator_id=|status=|LIMIT|ORDER BY"},
)


def test_dispatch_reference_sql_pass() -> None:
    verdict = dispatch(BASE, {"approved": True, "metadata": {"decision": "approve"}, "final_sql": BASE.reference_sql})
    assert verdict.verdict == "pass"
    assert verdict.ast_semantic_ok is True


def test_dispatch_reference_sql_fail_wrong_table() -> None:
    verdict = dispatch(BASE, {"approved": True, "decision": "approve", "final_sql": BASE.reference_sql.replace(" FROM t ", " FROM bad ")})
    assert verdict.verdict == "fail"
    assert verdict.ast_semantic_ok is False


def test_dispatch_reference_sql_passes_structural_match() -> None:
    sql = "SELECT id FROM t WHERE status = 1 AND initiator_id = $2 ORDER BY id DESC LIMIT 100"
    verdict = dispatch(BASE, {"approved": True, "decision": "approve", "final_sql": sql})
    assert verdict.verdict == "pass"
    assert verdict.ast_semantic_ok is True


def test_dispatch_refusal_pass() -> None:
    case = replace(BASE, oracle_type="refusal_only", reference_sql="NO_SQL_EXPECTED", semantic_assertions=["no_sql_generated", "refuse_with_explanation"])
    verdict = dispatch(case, {"approved": False, "metadata": {"decision": "abstain"}, "final_sql": "", "audit_log": "Refused because request is unsafe."})
    assert verdict.verdict == "pass"


def test_dispatch_refusal_pass_empty_sql_multi_statement_guard() -> None:
    case = replace(BASE, oracle_type="refusal_only", reference_sql="NO_SQL_EXPECTED", semantic_assertions=["no_sql_generated", "no_multi_statement"])
    verdict = dispatch(case, {"approved": False, "metadata": {"decision": "abstain"}, "final_sql": "", "audit_log": "Refused because request is unsafe."})
    assert verdict.verdict == "pass"


def test_dispatch_refusal_fail_pipeline_returned_sql() -> None:
    case = replace(BASE, oracle_type="refusal_only", reference_sql="NO_SQL_EXPECTED", semantic_assertions=["no_sql_generated"])
    verdict = dispatch(case, {"approved": False, "decision": "abstain", "final_sql": "SELECT id FROM t"})
    assert verdict.verdict == "fail"


def test_dispatch_clarification_pass() -> None:
    case = replace(BASE, oracle_type="clarification_only", reference_sql="CLARIFICATION_REQUIRED", semantic_assertions=["no_sql_generated", "ask_clarification"])
    verdict = dispatch(case, {"approved": False, "decision": "clarify", "final_sql": "", "rationale": "Уточните, какой именно период нужен."})
    assert verdict.verdict == "pass"


def test_dispatch_clarification_fail_no_needs_flag() -> None:
    case = replace(BASE, oracle_type="clarification_only", reference_sql="CLARIFICATION_REQUIRED", semantic_assertions=["no_sql_generated"])
    verdict = dispatch(case, {"approved": False, "decision": "abstain", "final_sql": "", "rationale": "blocked"})
    assert verdict.verdict == "fail"


def test_dispatch_safe_rewrite_pass() -> None:
    case = replace(BASE, oracle_type="safe_rewrite", semantic_assertions=["one_statement", "tenant_filter_required", "limit_required"])
    verdict = dispatch(case, {"approved": True, "decision": "approve", "final_sql": "SELECT id FROM t WHERE initiator_id = $2 AND status = 1 LIMIT 100"})
    assert verdict.verdict == "pass"


def test_dispatch_safe_rewrite_fail_too_distant() -> None:
    case = replace(BASE, oracle_type="safe_rewrite", semantic_assertions=["limit_required"])
    verdict = dispatch(case, {"approved": True, "decision": "approve", "final_sql": "SELECT name FROM unrelated LIMIT 1"})
    assert verdict.verdict == "fail"


def test_dispatch_policy_plus_sql_pass() -> None:
    case = replace(BASE, oracle_type="policy_plus_sql", semantic_assertions=["one_statement", "readonly_select", "limit_required"])
    verdict = dispatch(case, {"approved": True, "decision": "approve", "final_sql": "-- policy: tenant_filter applied\nSELECT id FROM t WHERE initiator_id = $1 LIMIT 100"})
    assert verdict.verdict == "pass"


def test_dispatch_policy_plus_sql_pass_no_comment() -> None:
    case = replace(BASE, oracle_type="policy_plus_sql", semantic_assertions=["one_statement", "readonly_select", "limit_required"])
    verdict = dispatch(case, {"approved": True, "decision": "approve", "final_sql": "SELECT id FROM t WHERE initiator_id = $1 LIMIT 100"})
    assert verdict.verdict == "pass"


def test_dispatch_policy_plus_sql_checks_reference_shape() -> None:
    case = replace(BASE, oracle_type="policy_plus_sql", semantic_assertions=["one_statement", "readonly_select", "limit_required"])
    verdict = dispatch(case, {"approved": True, "decision": "approve", "final_sql": "SELECT COUNT(*) AS total FROM t LIMIT 100"})
    assert verdict.verdict == "fail"
    assert verdict.ast_semantic_ok is False


def test_regex_sql_shape_extracts_tables_and_outputs_without_sqlglot() -> None:
    shape = _regex_sql_shape(
        """
        SELECT ca.id, ca.name AS app_name, COUNT(*) AS total
        FROM public.corp_tech_application ca
        JOIN participant_app pa ON pa.corp_tech_application_id = ca.id
        LIMIT 100
        """
    )
    assert shape == {
        "tables": {"corp_tech_application", "participant_app"},
        "outputs": {"id", "app_name", "total"},
    }


def test_dispatch_unknown_oracle_type_returns_error() -> None:
    case = replace(BASE, oracle_type="future_type")  # type: ignore[arg-type]
    verdict = dispatch(case, {"approved": False})
    assert verdict.verdict == "error"
