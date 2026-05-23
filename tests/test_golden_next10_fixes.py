"""Targeted tests for next-10 hypotheses pipeline fixes."""

from __future__ import annotations

from app import classifier, generator_selector, intent_classifier, llm_provider, sentinel, sql_guard
from app.classifier import ml


def _labels(sql: str, ctx: dict | None = None) -> set[str]:
    return {item.vuln_class for item in sql_guard.check(sql, ctx or {})}


# H2 sentinel detection -------------------------------------------------------


def test_sentinel_detects_refusal_required() -> None:
    sql = "SELECT 'REFUSAL_REQUIRED' AS reason, 'authz block' AS message;"
    detected = sentinel.detect(sql)
    assert detected is not None
    assert detected.kind == sentinel.REFUSAL_REQUIRED
    assert "authz block" in detected.message


def test_sentinel_detects_malformed_refusal_required() -> None:
    sql = "REFUSAL_REQUIRED AS reason, 'Requests DML operations beyond SELECT' AS message;"
    detected = sentinel.detect(sql)
    assert detected is not None
    assert detected.kind == sentinel.REFUSAL_REQUIRED
    assert "DML operations" in detected.message


def test_sentinel_detects_insufficient_context_missing() -> None:
    sql = "SELECT 'INSUFFICIENT_CONTEXT' AS reason, 'no overlay' AS missing"
    detected = sentinel.detect(sql)
    assert detected is not None
    assert detected.kind == sentinel.INSUFFICIENT_CONTEXT
    assert "no overlay" in detected.message


def test_sentinel_returns_none_for_business_sql() -> None:
    sql = "SELECT id, COUNT(*) AS n FROM sys_employee GROUP BY id LIMIT 100"
    assert sentinel.detect(sql) is None


# H3 fail-closed overlay ------------------------------------------------------


def test_enforce_overlay_blocks_known_table_without_allowed() -> None:
    sql = "SELECT id FROM sys_employee LIMIT 10"
    labels = _labels(sql, {"enforce_overlay": True, "allowed_tables": []})
    assert "SCHEMA_OVERLAY_MISSING" in labels
    # HALLUCINATED_TABLE не используется когда overlay в принципе пуст;
    # это другой класс ошибки и должен идти отдельной меткой.
    assert "HALLUCINATED_TABLE" not in labels


def test_enforce_overlay_with_explicit_allowed_keeps_business_table() -> None:
    sql = "SELECT id FROM sys_employee LIMIT 10"
    labels = _labels(
        sql,
        {
            "enforce_overlay": True,
            "allowed_tables": ["sys_employee"],
            "allowed_columns": {"sys_employee": ["id"]},
        },
    )
    assert "SCHEMA_OVERLAY_MISSING" not in labels
    assert "HALLUCINATED_TABLE" not in labels


# H4 banned identifiers -------------------------------------------------------


def test_selector_hard_rejects_banned_identifier() -> None:
    candidates = [
        "SELECT lim_sum FROM scp_application LIMIT 10",
        "SELECT id FROM scp_application LIMIT 10",
    ]
    ctx = {
        "allowed_tables": ["scp_application"],
        "allowed_columns": {"scp_application": ["id", "lim_sum"]},
        "banned_identifiers": ["lim_sum"],
    }
    selected = generator_selector.select_best_with_details(candidates, ctx)
    assert selected.selected_index == 1
    assert selected.scores[0]["banned_identifier_hits"] == ["lim_sum"]
    assert "BANNED_IDENTIFIER" in selected.scores[0]["hard_fail_labels"]


# H5 AST-aware PII ------------------------------------------------------------


def test_pii_aggregate_count_not_direct_sensitive() -> None:
    ctx = {"sensitive_fields": {"sys_employee": ["phone"]}}
    labels = _labels("SELECT COUNT(phone) AS n FROM sys_employee", ctx)
    assert "DIRECT_SENSITIVE" not in labels


def test_pii_count_distinct_not_direct_sensitive() -> None:
    ctx = {"sensitive_fields": {"sys_employee": ["phone"]}}
    labels = _labels(
        "SELECT COUNT(DISTINCT phone) AS n FROM sys_employee",
        ctx,
    )
    assert "DIRECT_SENSITIVE" not in labels


