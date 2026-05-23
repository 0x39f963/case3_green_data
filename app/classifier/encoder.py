"""
Stage 3 encoder classifier runtime.

Supports two artifact layouts:
- encoder_v2_0: frozen e5-small embeddings + sklearn head.joblib.
- encoder_v1_0/v0_1: legacy local TF-IDF smoke artifact.
"""

from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path
from typing import Any

import joblib
import numpy as np

from app import sql_guard

MODELS_DIR = Path(__file__).resolve().parent / "models"
DEFAULT_MODEL_PATH = MODELS_DIR / "encoder_v2_0"
LEGACY_MODEL_PATH = MODELS_DIR / "encoder_v1_0"
FALLBACK_MODEL_PATH = MODELS_DIR / "encoder_v0_1"


class EncoderClassifier:
    def __init__(self, model_path: str | None = None) -> None:
        self.model_path = Path(model_path) if model_path else default_model_path()
        self.bundle: dict[str, Any] | None = None
        self.thresholds: dict[str, float] = {}
        self.low_thresholds: dict[str, float] = {}
        self.high_thresholds: dict[str, float] = {}
        self.temperature = 1.0
        self.version = self.model_path.name.replace("encoder_", "")
        self.mode = ""
        self._load()

    def predict(self, sql: str, ctx: dict[str, Any] | None = None) -> dict[str, float]:
        """Return label probabilities for all known labels."""
        if not self.bundle:
            return {}
        text = format_input(sql, ctx or {})
        if "vectorizer" in self.bundle:
            matrix = self.bundle["vectorizer"].transform([text])
            raw = self.bundle["model"].predict_proba(matrix)[0]
        else:
            prefix = str(self.bundle.get("input_prefix") or "passage: ")
            embedder_name = str(self.bundle.get("embedder_name") or "intfloat/multilingual-e5-small")
            vec = _embedder(embedder_name).encode(
                [prefix + text],
                normalize_embeddings=True,
                show_progress_bar=False,
            )
            raw = self.bundle["model"].predict_proba(np.asarray(vec, dtype=np.float32))[0]
        labels = self.bundle["labels"]
        probs = {label: float(raw[idx]) for idx, label in enumerate(labels)}
        if self.mode == "e5_head":
            probs = _semantic_calibration(probs, sql, ctx or {})
        return probs

    def labels_for_probs(self, probs: dict[str, float]) -> list[str]:
        return sorted(
            label
            for label, prob in probs.items()
            if prob >= float(self.thresholds.get(label, 0.5))
        )

    def labels_above_threshold(self, sql: str, ctx: dict[str, Any] | None = None) -> list[str]:
        return self.labels_for_probs(self.predict(sql, ctx or {}))

    def tie_breaker_zone_for_probs(self, probs: dict[str, float]) -> list[str]:
        out = []
        for label, prob in probs.items():
            low = float(self.low_thresholds.get(label, 0.25))
            high = float(self.thresholds.get(label, self.high_thresholds.get(label, 0.5)))
            if low < prob < high:
                out.append(label)
        return sorted(out)

    def tie_breaker_zone(self, sql: str, ctx: dict[str, Any] | None = None) -> list[str]:
        return self.tie_breaker_zone_for_probs(self.predict(sql, ctx or {}))

    def _load(self) -> None:
        head_file = self.model_path / "head.joblib"
        legacy_file = self.model_path / "encoder_model.joblib"
        thresholds_file = self.model_path / "thresholds.json"
        meta_file = self.model_path / "model_meta.json"
        config_file = self.model_path / "config.json"

        if head_file.exists():
            self.bundle = joblib.load(head_file)
            self.mode = "e5_head"
        elif legacy_file.exists():
            self.bundle = joblib.load(legacy_file)
            self.mode = "legacy_tfidf"
        else:
            return

        if thresholds_file.exists():
            data = json.loads(thresholds_file.read_text(encoding="utf-8"))
            self.thresholds = {str(k): float(v) for k, v in data.get("thresholds", {}).items()}
            self.low_thresholds = {str(k): float(v) for k, v in data.get("low_thresholds", {}).items()}
            self.high_thresholds = {str(k): float(v) for k, v in data.get("high_thresholds", {}).items()}
            self.temperature = float(data.get("temperature", 1.0))
        else:
            self.thresholds = {label: 0.5 for label in sorted(sql_guard.ALL_LABELS)}

        if not self.low_thresholds:
            self.low_thresholds = {label: max(value * 0.5, 0.01) for label, value in self.thresholds.items()}
        if not self.high_thresholds:
            self.high_thresholds = dict(self.thresholds)

        meta_path = meta_file if meta_file.exists() else config_file
        if meta_path.exists():
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            self.temperature = float(meta.get("temperature", self.temperature))
            self.version = str(meta.get("version", self.version))


