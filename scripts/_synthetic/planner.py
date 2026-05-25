"""Budget and insertion order planning."""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass

from scripts._synthetic.introspect import SchemaInfo


WEIGHTS = {
    "small": 1,
    "medium": 8,
    "large": 40,
    "very_large": 120,
}


@dataclass(frozen=True)
class BudgetPlan:
    rows: dict[str, int]
    mode: str
    target_rows: int
    min_sum: int


FLOOR_ROWS = 20


def build_budget(
    schema: SchemaInfo,
    target_rows: int,
    target_mode: str = "total",
    table_limits: dict[str, int] | None = None,
) -> BudgetPlan:
    if target_rows <= 0:
        raise ValueError("--target-rows must be greater than zero")
    table_names = sorted(schema.tables)
    total = target_rows * len(table_names) if target_mode == "per_table_avg" else target_rows
    min_sum = FLOOR_ROWS * len(table_names)
    if total < min_sum:
        raise ValueError(
            f"--target-rows must be >= {min_sum} for a {len(table_names)}-table overlay (floor {FLOOR_ROWS} per table)"
        )

    rows = {name: FLOOR_ROWS for name in table_names}
    rest = total - min_sum

    class_counts: dict[str, int] = defaultdict(int)
    for name in table_names:
        class_counts[schema.tables[name].cardinality] += 1

    table_weights: dict[str, float] = {}
    total_weight = 0.0
    for name in table_names:
        card = schema.tables[name].cardinality
        weight = WEIGHTS.get(card, WEIGHTS["medium"]) / max(class_counts.get(card, 1), 1)
        table_weights[name] = weight
        total_weight += weight

    _spread_weighted(rows, rest, table_names, table_weights, total_weight)

    for table, limit in (table_limits or {}).items():
        if table not in rows:
            raise ValueError("unknown --table-limit table: " + table)
        if limit < 0:
            raise ValueError("--table-limit must be >= 0")
        rows[table] = limit
    _fix_rounding(rows, total if not table_limits else sum(rows.values()), schema)
    return BudgetPlan(rows=rows, mode="weighted_floor", target_rows=sum(rows.values()), min_sum=min_sum)


def insertion_order(schema: SchemaInfo) -> list[str]:
    tables = schema.tables
    graph: dict[str, set[str]] = defaultdict(set)
    indeg = {name: 0 for name in tables}
    for table in tables.values():
        for fk in table.fks:
            if fk.self_ref or fk.nullable:
                continue
            parent = fk.ref_table
            child = fk.table
            if child not in graph[parent]:
                graph[parent].add(child)
                indeg[child] += 1

    queue = deque(sorted(name for name, value in indeg.items() if value == 0))
    order: list[str] = []
    while queue:
        name = queue.popleft()
        order.append(name)
        for child in sorted(graph[name]):
            indeg[child] -= 1
            if indeg[child] == 0:
                queue.append(child)

    rest = sorted(name for name, value in indeg.items() if value > 0)
    if rest:
        raise ValueError("required FK cycle cannot be inserted safely: " + ", ".join(rest))
    return order


def _spread_weighted(
    rows: dict[str, int],
    rest: int,
    names: list[str],
    table_weights: dict[str, float],
    total_weight: float,
) -> None:
    if rest <= 0 or total_weight <= 0:
        return
    used = 0
    parts: list[tuple[float, str]] = []
    for name in names:
        raw = rest * table_weights[name] / total_weight
        value = int(raw)
        rows[name] += value
        used += value
        parts.append((raw - value, name))
    remain = rest - used
    for _, name in sorted(parts, reverse=True)[:remain]:
        rows[name] += 1


def _fix_rounding(rows: dict[str, int], target: int, schema: SchemaInfo) -> None:
    diff = target - sum(rows.values())
    if diff == 0:
        return
    biggest = max(rows, key=lambda name: (rows[name], WEIGHTS.get(schema.tables[name].cardinality, 8)))
    rows[biggest] += diff