def test_pii_md5_mask_not_direct_sensitive() -> None:
    ctx = {"sensitive_fields": {"sys_employee": ["phone"]}}
    labels = _labels(
        "SELECT md5(phone) AS phone_hash FROM sys_employee LIMIT 10",
        ctx,
    )
    assert "DIRECT_SENSITIVE" not in labels


def test_pii_left_substring_mask_not_direct_sensitive() -> None:
    ctx = {"sensitive_fields": {"sys_employee": ["phone"]}}
    labels = _labels(
        "SELECT LEFT(phone, 3) AS prefix FROM sys_employee LIMIT 10",
        ctx,
    )
    assert "DIRECT_SENSITIVE" not in labels


def test_pii_raw_projection_is_direct_sensitive() -> None:
    ctx = {"sensitive_fields": {"sys_employee": ["phone"]}}
    labels = _labels("SELECT phone FROM sys_employee LIMIT 10", ctx)
    assert "DIRECT_SENSITIVE" in labels


def test_pii_cast_still_direct_sensitive() -> None:
    ctx = {"sensitive_fields": {"sys_employee": ["phone"]}}
    labels = _labels(
        "SELECT phone::text AS p FROM sys_employee LIMIT 10",
        ctx,
    )
    assert "DIRECT_SENSITIVE" in labels


# H10 security vs quality bucket ----------------------------------------------


def test_label_buckets_classify_security_and_quality() -> None:
    assert sql_guard.label_bucket("SQL_INJ_CLASSIC") == "security"
    assert sql_guard.label_bucket("DIRECT_SENSITIVE") == "security"
    assert sql_guard.label_bucket("MASKING_REQUIRED") == "security"
    assert sql_guard.label_bucket("HALLUCINATED_TABLE") == "security"
    assert sql_guard.label_bucket("UNBOUND_PLACEHOLDER") == "security"
    assert sql_guard.label_bucket("NO_PAGINATION") == "quality"
    assert sql_guard.label_bucket("SELECT_STAR") == "quality"
    assert sql_guard.label_bucket("NON_SARGABLE_FILTER") == "quality"
    assert sql_guard.label_bucket("COST_DOS") == "quality"


# P1 PII-in-WHERE intent misunderstanding ------------------------------------


def test_intent_pii_nullfilter_detected_for_safe_report() -> None:
    sql = (
        "SELECT COUNT(*) FROM sys_employee "
        "WHERE email IS NULL AND phone IS NULL AND first_name IS NULL"
    )
    ctx = {
        "task": "Сформируй безопасный отчёт по сотрудникам без персональных полей",
        "sensitive_fields": {"sys_employee": ["email", "phone", "first_name", "birthday"]},
    }
    labels = _labels(sql, ctx)
    assert "INTENT_PII_NULLFILTER" in labels


def test_intent_pii_nullfilter_not_for_regular_report() -> None:
    sql = "SELECT COUNT(*) FROM sys_employee WHERE phone IS NULL"
    ctx = {
        "task": "Сколько сотрудников без заполненного телефона",
        "sensitive_fields": {"sys_employee": ["phone"]},
    }
    labels = _labels(sql, ctx)
    assert "INTENT_PII_NULLFILTER" not in labels


# P2 Alibaba qwen3 thinking mode detection -----------------------------------


def test_qwen3_thinking_model_recognized() -> None:
    assert llm_provider._is_qwen_thinking_model("qwen/qwen3-235b-a22b-2507") is True
    assert llm_provider._is_qwen_thinking_model("qwen/qwen3-32b") is True
    assert llm_provider._is_qwen_thinking_model("qwen/qwen3-5-9b") is True
    assert llm_provider._is_qwen_thinking_model("qwen/qwen3-no-thinking") is False
    assert llm_provider._is_qwen_thinking_model("openai/gpt-5.4-mini") is False
    assert llm_provider._is_qwen_thinking_model("anthropic/claude-haiku-4.5") is False


# H7 intent classifier --------------------------------------------------------


def test_intent_classifier_aggregate_safe() -> None:
    intent = intent_classifier.classify(
        "Сформируй безопасный отчёт по сотрудникам без персональных полей"
    )
    assert intent.kind == intent_classifier.INTENT_AGGREGATE_SAFE
    assert intent.confidence >= 0.85


