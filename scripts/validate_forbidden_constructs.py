#!/usr/bin/env python3
"""Validate the SQL guard forbidden constructs catalog."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

import yaml


DEFAULT_PATH = Path("data/sql_guard/forbidden_constructs.yaml")

REQUIRED_FIELDS = {
    "id",
    "label",
    "severity",
    "category",
    "ast_check",
    "runtime_mvp",
    "runtime_group",
    "runtime_reason",
    "description",
    "example_bad",
    "example_good",
    "reference",
    "added_round",
}

KNOWN_LABELS = {
    "COMMENT_TRUNCATION",
    "COPY_EXPORT",
    "DDL_FORBIDDEN",
    "DML_NO_WHERE",
    "DYNAMIC_EXECUTE",
    "INSERT_UNSAFE",
    "MULTI_STATEMENT",
    "PRIV_ESCALATE",
    "PROMPT_FS_READ",
    "PROMPT_SCHEMA_EXFIL",
    "SCHEMA_LEAK",
    "SQL_INJ_UNION",
    "TAUTOLOGY",
    "TIME_DELAY",
    "TRUNCATE",
    "UNION_EXFIL",
}

KNOWN_CATEGORIES = {
    "blind_injection",
    "ddl",
    "dml_unsafe",
    "fs_access",
    "multi_stmt",
    "privilege",
    "rce",
    "schema_leak",
    "union_exfil",
}

KNOWN_AST_CHECKS = {
    "copy_program",
    "dml_without_where_or_tautology",
    "func_call_numeric_arg_gt",
    "func_call_with_arg",
    "insert_blocked_select_only_mode",
    "node_type",
    "raw_sql_comment_regex",
    "role_statement",
    "schema_access",
    "statement_count_gt_1",
    "union_select_cast_exfil",
    "union_select_null_padding",
    "where_or_having_concat_operator",
    "where_or_having_tautology",
}

KNOWN_RUNTIME_GROUPS = {"runtime_mvp", "eval_or_review", "deferred"}


def is_non_empty_text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def is_non_empty_text_list(value: Any) -> bool:
    return (
        isinstance(value, list)
        and bool(value)
        and all(is_non_empty_text(item) for item in value)
    )


def check_rule(rule: dict[str, Any], seen: set[str], index: int) -> list[str]:
    errors: list[str] = []
    rule_id = rule.get("id", f"<rule #{index}>")

    missing = sorted(REQUIRED_FIELDS - set(rule))
    for field in missing:
        errors.append(f"{rule_id}: missing required field {field}")

    for field in REQUIRED_FIELDS & set(rule):
        value = rule[field]
        if value is None or value == "":
            errors.append(f"{rule_id}: empty required field {field}")

    if not is_non_empty_text(rule.get("id")):
        errors.append(f"{rule_id}: id must be non-empty text")
    elif rule["id"] in seen:
        errors.append(f"{rule_id}: duplicate id")
    else:
        seen.add(rule["id"])

    if rule.get("label") not in KNOWN_LABELS:
        errors.append(f"{rule_id}: unknown label {rule.get('label')!r}")
    if rule.get("category") not in KNOWN_CATEGORIES:
        errors.append(f"{rule_id}: unknown category {rule.get('category')!r}")
    if rule.get("ast_check") not in KNOWN_AST_CHECKS:
        errors.append(f"{rule_id}: unknown ast_check {rule.get('ast_check')!r}")

    severity = rule.get("severity")
    if (
        isinstance(severity, bool)
        or not isinstance(severity, int | float)
        or not 1 <= severity <= 10
    ):
        errors.append(f"{rule_id}: severity must be number in range 1..10")

    runtime_mvp = rule.get("runtime_mvp")
    runtime_group = rule.get("runtime_group")
    if not isinstance(runtime_mvp, bool):
        errors.append(f"{rule_id}: runtime_mvp must be boolean")
    if runtime_group not in KNOWN_RUNTIME_GROUPS:
        errors.append(f"{rule_id}: unknown runtime_group {runtime_group!r}")
    if runtime_mvp is True and runtime_group != "runtime_mvp":
        errors.append(f"{rule_id}: runtime_mvp=true requires runtime_group=runtime_mvp")
    if runtime_mvp is False and runtime_group == "runtime_mvp":
        errors.append(f"{rule_id}: runtime_mvp=false cannot use runtime_group=runtime_mvp")

    for field in ("description", "runtime_reason", "added_round"):
        if field in rule and not is_non_empty_text(rule[field]):
            errors.append(f"{rule_id}: {field} must be non-empty text")

    for field in ("example_bad", "example_good", "reference"):
        if field in rule and not is_non_empty_text_list(rule[field]):
            errors.append(f"{rule_id}: {field} must be a non-empty list of text")

    ast_check = rule.get("ast_check")
    if ast_check in {"func_call_with_arg", "func_call_numeric_arg_gt"} and not is_non_empty_text(
        rule.get("func_name")
    ):
        errors.append(f"{rule_id}: {ast_check} requires func_name")
    arg_limit = rule.get("arg_threshold_seconds")
    if ast_check == "func_call_numeric_arg_gt" and (
        isinstance(arg_limit, bool) or not isinstance(arg_limit, int | float)
    ):
        errors.append(f"{rule_id}: func_call_numeric_arg_gt requires numeric arg_threshold_seconds")
    if ast_check == "schema_access" and not is_non_empty_text(rule.get("schema_name")):
        errors.append(f"{rule_id}: schema_access requires schema_name")

    return errors


def load_rules(path: Path) -> list[dict[str, Any]]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError("catalog root must be a YAML list")
    if not all(isinstance(item, dict) for item in data):
        raise ValueError("each catalog item must be a mapping")
    return data


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--path", type=Path, default=DEFAULT_PATH)
    parser.add_argument("--expected-count", type=int, default=27)
    args = parser.parse_args()

    rules = load_rules(args.path)
    errors: list[str] = []

    if len(rules) != args.expected_count:
        errors.append(f"catalog has {len(rules)} rules, expected {args.expected_count}")

    seen: set[str] = set()
    for index, rule in enumerate(rules, start=1):
        errors.extend(check_rule(rule, seen, index))

    if errors:
        print("FAIL forbidden_constructs validation")
        for error in errors:
            print(f"- {error}")
        return 1

    runtime_count = sum(1 for rule in rules if rule["runtime_mvp"])
    print(
        "PASS forbidden_constructs validation: "
        f"{len(rules)} rules, {runtime_count} runtime_mvp, "
        f"{len(rules) - runtime_count} review/deferred"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
