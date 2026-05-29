from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts._oracle.loaders import (
    load_golden_v1_1,
    load_golden_v2,
    load_oracle_cases,
    parse_assertions,
    parse_reference_params,
)


GOLDEN = ROOT / "data" / "eval" / "golden_dataset_v1_1.csv"
GOLDEN_V2 = ROOT / "data" / "eval" / "golden_v2.jsonl"


def test_load_golden_v1_1_count() -> None:
    assert len(load_golden_v1_1(GOLDEN)) == 600


def test_load_golden_v1_1_semicolon_and_commas(tmp_path: Path) -> None:
    path = tmp_path / "sample.csv"
    path.write_text(
        "test_id;category_id;category_name;nl_prompt;attack_class;expected_behavior;oracle_type;reference_sql;"
        "reference_params;semantic_assertions;comparison_method;oracle_notes;severity_if_failed\n"
        '"TC-X";"1";"cat";"Покажи A, B и C";"none";"safe";"reference_sql";'
        '"SELECT id FROM t WHERE company_id = $1;";"{""$1"":""current_company_id""}";'
        '"one_statement|readonly_select";"ast_semantic+pattern_assertions";"notes";"P2"\n',
        encoding="utf-8",
    )
    cases = load_golden_v1_1(path)
    assert cases[0].nl_prompt == "Покажи A, B и C"
    assert cases[0].reference_params == {"$1": "current_company_id"}
    assert cases[0].semantic_assertions == ["one_statement", "readonly_select"]


def test_load_golden_v1_1_oracle_types() -> None:
    allowed = {"reference_sql", "refusal_only", "clarification_only", "safe_rewrite", "policy_plus_sql"}
    assert {case.oracle_type for case in load_golden_v1_1(GOLDEN)} <= allowed


def test_load_golden_v2_count_and_ids() -> None:
    cases = load_golden_v2(GOLDEN_V2)
    assert len(cases) == 835
    assert cases[0].test_id == "TC-0001"
    assert cases[1].test_id == "TC-0002"
    assert cases[1].reference_sql.startswith("SELECT COUNT(*) AS app_count")


def test_load_oracle_cases_selects_jsonl_loader() -> None:
    cases = load_oracle_cases(GOLDEN_V2)
    assert len(cases) == 835
    assert any(case.oracle_type == "refusal_only" for case in cases)


def test_parse_semantic_assertions_pipe() -> None:
    assert parse_assertions("one_statement| readonly_select ||") == ["one_statement", "readonly_select"]
    assert parse_assertions("") == []


def test_parse_reference_params_json() -> None:
    assert parse_reference_params('{"$1": "x"}') == {"$1": "x"}


def test_parse_reference_params_empty_or_bad() -> None:
    assert parse_reference_params("") == {}
    assert parse_reference_params("{}") == {}
    assert parse_reference_params("{bad") == {}
