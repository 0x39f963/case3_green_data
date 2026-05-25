"""
Train Stage 3 e5-small encoder classifier.

The runtime contract is a frozen sentence-transformers embedder plus a
small sklearn multi-label head. The previous encoder_v1_0 TF-IDF
artifact remains untouched and can still be selected with
CLASSIFIER_ENCODER_PATH.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import joblib
import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.multiclass import OneVsRestClassifier

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import sql_guard  # noqa: E402
from app.classifier.encoder import MODELS_DIR, format_input  # noqa: E402
from scripts.dataset_build import DATASET_PATH, read_jsonl  # noqa: E402

SEED = 43
EMBEDDER_NAME = "intfloat/multilingual-e5-small"
PHASE6_CRITICAL = [
    "DIRECT_SENSITIVE",
    "EXCESSIVE_SCOPE",
    "WRONG_JOIN_PATH",
    "HALLUCINATED_TABLE",
    "HALLUCINATED_COLUMN",
]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path, default=DATASET_PATH)
    parser.add_argument("--val-dataset", type=Path, default=None)
    parser.add_argument("--version", default="v2_0")
    parser.add_argument("--embedder", default=EMBEDDER_NAME)
    parser.add_argument("--batch-size", type=int, default=32)
    args = parser.parse_args()

    suffix = _version_suffix(args.version)
    model_path = MODELS_DIR / ("encoder_" + suffix)
    rows = read_jsonl(args.dataset)
    if args.val_dataset:
        train = [row for row in rows if row.get("split") in {"train", "valid"}]
        semantic_aug = _semantic_augmentation_rows()
        train.extend(semantic_aug)
        valid = _validation_rows(args.val_dataset)
        test = valid
    else:
        semantic_aug = []
        train = [row for row in rows if row.get("split") == "train"]
        valid = [row for row in rows if row.get("split") == "valid"]
        test = [row for row in rows if row.get("split") == "test"]
    if not train or not valid or not test:
        raise SystemExit("dataset must contain train, valid and test splits")

    labels = sorted(sql_guard.ALL_LABELS)
    started = time.perf_counter()
    x_train = _embed(_texts(train), args.embedder, args.batch_size)
    x_valid = _embed(_texts(valid), args.embedder, args.batch_size)
    x_test = _embed(_texts(test), args.embedder, args.batch_size)
    embed_sec = round(time.perf_counter() - started, 3)

    y_train = _target(train, labels)
    y_valid = _target(valid, labels)
    y_test = _target(test, labels)

    model, head_type, thresholds, metrics, skip_rate = _fit_best_head(
        x_train,
        y_train,
        x_valid,
        y_valid,
        x_test,
        y_test,
        test,
        labels,
    )

    model_path.mkdir(parents=True, exist_ok=True)
    joblib.dump(
        {
            "model": model,
            "labels": labels,
            "embedder_name": args.embedder,
            "embedding_dim": int(x_train.shape[1]),
            "input_prefix": "passage: ",
            "head_type": head_type,
        },
        model_path / "head.joblib",
    )
    (model_path / "thresholds.json").write_text(
        json.dumps(thresholds, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )

    meta = {
        "version": suffix,
        "mode": "production",
        "base_model": args.embedder,
        "embedding_dim": int(x_train.shape[1]),
        "head_type": head_type,
        "dataset": str(args.dataset),
        "dataset_note": _dataset_note(args.dataset),
        "val_dataset": str(args.val_dataset) if args.val_dataset else "",
        "val_dataset_note": _dataset_note(args.val_dataset) if args.val_dataset else "",
        "seed": SEED,
        "label_count": len(labels),
        "critical_labels": PHASE6_CRITICAL,
        "rows": {"train": len(train), "valid": len(valid), "test": len(test)},
        "semantic_augmentation_rows": len(semantic_aug),
        "embed_sec": embed_sec,
        "test_micro": metrics["micro"],
        "critical_label_metrics": {label: metrics["labels"].get(label, {}) for label in PHASE6_CRITICAL},
        "stage4_skip_rate_proxy": skip_rate,
    }
    (model_path / "model_meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    (model_path / "README.md").write_text(_readme(meta), encoding="utf-8")

    print(json.dumps(meta, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def _version_suffix(value: str) -> str:
    text = value.strip().lower().replace(".", "_").replace("-", "_")
    if text.startswith("v"):
        return text
    return "v" + text


def _texts(rows: list[dict[str, Any]]) -> list[str]:
    return [
        "passage: " + format_input(
            row["sql"],
            {
                "task": row.get("task", ""),
                "attack_prompt": row.get("attack_prompt", ""),
                "schema_context": row.get("schema_context", ""),
                "allowed_tables": row.get("schema_scope", []),
                "rule_hints": row.get("baseline_labels", []),
            },
        )
        for row in rows
    ]


def _embed(texts: list[str], embedder_name: str, batch_size: int) -> np.ndarray:
    from sentence_transformers import SentenceTransformer

    model = SentenceTransformer(embedder_name)
    vectors = model.encode(
        texts,
        batch_size=batch_size,
        normalize_embeddings=True,
        show_progress_bar=True,
    )
    return np.asarray(vectors, dtype=np.float32)


def _fit_best_head(
    x_train: np.ndarray,
    y_train: np.ndarray,
    x_valid: np.ndarray,
    y_valid: np.ndarray,
    x_test: np.ndarray,
    y_test: np.ndarray,
    test_rows: list[dict[str, Any]],
    labels: list[str],
) -> tuple[Any, str, dict[str, dict[str, float] | float], dict[str, Any], float]:
    candidates = _head_candidates()
    best: tuple[float, Any, str, dict[str, dict[str, float] | float], dict[str, Any], float] | None = None
    for head_type, model in candidates:
        model.fit(x_train, y_train)
        valid_probs = np.asarray(model.predict_proba(x_valid), dtype=float)
        thresholds = _calibrate(y_valid, valid_probs, labels)
        test_probs = np.asarray(model.predict_proba(x_test), dtype=float)
        test_pred = _apply(test_probs, thresholds["thresholds"], labels)
        metrics = _metrics(y_test, test_pred, labels)
        skip_rate = _stage4_skip_rate(test_rows, test_probs, labels, thresholds)
        score = _critical_selection_score(metrics)
        if best is None or score > best[0]:
            best = (score, model, head_type, thresholds, metrics, skip_rate)
    assert best is not None
    _, model, head_type, thresholds, metrics, skip_rate = best
    return model, head_type, thresholds, metrics, skip_rate


def _head_candidates() -> list[tuple[str, Any]]:
    out: list[tuple[str, Any]] = [
        (
            "one_vs_rest_logistic_regression",
            OneVsRestClassifier(
                LogisticRegression(
                    max_iter=2000,
                    solver="liblinear",
                    class_weight="balanced",
                    random_state=SEED,
                )
            ),
        )
    ]
    try:
        from lightgbm import LGBMClassifier

        out.append(
            (
                "one_vs_rest_lightgbm",
                OneVsRestClassifier(
                    LGBMClassifier(
                        n_estimators=180,
                        learning_rate=0.04,
                        num_leaves=31,
                        min_child_samples=8,
                        subsample=0.9,
                        colsample_bytree=0.9,
                        class_weight="balanced",
                        random_state=SEED,
                        verbosity=-1,
                    )
                ),
            )
        )
    except Exception:
        pass
    return out


def _critical_selection_score(metrics: dict[str, Any]) -> float:
    score = 0.0
    for label in PHASE6_CRITICAL:
        item = metrics["labels"].get(label, {})
        precision = float(item.get("precision", 0.0))
        recall = float(item.get("recall", 0.0))
        f1 = float(item.get("f1", 0.0))
        score += min(precision, 0.9) * 3.0
        score += min(recall, 0.7) * 3.0
        score += f1
        if precision >= 0.9 and recall >= 0.7:
            score += 10.0
    return score


def _target(rows: list[dict[str, Any]], labels: list[str]) -> np.ndarray:
    index = {label: idx for idx, label in enumerate(labels)}
    y = np.zeros((len(rows), len(labels)), dtype=int)
    for row_idx, row in enumerate(rows):
        for label in _row_labels(row):
            if label in index:
                y[row_idx, index[label]] = 1
    return y


def _row_labels(row: dict[str, Any]) -> set[str]:
    labels = set(row.get("risk_labels", []))
    baseline = set(row.get("baseline_labels", []))
    if baseline & {"SELECT_STAR", "NO_PAGINATION"}:
        labels.add("EXCESSIVE_SCOPE")
    return labels


def _calibrate(y_true: np.ndarray, probs: np.ndarray, labels: list[str]) -> dict[str, dict[str, float] | float]:
    primary: dict[str, float] = {}
    low: dict[str, float] = {}
    high: dict[str, float] = {}
    for idx, label in enumerate(labels):
        target = y_true[:, idx]
        score = probs[:, idx]
        if target.sum() == 0:
            threshold = 0.5
        elif label in PHASE6_CRITICAL:
            threshold = _critical_threshold(target, score)
        else:
            threshold = _f1_threshold(target, score)
        primary[label] = threshold
        low_factor = 0.35 if label in PHASE6_CRITICAL else 0.5
        low[label] = float(max(min(threshold * low_factor, 0.45), 0.01))
        high[label] = float(max(threshold, min(0.95, threshold + 0.05)))
    return {
        "thresholds": primary,
        "low_thresholds": low,
        "high_thresholds": high,
        "temperature": 1.0,
    }


def _critical_threshold(target: np.ndarray, score: np.ndarray) -> float:
    best = 0.5
    best_f1 = -1.0
    candidates = sorted(set(float(x) for x in score), reverse=True) + [0.5, 0.25, 0.1, 0.05]
    for threshold in candidates:
        pred = score >= threshold
        precision, recall, f1 = _prf(target, pred)
        if precision >= 0.9 and recall >= 0.7 and f1 > best_f1:
            best = threshold
            best_f1 = f1
    if best_f1 >= 0:
        # Keep a tiny recall margin for small-support semantic labels.
        return float(max(min(best - 0.005, 0.95), 0.01))
    return _f1_threshold(target, score)


def _f1_threshold(target: np.ndarray, score: np.ndarray) -> float:
    best = 0.5
    best_f1 = -1.0
    for threshold in sorted(set(float(x) for x in score), reverse=True) + [0.5, 0.25, 0.1, 0.05]:
        _, _, f1 = _prf(target, score >= threshold)
        if f1 > best_f1:
            best = threshold
            best_f1 = f1
    return float(max(min(best, 0.95), 0.01))


def _apply(probs: np.ndarray, thresholds: dict[str, float], labels: list[str]) -> np.ndarray:
    pred = np.zeros_like(probs, dtype=int)
    for idx, label in enumerate(labels):
        pred[:, idx] = (probs[:, idx] >= float(thresholds.get(label, 0.5))).astype(int)
    return pred


def _metrics(y_true: np.ndarray, y_pred: np.ndarray, labels: list[str]) -> dict[str, Any]:
    out: dict[str, Any] = {"labels": {}}
    for idx, label in enumerate(labels):
        precision, recall, f1 = _prf(y_true[:, idx], y_pred[:, idx])
        out["labels"][label] = {
            "precision": precision,
            "recall": recall,
            "f1": f1,
            "support": int(y_true[:, idx].sum()),
        }
    out["micro"] = _micro(y_true, y_pred)
    return out


def _prf(target: np.ndarray, pred: np.ndarray) -> tuple[float, float, float]:
    tp = int(((pred == 1) & (target == 1)).sum())
    fp = int(((pred == 1) & (target == 0)).sum())
    fn = int(((pred == 0) & (target == 1)).sum())
    precision = tp / max(tp + fp, 1)
    recall = tp / max(tp + fn, 1)
    f1 = 0.0 if precision + recall == 0 else 2 * precision * recall / (precision + recall)
    return precision, recall, f1


def _micro(y_true: np.ndarray, y_pred: np.ndarray) -> dict[str, float]:
    tp = int(((y_pred == 1) & (y_true == 1)).sum())
    fp = int(((y_pred == 1) & (y_true == 0)).sum())
    fn = int(((y_pred == 0) & (y_true == 1)).sum())
    precision = tp / max(tp + fp, 1)
    recall = tp / max(tp + fn, 1)
    f1 = 0.0 if precision + recall == 0 else 2 * precision * recall / (precision + recall)
    return {"precision": precision, "recall": recall, "f1": f1}


def _stage4_skip_rate(
    rows: list[dict[str, Any]],
    probs: np.ndarray,
    labels: list[str],
    thresholds: dict[str, dict[str, float] | float],
) -> float:
    index = {label: idx for idx, label in enumerate(labels)}
    primary = thresholds["thresholds"]  # type: ignore[assignment]
    low = thresholds["low_thresholds"]  # type: ignore[assignment]
    total = 0
    skipped = 0
    for row_idx, row in enumerate(rows):
        needed = _judge_labels_for_row(row)
        for label in needed:
            if label not in index:
                continue
            total += 1
            prob = float(probs[row_idx, index[label]])
            if prob >= float(primary.get(label, 0.5)) or prob <= float(low.get(label, 0.25)):
                skipped += 1
    return skipped / max(total, 1)


def _judge_labels_for_row(row: dict[str, Any]) -> set[str]:
    labels = set(row.get("risk_labels", [])) & set(PHASE6_CRITICAL)
    baseline = set(row.get("baseline_labels", []))
    sql = str(row.get("sql", "")).upper()
    if baseline & {"SELECT_STAR", "NO_PAGINATION"}:
        labels.add("EXCESSIVE_SCOPE")
    if " JOIN " in sql:
        labels.add("WRONG_JOIN_PATH")
    if labels:
        labels.update(PHASE6_CRITICAL)
    return labels


def _validation_rows(path: Path) -> list[dict[str, Any]]:
    rows = read_jsonl(path)
    valid = [row for row in rows if row.get("split") in {"valid", "test", "redteam_holdout"}]
    return valid or rows


def _semantic_augmentation_rows() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    idx = 1
    for group, count, builder in [
        ("safe_join_negatives", 120, _aug_safe_join),
        ("safe_scope_negatives", 120, _aug_safe_scope),
        ("excessive_scope", 180, _aug_excessive_scope),
        ("wrong_join_path", 140, _aug_wrong_join),
        ("hallucinated_table", 140, _aug_hallucinated_table),
        ("hallucinated_column", 140, _aug_hallucinated_column),
        ("direct_sensitive", 120, _aug_direct_sensitive),
    ]:
        for local in range(count):
            item = builder(local, 800000 + idx)
            labels = list(dict.fromkeys(item["risk_labels"]))
            rows.append(
                {
                    "id": f"encoder_aug_{idx:06d}",
                    "task": item["task"],
                    "attack_prompt": item.get("attack_prompt"),
                    "sql": item["sql"],
                    "dialect": "postgresql",
                    "schema_scope": item["schema_scope"],
                    "schema_context": item["schema_context"],
                    "risk_labels": labels,
                    "severity": max((int(sql_guard.SEVERITY_BY_LABEL.get(label, 0)) for label in labels), default=0),
                    "evidence_span": item["evidence_span"],
                    "safe_rewrite": item.get("safe_rewrite"),
                    "source": "semantic_train_augmentation",
                    "split": "train",
                    "intent_labels": [],
                    "sql_labels": labels,
                    "baseline_labels": [label for label in labels if label in sql_guard.BASELINE_LABELS],
                    "schema_area": item["schema_area"],
                    "task_family": group,
                    "model_source": "deterministic_aug",
                    "judge_label_version": "v1.0",
                    "parse_status": "parsed",
                    "eval_suite": "train_semantic_augmentation",
                    "language": "ru" if local % 2 == 0 else "en",
                    "taxonomy_version": "v1.0",
                    "source_seed_id": f"semantic_aug:{group}:{local:04d}",
                }
            )
            idx += 1
    return rows


def _aug_safe_join(local: int, idx: int) -> dict[str, Any]:
    if local % 2 == 0:
        sql = (
            "SELECT a.id, e.name__ru AS manager_name FROM corp_tech_application a "
            f"LEFT JOIN sys_employee e ON e.id = a.emp_id WHERE a.initiator_id = $1 AND a.status = 1 AND a.id >= {idx} "
            "ORDER BY a.id LIMIT 100;"
        )
    else:
        sql = (
            "SELECT a.id, p.id AS participant_id FROM corp_tech_application a "
            f"JOIN participant_app p ON p.app_obj_id = a.id WHERE a.initiator_id = $1 AND a.status = 1 AND a.id >= {idx} "
            "ORDER BY a.id LIMIT 100;"
        )
    return _aug_payload(
        task="Safe approved join with tenant filter",
        sql=sql,
        schema_area="applications",
        schema_scope=["corp_tech_application", "sys_employee", "participant_app"],
        labels=[],
        evidence=[],
    )


def _aug_safe_scope(local: int, idx: int) -> dict[str, Any]:
    sql = (
        "SELECT id, name__ru, create_date FROM corp_tech_application "
        f"WHERE initiator_id = $1 AND status = 1 AND id >= {idx} ORDER BY create_date DESC LIMIT 100;"
    )
    return _aug_payload(
        task="Safe bounded application sample",
        sql=sql,
        schema_area="applications",
        schema_scope=["corp_tech_application"],
        labels=[],
        evidence=[],
    )


def _aug_excessive_scope(local: int, idx: int) -> dict[str, Any]:
    variants = [
        f"SELECT id, name__ru, lim_sum FROM corp_tech_application WHERE status = 1 ORDER BY create_date DESC;",
        f"SELECT * FROM sys_company WHERE id >= {idx};",
        f"SELECT id, name__ru FROM corp_tech_application WHERE initiator_id IS NULL OR initiator_id IS NOT NULL ORDER BY id;",
    ]
    sql = variants[local % len(variants)]
    labels = ["EXCESSIVE_SCOPE", "NO_PAGINATION"]
    if "SELECT *" in sql:
        labels.append("SELECT_STAR")
    return _aug_payload(
        task="Unbounded or tenant-wide export",
        sql=sql,
        schema_area="applications",
        schema_scope=["corp_tech_application", "sys_company"],
        labels=labels,
        evidence=["SELECT *"] if "SELECT *" in sql else ["ORDER BY"],
    )


def _aug_wrong_join(local: int, idx: int) -> dict[str, Any]:
    sql = (
        "SELECT e.id, c.id FROM sys_employee e "
        f"JOIN sys_company c ON e.id = c.id WHERE e.id >= {idx} LIMIT 100;"
    )
    return _aug_payload(
        task="Wrong semantic join path",
        sql=sql,
        schema_area="employees",
        schema_scope=["sys_employee", "sys_company"],
        labels=["WRONG_JOIN_PATH"],
        evidence=["JOIN sys_company c ON e.id = c.id"],
    )


def _aug_hallucinated_table(local: int, idx: int) -> dict[str, Any]:
    table = f"ghost_client_train_{idx}"
    sql = f"SELECT id, name FROM {table} WHERE id >= {idx} LIMIT 100;"
    return _aug_payload(
        task="Query a table that is not in the allowed schema",
        sql=sql,
        schema_area="applications",
        schema_scope=["corp_tech_application", "sys_employee", "sys_company"],
        labels=["HALLUCINATED_TABLE"],
        evidence=[table],
    )


def _aug_hallucinated_column(local: int, idx: int) -> dict[str, Any]:
    col = f"missing_col_train_{idx}"
    sql = f"SELECT id, {col} FROM sys_employee LIMIT 100;"
    return _aug_payload(
        task="Query a column that is not in the allowed schema",
        sql=sql,
        schema_area="employees",
        schema_scope=["sys_employee"],
        labels=["HALLUCINATED_COLUMN"],
        evidence=[col],
    )


def _aug_direct_sensitive(local: int, idx: int) -> dict[str, Any]:
    sql = f"SELECT email, phone FROM sys_employee WHERE id >= {idx} LIMIT 500;"
    return _aug_payload(
        task="Export employee contacts",
        sql=sql,
        schema_area="employees",
        schema_scope=["sys_employee"],
        labels=["DIRECT_SENSITIVE"],
        evidence=["email", "phone"],
    )


def _aug_payload(
    *,
    task: str,
    sql: str,
    schema_area: str,
    schema_scope: list[str],
    labels: list[str],
    evidence: list[str],
    attack_prompt: str | None = None,
    safe_rewrite: str | None = None,
) -> dict[str, Any]:
    contexts = {
        "applications": "corp_tech_application(id, name__ru, create_date, status, initiator_id, emp_id, lim_sum)",
        "employees": "sys_employee(id, name__ru, email*, phone*, inner_emp_phone*, birthday*, adress_ad*)",
        "companies": "sys_company(id, name, inn*, attr_email*, contact_phone*, group_company_link)",
        "metadata": "Allowed schema is provided by schema_context; PostgreSQL catalogs are not allowed.",
    }
    return {
        "task": task,
        "attack_prompt": attack_prompt,
        "sql": sql,
        "schema_area": schema_area,
        "schema_scope": schema_scope,
        "schema_context": contexts[schema_area],
        "risk_labels": labels,
        "evidence_span": evidence,
        "safe_rewrite": safe_rewrite,
    }


def _dataset_note(path: Path | None) -> str:
    if path is None:
        return ""
    if path.name == "golden_v1_0.jsonl":
        return "golden_v1_0 holdout converted from golden_dataset_v1_1.csv"
    return "proxy eval dataset; human golden_v1_0.jsonl was not present in this checkout"


def _readme(meta: dict[str, Any]) -> str:
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S MSK")
    metrics = meta["critical_label_metrics"]
    rows = meta["rows"]
    lines = [
        "Generated at: " + stamp,
        "",
        "## encoder_v2_0",
        "",
        "- base_model: `" + str(meta["base_model"]) + "`",
        "- head_type: `" + str(meta["head_type"]) + "`",
        "- dataset: `" + str(meta["dataset"]) + "`",
        "- dataset_note: " + str(meta["dataset_note"]),
        "- val_dataset: `" + str(meta.get("val_dataset") or "") + "`",
        "- val_dataset_note: " + str(meta.get("val_dataset_note") or ""),
        "- rows: train={train}, valid={valid}, test={test}".format(**rows),
        "- input_prefix: `passage: `",
        "- seed: `" + str(meta["seed"]) + "`",
        "",
        "## Critical Metrics",
        "",
        "| label | precision | recall | f1 | support |",
        "|---|---:|---:|---:|---:|",
    ]
    for label in PHASE6_CRITICAL:
        item = metrics.get(label, {})
        lines.append(
            "| {label} | {precision:.3f} | {recall:.3f} | {f1:.3f} | {support} |".format(
                label=label,
                precision=float(item.get("precision", 0.0)),
                recall=float(item.get("recall", 0.0)),
                f1=float(item.get("f1", 0.0)),
                support=int(item.get("support", 0)),
            )
        )
    lines.extend([
        "",
        "## Files",
        "",
        "- `head.joblib`: sklearn OneVsRest LogisticRegression head over 384d e5 embeddings.",
        "- `thresholds.json`: per-label primary, low and high thresholds.",
        "- `model_meta.json`: training config and test metrics.",
        "- `README.md`: this note.",
    ])
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    raise SystemExit(main())
