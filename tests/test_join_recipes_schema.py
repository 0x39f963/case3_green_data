# Generated at: 2026-05-29 13:54:40 MSK

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from app import rag_adapter


JOIN_RE = re.compile(
    r"^([A-Za-z_][\w]*)\.([A-Za-z_][\w]*)\s*=\s*([A-Za-z_][\w]*)\.([A-Za-z_][\w]*)$"
)


def _columns(table_meta: dict[str, Any]) -> set[str]:
    raw = table_meta.get("columns") or {}
    if isinstance(raw, dict):
        return {str(name) for name in raw}
    if isinstance(raw, list):
        return {str(name) for name in raw}
    return set()


def _fks_for_col(table_meta: dict[str, Any], col: str) -> list[dict[str, Any]]:
    target = str(col).lower()
    out: list[dict[str, Any]] = []
    for fk in table_meta.get("foreign_keys") or []:
        cols = [str(item).lower() for item in fk.get("columns") or []]
        if cols == [target]:
            out.append(fk)
    return out


def _fk_matches(
    tables: dict[str, dict[str, Any]],
    left_table: str,
    left_col: str,
    right_table: str,
    right_col: str,
) -> bool:
    table_meta = tables.get(left_table) or {}
    for fk in _fks_for_col(table_meta, left_col):
        ref_cols = [str(item).lower() for item in fk.get("references_columns") or []]
        if (
            str(fk.get("references_table")) == right_table
            and ref_cols == [right_col.lower()]
        ):
            return True
    return False


def test_join_recipes_reference_existing_schema_objects_and_fks() -> None:
    recipes_path = Path("deploy/join_recipes.json")
    data = json.loads(recipes_path.read_text(encoding="utf-8"))
    recipes = data.get("recipes") or []
    tables = (rag_adapter._load_schema() or {}).get("tables") or {}

    assert recipes

    for recipe in recipes:
        recipe_id = recipe.get("id")
        for table in recipe.get("tables") or []:
            assert table in tables, f"{recipe_id}: missing table {table}"

        for join in recipe.get("joins") or []:
            match = JOIN_RE.match(str(join))
            assert match, f"{recipe_id}: unsupported join expression {join!r}"
            left_table, left_col, right_table, right_col = match.groups()

            assert left_table in tables, f"{recipe_id}: missing table {left_table}"
            assert right_table in tables, f"{recipe_id}: missing table {right_table}"
            assert left_col in _columns(tables[left_table]), (
                f"{recipe_id}: missing column {left_table}.{left_col}"
            )
            assert right_col in _columns(tables[right_table]), (
                f"{recipe_id}: missing column {right_table}.{right_col}"
            )

            left_fks = _fks_for_col(tables[left_table], left_col)
            right_fks = _fks_for_col(tables[right_table], right_col)
            if left_fks or right_fks:
                assert _fk_matches(
                    tables,
                    left_table,
                    left_col,
                    right_table,
                    right_col,
                ) or _fk_matches(
                    tables,
                    right_table,
                    right_col,
                    left_table,
                    left_col,
                ), f"{recipe_id}: join is not backed by FK metadata: {join}"
