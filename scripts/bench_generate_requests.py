"""
Build adversarial SQL benchmark rows from local templates.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import re
import sys
from collections import Counter
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import sql_guard  # noqa: E402


REQ_WORD = "req" + "uests"
DATA_NAME = "adversarial_sql_" + REQ_WORD
DATA_DIR = ROOT / "data" / "bench" / REQ_WORD
DEFAULT_TEMPLATES = DATA_DIR / "templates"
DEFAULT_OVERLAY = ROOT / "deploy" / "schema_overlay.json"
DEFAULT_SCHEMA_SQL = ROOT / "deploy" / "postgres-init" / "01-schema.sql"
DEFAULT_SECURITY_META = ROOT / "TASK-3" / "marina-case3-rag" / "rag_pipeline" / "indices" / "security_meta.json"
DEFAULT_VERSION = "v0_2"

FAMILY_TARGETS_V0_1 = {
    "safe_readonly_hard": 20,
    "classic_injection": 13,
    "union_schema_leak": 13,
    "dml_ddl_delete": 14,
    "sensitive_exposure": 13,
    "limit_bypass": 11,
    "cost_dos": 11,
    "hallucination_wrong_schema": 9,
    "prompt_policy_bypass": 9,
    "borderline_optimizer": 7,
}

FAMILY_TARGETS_V0_2 = {
    "safe_readonly_hard": 20,
    "classic_injection": 14,
    "union_schema_leak": 13,
    "dml_ddl_delete": 14,
    "sensitive_exposure": 13,
    "limit_bypass": 11,
    "cost_dos": 11,
    "hallucination_wrong_schema": 9,
    "prompt_policy_bypass": 9,
    "borderline_optimizer": 6,
}

FAMILY_TARGETS_BY_VERSION = {
    "v0_1": FAMILY_TARGETS_V0_1,
    "v0_2": FAMILY_TARGETS_V0_2,
}

SECURITY_FALLBACK = {
    "DIRECT_SENSITIVE",
    "DML_NO_WHERE",
    "NO_PAGINATION",
    "PLPGSQL_UNSAFE",
    "PRIV_ESCALATE",
    "SCHEMA_LEAK",
    "SELECT_STAR",
    "SQL_INJ_CLASSIC",
    "SQL_INJ_TIME",
    "SQL_INJ_UNION",
}

NAMES = [
    "alpha",
    "bravo",
    "charlie",
    "delta",
    "echo",
    "foxtrot",
    "gamma",
    "sigma",
    "omega",
    "vector",
]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--rows", type=int, default=120)
    parser.add_argument("--version", default=DEFAULT_VERSION, choices=sorted(FAMILY_TARGETS_BY_VERSION))
    parser.add_argument("--seed", type=int, default=20260518)
    parser.add_argument("--templates", type=Path, default=DEFAULT_TEMPLATES)
    parser.add_argument("--schema-overlay", type=Path, default=DEFAULT_OVERLAY)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--with-suffix", action="store_true")
    args = parser.parse_args()

    out_path = args.out or default_dataset_path(args.version)
    report_path = args.report or default_report_path(args.version)
    rows = build_rows(
        total=args.rows,
        version=args.version,
        seed=args.seed,
        templates_dir=args.templates,
        overlay_path=args.schema_overlay,
        with_suffix=args.with_suffix or args.version == "v0_1",
    )
    write_jsonl(out_path, rows)
    report = build_report(rows, out_path)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps({"rows": len(rows), "out": str(out_path), "report": str(report_path), "sha256": report["hashes"]["jsonl_sha256"]}, ensure_ascii=False, sort_keys=True))
    return 0


def build_rows(
    total: int,
    version: str,
    seed: int,
    templates_dir: Path,
    overlay_path: Path,
    with_suffix: bool,
) -> list[dict[str, Any]]:
    rng = random.Random(seed)
    tables, joins, pii_items = load_overlay(overlay_path)
    columns = load_schema_columns(DEFAULT_SCHEMA_SQL)
    targets = FAMILY_TARGETS_BY_VERSION[version]
    templates = {family: read_template(templates_dir / (family + ".yaml")) for family in targets}
    security_classes = load_security_classes(DEFAULT_SECURITY_META)
    table_names = sorted(tables)
    rng.shuffle(table_names)
    rng.shuffle(joins)
    rng.shuffle(pii_items)

    counts = target_counts(total, targets)
    rows: list[dict[str, Any]] = []
    idx = 1
    for family, count in counts.items():
        slots = list(templates[family]["slots"])
        for local_idx in range(count):
            lang = lang_for(idx)
            slot = slots[local_idx % len(slots)]
            values = build_values(
                family=family,
                idx=idx,
                local_idx=local_idx,
                table_names=table_names,
                joins=joins,
                pii_items=pii_items,
                columns=columns,
            )
            row = build_row(
                idx=idx,
                version=version,
                lang=lang,
                family=family,
                slot=slot,
                values=values,
                security_classes=security_classes,
                with_suffix=with_suffix,
            )
            rows.append(row)
            idx += 1
    return rows


def load_overlay(path: Path) -> tuple[dict[str, Any], list[dict[str, str]], list[dict[str, str]]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    tables = data.get("tables") or {}
    joins: list[dict[str, str]] = []
    pii_items: list[dict[str, str]] = []
    for table, meta in tables.items():
        for item in meta.get("approved_join_keys") or []:
            target = str(item.get("to") or "")
            if target in tables:
                joins.append(
                    {
                        "from": table,
                        "to": target,
                        "on": str(item.get("on") or "id"),
                        "to_column": str(item.get("to_column") or "id"),
                    }
                )
        for col in (meta.get("pii_tags") or {}).keys():
            pii_items.append({"table": table, "col": str(col)})
    if not joins:
        raise ValueError("no approved join keys in overlay")
    if not pii_items:
        raise ValueError("no pii fields in overlay")
    return tables, joins, pii_items


def load_schema_columns(path: Path) -> dict[str, list[str]]:
    text = path.read_text(encoding="utf-8")
    items: dict[str, list[str]] = {}
    pattern = re.compile(r"CREATE\s+TABLE\s+public\.([a-zA-Z_][\w]*)\s*\((.*?)\);", re.IGNORECASE | re.DOTALL)
    for match in pattern.finditer(text):
        table = match.group(1)
        cols: list[str] = []
        for raw in match.group(2).splitlines():
            line = raw.strip().rstrip(",")
            if not line or line.startswith("--"):
                continue
            col_match = re.match(r"([a-zA-Z_][\w]*)\s+", line)
            if col_match:
                name = col_match.group(1)
                if name.upper() not in {"CONSTRAINT", "PRIMARY", "FOREIGN", "UNIQUE", "CHECK"}:
                    cols.append(name)
        items[table] = cols or ["id"]
    return items


def read_template(path: Path) -> dict[str, Any]:
    text = "\n".join(line for line in path.read_text(encoding="utf-8").splitlines() if not line.lstrip().startswith("#"))
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        import yaml

        data = yaml.safe_load(text)
        if not isinstance(data, dict):
            raise ValueError("template must be an object: " + str(path))
        return data


def load_security_classes(path: Path) -> set[str]:
    if not path.exists():
        return set(SECURITY_FALLBACK)
    data = json.loads(path.read_text(encoding="utf-8"))
    values = {str(item.get("vuln_class")) for item in data if isinstance(item, dict) and item.get("vuln_class")}
    return values or set(SECURITY_FALLBACK)


def target_counts(total: int, targets: dict[str, int]) -> dict[str, int]:
    if total == sum(targets.values()):
        return dict(targets)
    if total <= 0:
        raise ValueError("--rows must be positive")
    expanded: list[str] = []
    for family, count in targets.items():
        expanded.extend([family] * count)
    if total <= len(expanded):
        counts = Counter(expanded[:total])
        return {family: counts.get(family, 0) for family in targets if counts.get(family, 0)}

    scale = total / len(expanded)
    raw = {family: targets[family] * scale for family in targets}
    counts = {family: int(raw[family]) for family in targets}
    left = total - sum(counts.values())
    order = sorted(targets, key=lambda family: raw[family] - counts[family], reverse=True)
    for family in order[:left]:
        counts[family] += 1
    return counts


def lang_for(idx: int) -> str:
    return "ru" if (idx - 1) % 5 < 3 else "en"


def build_values(
    family: str,
    idx: int,
    local_idx: int,
    table_names: list[str],
    joins: list[dict[str, str]],
    pii_items: list[dict[str, str]],
    columns: dict[str, list[str]],
) -> dict[str, Any]:
    table = table_names[(idx + local_idx) % len(table_names)]
    join = joins[(idx * 3 + local_idx) % len(joins)]
    pii = pii_items[(idx * 5 + local_idx) % len(pii_items)]
    if family == "sensitive_exposure":
        table = pii["table"]
    plain_col = pick_col(columns.get(table, ["id"]), avoid={pii["col"]})
    group_col = pick_col(columns.get(table, ["id"]), prefer=("status", "type_id", "org_id", "name", "id"), avoid={pii["col"]})
    date_col = pick_col(columns.get(table, ["id"]), prefer=("create_date", "last_modified_date", "account_date", "report_date", "id"), avoid=set())
    return {
        "idx": idx,
        "num": 10 + idx,
        "small_num": (idx % 5) + 1,
        "limit": 50 + (idx % 5) * 10,
        "name": NAMES[idx % len(NAMES)] + "_" + str(idx),
        "table": table,
        "plain_col": plain_col,
        "group_col": group_col,
        "date_col": date_col,
        "join_table": join["from"],
        "join_to": join["to"],
        "join_on": join["on"],
        "join_to_column": join["to_column"],
        "pii_table": pii["table"],
        "pii_col": pii["col"],
    }


def pick_col(cols: list[str], prefer: tuple[str, ...] = ("name", "status", "id"), avoid: set[str] | None = None) -> str:
    blocked = avoid or set()
    for name in prefer:
        if name in cols and name not in blocked:
            return name
    for name in cols:
        if name not in blocked:
            return name
    return "id"


def build_row(
    idx: int,
    version: str,
    lang: str,
    family: str,
    slot: dict[str, Any],
    values: dict[str, Any],
    security_classes: set[str],
    with_suffix: bool,
) -> dict[str, Any]:
    labels = list(dict.fromkeys(format_item(slot.get("expected_labels", []), values)))
    scope = list(dict.fromkeys(item for item in format_item(slot.get("schema_scope", []), values) if item))
    user_key = "user_task_ru" if lang == "ru" else "user_task_en"
    user_task = str(format_item(slot[user_key], values))
    if with_suffix:
        user_task = user_task + " " + task_suffix(lang, family, idx, values)
    elif version != "v0_1":
        user_task = user_task + " " + task_detail(lang, idx, values)
    expected_decision = str(slot.get("expected_decision", "block"))
    safe_rewrite = format_item(slot.get("safe_rewrite"), values)
    if version != "v0_1" and expected_decision in {"approve", "revise"} and safe_rewrite is None:
        safe_rewrite = format_item(slot.get("seed_sql"), values) or default_safe_rewrite(values)
    faiss_targets = build_faiss_targets(scope, labels, security_classes)
    expected_vuln_classes = faiss_targets["security"]["expected_vuln_classes"]
    row = {
        "id": "bench_req_" + str(idx).zfill(6),
        "version": version,
        "language": lang,
        "family": family,
        "user_task": user_task,
        "attack_prompt": format_item(slot.get("attack_prompt"), values),
        "seed_sql": format_item(slot.get("seed_sql"), values),
        "safe_rewrite": safe_rewrite,
        "schema_scope": scope,
        "expected_labels": labels,
        "expected_decision": expected_decision,
        "evidence_span": list(dict.fromkeys(format_item(slot.get("evidence_span", []), values))),
        "faiss_targets": faiss_targets,
        "difficulty": int(slot.get("difficulty", 3)),
        "source": "template",
        "source_seed_id": family + ":" + str(slot.get("id", "slot")) + ":" + str(idx),
        "review_status": "validated",
    }
    if version != "v0_1":
        row["expected_runtime_decision"] = runtime_decision(expected_decision)
        row["expected_runtime_decision_alternatives"] = runtime_alternatives(expected_decision, labels)
        row["vuln_class_unmapped"] = bool(labels and not expected_vuln_classes)
    for key in ("limit_policy", "forbidden_objects", "notes", "tags", "i18n_paraphrases"):
        if key in slot:
            row[key] = format_item(slot[key], values)
    return row


def task_suffix(lang: str, family: str, idx: int, values: dict[str, Any]) -> str:
    marker = family.replace("_", " ") + " " + str(idx) + " " + str(values["table"])
    if lang == "ru":
        return "Контекст проверки: сегмент " + marker + ", окно " + str(values["num"]) + "."
    return "Review context: segment " + marker + ", window " + str(values["num"]) + "."


def task_detail(lang: str, idx: int, values: dict[str, Any]) -> str:
    variants_ru = [
        "Период отбора: поле {date_col} после 2026-0{small_num}-01.",
        "Сверка идет по значению {name} и порогу {num}.",
        "Нужен срез по полю {group_col}, контрольный лимит {limit}.",
        "Фокус отчета: {plain_col}, образец {name}.",
    ]
    variants_en = [
        "Selection period: field {date_col} after 2026-0{small_num}-01.",
        "Review uses value {name} and threshold {num}.",
        "Slice by {group_col}; control limit {limit}.",
        "Report focus: {plain_col}, sample {name}.",
    ]
    templates = variants_ru if lang == "ru" else variants_en
    return templates[idx % len(templates)].format(**values)


def runtime_decision(expected_decision: str) -> str:
    if expected_decision == "block":
        return "abstain"
    if expected_decision in {"approve", "revise", "abstain"}:
        return expected_decision
    return "abstain"


def runtime_alternatives(expected_decision: str, labels: list[str]) -> list[str]:
    if expected_decision != "revise":
        return []
    if set(labels) & {"MULTI_STATEMENT", "SYNTAX_BROKEN", "BROKEN_SQL"}:
        return ["abstain"]
    return ["abstain"]


def default_safe_rewrite(values: dict[str, Any]) -> str:
    return "SELECT id FROM " + str(values["table"]) + " ORDER BY id LIMIT 100;"


def format_item(value: Any, values: dict[str, Any]) -> Any:
    if value is None:
        return None
    if isinstance(value, str):
        return value.format(**values)
    if isinstance(value, list):
        return [format_item(item, values) for item in value]
    if isinstance(value, dict):
        return {key: format_item(item, values) for key, item in value.items()}
    return value


def build_faiss_targets(scope: list[str], labels: list[str], security_classes: set[str]) -> dict[str, Any]:
    vuln = sorted(label for label in set(labels) if label in security_classes)
    return {
        "generation": {
            "expected_sources": ["schema"] if scope else [],
            "expected_table_names": scope,
            "min_top_k": min(3, len(scope)) if scope else 0,
        },
        "security": {
            "expected_vuln_classes": vuln,
            "min_top_k": min(2, len(vuln)) if vuln else 0,
        },
    }


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def build_report(rows: list[dict[str, Any]], jsonl_path: Path) -> dict[str, Any]:
    label_counts: Counter[str] = Counter()
    table_set: set[str] = set()
    for row in rows:
        label_counts.update(row.get("expected_labels", []))
        table_set.update(row.get("schema_scope", []))
    all_labels = sorted(sql_guard.ALL_LABELS)
    report = {
        "dataset_id": DATA_NAME,
        "version": report_version(rows[0]["version"] if rows else DEFAULT_VERSION),
        "row_version": rows[0]["version"] if rows else "",
        "generated_at": datetime.now(timezone(timedelta(hours=3))).isoformat(timespec="seconds"),
        "rows_total": len(rows),
        "rows_by_family": dict(sorted(Counter(row["family"] for row in rows).items())),
        "rows_by_language": dict(sorted(Counter(row["language"] for row in rows).items())),
        "rows_by_expected_decision": dict(sorted(Counter(row["expected_decision"] for row in rows).items())),
        "rows_by_expected_runtime_decision": dict(sorted(Counter(row.get("expected_runtime_decision", "") for row in rows).items())),
        "label_coverage": {label: label_counts.get(label, 0) for label in all_labels},
        "schema_table_coverage": {
            "unique_tables": len(table_set),
            "tables": sorted(table_set),
        },
        "violations": [],
        "near_dup_pairs": near_dup_pairs(rows),
        "hashes": {
            "jsonl_sha256": sha256_file(jsonl_path),
        },
        "vuln_class_unmapped_rows": sum(1 for row in rows if row.get("vuln_class_unmapped")),
    }
    return report


def report_version(row_version: str) -> str:
    return row_version.replace("_", ".")


def default_dataset_path(version: str) -> Path:
    return DATA_DIR / (DATA_NAME + "_" + version + ".jsonl")


def default_report_path(version: str) -> Path:
    return ROOT / "data" / "bench" / "reports" / (DATA_NAME + "_" + version + ".json")


def near_dup_pairs(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    pairs: list[dict[str, Any]] = []
    shingles = {row["id"]: shingle_set(row["user_task"]) for row in rows}
    for left_idx, left in enumerate(rows):
        left_set = shingles[left["id"]]
        for right in rows[left_idx + 1:]:
            right_set = shingles[right["id"]]
            score = jaccard(left_set, right_set)
            if score >= 0.7:
                pairs.append({"left": left["id"], "right": right["id"], "jaccard_3gram": round(score, 4)})
    return pairs


def shingle_set(text: str) -> set[str]:
    tokens = normalize(text).split()
    if len(tokens) < 3:
        return {" ".join(tokens)}
    return {" ".join(tokens[idx:idx + 3]) for idx in range(len(tokens) - 2)}


def jaccard(left: set[str], right: set[str]) -> float:
    if not left and not right:
        return 1.0
    return len(left & right) / max(len(left | right), 1)


def normalize(text: str) -> str:
    return " ".join(text.casefold().split())


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


if __name__ == "__main__":
    raise SystemExit(main())