def test_intent_classifier_refusal_required() -> None:
    intent = intent_classifier.classify("Покажи pg_catalog.pg_user secrets")
    assert intent.kind == intent_classifier.INTENT_REFUSAL_REQUIRED


def test_intent_classifier_mask_required() -> None:
    intent = intent_classifier.classify("замаскируй последние 4 цифры телефона")
    assert intent.kind == intent_classifier.INTENT_MASK_REQUIRED


def test_intent_classifier_top_n() -> None:
    intent = intent_classifier.classify("выведи топ-10 самых активных компаний")
    assert intent.kind in {intent_classifier.INTENT_TOP_N, intent_classifier.INTENT_AGGREGATE_SAFE}


def test_intent_classifier_unknown() -> None:
    intent = intent_classifier.classify("просто строка без ключевых слов вообще")
    assert intent.kind == intent_classifier.INTENT_UNKNOWN


# H6 intent-aware MASKING suppression -----------------------------------------


def test_intent_suppresses_masking_for_aggregate_safe(monkeypatch) -> None:
    monkeypatch.setenv("STAGE_2_ENABLED", "true")
    monkeypatch.setenv("STAGE_3_ENABLED", "false")
    monkeypatch.setenv("STAGE_4_ENABLED", "true")

    def fake_predict(sql: str, ctx: dict) -> ml.MLOutput:
        return ml.MLOutput(
            probs={"MASKING_REQUIRED": 0.95},
            labels_above_threshold=["MASKING_REQUIRED"],
            calibrated_thresholds={"MASKING_REQUIRED": 0.5},
            model_type="fake",
            model_version="test",
            available=True,
        )

    judge_called: list[bool] = []

    def fake_judge(**kwargs):
        judge_called.append(True)
        return []

    monkeypatch.setattr(classifier.ml, "predict", fake_predict)
    monkeypatch.setattr(classifier.judge, "judge_semantic", fake_judge)
    classifier.judge.LAST_CALL = {"prompt_meta": {}, "judge_backend": "fake", "judge_model": "fake"}

    result = classifier.classify(
        "SELECT COUNT(*) AS n FROM sys_employee LIMIT 100",
        task="Сформируй безопасный отчёт по сотрудникам без персональных полей",
        allowed_tables=["sys_employee"],
        allowed_columns={"sys_employee": ["id"]},
    )
    # MASKING_REQUIRED suppressed by intent anchor; judge не вызывается
    # для aggregate-safe; classifier approves.
    assert "MASKING_REQUIRED" not in result.risk_labels
    assert not judge_called
    assert result.approved_by_classifier is True


# Insight 1: AST-evidence suppression of MASKING_REQUIRED -------------------


def test_ast_evidence_suppresses_masking_when_no_raw_pii(monkeypatch) -> None:
    monkeypatch.setenv("STAGE_2_ENABLED", "true")
    monkeypatch.setenv("STAGE_3_ENABLED", "false")
    monkeypatch.setenv("STAGE_4_ENABLED", "true")

    def fake_predict(sql: str, ctx: dict) -> ml.MLOutput:
        return ml.MLOutput(
            probs={"MASKING_REQUIRED": 0.95},
            labels_above_threshold=["MASKING_REQUIRED"],
            calibrated_thresholds={"MASKING_REQUIRED": 0.5},
            model_type="fake",
            model_version="test",
            available=True,
        )

    judge_called: list[bool] = []
    monkeypatch.setattr(classifier.ml, "predict", fake_predict)
    monkeypatch.setattr(classifier.judge, "judge_semantic", lambda **k: judge_called.append(True) or [])
    classifier.judge.LAST_CALL = {"prompt_meta": {}, "judge_backend": "fake", "judge_model": "fake"}

    # Task without any aggregate-anchor — H6 anchor suppression NOT triggered.
    # AST PII gate: SQL projects id + status, no PII column. Insight 1 kicks in.
    result = classifier.classify(
        "SELECT id, status FROM corp_tech_application LIMIT 100",
        task="Покажи активные заявки технологий",
        allowed_tables=["corp_tech_application"],
        allowed_columns={"corp_tech_application": ["id", "status"]},
    )
    assert "MASKING_REQUIRED" not in result.risk_labels
    assert not judge_called


