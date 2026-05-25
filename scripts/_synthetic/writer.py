"""Append-only writer and validation helpers."""

from __future__ import annotations

import hashlib
import logging
import random
import re
import time
from dataclasses import dataclass, field
from typing import Any

import psycopg2.extras
from psycopg2 import sql

from scripts._synthetic.introspect import ForeignKeyInfo, SchemaInfo, TableInfo
from scripts._synthetic.values import EMAIL_DOMAINS, ValueMaker


@dataclass
class IdPool:
    samples: list[int] = field(default_factory=list)
    ranges: list[tuple[int, int]] = field(default_factory=list)

    def add_sample(self, values: list[int], limit: int = 50_000) -> None:
        if len(self.samples) >= limit:
            return
        self.samples.extend(values[: max(0, limit - len(self.samples))])

    def add_range(self, start: int, end: int) -> None:
        if end >= start:
            self.ranges.append((start, end))

    def any(self, rng: random.Random) -> int | None:
        if self.ranges and (not self.samples or rng.random() < 0.75):
            start, end = rng.choice(self.ranges)
            return rng.randint(start, end)
        if self.samples:
            return rng.choice(self.samples)
        return None


@dataclass
class WriteResult:
    inserted: dict[str, int]
    errors: dict[str, str]
    sequences_updated: int
    sequences_skipped: int
    sequences_skipped_reason: str | None
    analyzed: bool
    sample_uniqueness: dict[str, dict[str, float | int]]
    fk_orphan_check: dict[str, int]
    pii_check: dict[str, int | dict[str, int]]
    fragment_sha256: dict[str, str]


