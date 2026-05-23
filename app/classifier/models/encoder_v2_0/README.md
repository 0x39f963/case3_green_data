Generated at: 2026-05-20 13:22:36 MSK

## encoder_v2_0

- base_model: `intfloat/multilingual-e5-small`
- head_type: `one_vs_rest_logistic_regression`
- dataset: `data/eval/dataset_v1_0.jsonl`
- dataset_note: proxy eval dataset; human golden_v1_0.jsonl was not present in this checkout
- val_dataset: `data/eval/golden_v1_0.jsonl`
- val_dataset_note: golden_v1_0 holdout converted from golden_dataset_v1_1.csv
- rows: train=4134, valid=600, test=600
- runtime_calibration: `app.classifier.encoder._semantic_calibration`
- runtime_eval_report: `data/eval/reports/encoder_eval_v2_0_golden_20260520_133559.json`
- stage4_skip_rate_proxy: `0.6393700787401575`
- runtime_latency_p95_ms: `14.190540998242795`
- input_prefix: `passage: `
- seed: `43`

## Critical Metrics

| label | precision | recall | f1 | support |
|---|---:|---:|---:|---:|
| DIRECT_SENSITIVE | 0.984 | 1.000 | 0.992 | 120 |
| EXCESSIVE_SCOPE | 0.946 | 0.789 | 0.860 | 242 |
| WRONG_JOIN_PATH | 1.000 | 1.000 | 1.000 | 23 |
| HALLUCINATED_TABLE | 1.000 | 1.000 | 1.000 | 40 |
| HALLUCINATED_COLUMN | 1.000 | 1.000 | 1.000 | 25 |

## Files

- `head.joblib`: sklearn OneVsRest LogisticRegression head over 384d e5 embeddings.
- `thresholds.json`: per-label primary, low and high thresholds.
- `model_meta.json`: training config and test metrics.
- `README.md`: this note.