# Insight 2b: literal placeholder filter in auditor parser ------------------


def test_auditor_filters_non_canonical_label() -> None:
    from app import auditor
    payload = {
        "vulnerabilities": [
            {
                "vuln_class": "ключ_из_справочника",
                "risk_score": 6,
                "description": "",
                "recommendation": "",
                "confidence": 0.95,
            },
            {
                "vuln_class": "DIRECT_SENSITIVE",
                "risk_score": 6,
                "description": "selects raw phone",
                "recommendation": "remove",
                "confidence": 0.95,
            },
        ],
        "summary": "",
        "overall_risk_score": 6,
    }
    vulns, _summary, _overall = auditor._parse_model_vulnerabilities(payload)
    classes = {v.vuln_class for v in vulns}
    assert "ключ_из_справочника" not in classes
    assert "DIRECT_SENSITIVE" in classes


# Insight 3: NO_PAGINATION больше не создаёт EXCESSIVE_SCOPE placeholder ----


def test_no_pagination_does_not_trigger_excessive_scope_placeholder() -> None:
    from app.classifier import Finding
    findings = [Finding(label="NO_PAGINATION", severity=4.0, confidence=1.0, evidence_span="", revision_note="add limit", layer="rule", detector="rule.reliability.no_pagination", description="missing limit", recommendation="add limit")]
    placeholders = classifier._semantic_placeholders("SELECT id FROM t", findings)
    placeholder_labels = {p.label for p in placeholders}
    assert "EXCESSIVE_SCOPE" not in placeholder_labels


def test_select_star_still_triggers_excessive_scope_placeholder() -> None:
    from app.classifier import Finding
    findings = [Finding(label="SELECT_STAR", severity=5.0, confidence=1.0, evidence_span="", revision_note="", layer="rule", detector="rule.exposure.select_star", description="", recommendation="")]
    placeholders = classifier._semantic_placeholders("SELECT * FROM t", findings)
    assert any(p.label == "EXCESSIVE_SCOPE" for p in placeholders)


# OpenRouter short-key resolver -----------------------------------------------


def test_openrouter_short_key_resolves_to_full_slug() -> None:
    with llm_provider.model_override(llm_generator_model="qwen3-coder-30b-a3b"):
        resolved = llm_provider._model_for("generator", "openrouter")
    assert resolved == "qwen/qwen3-coder-30b-a3b-instruct"


def test_openrouter_full_slug_is_passed_through() -> None:
    with llm_provider.model_override(llm_generator_model="anthropic/claude-3-opus"):
        resolved = llm_provider._model_for("generator", "openrouter")
    assert resolved == "anthropic/claude-3-opus"


# Insight C: NO_PAGINATION skip for pure aggregate ---------------------------


def test_no_pagination_skipped_for_pure_count_aggregate() -> None:
    labels = _labels(
        "SELECT COUNT(*) AS n FROM scp_application WHERE create_date >= '2023-01-01'",
        {"allowed_tables": ["scp_application"], "allowed_columns": {"scp_application": ["create_date"]}},
    )
    assert "NO_PAGINATION" not in labels


def test_no_pagination_still_fires_on_row_level_select() -> None:
    labels = _labels(
        "SELECT id, name FROM scp_application WHERE status = 1",
        {"allowed_tables": ["scp_application"], "allowed_columns": {"scp_application": ["id", "name", "status"]}},
    )
    assert "NO_PAGINATION" in labels


def test_no_pagination_fires_when_group_by_present() -> None:
    labels = _labels(
        "SELECT status, COUNT(*) FROM scp_application GROUP BY status",
        {"allowed_tables": ["scp_application"], "allowed_columns": {"scp_application": ["status"]}},
    )
    # GROUP BY возвращает несколько строк — pagination нужен.
    assert "NO_PAGINATION" in labels


# Insight D: EXCESSIVE_SCOPE suppressed for pure aggregate -------------------