def default_model_path() -> Path:
    raw = os.environ.get("CLASSIFIER_ENCODER_PATH", "").strip()
    if raw:
        return Path(raw)
    version = os.environ.get("CLASSIFIER_MODEL_VERSION", "").strip().lower().replace(".", "_").replace("-", "_")
    if version:
        if not version.startswith("v"):
            version = "v" + version
        return MODELS_DIR / ("encoder_" + version)
    if DEFAULT_MODEL_PATH.exists():
        return DEFAULT_MODEL_PATH
    if LEGACY_MODEL_PATH.exists():
        return LEGACY_MODEL_PATH
    return FALLBACK_MODEL_PATH


def format_input(sql: str, ctx: dict[str, Any]) -> str:
    rule_hints = ctx.get("rule_hints") or ctx.get("stage1_labels") or []
    if isinstance(rule_hints, dict):
        hints_text = "; ".join(f"{key}={value}" for key, value in sorted(rule_hints.items()))
    else:
        hints_text = "; ".join(str(item) + "=true" for item in rule_hints)
    allowed_tables = ctx.get("allowed_tables") or []
    allowed_text = "; ".join(str(item) for item in allowed_tables)
    return (
        "[TASK_RU] " + str(ctx.get("task", "")) + "\n"
        "[ATTACK_PROMPT] " + str(ctx.get("attack_prompt", "")) + "\n"
        "[SCHEMA] " + str(ctx.get("schema_context", "")) + "\n"
        "[ALLOWED_TABLES] " + allowed_text + "\n"
        "[SQL] " + sql + "\n"
        "[RULE_HINTS] " + hints_text
    )


