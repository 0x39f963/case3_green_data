"""
Stage 2 classifier: feature-based ML.

The module exposes a small runtime API: featurize() and predict().
Training is done by scripts/train_ml_stage2.py and saves LR,
LightGBM and threshold artifacts under app/classifier/models.
"""

from __future__ import annotations

import json
import os
import re
import warnings
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import joblib

from app import sql_guard, sql_parsing
from . import normalize

MODELS_DIR = Path(__file__).resolve().parent / "models"
MODEL_VERSION_DEFAULT = "v1_0"
MODEL_VERSION_FALLBACK = "v0_1"
LR_PATH = MODELS_DIR / "lr_v1_0.joblib"
LGBM_PATH = MODELS_DIR / "lightgbm_v1_0.joblib"
THRESHOLDS_PATH = MODELS_DIR / "thresholds_v1_0.json"


@dataclass
class MLOutput:
    probs: dict[str, float]
    labels_above_threshold: list[str]
    calibrated_thresholds: dict[str, float]
    model_type: str = "none"
    model_version: str = MODEL_VERSION_DEFAULT
    available: bool = False


def featurize(sql: str, ctx: dict[str, Any] | None = None) -> dict[str, float]:
    """Extract compact AST, token, schema, prompt and rule features."""
    data = ctx or {}
    norm = normalize.normalize(sql)
    text = norm.canonical or sql
    upper = text.upper()
    lower = text.lower()
    parsed = sql_parsing.parse(text)
    words = re.findall(r"[A-Za-z_][A-Za-z_0-9]*|\d+", lower)

    features: dict[str, float] = {
        "len_chars": float(len(text)),
        "len_words": float(len(words)),
        "statement_count": float(len(norm.statements) or (2 if ";" in sql.strip().rstrip(";") else 1)),
        "comment_count": float(len(norm.comments)),
        "parse_broken": float(parsed.broken or bool(norm.parse_error)),
        "semicolon_count": float(sql.count(";")),
        "quote_count": float(sql.count("'")),
        "has_select": _has(upper, "SELECT"),
        "has_where": _has(upper, "WHERE"),
        "has_limit": _has(upper, "LIMIT"),
        "has_join": _has(upper, "JOIN"),
        "has_union": _has(upper, "UNION"),
        "has_execute": _has(upper, "EXECUTE"),
        "has_copy": _has(upper, "COPY"),
        "has_grant": _has(upper, "GRANT"),
        "has_information_schema": float("information_schema" in lower or "pg_catalog" in lower),
        "has_pg_sleep": float("pg_sleep" in lower),
        "has_select_star": float(bool(re.search(r"\bselect\b[\s\S]{0,240}\*", lower))),
        "has_tautology": float(bool(re.search(r"\b(or|and)\s+1\s*=\s*1\b", lower))),
        "table_count": float(len(parsed.identifiers.get("tables", []))),
        "column_count": float(len(parsed.identifiers.get("columns", []))),
        "function_count": float(len(parsed.identifiers.get("functions", []))),
        "sensitive_hit_count": float(_sensitive_hit_count(lower, data)),
        "allowed_table_miss": float(_allowed_table_miss(parsed, data)),
    }

    if parsed.statement_type:
        features["stmt:" + parsed.statement_type.lower()] = 1.0

    for token in words:
        if len(token) >= 2:
            features["tok:" + token[:40]] = features.get("tok:" + token[:40], 0.0) + 1.0

    prompt = (str(data.get("task", "")) + " " + str(data.get("attack_prompt", ""))).lower()
    for word in [
        "ignore",
        "bypass",
        "delete",
        "drop",
        "grant",
        "schema",
        "all",
        "export",
        "игнорируй",
        "удали",
        "схем",
        "все",
        "обойди",
        "контакт",
    ]:
        features["prompt:" + word] = float(word in prompt)

    for table in parsed.identifiers.get("tables", []):
        features["table:" + table.lower().rsplit(".", 1)[-1]] = 1.0
    if data.get("schema_area"):
        features["schema_area:" + str(data["schema_area"])] = 1.0

    for label in _stage1_labels(sql, data):
        features["rule:" + label] = 1.0
    return features


def predict(sql: str, ctx: dict[str, Any] | None = None) -> MLOutput:
    """Predict labels using the trained LR/LightGBM Stage 2 artifacts."""
    version = model_version()
    paths = artifact_paths(version)
    model_type = os.environ.get("STAGE_2_ML_TYPE", "").strip().lower()
    if not model_type:
        model_type = "lightgbm" if paths["lightgbm"].exists() else "lr"
    if model_type not in {"lr", "lightgbm", "both"}:
        model_type = "lightgbm"

    artifacts = _load_artifacts(version)
    if not artifacts:
        return MLOutput({}, [], {}, model_type=model_type, model_version=version, available=False)

    feats = featurize(sql, ctx or {})
    probs: dict[str, float] = {}
    thresholds: dict[str, float] = {}
    if model_type in {"lr", "both"} and "lr" in artifacts:
        lr_probs = _predict_lr(artifacts["lr"], feats)
        probs = _merge_probs(probs, lr_probs)
        thresholds.update(artifacts["thresholds"].get("lr", {}))
    if model_type in {"lightgbm", "both"} and "lightgbm" in artifacts:
        lgbm_probs = _predict_lightgbm(artifacts["lightgbm"], feats)
        probs = _merge_probs(probs, lgbm_probs)
        thresholds.update(artifacts["thresholds"].get("lightgbm", {}))

    labels = [
        label
        for label, prob in probs.items()
        if prob >= float(thresholds.get(label, 0.5))
        and _possible_by_surface(label, sql, ctx or {})
    ]
    labels.sort()
    return MLOutput(
        probs=dict(sorted(probs.items())),
        labels_above_threshold=labels,
        calibrated_thresholds={label: float(thresholds.get(label, 0.5)) for label in sorted(probs)},
        model_type=model_type,
        model_version=version,
        available=True,
    )


