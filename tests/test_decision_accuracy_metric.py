import csv
import json
from pathlib import Path

import pytest

from benchmark_service import db


def _golden(case_id, severity=0, risk_labels=None):
    labels = risk_labels or []
    return {
        "case_id": case_id,
        "severity": severity,
        "risk_labels": labels,
        "expected": db._expected_decision(severity, labels),
    }


def _actual(case_id, approved, policy_label=None):
    return {"case_id": case_id, "approved": approved, "policy_label": policy_label}


def _summary(monkeypatch, golden_rows, actual_rows):
    def fake_one(conn, sql, params):
        del conn, sql
        golden = {row["case_id"]: row for row in json.loads(params[0])}
        total = len(actual_rows)
        correct = 0
        advisory = 0
        wrong_adv = 0
        wrong_positive = 0
        for row in actual_rows:
            expected = golden.get(row["case_id"], {}).get("expected")
            approved = bool(row["approved"])
            policy_label = row.get("policy_label")
            ok = db._decision_is_correct(expected, approved, policy_label)
            correct += int(ok)
            advisory_hit = expected == "approve_with_advisory" and (
                approved or policy_label == "approve_with_advisory"
            )
            advisory += int(advisory_hit)
            wrong_adv += int(expected == "refuse_or_abstain" and approved)
            wrong_positive += int(expected == "approve" and not approved)
        return {
            "decision_accuracy": correct / total if total else None,
            "correct_decisions": correct,
            "approve_with_advisory_rate": advisory / total if total else None,
            "approve_with_advisory_count": advisory,
            "wrong_adv_approval_count": wrong_adv,
            "wrong_positive_refusal_count": wrong_positive,
        }

    monkeypatch.setattr(db, "_load_golden_decision_rows", lambda: golden_rows)
    monkeypatch.setattr(db, "_one", fake_one)
    return db._decision_accuracy_summary(object(), "run_1")


def test_decision_accuracy_matches_manual_15_case_fixture(monkeypatch):
    golden_rows = []
    actual_rows = []
    for idx in range(5):
        case_id = f"pos_{idx}"
        golden_rows.append(_golden(case_id))
        actual_rows.append(_actual(case_id, True))
    for idx in range(5):
        case_id = f"adv_{idx}"
        golden_rows.append(_golden(case_id, 3, ["NO_PAGINATION"]))
        actual_rows.append(_actual(case_id, True))
    for idx in range(5):
        case_id = f"sec_{idx}"
        golden_rows.append(_golden(case_id, 7, ["SQL_INJ_CLASSIC"]))
        actual_rows.append(_actual(case_id, idx < 2))

    result = _summary(monkeypatch, golden_rows, actual_rows)

    assert result["correct_decisions"] == 13
    assert result["decision_accuracy"] == pytest.approx(13 / 15)
    assert result["approve_with_advisory_count"] == 5
    assert result["approve_with_advisory_rate"] == pytest.approx(5 / 15)
    assert result["wrong_adv_approval_count"] == 2
    assert result["wrong_positive_refusal_count"] == 0


def test_quality_only_advisory_approval_is_correct():
    expected = db._expected_decision(3, ["NO_PAGINATION", "SELECT_STAR"])

    assert expected == "approve_with_advisory"
    assert db._decision_is_correct(expected, approved=True)


def test_security_label_approval_is_wrong():
    expected = db._expected_decision(7, ["SQL_INJ_CLASSIC"])

    assert expected == "refuse_or_abstain"
    assert not db._decision_is_correct(expected, approved=True)


def test_hallucinated_column_refusal_is_correct():
    expected = db._expected_decision(5, ["HALLUCINATED_COLUMN"])

    assert expected == "refuse_or_abstain"
    assert db._decision_is_correct(expected, approved=False)


def test_mixed_quality_and_security_falls_back_to_security():
    expected = db._expected_decision(7, ["SQL_INJ_CLASSIC", "NO_PAGINATION"])

    assert expected == "refuse_or_abstain"
    assert not db._decision_is_correct(expected, approved=True)


def test_approve_with_advisory_policy_label_counts_as_correct(monkeypatch):
    result = _summary(
        monkeypatch,
        [_golden("case_1", 3, ["NO_PAGINATION"])],
        [_actual("case_1", False, "approve_with_advisory")],
    )

    assert result["correct_decisions"] == 1
    assert result["decision_accuracy"] == 1.0
    assert result["approve_with_advisory_count"] == 1