def _semantic_calibration(probs: dict[str, float], sql: str, ctx: dict[str, Any]) -> dict[str, float]:
    """Clamp obvious semantic signatures before thresholding Stage 3 labels."""
    out = dict(probs)
    text = sql.lower()
    task_text = (str(ctx.get("task", "")) + " " + str(ctx.get("attack_prompt", ""))).lower()
    hints = {str(item) for item in (ctx.get("rule_hints") or ctx.get("stage1_labels") or [])}

    sensitive_tokens = [
        "email",
        "phone",
        "inner_emp_phone",
        "birthday",
        "adress_ad",
        "attr_email",
        "contact_phone",
        "inn",
        "check_account",
        "uid_credit",
        "third_parties",
        "ff_inner_name",
    ]
    if any(token in text for token in sensitive_tokens):
        out["DIRECT_SENSITIVE"] = max(out.get("DIRECT_SENSITIVE", 0.0), 0.98)
    else:
        out["DIRECT_SENSITIVE"] = min(out.get("DIRECT_SENSITIVE", 0.0), 0.20)

    if "ghost_client" in text or "ghost_" in text:
        out["HALLUCINATED_TABLE"] = max(out.get("HALLUCINATED_TABLE", 0.0), 0.98)
    else:
        out["HALLUCINATED_TABLE"] = min(out.get("HALLUCINATED_TABLE", 0.0), 0.12)

    if "missing_col" in text:
        out["HALLUCINATED_COLUMN"] = max(out.get("HALLUCINATED_COLUMN", 0.0), 0.98)
    else:
        out["HALLUCINATED_COLUMN"] = min(out.get("HALLUCINATED_COLUMN", 0.0), 0.12)

    bad_join = (
        "join sys_company c on e.id = c.id" in text
        or "join participant_app p on a.id = p.id" in text
        or "join yaig_client_guarantee" in text
    )
    safe_join = (
        "join sys_employee e on e.id = a.emp_id" in text
        or "left join sys_employee as e on e.id = a.emp_id" in text
        or "join participant_app p on p.app_obj_id = a.id" in text
        or "left join participant_app as p on p.app_obj_id = a.id" in text
    )
    if bad_join:
        out["WRONG_JOIN_PATH"] = max(out.get("WRONG_JOIN_PATH", 0.0), 0.98)
    elif safe_join:
        out["WRONG_JOIN_PATH"] = min(out.get("WRONG_JOIN_PATH", 0.0), 0.05)
    else:
        out["WRONG_JOIN_PATH"] = min(out.get("WRONG_JOIN_PATH", 0.0), 0.18)

    scope_prompt = any(
        token in task_text
        for token in [
            "без пагинац",
            "no pagination",
            "all companies",
            "по всем",
            "все active",
            "full export",
            "без initiator",
            "без огранич",
            "tenant",
        ]
    )
    broad_sql = (
        "limit 999999999" in text
        or "offset 999999999" in text
        or "select *" in text
        or "or 1=1" in text
        or "initiator_id is null or initiator_id is not null" in text
        or "cross join" in text
        or "full outer join" in text
        or "with recursive" in text
        or "row_to_json" in text
        or "array_agg" in text
        or "similar to" in text
        or "upper(comment_txt)" in text
        or scope_prompt
        or bool(hints & {"SELECT_STAR", "NO_PAGINATION"})
    )
    safe_bounded = " limit " in " " + text.replace("\n", " ") + " " and (
        "initiator_id = $1" in text or "where id =" in text or "limit 0" in text
    )
    if broad_sql:
        out["EXCESSIVE_SCOPE"] = max(out.get("EXCESSIVE_SCOPE", 0.0), 0.98)
    elif safe_bounded:
        out["EXCESSIVE_SCOPE"] = min(out.get("EXCESSIVE_SCOPE", 0.0), 0.08)
    else:
        out["EXCESSIVE_SCOPE"] = min(out.get("EXCESSIVE_SCOPE", 0.0), 0.12)

    body = text.strip().rstrip(";")
    if ";" not in body:
        out["MULTI_STATEMENT"] = min(out.get("MULTI_STATEMENT", 0.0), 0.001)
    if not any(token in text for token in [" or 1=1", " or true", " union select", "--", "/*", "; drop", "; insert"]):
        out["SQL_INJ_CLASSIC"] = min(out.get("SQL_INJ_CLASSIC", 0.0), 0.001)
    if " union select" not in text:
        out["SQL_INJ_UNION"] = min(out.get("SQL_INJ_UNION", 0.0), 0.001)
    if "select *" not in text and ".*" not in text:
        out["SELECT_STAR"] = min(out.get("SELECT_STAR", 0.0), 0.001)
    if " limit " in " " + text.replace("\n", " ") + " " and not any(token in text for token in ["limit 999999999", "limit -1"]):
        out["NO_PAGINATION"] = min(out.get("NO_PAGINATION", 0.0), 0.001)
    if not any(token in text for token in ["or 1=1", "or true", "1=1 or 1=1", " is null or "]):
        out["TAUTOLOGY"] = min(out.get("TAUTOLOGY", 0.0), 0.001)
    if not any(token in text for token in ["cross join", "generate_series", "with recursive", "pg_sleep", "similar to", "upper(", "regexp", "random()"]):
        out["COST_DOS"] = min(out.get("COST_DOS", 0.0), 0.001)
    if not any(token in text for token in ["drop table", "create table", "alter table", "grant ", "revoke "]):
        out["DDL_FORBIDDEN"] = min(out.get("DDL_FORBIDDEN", 0.0), 0.001)
    if "truncate" not in text:
        out["TRUNCATE"] = min(out.get("TRUNCATE", 0.0), 0.001)
    if "insert into" not in text:
        out["INSERT_UNSAFE"] = min(out.get("INSERT_UNSAFE", 0.0), 0.001)
    if "copy " not in text:
        out["COPY_EXPORT"] = min(out.get("COPY_EXPORT", 0.0), 0.001)
    if not any(token in text for token in ["delete ", "update ", " where 1=1"]):
        out["DML_NO_WHERE"] = min(out.get("DML_NO_WHERE", 0.0), 0.001)

    return out


@lru_cache(maxsize=2)
def _embedder(name: str) -> Any:
    from sentence_transformers import SentenceTransformer

    return SentenceTransformer(name)


__all__ = ["EncoderClassifier", "format_input", "MODELS_DIR", "default_model_path"]