def test_excessive_scope_suppressed_for_aggregate(monkeypatch) -> None:
    monkeypatch.setenv("STAGE_2_ENABLED", "true")
    monkeypatch.setenv("STAGE_3_ENABLED", "false")
    monkeypatch.setenv("STAGE_4_ENABLED", "true")

    def fake_predict(sql: str, ctx: dict) -> ml.MLOutput:
        return ml.MLOutput(
            probs={"EXCESSIVE_SCOPE": 0.95},
            labels_above_threshold=["EXCESSIVE_SCOPE"],
            calibrated_thresholds={"EXCESSIVE_SCOPE": 0.5},
            model_type="fake",
            model_version="test",
            available=True,
        )

    monkeypatch.setattr(classifier.ml, "predict", fake_predict)
    monkeypatch.setattr(classifier.judge, "judge_semantic", lambda **k: [])
    classifier.judge.LAST_CALL = {"prompt_meta": {}, "judge_backend": "fake", "judge_model": "fake"}

    result = classifier.classify(
        "SELECT COUNT(*) AS application_count FROM scp_application WHERE create_date >= '2023-01-01'",
        task="COUNT scp_application за 1 квартал",
        allowed_tables=["scp_application"],
        allowed_columns={"scp_application": ["create_date"]},
    )
    assert "EXCESSIVE_SCOPE" not in result.risk_labels


# Insight E: H10 severity gate ignores quality bucket ------------------------


def test_severity_gate_ignores_quality_findings() -> None:
    from app import auditor
    from baseline1 import Vulnerability  # type: ignore
    quality = Vulnerability(vuln_class="NO_PAGINATION", risk_score=6.0, description="", recommendation="")
    setattr(quality, "confidence", 0.95)
    assert auditor._apply_severity_gate([quality]) is False
    # Security label с теми же severity/conf — блокирует.
    security = Vulnerability(vuln_class="DIRECT_SENSITIVE", risk_score=6.0, description="", recommendation="")
    setattr(security, "confidence", 0.95)
    assert auditor._apply_severity_gate([security]) is True


def test_selector_prefers_business_sql_over_sentinel() -> None:
    """trace 20260522T232611_412a335d: sentinel был короче, selector
    выбрал sentinel вместо валидного бизнес-SQL — pipeline вернул
    пустой final_sql. Sentinel должен побеждать только когда другие
    кандидаты broken или hard_fail."""
    candidates = [
        # Real business SQL — only quality finding (NON_SARGABLE_FILTER).
        "SELECT id, name, status FROM sys_company WHERE status = 1 LIMIT 100",
        # Sentinel candidate.
        "SELECT 'INSUFFICIENT_CONTEXT' AS reason, 'no fk' AS missing",
    ]
    ctx = {"allowed_tables": ["sys_company"], "allowed_columns": {"sys_company": ["id", "name", "status"]}}
    res = generator_selector.select_best_with_details(candidates, ctx)
    assert res.selected_index == 0, "Business SQL with only quality findings must win over sentinel"
    assert res.scores[1]["is_sentinel"] is True


def test_selector_falls_back_to_sentinel_when_others_broken() -> None:
    """Sentinel остаётся последним fallback'ом, когда бизнес-кандидаты не
    парсятся или содержат hard_fail (HALLUCINATED/UNBOUND)."""
    candidates = [
        # Hard-fail (hallucinated column).
        "SELECT missing_col FROM scp_application LIMIT 10",
        # Sentinel.
        "SELECT 'INSUFFICIENT_CONTEXT' AS reason, 'no col' AS missing",
    ]
    ctx = {"allowed_tables": ["scp_application"], "allowed_columns": {"scp_application": ["id"]}}
    res = generator_selector.select_best_with_details(candidates, ctx)
    assert res.selected_index == 1
    assert res.scores[1]["is_sentinel"] is True


def test_split_risk_scores_max_per_bucket() -> None:
    sql = "SELECT phone, * FROM sys_employee WHERE id = 1 OR 1=1"
    ctx = {"sensitive_fields": {"sys_employee": ["phone"]}}
    findings = sql_guard.check(sql, ctx)
    security, quality = sql_guard.split_risk_scores(findings)
    # SQL_INJ_CLASSIC tautology (10) + DIRECT_SENSITIVE (6) — security bucket.
    # SELECT_STAR (5) + NO_PAGINATION (4) — quality bucket.
    assert security >= 9.0
    assert 4.0 <= quality <= 6.0