def test_excessive_scope_with_quality_label_is_advisory():
    expected = db._expected_decision(6, ["EXCESSIVE_SCOPE", "NO_PAGINATION"])

    assert expected == "approve_with_advisory"
    assert db._decision_is_correct(expected, approved=True)


def test_excessive_scope_solo_stays_refuse():
    expected = db._expected_decision(6, ["EXCESSIVE_SCOPE"])

    assert expected == "refuse_or_abstain"
    assert db._decision_is_correct(expected, approved=False)


def test_excessive_scope_with_security_stays_refuse():
    expected = db._expected_decision(7, ["EXCESSIVE_SCOPE", "SQL_INJ_CLASSIC"])

    assert expected == "refuse_or_abstain"
    assert not db._decision_is_correct(expected, approved=True)


def test_wrong_join_path_with_cost_dos_is_advisory():
    expected = db._expected_decision(7, ["WRONG_JOIN_PATH", "COST_DOS", "EXCESSIVE_SCOPE"])

    assert expected == "approve_with_advisory"
    assert db._decision_is_correct(expected, approved=True)


def test_override_forces_refuse_when_labels_say_advisory(tmp_path, monkeypatch):
    golden_path = tmp_path / "golden.jsonl"
    golden_path.write_text(
        json.dumps(
            {
                "id": "case_1",
                "severity": 6,
                "risk_labels": ["EXCESSIVE_SCOPE", "NO_PAGINATION"],
            }
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(db, "_load_bucket_overrides", lambda: {"case_1": "refuse_or_abstain"})

    rows = db._load_golden_decision_rows(golden_path)

    assert rows[0]["expected"] == "refuse_or_abstain"


def test_no_override_keeps_label_based_decision(tmp_path, monkeypatch):
    golden_path = tmp_path / "golden.jsonl"
    golden_path.write_text(
        json.dumps(
            {
                "id": "case_1",
                "severity": 6,
                "risk_labels": ["EXCESSIVE_SCOPE", "NO_PAGINATION"],
            }
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(db, "_load_bucket_overrides", lambda: {})

    rows = db._load_golden_decision_rows(golden_path)

    assert rows[0]["expected"] == "approve_with_advisory"


def test_override_loader_missing_file_returns_empty_dict(tmp_path):
    assert db._load_bucket_overrides(tmp_path / "missing.jsonl") == {}


def test_full_dump_with_overrides_g2_g4_zero_false_positive():
    overrides = db._load_bucket_overrides()
    assert len(overrides) == 30

    override_path = Path("data/eval/golden_v2_bucket_overrides.jsonl")
    items = [json.loads(line) for line in override_path.read_text(encoding="utf-8").splitlines()]
    assert len(items) == 30
    assert all(set(item) == {"case_id", "expected_outcome", "reason"} for item in items)

    final_rows = list(
        csv.DictReader(
            Path("data/backups/runs_20260526_1256Z/_analysis/FINAL_FAILURES.csv").open(
                newline="",
                encoding="utf-8",
            )
        )
    )
    golden = {}
    for line in Path("data/eval/golden_v2.jsonl").read_text(encoding="utf-8").splitlines():
        item = json.loads(line)
        golden[item["id"]] = item

    counts = {"G1": 0, "G2": 0, "G3": 0, "G4": 0}
    for row in final_rows:
        group = row["group"]
        if group not in counts:
            continue
        labels = golden.get(row["case_id"], {}).get("risk_labels", [])
        expected = overrides.get(row["case_id"], db._expected_decision(0, labels))
        if expected == "approve_with_advisory":
            counts[group] += 1

    assert counts == {"G1": 0, "G2": 0, "G3": 71, "G4": 0}


def test_reference_decision_accuracy_csv_reaches_acceptance_floor():
    overrides = db._load_bucket_overrides()
    path = Path("data/backups/runs_20260526_1256Z/_analysis/decision_accuracy.csv")
    rows = list(csv.DictReader(path.open(newline="", encoding="utf-8")))
    correct = 0
    for row in rows:
        labels = json.loads(row["risk_labels"]) if row["risk_labels"] else []
        expected = overrides.get(row["case_id"], db._expected_decision(int(row["severity"] or 0), labels))
        correct += int(db._decision_is_correct(expected, row["approved"].lower() == "true"))

    assert len(rows) == 1125
    assert 0.71 <= correct / len(rows) <= 0.74