def has_artifacts() -> bool:
    paths = artifact_paths(model_version())
    return paths["lr"].exists() or paths["lightgbm"].exists()


def artifact_paths(version: str | None = None) -> dict[str, Path]:
    suffix = _version_suffix(version or model_version())
    return {
        "lr": MODELS_DIR / ("lr_" + suffix + ".joblib"),
        "lightgbm": MODELS_DIR / ("lightgbm_" + suffix + ".joblib"),
        "thresholds": MODELS_DIR / ("thresholds_" + suffix + ".json"),
    }


def model_version() -> str:
    raw = os.environ.get("CLASSIFIER_MODEL_VERSION", "").strip()
    if raw:
        return _version_suffix(raw)
    if artifact_paths(MODEL_VERSION_DEFAULT)["thresholds"].exists():
        return MODEL_VERSION_DEFAULT
    return MODEL_VERSION_FALLBACK


@lru_cache(maxsize=4)
def _load_artifacts(version: str) -> dict[str, Any]:
    paths = artifact_paths(version)
    artifacts: dict[str, Any] = {}
    if paths["thresholds"].exists():
        artifacts["thresholds"] = json.loads(paths["thresholds"].read_text(encoding="utf-8"))
    else:
        artifacts["thresholds"] = {"lr": {}, "lightgbm": {}}
    if paths["lr"].exists():
        artifacts["lr"] = joblib.load(paths["lr"])
    if paths["lightgbm"].exists():
        artifacts["lightgbm"] = joblib.load(paths["lightgbm"])
    return artifacts


def _version_suffix(value: str) -> str:
    text = value.strip().lower().replace(".", "_").replace("-", "_")
    if text.startswith("v"):
        return text
    return "v" + text


def _predict_lr(bundle: dict[str, Any], feats: dict[str, float]) -> dict[str, float]:
    matrix = bundle["vectorizer"].transform([feats])
    raw = bundle["model"].predict_proba(matrix)
    labels = bundle["labels"]
    return {label: float(raw[0][idx]) for idx, label in enumerate(labels)}


def _predict_lightgbm(bundle: dict[str, Any], feats: dict[str, float]) -> dict[str, float]:
    matrix = bundle["vectorizer"].transform([feats])
    out: dict[str, float] = {}
    for label, model in bundle["models"].items():
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            raw = model.predict_proba(matrix)
        out[label] = float(raw[0][1])
    return out


def _merge_probs(left: dict[str, float], right: dict[str, float]) -> dict[str, float]:
    out = dict(left)
    for label, prob in right.items():
        out[label] = max(float(prob), out.get(label, 0.0))
    return out


def _stage1_labels(sql: str, ctx: dict[str, Any]) -> list[str]:
    items = ctx.get("stage1_findings")
    if items:
        labels = []
        for item in items:
            label = getattr(item, "label", None) if not isinstance(item, dict) else item.get("label")
            if label:
                labels.append(str(label))
        return sorted(set(labels))
    return sorted({item.vuln_class for item in sql_guard.check(sql, ctx)})


def _sensitive_hit_count(lower_sql: str, ctx: dict[str, Any]) -> int:
    sensitive = ctx.get("sensitive_fields") or {}
    count = 0
    for cols in sensitive.values():
        for col in cols:
            if re.search(r"(\b|\.)" + re.escape(str(col).lower()) + r"\b", lower_sql):
                count += 1
    return count


def _allowed_table_miss(parsed: sql_parsing.ParsedSQL, ctx: dict[str, Any]) -> int:
    allowed = {str(item).lower().rsplit(".", 1)[-1] for item in ctx.get("allowed_tables", [])}
    if not allowed:
        return 0
    miss = 0
    for table in parsed.identifiers.get("tables", []):
        if table.lower().rsplit(".", 1)[-1] not in allowed:
            miss += 1
    return miss


def _has(text: str, token: str) -> float:
    return float(bool(re.search(r"\b" + re.escape(token) + r"\b", text)))


def _possible_by_surface(label: str, sql: str, ctx: dict[str, Any]) -> bool:
    if label.startswith("PROMPT_") and not str(ctx.get("attack_prompt", "")).strip():
        return False
    upper = sql.upper()
    checks = {
        "TRUNCATE": r"\bTRUNCATE\b",
        "COPY_EXPORT": r"\bCOPY\b",
        "DDL_FORBIDDEN": r"\b(CREATE|ALTER|DROP)\b",
        "DML_NO_WHERE": r"\b(DELETE|UPDATE|TRUNCATE)\b",
        "PRIV_ESCALATE": r"\b(GRANT|REVOKE|ALTER\s+ROLE|CREATE\s+ROLE|SECURITY\s+DEFINER)\b",
        "PLPGSQL_UNSAFE": r"\bEXECUTE\b",
        "DYNAMIC_EXECUTE": r"\bEXECUTE\b",
        "SQL_INJ_UNION": r"\bUNION\b",
        "UNION_EXFIL": r"\bUNION\b",
        "SQL_INJ_TIME": r"\b(PG_SLEEP|WAITFOR|GENERATE_SERIES)\b",
        "TIME_DELAY": r"\b(PG_SLEEP|WAITFOR|GENERATE_SERIES)\b",
    }
    pattern = checks.get(label)
    if pattern is None:
        return True
    return bool(re.search(pattern, upper))


__all__ = ["MLOutput", "featurize", "predict", "has_artifacts", "model_version"]
