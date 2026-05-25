"""Smoke check for RAG bridge-multiselect filter.

Verifies that:
- the v2 table_knowledge index CSV is parsed,
- ms_* bridge tables (e.g. ms_0n8ohjyx7oszo6a47ca9g0s6f) are flagged as bridge,
- canonical entity tables (corp_tech_application) are NOT flagged,
- strip_bridge_table_blocks removes ms_* sections from a Marina-style legacy
  document while keeping the canonical application_fact block.

Run as:
    PYTHONPATH=. .venv/bin/python scripts/smoke_rag_bridge_filter.py
"""

from __future__ import annotations

import json
import sys

from app import rag_adapter


CASES = (
    ("ms_0n8ohjyx7oszo6a47ca9g0s6f", "bridge_multiselect", True),
    ("ms_0golbfqyrdq4im6jf6ajivwy9", "bridge_multiselect", True),
    ("corp_tech_application", "application_fact", False),
    ("credit_contract", "contract_fact", False),
)


def main() -> int:
    rag_adapter._entity_role_map_reset_for_tests()
    mapping = rag_adapter._entity_role_map()
    print("table_knowledge_v2 csv mapping: " + str(len(mapping)) + " rows")

    errors: list[str] = []
    for table, expected_role, expected_bridge in CASES:
        actual_role = mapping.get(table, "<missing>")
        actual_bridge = rag_adapter.is_bridge_table(table)
        marker = "OK" if (actual_role == expected_role and actual_bridge == expected_bridge) else "FAIL"
        print(f" - {marker:4} {table:40} role={actual_role:24} bridge={actual_bridge}")
        if actual_role != expected_role:
            errors.append(f"{table}: expected role {expected_role!r}, got {actual_role!r}")
        if actual_bridge != expected_bridge:
            errors.append(f"{table}: expected bridge={expected_bridge}, got {actual_bridge}")

    raw = (
        "Таблица: corp_tech_application\nОписание: Заявка на корп. тех.\nКолонки: id, status\n\n"
        "Таблица: ms_0n8ohjyx7oszo6a47ca9g0s6f\nОписание: MultiSelect container\nКолонки: id, obj_id\n\n"
        "Таблица: credit_contract\nОписание: Договор\nКолонки: id, contract_date\n"
    )
    stripped = rag_adapter.strip_bridge_table_blocks(raw)
    if "ms_0n8ohjyx7oszo6a47ca9g0s6f" in stripped:
        errors.append("strip_bridge_table_blocks left ms_* block in output")
    for keep in ("corp_tech_application", "credit_contract"):
        if keep not in stripped:
            errors.append(f"strip_bridge_table_blocks dropped legit block {keep!r}")

    hits_in = [
        {"table_name": "corp_tech_application", "score": 0.91},
        {"table_name": "ms_0n8ohjyx7oszo6a47ca9g0s6f", "score": 0.88},
        {"table_name": "credit_contract", "score": 0.74},
    ]
    hits_out = rag_adapter._filter_bridge_hits(hits_in)
    if any(h["table_name"].startswith("ms_") for h in hits_out):
        errors.append("_filter_bridge_hits left an ms_* hit in output")

    print()
    if errors:
        print("FAIL")
        for line in errors:
            print(" - " + line)
        print(json.dumps({"verdict": "FAIL", "errors": errors}, ensure_ascii=False))
        return 1
    print("OK")
    print(json.dumps({
        "verdict": "PASS",
        "csv_rows": len(mapping),
        "bridge_count": sum(1 for r in mapping.values() if r == "bridge_multiselect"),
        "application_fact_count": sum(1 for r in mapping.values() if r == "application_fact"),
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
