"""
Train Stage 2 feature ML: Logistic Regression and LightGBM.
"""

from __future__ import annotations

import argparse
import json
import sys
import warnings
from pathlib import Path
from typing import Any

import joblib
import numpy as np
from lightgbm import LGBMClassifier
from sklearn.feature_extraction import DictVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.multiclass import OneVsRestClassifier

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import sql_guard  # noqa: E402
from app.classifier.ml import MODELS_DIR, featurize  # noqa: E402
from scripts.dataset_build import DATASET_PATH, read_jsonl  # noqa: E402

SEED = 42

CRITICAL_LABELS = [
    "SQL_INJ_CLASSIC",
    "DML_NO_WHERE",
    "PLPGSQL_UNSAFE",
    "PRIV_ESCALATE",
    "SQL_INJ_UNION",
    "SQL_INJ_TIME",
    "DDL_FORBIDDEN",
    "COPY_EXPORT",
    "DYNAMIC_EXECUTE",
]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path, default=DATASET_PATH)
    parser.add_argument("--version", default="v1_0")
    args = parser.parse_args()

    suffix = _version_suffix(args.version)
    lr_path = MODELS_DIR / ("lr_" + suffix + ".joblib")
    lgbm_path = MODELS_DIR / ("lightgbm_" + suffix + ".joblib")
    thresholds_path = MODELS_DIR / ("thresholds_" + suffix + ".json")
    meta_path = MODELS_DIR / ("stage2_metrics_" + suffix + ".json")

    rows = read_jsonl(args.dataset)
    labels = sorted(sql_guard.ALL_LABELS)
    train = [row for row in rows if row["split"] == "train"]
    valid = [row for row in rows if row["split"] == "valid"]
    if not train or not valid:
        raise SystemExit("dataset must contain train and valid splits")

    vectorizer = DictVectorizer(sparse=True)
    x_train = vectorizer.fit_transform(_features(train))
    y_train = _target(train, labels)
    x_valid = vectorizer.transform(_features(valid))
    y_valid = _target(valid, labels)

    lr = OneVsRestClassifier(
        LogisticRegression(
            max_iter=500,
            solver="liblinear",
            class_weight="balanced",
            random_state=SEED,
        )
    )
    lr.fit(x_train, y_train)
    lr_probs = lr.predict_proba(x_valid)
    lr_thresholds = _calibrate(y_valid, lr_probs, labels)

    lgbm_models: dict[str, LGBMClassifier] = {}
    lgbm_probs = np.zeros_like(y_valid, dtype=float)
    for idx, label in enumerate(labels):
        model = LGBMClassifier(
            n_estimators=35,
            learning_rate=0.08,
            num_leaves=15,
            min_child_samples=5,
            subsample=0.9,
            colsample_bytree=0.9,
            class_weight="balanced",
            random_state=SEED + idx,
            n_jobs=1,
            verbose=-1,
        )
        model.fit(x_train, y_train[:, idx])
        lgbm_models[label] = model
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            lgbm_probs[:, idx] = model.predict_proba(x_valid)[:, 1]
    lgbm_thresholds = _calibrate(y_valid, lgbm_probs, labels)

    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    joblib.dump({"model": lr, "vectorizer": vectorizer, "labels": labels}, lr_path)
    joblib.dump({"models": lgbm_models, "vectorizer": vectorizer, "labels": labels}, lgbm_path)

    thresholds = {
        "version": suffix.replace("_", "."),
        "critical_labels": CRITICAL_LABELS,
        "lr": lr_thresholds,
        "lightgbm": lgbm_thresholds,
    }

    metrics = {
        "dataset_rows": len(rows),
        "train_rows": len(train),
        "valid_rows": len(valid),
        "label_count": len(labels),
        "dataset": str(args.dataset),
        "version": suffix,
        "artifact_lr": str(lr_path),
        "artifact_lightgbm": str(lgbm_path),
        "lr_valid_micro": _micro_metrics(y_valid, _apply(lr_probs, lr_thresholds, labels)),
        "lightgbm_valid_micro": _micro_metrics(y_valid, _apply(lgbm_probs, lgbm_thresholds, labels)),
    }
    thresholds_path.write_text(json.dumps(thresholds, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    meta_path.write_text(json.dumps(metrics, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(metrics, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def _version_suffix(value: str) -> str:
    text = value.strip().lower().replace(".", "_").replace("-", "_")
    if text.startswith("v"):
        return text
    return "v" + text


def _features(rows: list[dict[str, Any]]) -> list[dict[str, float]]:
    return [
        featurize(
            row["sql"],
            {
                "task": row.get("task", ""),
                "attack_prompt": row.get("attack_prompt", ""),
                "schema_area": row.get("schema_area", ""),
                "allowed_tables": row.get("schema_scope", []),
            },
        )
        for row in rows
    ]


def _target(rows: list[dict[str, Any]], labels: list[str]) -> np.ndarray:
    index = {label: idx for idx, label in enumerate(labels)}
    y = np.zeros((len(rows), len(labels)), dtype=int)
    for row_idx, row in enumerate(rows):
        for label in row["risk_labels"]:
            if label in index:
                y[row_idx, index[label]] = 1
    return y


def _calibrate(y_true: np.ndarray, probs: np.ndarray, labels: list[str]) -> dict[str, float]:
    thresholds: dict[str, float] = {}
    for idx, label in enumerate(labels):
        target = y_true[:, idx]
        score = probs[:, idx]
        if target.sum() == 0:
            thresholds[label] = 0.5
            continue
        if label in CRITICAL_LABELS:
            thresholds[label] = _recall_threshold(target, score, min_recall=0.95)
        else:
            thresholds[label] = _f1_threshold(target, score)
    return thresholds


def _recall_threshold(target: np.ndarray, score: np.ndarray, min_recall: float) -> float:
    best_threshold = 0.01
    best_precision = -1.0
    for threshold in sorted(set(float(x) for x in score), reverse=True) + [0.01]:
        pred = score >= threshold
        tp = int(((pred == 1) & (target == 1)).sum())
        fn = int(((pred == 0) & (target == 1)).sum())
        fp = int(((pred == 1) & (target == 0)).sum())
        recall = tp / max(tp + fn, 1)
        precision = tp / max(tp + fp, 1)
        if recall >= min_recall and precision > best_precision:
            best_precision = precision
            best_threshold = threshold
    return float(max(min(best_threshold, 0.95), 0.01))


def _f1_threshold(target: np.ndarray, score: np.ndarray) -> float:
    best_threshold = 0.5
    best_f1 = -1.0
    for threshold in sorted(set(float(x) for x in score), reverse=True) + [0.5, 0.25, 0.1]:
        pred = score >= threshold
        tp = int(((pred == 1) & (target == 1)).sum())
        fp = int(((pred == 1) & (target == 0)).sum())
        fn = int(((pred == 0) & (target == 1)).sum())
        precision = tp / max(tp + fp, 1)
        recall = tp / max(tp + fn, 1)
        f1 = 0.0 if precision + recall == 0 else 2 * precision * recall / (precision + recall)
        if f1 > best_f1:
            best_f1 = f1
            best_threshold = threshold
    return float(max(min(best_threshold, 0.95), 0.01))


def _apply(probs: np.ndarray, thresholds: dict[str, float], labels: list[str]) -> np.ndarray:
    pred = np.zeros_like(probs, dtype=int)
    for idx, label in enumerate(labels):
        pred[:, idx] = (probs[:, idx] >= thresholds.get(label, 0.5)).astype(int)
    return pred


def _micro_metrics(y_true: np.ndarray, y_pred: np.ndarray) -> dict[str, float]:
    tp = int(((y_pred == 1) & (y_true == 1)).sum())
    fp = int(((y_pred == 1) & (y_true == 0)).sum())
    fn = int(((y_pred == 0) & (y_true == 1)).sum())
    precision = tp / max(tp + fp, 1)
    recall = tp / max(tp + fn, 1)
    f1 = 0.0 if precision + recall == 0 else 2 * precision * recall / (precision + recall)
    return {"precision": precision, "recall": recall, "f1": f1}


if __name__ == "__main__":
    raise SystemExit(main())
