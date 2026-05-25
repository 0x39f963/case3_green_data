from __future__ import annotations

from app import generator_selector


def test_selector_prefers_month_aligned_candidate_over_clean_but_wrong_sql() -> None:
    candidates = [
        """
        SELECT sc.status, COUNT(*) AS client_count
        FROM sys_company sc
        WHERE sc.status = 1
          AND sc.create_date >= DATE_TRUNC('month', CURRENT_DATE)
        GROUP BY sc.status
        """,
        """
        SELECT sc.status, COUNT(*) AS client_count
        FROM sys_company sc
        WHERE sc.status = 1
        GROUP BY sc.status
        """,
    ]
    ctx = {
        "task": "Покажи активных клиентов по статусам за месяц",
        "allowed_tables": ["sys_company"],
        "allowed_columns": {"sys_company": ["id", "status", "create_date"]},
    }

    selected = generator_selector.select_best_with_details(candidates, ctx)

    assert selected.selected_index == 0
    assert "MISSING_REQUIRED_FILTER" not in selected.scores[0]["business_alignment_labels"]
    assert selected.scores[1]["business_alignment_labels"] == ["MISSING_REQUIRED_FILTER"]
    assert "NO_PAGINATION" in selected.scores[0]["labels"]
    assert selected.scores[0]["selector_reason"] == "selected because no blocking business/security findings"
