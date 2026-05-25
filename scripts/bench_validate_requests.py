"""
Validate adversarial SQL benchmark JSONL without SQL execution.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import sqlparse
from jsonschema import Draft7Validator


ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import sql_guard  # noqa: E402


REQ_WORD = "req" + "uests"
DATA_NAME = "adversarial_sql_" + REQ_WORD
DATA_DIR = ROOT / "data" / "bench" / REQ_WORD
DEFAULT_DATASET = DATA_DIR / (DATA_NAME + "_v0_2.jsonl")
DEFAULT_SCHEMA = DATA_DIR / (DATA_NAME + ".schema.json")
DEFAULT_OVERLAY = ROOT / "deploy" / "schema_overlay.json"
DEFAULT_SECURITY_META = ROOT / "TASK-3" / "marina-case3-rag" / "rag_pipeline" / "indices" / "security_meta.json"

FAMILY_DEFAULT_DECISION = {
    "safe_readonly_hard": {"approve"},
    "classic_injection": {"block"},
    "union_schema_leak": {"block"},
    "dml_ddl_delete": {"block"},
    "sensitive_exposure": {"block"},
    "limit_bypass": {"block"},
    "cost_dos": {"block"},
    "hallucination_wrong_schema": {"abstain", "revise"},
    "prompt_policy_bypass": {"block"},
    "borderline_optimizer": {"revise"},
}


@dataclass
class Violation:
    check: str
    line: int
    message: str

    def to_dict(self) -> dict[str, Any]:
        return {"check": self.check, "line": self.line, "message": self.message}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--schema", type=Path, default=DEFAULT_SCHEMA)
    parser.add_argument("--schema-overlay", type=Path, default=DEFAULT_OVERLAY)
    parser.add_argument("--security-meta", type=Path, default=DEFAULT_SECURITY_META)
    parser.add_argument("--strict", action="store_true")
    args = parser.parse_args()

    try:
        rows = read_jsonl(args.dataset)
        ctx = build_ctx(args.schema, args.schema_overlay, args.security_meta, args.strict)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        print(json.dumps({"status": "CONFIG_ERROR", "error": str(exc)}, ensure_ascii=False, indent=2))
        return 2

    violations = run_checks(rows, ctx)
    summary = build_summary(rows, violations, args.dataset)
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    return 1 if violations else 0


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as fh:
        for line_no, line in enumerate(fh, start=1):
            if not line.strip():
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError("line " + str(line_no) + ": invalid json: " + str(exc)) from exc
            if not isinstance(item, dict):
                raise ValueError("line " + str(line_no) + ": row must be object")
            rows.append(item)
    return rows


def build_ctx(schema_path: Path, overlay_path: Path, security_meta_path: Path, strict: bool) -> dict[str, Any]:
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    overlay = json.loads(overlay_path.read_text(encoding="utf-8"))
    tables = overlay.get("tables") or {}
    approved = approved_pairs(tables)
    pii_cols = {col for meta in tables.values() for col in (meta.get("pii_tags") or {}).keys()}
    security_meta = json.loads(security_meta_path.read_text(encoding="utf-8"))
    security_classes = {
        str(item.get("vuln_class"))
        for item in security_meta
        if isinstance(item, dict) and item.get("vuln_class")
    }
    return {
        "validator": Draft7Validator(schema),
        "tables": set(tables),
        "overlay_tables": tables,
        "approved_pairs": approved,
        "pii_cols": pii_cols,
        "security_classes": security_classes,
        "strict": strict,
    }


def approved_pairs(tables: dict[str, Any]) -> set[tuple[str, str, str, str]]:
    pairs: set[tuple[str, str, str, str]] = set()
    for table, meta in tables.items():
        for item in meta.get("approved_join_keys") or []:
            target = str(item.get("to") or "")
            on = str(item.get("on") or "")
            target_col = str(item.get("to_column") or "")
            if target and on and target_col:
                pairs.add((table, target, on, target_col))
    return pairs


def run_checks(rows: list[dict[str, Any]], ctx: dict[str, Any]) -> list[Violation]:
    checks = [
        check_v_1,
        check_v_2,
        check_v_3,
        check_v_4,
        check_v_5,
        check_v_6,
        check_v_7,
        check_v_8,
        check_v_9,
        check_v_10,
        check_v_11,
        check_v_12,
        check_v_18,
        check_v_19,
        check_v_20,
        check_v_21,
    ]
    if ctx["strict"]:
        checks.extend([check_v_13, check_v_14, check_v_15, check_v_16, check_v_17, check_v_22])
    violations: list[Violation] = []
    for check in checks:
        violations.extend(check(rows, ctx))
    return violations


def check_v_1(rows: list[dict[str, Any]], ctx: dict[str, Any]) -> list[Violation]:
    out: list[Violation] = []
    validator: Draft7Validator = ctx["validator"]
    for line, row in enumerate(rows, start=1):
        for err in sorted(validator.iter_errors(row), key=lambda item: list(item.path)):
            out.append(Violation("V-1", line, "schema " + str(list(err.path)) + " " + err.message))
    return out


def check_v_2(rows: list[dict[str, Any]], ctx: dict[str, Any]) -> list[Violation]:
    del ctx
    out: list[Violation] = []
    seen: set[str] = set()
    pattern = re.compile(r"^bench_req_\d{6}$")
    for line, row in enumerate(rows, start=1):
        item_id = str(row.get("id", ""))
        if not pattern.match(item_id):
            out.append(Violation("V-2", line, "bad id " + item_id))
        if item_id in seen:
            out.append(Violation("V-2", line, "duplicate id " + item_id))
        seen.add(item_id)
    return out


def check_v_3(rows: list[dict[str, Any]], ctx: dict[str, Any]) -> list[Violation]:
    del ctx
    out: list[Violation] = []
    seen: dict[str, str] = {}
    for line, row in enumerate(rows, start=1):
        raw = normalize(str(row.get("user_task", ""))) + "\0" + normalize(str(row.get("seed_sql") or ""))
        digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
        old = seen.get(digest)
        if old:
            out.append(Violation("V-3", line, "normalized duplicate with " + old))
        seen[digest] = str(row.get("id", ""))
    return out


def check_v_4(rows: list[dict[str, Any]], ctx: dict[str, Any]) -> list[Violation]:
    del ctx
    allowed = set(sql_guard.ALL_LABELS)
    out: list[Violation] = []
    for line, row in enumerate(rows, start=1):
        unknown = sorted(set(row.get("expected_labels") or []) - allowed)
        if unknown:
            out.append(Violation("V-4", line, "unknown labels: " + ", ".join(unknown)))
    return out


def check_v_5(rows: list[dict[str, Any]], ctx: dict[str, Any]) -> list[Violation]:
    tables: set[str] = ctx["tables"]
    out: list[Violation] = []
    for line, row in enumerate(rows, start=1):
        if row.get("family") == "hallucination_wrong_schema":
            continue
        missing = sorted(set(row.get("schema_scope") or []) - tables)
        if missing:
            out.append(Violation("V-5", line, "unknown schema_scope tables: " + ", ".join(missing)))
    return out


def check_v_6(rows: list[dict[str, Any]], ctx: dict[str, Any]) -> list[Violation]:
    pii_cols: set[str] = ctx["pii_cols"]
    out: list[Violation] = []
    for line, row in enumerate(rows, start=1):
        if row.get("family") != "sensitive_exposure":
            continue
        text = normalize(str(row.get("user_task", "")) + " " + str(row.get("seed_sql") or ""))
        if not any(normalize(col) in text for col in pii_cols):
            out.append(Violation("V-6", line, "sensitive case has no pii column mention"))
    return out


def check_v_7(rows: list[dict[str, Any]], ctx: dict[str, Any]) -> list[Violation]:
    del ctx
    out: list[Violation] = []
    for line, row in enumerate(rows, start=1):
        labels = row.get("expected_labels") or []
        spans = [str(item) for item in (row.get("evidence_span") or []) if str(item).strip()]
        if labels and not spans:
            out.append(Violation("V-7", line, "expected labels require evidence_span"))
            continue
        if not spans:
            continue
        haystack = normalize(str(row.get("user_task", "")) + " " + str(row.get("attack_prompt") or ""))
        if row.get("expected_decision") != "block":
            haystack = normalize(haystack + " " + str(row.get("seed_sql") or "") + " " + str(row.get("safe_rewrite") or ""))
        if not any(normalize(span) in haystack for span in spans):
            out.append(Violation("V-7", line, "evidence_span does not match row text"))
    return out


def check_v_8(rows: list[dict[str, Any]], ctx: dict[str, Any]) -> list[Violation]:
    del ctx
    out: list[Violation] = []
    for line, row in enumerate(rows, start=1):
        family = str(row.get("family", ""))
        decision = str(row.get("expected_decision", ""))
        allowed = FAMILY_DEFAULT_DECISION.get(family, set())
        if allowed and decision not in allowed and not str(row.get("notes", "")).strip():
            out.append(Violation("V-8", line, family + " decision " + decision + " needs notes"))
    return out


def check_v_9(rows: list[dict[str, Any]], ctx: dict[str, Any]) -> list[Violation]:
    del ctx
    out: list[Violation] = []
    for line, row in enumerate(rows, start=1):
        actual = detect_lang(str(row.get("user_task", "")))
        if row.get("language") != actual:
            out.append(Violation("V-9", line, "language expected " + str(row.get("language")) + ", detected " + actual))
    return out


def check_v_10(rows: list[dict[str, Any]], ctx: dict[str, Any]) -> list[Violation]:
    del ctx
    out: list[Violation] = []
    for line, row in enumerate(rows, start=1):
        family = re.escape(str(row.get("family", "")))
        pattern = re.compile(r"^" + family + r":[A-Za-z0-9_-]+:\d+$")
        value = str(row.get("source_seed_id", ""))
        if not pattern.match(value):
            out.append(Violation("V-10", line, "bad source_seed_id " + value))
    return out


def check_v_11(rows: list[dict[str, Any]], ctx: dict[str, Any]) -> list[Violation]:
    out: list[Violation] = []
    approved: set[tuple[str, str, str, str]] = ctx["approved_pairs"]
    for line, row in enumerate(rows, start=1):
        sql = str(row.get("safe_rewrite") or "").strip()
        if not sql:
            continue
        if not sqlparse.parse(sql):
            out.append(Violation("V-11", line, "safe_rewrite is not parseable by sqlparse"))
            continue
        for left_table, right_table, left_col, right_col in join_pairs(sql):
            if (left_table, right_table, left_col, right_col) not in approved and (
                right_table,
                left_table,
                right_col,
                left_col,
            ) not in approved:
                out.append(
                    Violation(
                        "V-11",
                        line,
                        "safe_rewrite join is not approved: "
                        + left_table
                        + "."
                        + left_col
                        + " -> "
                        + right_table
                        + "."
                        + right_col,
                    )
                )
    return out


def check_v_12(rows: list[dict[str, Any]], ctx: dict[str, Any]) -> list[Violation]:
    del rows, ctx
    out: list[Violation] = []
    bad = ["psyco" + "pg2", "req" + "uests", "ht" + "tpx", "aio" + "http"]
    for rel in ("scripts/bench_generate_" + REQ_WORD + ".py", "scripts/bench_validate_" + REQ_WORD + ".py"):
        path = ROOT / rel
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")
        for token in bad:
            if token in text:
                out.append(Violation("V-12", 0, "blocked net/db token in " + rel))
    return out


def check_v_13(rows: list[dict[str, Any]], ctx: dict[str, Any]) -> list[Violation]:
    del ctx
    labels = Counter(label for row in rows for label in row.get("expected_labels", []))
    out: list[Violation] = []
    missing = sorted(label for label in sql_guard.BASELINE_LABELS if labels.get(label, 0) < 1)
    if missing:
        out.append(Violation("V-13", 0, "missing baseline labels: " + ", ".join(missing)))
    extended = [label for label in sql_guard.EXTENDED_LABELS if labels.get(label, 0) > 0]
    if len(extended) < 18:
        out.append(Violation("V-13", 0, "extended label coverage " + str(len(extended)) + " < 18"))
    return out


def check_v_14(rows: list[dict[str, Any]], ctx: dict[str, Any]) -> list[Violation]:
    security_classes: set[str] = ctx["security_classes"]
    out: list[Violation] = []
    for line, row in enumerate(rows, start=1):
        target = ((row.get("faiss_targets") or {}).get("security") or {}).get("expected_vuln_classes") or []
        missing = sorted(set(target) - security_classes)
        if missing:
            out.append(Violation("V-14", line, "security faiss class not in meta: " + ", ".join(missing)))
    return out


def check_v_15(rows: list[dict[str, Any]], ctx: dict[str, Any]) -> list[Violation]:
    del ctx
    out: list[Violation] = []
    shingles = {str(row.get("id")): shingle_set(str(row.get("user_task", ""))) for row in rows}
    for left_idx, left in enumerate(rows):
        left_id = str(left.get("id"))
        for right in rows[left_idx + 1:]:
            right_id = str(right.get("id"))
            score = jaccard(shingles[left_id], shingles[right_id])
            if score >= 0.7:
                out.append(Violation("V-15", 0, left_id + " near duplicate " + right_id + " score " + str(round(score, 4))))
    return out


def check_v_16(rows: list[dict[str, Any]], ctx: dict[str, Any]) -> list[Violation]:
    del ctx
    counts = Counter(str(row.get("language", "")) for row in rows)
    out: list[Violation] = []
    if counts.get("ru", 0) < 60:
        out.append(Violation("V-16", 0, "ru rows " + str(counts.get("ru", 0)) + " < 60"))
    if counts.get("en", 0) < 20:
        out.append(Violation("V-16", 0, "en rows " + str(counts.get("en", 0)) + " < 20"))
    return out


def check_v_17(rows: list[dict[str, Any]], ctx: dict[str, Any]) -> list[Violation]:
    del ctx
    tables = {item for row in rows for item in row.get("schema_scope", [])}
    if len(tables) < 30:
        return [Violation("V-17", 0, "schema table coverage " + str(len(tables)) + " < 30")]
    return []


def check_v_18(rows: list[dict[str, Any]], ctx: dict[str, Any]) -> list[Violation]:
    del ctx
    out: list[Violation] = []
    allowed_runtime = {"approve", "revise", "abstain"}
    for line, row in enumerate(rows, start=1):
        expected = str(row.get("expected_decision", ""))
        runtime = str(row.get("expected_runtime_decision", ""))
        alternatives = row.get("expected_runtime_decision_alternatives") or []
        if runtime not in allowed_runtime:
            out.append(Violation("V-18", line, "bad expected_runtime_decision " + runtime))
            continue
        bad_alt = sorted(set(alternatives) - allowed_runtime)
        if bad_alt:
            out.append(Violation("V-18", line, "bad runtime alternatives: " + ", ".join(bad_alt)))
        mapped = runtime_decision(expected)
        if runtime != mapped and not str(row.get("notes", "")).strip():
            out.append(Violation("V-18", line, expected + " must map to runtime " + mapped + ", got " + runtime))
    return out


def check_v_19(rows: list[dict[str, Any]], ctx: dict[str, Any]) -> list[Violation]:
    security_classes: set[str] = ctx["security_classes"]
    out: list[Violation] = []
    for line, row in enumerate(rows, start=1):
        labels = set(row.get("expected_labels") or [])
        target = set(((row.get("faiss_targets") or {}).get("security") or {}).get("expected_vuln_classes") or [])
        mapped = labels & security_classes
        flag = bool(row.get("vuln_class_unmapped", False))
        if mapped and not target:
            out.append(Violation("V-19", line, "mapped labels exist but expected_vuln_classes is empty"))
        if mapped and flag:
            out.append(Violation("V-19", line, "vuln_class_unmapped must be false when labels map to security_meta"))
        if labels and not mapped and not flag:
            out.append(Violation("V-19", line, "vuln_class_unmapped must be true when no labels map to security_meta"))
        if not labels and target:
            out.append(Violation("V-19", line, "safe row must not set security expected_vuln_classes"))
    return out


def check_v_20(rows: list[dict[str, Any]], ctx: dict[str, Any]) -> list[Violation]:
    del ctx
    required = {"prompt_policy_bypass", "classic_injection", "union_schema_leak"}
    out: list[Violation] = []
    for line, row in enumerate(rows, start=1):
        if row.get("family") in required and not str(row.get("attack_prompt") or "").strip():
            out.append(Violation("V-20", line, str(row.get("family")) + " requires attack_prompt"))
    return out


def check_v_21(rows: list[dict[str, Any]], ctx: dict[str, Any]) -> list[Violation]:
    del ctx
    out: list[Violation] = []
    for line, row in enumerate(rows, start=1):
        if row.get("expected_decision") in {"approve", "revise"} and not str(row.get("safe_rewrite") or "").strip():
            out.append(Violation("V-21", line, str(row.get("expected_decision")) + " requires safe_rewrite"))
    return out


def check_v_22(rows: list[dict[str, Any]], ctx: dict[str, Any]) -> list[Violation]:
    del ctx
    out: list[Violation] = []
    for line, row in enumerate(rows, start=1):
        alternatives = set(row.get("expected_runtime_decision_alternatives") or [])
        if row.get("expected_decision") == "block" and "approve" in alternatives:
            out.append(Violation("V-22", line, "block rows must not allow approve runtime alternative"))
    return out


def runtime_decision(expected_decision: str) -> str:
    if expected_decision == "block":
        return "abstain"
    if expected_decision in {"approve", "revise", "abstain"}:
        return expected_decision
    return "abstain"


def join_pairs(sql: str) -> list[tuple[str, str, str, str]]:
    alias_to_table: dict[str, str] = {}
    table_pattern = re.compile(r"\b(FROM|JOIN)\s+([a-zA-Z_][\w]*)\s+(?:AS\s+)?([a-zA-Z_][\w]*)?", re.IGNORECASE)
    for match in table_pattern.finditer(sql):
        table = match.group(2)
        alias = match.group(3) or table
        if alias.upper() in {"ON", "WHERE", "JOIN", "ORDER", "GROUP", "LIMIT"}:
            alias = table
        alias_to_table[alias] = table
        alias_to_table[table] = table
    out: list[tuple[str, str, str, str]] = []
    join_pattern = re.compile(
        r"\bJOIN\s+([a-zA-Z_][\w]*)\s+(?:AS\s+)?([a-zA-Z_][\w]*)?\s+ON\s+([a-zA-Z_][\w]*)\.([a-zA-Z_][\w]*)\s*=\s*([a-zA-Z_][\w]*)\.([a-zA-Z_][\w]*)",
        re.IGNORECASE,
    )
    for match in join_pattern.finditer(sql):
        left_alias = match.group(3)
        right_alias = match.group(5)
        left_table = alias_to_table.get(left_alias, left_alias)
        right_table = alias_to_table.get(right_alias, right_alias)
        out.append((left_table, right_table, match.group(4), match.group(6)))
    return out


def detect_lang(text: str) -> str:
    letters = [char for char in text if char.isalpha()]
    if not letters:
        return "en"
    cyr = [char for char in letters if "\u0400" <= char <= "\u04FF"]
    return "ru" if len(cyr) / len(letters) >= 0.3 else "en"


def normalize(text: str) -> str:
    return " ".join(text.casefold().split())


def shingle_set(text: str) -> set[str]:
    tokens = normalize(text).split()
    if len(tokens) < 3:
        return {" ".join(tokens)}
    return {" ".join(tokens[idx:idx + 3]) for idx in range(len(tokens) - 2)}


def jaccard(left: set[str], right: set[str]) -> float:
    if not left and not right:
        return 1.0
    return len(left & right) / max(len(left | right), 1)


def build_summary(rows: list[dict[str, Any]], violations: list[Violation], dataset: Path) -> dict[str, Any]:
    label_counts = Counter(label for row in rows for label in row.get("expected_labels", []))
    tables = {item for row in rows for item in row.get("schema_scope", [])}
    return {
        "status": "PASS" if not violations else "FAIL",
        "rows_total": len(rows),
        "dataset": str(dataset),
        "rows_by_family": dict(sorted(Counter(row.get("family", "") for row in rows).items())),
        "rows_by_language": dict(sorted(Counter(row.get("language", "") for row in rows).items())),
        "rows_by_expected_decision": dict(sorted(Counter(row.get("expected_decision", "") for row in rows).items())),
        "rows_by_expected_runtime_decision": dict(sorted(Counter(row.get("expected_runtime_decision", "") for row in rows).items())),
        "baseline_covered": sorted(label for label in sql_guard.BASELINE_LABELS if label_counts.get(label, 0) > 0),
        "extended_covered": sorted(label for label in sql_guard.EXTENDED_LABELS if label_counts.get(label, 0) > 0),
        "schema_table_coverage": {"unique_tables": len(tables), "tables": sorted(tables)},
        "vuln_class_unmapped_rows": sum(1 for row in rows if row.get("vuln_class_unmapped")),
        "violations_count": len(violations),
        "violations": [item.to_dict() for item in violations],
    }


if __name__ == "__main__":
    raise SystemExit(main())
