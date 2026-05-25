"""Read active PostgreSQL schema objects for the synthetic loader."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class ColumnInfo:
    name: str
    ordinal: int
    data_type: str
    udt_name: str
    nullable: bool
    default: str | None
    char_max: int | None
    numeric_precision: int | None
    numeric_scale: int | None


@dataclass(frozen=True)
class ForeignKeyInfo:
    name: str
    table: str
    columns: tuple[str, ...]
    ref_table: str
    ref_columns: tuple[str, ...]
    nullable: bool
    self_ref: bool


@dataclass
class TableInfo:
    name: str
    columns: list[ColumnInfo] = field(default_factory=list)
    pk: tuple[str, ...] = ()
    fks: list[ForeignKeyInfo] = field(default_factory=list)
    cardinality: str = "medium"
    pii_tags: dict[str, str] = field(default_factory=dict)

    @property
    def column_map(self) -> dict[str, ColumnInfo]:
        return {item.name: item for item in self.columns}


@dataclass
class SchemaInfo:
    schema: str
    tables: dict[str, TableInfo]
    overlay_path: str
    active_fk_count: int
    active_pk_count: int


def load_overlay(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    tables = data.get("tables")
    if not isinstance(tables, dict) or not tables:
        raise ValueError("overlay must contain non-empty 'tables' object")
    return data


def inspect_schema(conn: Any, schema: str, overlay_path: Path) -> SchemaInfo:
    overlay = load_overlay(overlay_path)
    overlay_tables = set((overlay.get("tables") or {}).keys())
    tables = {
        name: TableInfo(
            name=name,
            cardinality=str(meta.get("cardinality_hint") or "medium"),
            pii_tags={str(k): str(v) for k, v in (meta.get("pii_tags") or {}).items()},
        )
        for name, meta in (overlay.get("tables") or {}).items()
    }

    db_tables = _base_tables(conn, schema)
    missing = sorted(overlay_tables - db_tables)
    if missing:
        raise ValueError("overlay tables missing in DB: " + ", ".join(missing))

    _read_columns(conn, schema, tables)
    active_pk_count = _read_pk(conn, schema, tables)
    active_fk_count = _read_fk(conn, schema, tables, overlay_tables)

    no_pk = sorted(name for name, item in tables.items() if not item.pk)
    if no_pk:
        raise ValueError("tables without active PK: " + ", ".join(no_pk))

    return SchemaInfo(
        schema=schema,
        tables=dict(sorted(tables.items())),
        overlay_path=str(overlay_path),
        active_fk_count=active_fk_count,
        active_pk_count=active_pk_count,
    )


def _base_tables(conn: Any, schema: str) -> set[str]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT table_name
            FROM information_schema.tables
            WHERE table_schema = %s AND table_type = 'BASE TABLE'
            """,
            (schema,),
        )
        return {str(row[0]) for row in cur.fetchall()}


def _read_columns(conn: Any, schema: str, tables: dict[str, TableInfo]) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT table_name, column_name, ordinal_position, data_type, udt_name,
                   is_nullable, column_default, character_maximum_length,
                   numeric_precision, numeric_scale
            FROM information_schema.columns
            WHERE table_schema = %s AND table_name = ANY(%s)
            ORDER BY table_name, ordinal_position
            """,
            (schema, list(tables)),
        )
        for row in cur.fetchall():
            table = tables[str(row[0])]
            table.columns.append(
                ColumnInfo(
                    name=str(row[1]),
                    ordinal=int(row[2]),
                    data_type=str(row[3]),
                    udt_name=str(row[4]),
                    nullable=str(row[5]) == "YES",
                    default=row[6],
                    char_max=row[7],
                    numeric_precision=row[8],
                    numeric_scale=row[9],
                )
            )


def _read_pk(conn: Any, schema: str, tables: dict[str, TableInfo]) -> int:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT c.relname AS table_name,
                   array_agg(a.attname ORDER BY u.ord) AS cols
            FROM pg_constraint con
            JOIN pg_class c ON c.oid = con.conrelid
            JOIN pg_namespace n ON n.oid = c.relnamespace
            JOIN unnest(con.conkey) WITH ORDINALITY AS u(attnum, ord) ON true
            JOIN pg_attribute a ON a.attrelid = c.oid AND a.attnum = u.attnum
            WHERE n.nspname = %s AND con.contype = 'p' AND c.relname = ANY(%s)
            GROUP BY c.relname
            """,
            (schema, list(tables)),
        )
        count = 0
        for table_name, cols in cur.fetchall():
            tables[str(table_name)].pk = tuple(str(item) for item in cols)
            count += 1
        return count


def _read_fk(
    conn: Any,
    schema: str,
    tables: dict[str, TableInfo],
    overlay_tables: set[str],
) -> int:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT child.relname AS child_table,
                   parent.relname AS parent_table,
                   con.conname,
                   array_agg(ca.attname ORDER BY u.ord) AS child_cols,
                   array_agg(pa.attname ORDER BY u.ord) AS parent_cols,
                   bool_and(NOT ca.attnotnull) AS nullable
            FROM pg_constraint con
            JOIN pg_class child ON child.oid = con.conrelid
            JOIN pg_class parent ON parent.oid = con.confrelid
            JOIN pg_namespace n ON n.oid = child.relnamespace
            JOIN unnest(con.conkey, con.confkey) WITH ORDINALITY
                 AS u(child_attnum, parent_attnum, ord) ON true
            JOIN pg_attribute ca ON ca.attrelid = child.oid AND ca.attnum = u.child_attnum
            JOIN pg_attribute pa ON pa.attrelid = parent.oid AND pa.attnum = u.parent_attnum
            WHERE n.nspname = %s AND con.contype = 'f' AND child.relname = ANY(%s)
            GROUP BY child.relname, parent.relname, con.conname
            ORDER BY child.relname, con.conname
            """,
            (schema, list(tables)),
        )
        count = 0
        missing_parent: list[str] = []
        for child, parent, name, child_cols, parent_cols, nullable in cur.fetchall():
            child_name = str(child)
            parent_name = str(parent)
            if parent_name not in overlay_tables:
                missing_parent.append(child_name + "." + str(name) + " -> " + parent_name)
                continue
            fk = ForeignKeyInfo(
                name=str(name),
                table=child_name,
                columns=tuple(str(item) for item in child_cols),
                ref_table=parent_name,
                ref_columns=tuple(str(item) for item in parent_cols),
                nullable=bool(nullable),
                self_ref=child_name == parent_name,
            )
            tables[child_name].fks.append(fk)
            count += 1
        if missing_parent:
            raise ValueError("active FK parent outside overlay: " + "; ".join(missing_parent))
        return count
