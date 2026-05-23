from app import tools
from app.tools import check_hallucination, get_approved_joins, get_sensitive_fields


def test_dispatch_unknown_tool_returns_error_dict():
    result = tools.dispatch("unknown_tool", {})

    assert result["error"].startswith("unknown_tool:")
    assert "check_hallucination" in result["available_tools"]


def test_check_hallucination_parse_error():
    result = check_hallucination.invoke({"sql": "SELECT FROM"})

    assert result["ok"] is False
    assert result["parse_error"]


def test_check_hallucination_unknown_table():
    result = check_hallucination.invoke({"sql": "SELECT id FROM totally_missing_table"})

    assert result["ok"] is False
    assert "totally_missing_table" in result["unknown_tables"]


def test_get_sensitive_fields_empty_tables_returns_full_map():
    result = get_sensitive_fields.invoke({"tables": []})

    assert result["tables_queried"] == []
    assert result["sensitive_fields"]
    assert sorted(result["sensitive_fields"]) == result["tables_with_sensitive"]


def test_get_approved_joins_direct_and_reverse_real_pairs():
    pairs = [
        ("application_obj", "sys_employee"),
        ("scp_application", "sys_company"),
        ("count_turnover", "sys_company"),
    ]

    for left, right in pairs:
        direct = get_approved_joins.invoke({"table_a": left, "table_b": right})
        reverse = get_approved_joins.invoke({"table_a": right, "table_b": left})

        assert direct["has_approved"] is True
        assert direct["approved_keys"]
        assert reverse["has_approved"] is True
        assert reverse["approved_keys"]
        assert all(item.get("source") for item in direct["approved_keys"])