class SyntheticWriter:
    def __init__(
        self,
        conn: Any,
        schema: SchemaInfo,
        budget: dict[str, int],
        order: list[str],
        seed: int,
        batch_size: int,
        locale: str,
        logger: logging.Logger,
    ) -> None:
        self.conn = conn
        self.schema = schema
        self.budget = budget
        self.order = order
        self.rng = random.Random(seed)
        self.value_maker = ValueMaker(seed, locale)
        self.batch_size = batch_size
        self.logger = logger
        self.pools: dict[str, IdPool] = {name: IdPool() for name in schema.tables}
        self.next_id: dict[str, int] = {}
        self.seen_pk: dict[str, set[tuple[Any, ...]]] = {name: set() for name in schema.tables}
        self.fk_by_col = _fk_by_col(schema)

    def load_state(self) -> None:
        for table in self.schema.tables.values():
            self.next_id[table.name] = self._max_id(table.name) + 1
            self.pools[table.name].add_sample(self._sample_ids(table.name))

    def write(self, analyze: bool = True) -> WriteResult:
        self.load_state()
        inserted: dict[str, int] = {}
        errors: dict[str, str] = {}
        fragments: dict[str, str] = {}
        seq_updated = 0
        seq_skipped = 0
        seq_skip_reasons: set[str] = set()

        for table_name in self.order:
            table = self.schema.tables[table_name]
            target = self.budget.get(table_name, 0)
            inserted[table_name] = 0
            started = time.perf_counter()
            try:
                for offset in range(0, target, self.batch_size):
                    size = min(self.batch_size, target - offset)
                    rows = [self._row(table, offset + idx) for idx in range(size)]
                    self._insert_batch(table, rows)
                    inserted[table_name] += len(rows)
                    self._add_inserted_ids(table, rows)
                    if table_name not in fragments:
                        fragments[table_name] = _rows_hash(rows[:5])
                updated, skipped_reason = self._set_sequence(table)
                seq_updated += updated
                if skipped_reason:
                    seq_skipped += 1
                    seq_skip_reasons.add(skipped_reason)
            except Exception as exc:
                self.conn.rollback()
                errors[table_name] = exc.__class__.__name__ + ": " + str(exc)
                self.logger.exception("table failed %s", table_name)
                break
            elapsed = round(time.perf_counter() - started, 3)
            self.logger.info("table=%s inserted=%s elapsed_sec=%s", table_name, inserted[table_name], elapsed)

        analyzed = False
        if analyze and not errors:
            self._analyze([name for name, count in inserted.items() if count > 0])
            analyzed = True

        return WriteResult(
            inserted=inserted,
            errors=errors,
            sequences_updated=seq_updated,
            sequences_skipped=seq_skipped,
            sequences_skipped_reason=_sequence_skip_reason(seq_skip_reasons),
            analyzed=analyzed,
            sample_uniqueness=self.sample_uniqueness(),
            fk_orphan_check=self.validate_fk(),
            pii_check=self.validate_pii(),
            fragment_sha256=fragments,
        )

    def _row(self, table: TableInfo, row_no: int) -> dict[str, Any]:
        for _ in range(30):
            values: dict[str, Any] = {}
            self._fill_fk_values(table, values, row_no)
            if table.pk == ("id",) and "id" not in values:
                values["id"] = self.next_id[table.name]
                self.next_id[table.name] += 1
            value_no = int(values.get("id") or (row_no + self.next_id.get(table.name, 1)))
            for col in table.columns:
                if col.name in values:
                    continue
                if col.nullable and col.name not in table.pk and self.rng.random() < 0.08:
                    values[col.name] = None
                else:
                    values[col.name] = self.value_maker.value(table, col, value_no)
            pk = tuple(values[col] for col in table.pk)
            if pk not in self.seen_pk[table.name]:
                self.seen_pk[table.name].add(pk)
                return values
        raise RuntimeError("cannot build unique PK tuple for " + table.name)

    def _fill_fk_values(self, table: TableInfo, values: dict[str, Any], row_no: int) -> None:
        for fk in table.fks:
            if len(fk.columns) != 1 or len(fk.ref_columns) != 1:
                continue
            col = table.column_map[fk.columns[0]]
            required = (not fk.nullable) or col.name in table.pk
            if fk.self_ref:
                value = self._self_ref(table.name, row_no) if row_no > 0 else None
            else:
                value = self.pools[fk.ref_table].any(self.rng)
            if value is None:
                if required:
                    raise RuntimeError("empty parent pool for FK " + fk.name)
                values[col.name] = None
                continue
            if required or self.rng.random() < self._fk_fill_rate(fk):
                values[col.name] = value
            else:
                values[col.name] = None

    def _self_ref(self, table_name: str, row_no: int) -> int | None:
        if row_no < max(1, self.budget.get(table_name, 0) // 20):
            return None
        return self.pools[table_name].any(self.rng)

    def _fk_fill_rate(self, fk: ForeignKeyInfo) -> float:
        hint = self.schema.tables[fk.ref_table].cardinality
        if hint in {"large", "very_large"}:
            return 0.95
        if hint == "medium":
            return 0.85
        return 0.70

    def _insert_batch(self, table: TableInfo, rows: list[dict[str, Any]]) -> None:
        if not rows:
            return
        cols = [col.name for col in table.columns]
        data = [tuple(row[col] for col in cols) for row in rows]
        query = sql.SQL("INSERT INTO {}.{} ({}) VALUES %s").format(
            sql.Identifier(self.schema.schema),
            sql.Identifier(table.name),
            sql.SQL(", ").join(sql.Identifier(col) for col in cols),
        )
        with self.conn.cursor() as cur:
            psycopg2.extras.execute_values(
                cur,
                query.as_string(self.conn),
                data,
                page_size=len(data),
            )
        self.conn.commit()

    def _add_inserted_ids(self, table: TableInfo, rows: list[dict[str, Any]]) -> None:
        if "id" not in table.column_map:
            return
        ids = [int(row["id"]) for row in rows if row.get("id") is not None]
        if table.pk == ("id",) and ids:
            self.pools[table.name].add_range(min(ids), max(ids))
        self.pools[table.name].add_sample(ids)

    def _max_id(self, table: str) -> int:
        query = sql.SQL("SELECT COALESCE(MAX(id), 0) FROM {}.{}").format(
            sql.Identifier(self.schema.schema),
            sql.Identifier(table),
        )
        with self.conn.cursor() as cur:
            cur.execute(query)
            return int(cur.fetchone()[0] or 0)

    def _sample_ids(self, table: str) -> list[int]:
        query = sql.SQL("SELECT id FROM {}.{} WHERE id IS NOT NULL ORDER BY id DESC LIMIT 50000").format(
            sql.Identifier(self.schema.schema),
            sql.Identifier(table),
        )
        with self.conn.cursor() as cur:
            cur.execute(query)
            return [int(row[0]) for row in cur.fetchall()]

    def _set_sequence(self, table: TableInfo) -> tuple[int, str | None]:
        if "id" not in table.pk:
            return 0, "no_id_pk"
        with self.conn.cursor() as cur:
            cur.execute("SELECT pg_get_serial_sequence(%s, %s)", (self.schema.schema + "." + table.name, "id"))
            seq = cur.fetchone()[0]
            if not seq:
                self.conn.commit()
                return 0, "no_serial_sequence"
            query = sql.SQL("SELECT setval(%s, COALESCE(MAX(id), 1)) FROM {}.{}").format(
                sql.Identifier(self.schema.schema),
                sql.Identifier(table.name),
            )
            cur.execute(query, (seq,))
        self.conn.commit()
        return 1, None

    def _analyze(self, tables: list[str]) -> None:
        with self.conn.cursor() as cur:
            for table in tables:
                cur.execute(
                    sql.SQL("ANALYZE {}.{}").format(
                        sql.Identifier(self.schema.schema),
                        sql.Identifier(table),
                    )
                )
        self.conn.commit()

    def validate_fk(self) -> dict[str, int]:
        checked = 0
        violations = 0
        with self.conn.cursor() as cur:
            for table in self.schema.tables.values():
                for fk in table.fks:
                    if len(fk.columns) != 1 or len(fk.ref_columns) != 1:
                        continue
                    checked += 1
                    query = sql.SQL(
                        "SELECT COUNT(*) FROM {}.{} child "
                        "LEFT JOIN {}.{} parent ON child.{} = parent.{} "
                        "WHERE child.{} IS NOT NULL AND parent.{} IS NULL"
                    ).format(
                        sql.Identifier(self.schema.schema),
                        sql.Identifier(fk.table),
                        sql.Identifier(self.schema.schema),
                        sql.Identifier(fk.ref_table),
                        sql.Identifier(fk.columns[0]),
                        sql.Identifier(fk.ref_columns[0]),
                        sql.Identifier(fk.columns[0]),
                        sql.Identifier(fk.ref_columns[0]),
                    )
                    cur.execute(query)
                    violations += int(cur.fetchone()[0] or 0)
        return {"checked": checked, "violations": violations}

    def validate_pii(self) -> dict[str, int | dict[str, int]]:
        tagged = 0
        checked_with_regex = 0
        skipped_non_text = 0
        skipped_no_regex = 0
        violations = 0
        skipped_by_reason = {"non_text": 0, "no_regex_for_tag": 0, "missing_column": 0}
        with self.conn.cursor() as cur:
            for table in self.schema.tables.values():
                for col, tag in table.pii_tags.items():
                    tagged += 1
                    if col not in table.column_map:
                        skipped_by_reason["missing_column"] += 1
                        continue
                    data_type = table.column_map[col].data_type
                    check = _pii_bad_sql(self.schema.schema, table.name, col, tag, data_type)
                    if check is None:
                        if data_type.lower() not in {"text", "character varying"}:
                            skipped_non_text += 1
                            skipped_by_reason["non_text"] += 1
                        else:
                            skipped_no_regex += 1
                            skipped_by_reason["no_regex_for_tag"] += 1
                        continue
                    checked_with_regex += 1
                    bad_sql, params = check
                    cur.execute(bad_sql, params)
                    violations += int(cur.fetchone()[0] or 0)
        return {
            "tagged_columns": tagged,
            "checked": checked_with_regex,
            "checked_with_regex": checked_with_regex,
            "skipped_non_text": skipped_non_text,
            "skipped_no_regex": skipped_no_regex,
            "skipped_by_reason": skipped_by_reason,
            "violations": violations,
        }

    def sample_uniqueness(self) -> dict[str, dict[str, float | int]]:
        result: dict[str, dict[str, float | int]] = {}
        with self.conn.cursor() as cur:
            for table in self.schema.tables.values():
                if table.cardinality not in {"large", "very_large"}:
                    continue
                for col in table.columns:
                    if col.data_type.lower() not in {"text", "character varying"}:
                        continue
                    name = table.name + "." + col.name
                    query = sql.SQL(
                        "WITH s AS (SELECT {} AS v FROM {}.{} WHERE {} IS NOT NULL LIMIT 5000) "
                        "SELECT COUNT(*)::int, COUNT(DISTINCT v)::int FROM s"
                    ).format(
                        sql.Identifier(col.name),
                        sql.Identifier(self.schema.schema),
                        sql.Identifier(table.name),
                        sql.Identifier(col.name),
                    )
                    cur.execute(query)
                    sample, distinct = cur.fetchone()
                    if sample:
                        result[name] = {
                            "sample_size": int(sample),
                            "distinct_ratio": round(int(distinct) / max(int(sample), 1), 4),
                        }
        return result


def _fk_by_col(schema: SchemaInfo) -> dict[str, dict[str, ForeignKeyInfo]]:
    out: dict[str, dict[str, ForeignKeyInfo]] = {}
    for table in schema.tables.values():
        out[table.name] = {}
        for fk in table.fks:
            for col in fk.columns:
                out[table.name][col] = fk
    return out


def _rows_hash(rows: list[dict[str, Any]]) -> str:
    h = hashlib.sha256()
    for row in rows:
        safe = {key: type(value).__name__ for key, value in sorted(row.items())}
        h.update(repr(safe).encode("utf-8"))
    return h.hexdigest()


def _pii_bad_sql(schema: str, table: str, col: str, tag: str, data_type: str) -> tuple[Any, tuple[Any, ...]] | None:
    tag_l = tag.lower()
    if data_type.lower() not in {"text", "character varying"}:
        return None
    ident = sql.SQL("{}.{}").format(sql.Identifier(schema), sql.Identifier(table))
    col_id = sql.Identifier(col)
    if "email" in tag_l or "email" in col.lower():
        pattern = r"^[^@]+@(" + "|".join(re.escape(item) for item in EMAIL_DOMAINS) + ")$"
        query = sql.SQL("SELECT COUNT(*) FROM {} WHERE {} IS NOT NULL AND {}::text !~ %s").format(ident, col_id, col_id)
        return query, (pattern,)
    if "phone" in tag_l or "phone" in col.lower():
        query = sql.SQL("SELECT COUNT(*) FROM {} WHERE {} IS NOT NULL AND {}::text !~ %s").format(ident, col_id, col_id)
        return query, (r"^\+79[0-9]{9}$",)
    if "inn" in tag_l or col.lower() == "inn":
        query = sql.SQL("SELECT COUNT(*) FROM {} WHERE {} IS NOT NULL AND {}::text !~ %s").format(ident, col_id, col_id)
        return query, (r"^[0-9]{12}$",)
    if any(word in tag_l for word in ("contract", "credit", "passport")):
        query = sql.SQL(
            "SELECT COUNT(*) FROM {} WHERE {} IS NOT NULL AND {}::text ~* %s"
        ).format(ident, col_id, col_id)
        return query, (r"(gmail|yandex|mail\.ru|@)",)
    return None


def _sequence_skip_reason(reasons: set[str]) -> str | None:
    if not reasons:
        return None
    if reasons == {"no_serial_sequence"}:
        return "no_serial_sequence"
    return ",".join(sorted(reasons))
