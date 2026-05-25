"""
Реализация SecurityAuditor: гибрид правил и языковой модели.

Сначала прогоняем быстрые правила sql_guard, при сломанном EXPLAIN
добавляем SYNTAX_BROKEN. Потом зовем модель-судью с RAG-контекстом и
списком чувствительных полей. Результаты обоих источников сливаются.
Если JSON от модели невалидный - добавляем AUDIT_UNCERTAIN с риском
выше порога одобрения, чтобы непрочитанный вердикт не превращался в
безопасный итог.

Internal labels вне baseline VULN_CLASSES:
- AUDIT_UNCERTAIN: модельный аудитор вернул невалидный JSON. Риск
  ставится выше порога, чтобы parse error не стал случайным approve.
- SYNTAX_BROKEN: deprecated marker для ошибок EXPLAIN/parse.
- BROKEN_SQL: новый B3 marker для parser/EXPLAIN ошибок.
Эти labels живут параллельно с 9 baseline и пишутся в metadata
аудита как internal_labels.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any

_TASK3_ROOT = Path(__file__).resolve().parent.parent / "TASK-3"
if str(_TASK3_ROOT) not in sys.path:
    sys.path.insert(0, str(_TASK3_ROOT))

from baseline1 import (  # noqa: E402
    AuditResult,
    SecurityAuditor as _BaseSecurityAuditor,
    Vulnerability,
)

from app import auditor_group_runner, classifier, llm_provider, prompt_registry, rag_adapter, runtime_context, sql_guard  # noqa: E402

_PROMPTS_DIR = Path(__file__).parent / "prompts"
INTERNAL_LABELS = frozenset({"AUDIT_UNCERTAIN", "SYNTAX_BROKEN", "BROKEN_SQL", "NEEDS_HUMAN_REVIEW"})


def _load_prompt(name: str) -> str:
    """Прочитать текст приглашения. Файлы маленькие, читаем каждый раз без кеша."""
    return (_PROMPTS_DIR / name).read_text(encoding="utf-8")


def _prompt_trace_fields(record: prompt_registry.PromptRecord, user_prompt: str) -> dict[str, Any]:
    return {
        "prompt_system": record.text,
        "prompt_user": user_prompt,
        "prompt_meta": record.meta,
        **record.meta,
        "prompt_request_sha256": prompt_registry.sha256_text(record.text + "\n\0\n" + user_prompt),
    }


def _format_guard_findings(findings: list[Vulnerability]) -> str:
    """Сжатое представление того, что нашли быстрые правила, для приглашения аудитора."""
    if not findings:
        return "Быстрые правила ничего не нашли."
    lines = []
    for vuln in findings:
        lines.append(
            "- [" + vuln.vuln_class + "] риск " + str(vuln.risk_score)
            + ": " + vuln.description
        )
    return "\n".join(lines)


def _merge(
    rule_findings: list[Vulnerability],
    model_findings: list[Vulnerability],
) -> list[Vulnerability]:
    """
    Слить две коллекции уязвимостей. По каждому классу оставляем самую
    тяжелую оценку. Если у модели нашлось что-то нового - добавляем.
    Это объединяет точность правил и широту языковой модели в один список.
    """
    by_class: dict[str, Vulnerability] = {v.vuln_class: v for v in rule_findings}
    for vuln in model_findings:
        existing = by_class.get(vuln.vuln_class)
        if existing is None or vuln.risk_score > existing.risk_score:
            by_class[vuln.vuln_class] = vuln
    return list(by_class.values())


def _build_overall_risk(vulns: list[Vulnerability]) -> float:
    """
    Итоговая оценка - максимум по найденным уязвимостям. Один тяжелый
    риск не маскируется кучей мелких. Если уязвимостей нет, итог 0.
    """
    if not vulns:
        return 0.0
    return float(max(v.risk_score for v in vulns))


def _apply_severity_gate(vulns: list[Vulnerability]) -> bool:
    """
    Return True when a finding must hard block approval.

    H10: блокируем только метки бакета **security**. Quality-метки
    (NO_PAGINATION, SELECT_STAR, NON_SARGABLE_FILTER, COST_DOS, ...)
    advisory: они должны быть видны пользователю, но не запирать
    pipeline в retry loop, иначе простой `COUNT(*) ...` за квартал
    проходит 5 итераций впустую (см. golden tc-0003).
    """
    for vuln in vulns:
        if sql_guard.label_bucket(vuln.vuln_class) != "security":
            continue
        confidence = float(getattr(vuln, "confidence", 1.0))
        if vuln.risk_score >= 9.0:
            return True
        if vuln.risk_score >= 6.0 and confidence >= 0.9:
            return True
    return False


def _internal_labels(vulns: list[Vulnerability]) -> list[str]:
    """Return sorted internal label names found in current audit result."""
    return sorted({v.vuln_class for v in vulns if v.vuln_class in INTERNAL_LABELS})


def _attach_meta(
    vuln: Vulnerability,
    *,
    confidence: float = 1.0,
    evidence_span: str = "",
    revision_note: str | None = None,
    detector: str = "model.audit",
) -> Vulnerability:
    """Attach B3 metadata to baseline Vulnerability without changing its dataclass."""
    setattr(vuln, "confidence", confidence)
    setattr(vuln, "evidence_span", evidence_span)
    setattr(vuln, "revision_note", revision_note or vuln.recommendation)
    setattr(vuln, "layer", "judge")
    setattr(vuln, "detector", detector)
    return vuln


def _classifier_vuln(finding: classifier.Finding) -> Vulnerability:
    """Convert classifier Finding back to baseline Vulnerability."""
    vuln = Vulnerability(
        vuln_class=finding.label,
        risk_score=float(finding.severity),
        description=finding.description,
        recommendation=finding.recommendation or finding.revision_note,
    )
    setattr(vuln, "confidence", float(finding.confidence))
    setattr(vuln, "evidence_span", finding.evidence_span)
    setattr(vuln, "revision_note", finding.revision_note)
    setattr(vuln, "layer", finding.layer)
    setattr(vuln, "detector", finding.detector)
    return vuln


_ALLOWED_AUDIT_LABELS: frozenset[str] | None = None


def _allowed_audit_labels() -> frozenset[str]:
    """Каноничные labels, которые pipeline понимает.

    Любое значение vuln_class вне этого множества (например literal placeholder
    из prompt-шаблона типа `ключ_из_справочника`) считается шумом и отбрасывается.
    """
    global _ALLOWED_AUDIT_LABELS
    if _ALLOWED_AUDIT_LABELS is None:
        _ALLOWED_AUDIT_LABELS = sql_guard.ALL_LABELS | INTERNAL_LABELS | frozenset(
            {"INTENT_PII_NULLFILTER", "SCHEMA_OVERLAY_MISSING"}
        )
    return _ALLOWED_AUDIT_LABELS


def _parse_model_vulnerabilities(payload: dict[str, Any]) -> tuple[list[Vulnerability], str, float]:
    """
    Достать список уязвимостей, summary и общую оценку из JSON-ответа модели.
    Битые элементы пропускаем по одному, не валим весь аудит.

    Поддерживаем строгий контракт: vuln_class должен быть из канонической
    таксономии. Слабые модели иногда копируют literal placeholder из
    prompt-шаблона (e.g. `ключ_из_справочника`) — такие записи отбрасываем,
    чтобы они не попадали в audit findings с фиктивным risk_score.
    """
    allowed = _allowed_audit_labels()
    items = payload.get("vulnerabilities") or []
    parsed: list[Vulnerability] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        raw_label = str(item.get("vuln_class", "UNKNOWN")).strip()
        if raw_label not in allowed:
            continue
        try:
            vuln = Vulnerability(
                vuln_class=raw_label,
                risk_score=float(item.get("risk_score", 0)),
                description=str(item.get("description", "")),
                recommendation=str(item.get("recommendation", "")),
            )
            parsed.append(
                _attach_meta(
                    vuln,
                    confidence=float(item.get("confidence", 1.0)),
                    evidence_span=str(item.get("evidence_span", "")),
                    revision_note=str(item.get("revision_note", item.get("recommendation", ""))),
                )
            )
        except (TypeError, ValueError):
            continue
    summary = str(payload.get("summary", "")).strip()
    overall = payload.get("overall_risk_score")
    try:
        overall_value = float(overall) if overall is not None else 0.0
    except (TypeError, ValueError):
        overall_value = 0.0
    return parsed, summary, overall_value


def _filter_model_findings(sql_query: str, findings: list[Vulnerability]) -> list[Vulnerability]:
    """
    Отсеять невозможные детерминированные находки модели.

    Модель может перепутать явный список колонок с SELECT * или не
    заметить LIMIT на новой строке. Для этих двух классов доверяем
    простым синтаксическим признакам, чтобы не ломать safe smoke.
    """
    upper = sql_query.upper()
    filtered: list[Vulnerability] = []
    for vuln in findings:
        if vuln.vuln_class == "SELECT_STAR":
            if not re.search(r"\bSELECT\s+\*", upper):
                continue
        if vuln.vuln_class == "NO_PAGINATION":
            has_limit = re.search(r"\bLIMIT\b", upper)
            has_fetch = re.search(r"FETCH\s+(FIRST|NEXT)\s+\d+\s+ROWS", upper)
            if has_limit or has_fetch:
                continue
        filtered.append(vuln)
    return filtered


def _audit_uncertain_vuln(reason: str, threshold: float) -> Vulnerability:
    """
    Сконструировать пометку о непрочитанном вердикте модели. Риск ставим
    выше порога одобрения - это превращает любой такой случай в отказ
    с понятной причиной, а не в тихое разрешение.
    """
    risk = max(threshold + 1.0, 5.0)
    vuln = Vulnerability(
        vuln_class="AUDIT_UNCERTAIN",
        risk_score=risk,
        description="Языковая модель вернула невалидный JSON-вердикт: " + reason,
        recommendation="Повторить запрос или временно переключить режим аудитора на сильную модель.",
    )
    return _attach_meta(
        vuln,
        confidence=1.0,
        evidence_span=reason,
        detector="internal.audit_uncertain",
    )


class SecurityAuditor(_BaseSecurityAuditor):
    """
    Гибридный аудитор: правила + языковая модель. Сохраняет сигнатуру
    audit() из контракта заказчика.

    После каждого вызова self.last_call содержит развернутую трассу:
    промпты, ответ модели, обе коллекции уязвимостей до слияния, причину
    итогового вердикта. Оркестратор пишет это в JSON для просмотрщика.
    """

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.last_call: dict[str, Any] = {}

    def audit(
        self,
        sql_query: str,
        db_schema: dict[str, Any] | None = None,
        explain_error: str | None = None,
        task: str = "",
        schema_context: str = "",
        allowed_tables: list[str] | None = None,
        allowed_columns: dict[str, list[str]] | None = None,
    ) -> AuditResult:
        """
        Проверить SQL и вернуть AuditResult. На вход - сам запрос плюс
        опционально текст ошибки EXPLAIN (его передает оркестратор после
        песочницы). На выход - список уязвимостей, итоговый риск и
        текстовая сводка для аналитика.
        """
        sensitive = rag_adapter.get_sensitive_fields()
        grouped_enabled = auditor_group_runner.grouped_auditor_enabled()
        classifier_output = classifier.classify(
            sql_query,
            task=task,
            schema_context=schema_context,
            sensitive_fields=sensitive,
            allowed_tables=allowed_tables or [],
            allowed_columns=allowed_columns or {},
            enable_judge=llm_provider.stage4_enabled() and not grouped_enabled,
        )
        rule_findings = [_classifier_vuln(item) for item in classifier_output.findings]

        if explain_error:
            rule_findings.append(
                Vulnerability(
                    vuln_class="SYNTAX_BROKEN",
                    risk_score=8.0,
                    description="EXPLAIN на тестовой базе вернул ошибку: " + explain_error,
                    recommendation="Исправить синтаксис или ссылки на несуществующие объекты.",
                )
            )
            rule_findings.append(
                sql_guard.make_vulnerability(
                    "BROKEN_SQL",
                    "EXPLAIN на тестовой базе вернул ошибку: " + explain_error,
                    "Исправить синтаксис или ссылки на несуществующие объекты.",
                    explain_error,
                    detector="explain_sandbox.error",
                )
            )

        if grouped_enabled:
            context = {
                "task": task,
                "schema_context": schema_context,
                "sensitive_fields": sensitive,
                "allowed_tables": allowed_tables or [],
                "allowed_columns": allowed_columns or {},
            }
            group_results = auditor_group_runner.run_grouped_audit_blocking(sql_query, context)
            group_findings = _filter_model_findings(
                sql_query,
                auditor_group_runner.flatten_findings(group_results),
            )
            merged = _merge(rule_findings, group_findings)
            overall = _build_overall_risk(merged)
            security_risk, quality_risk = sql_guard.split_risk_scores(merged)
            severity_blocked = _apply_severity_gate(merged)
            approved = (security_risk < self.RISK_THRESHOLD) and not severity_blocked
            internal_labels = _internal_labels(merged)
            if approved:
                summary = "Запрос одобрен grouped auditor framework. " + self._explain_approval(
                    merged,
                    "",
                    explain_error,
                )
            else:
                top = sorted(merged, key=lambda v: v.risk_score, reverse=True)[:3]
                details = "; ".join(v.vuln_class for v in top)
                summary = "Запрос отклонен grouped auditor framework. Основные риски: " + details + "."

            result = AuditResult(
                approved=approved,
                vulnerabilities=merged,
                overall_risk_score=overall,
                summary=summary,
            )
            setattr(result, "metadata", {
                "internal_labels": internal_labels,
                "security_risk_score": security_risk,
                "quality_risk_score": quality_risk,
                "audit_groups": [item.to_dict() for item in group_results],
            })

            self.last_call = {
                "grouped_auditor_enabled": True,
                "audit_groups": [item.to_dict() for item in group_results],
                "rule_findings": [v.__dict__ for v in rule_findings],
                "model_findings": [v.__dict__ for v in group_findings],
                "merged_findings": [v.__dict__ for v in merged],
                "parse_error": None,
                "explain_error": explain_error,
                "backend": "grouped_framework",
                "model": "deterministic_mvp",
                "approved": approved,
                "severity_gate_blocked": severity_blocked,
                "internal_labels": internal_labels,
                "overall_risk_score": overall,
                "security_risk_score": security_risk,
                "quality_risk_score": quality_risk,
                "summary": summary,
                "llm_call": {
                    "backend": "grouped_framework",
                    "model": "deterministic_mvp",
                    "latency_ms": sum(int(item.latency_ms) for item in group_results),
                },
            }
            return result

        # Phase 0.4 — sub-timing security RAG: cold start vs warm cache.
        security_context, security_context_timing = rag_adapter.get_security_context_timed(sql_query)
        security_hits, security_hits_timing = rag_adapter.get_security_hits_timed(sql_query)
        sensitive_text = rag_adapter.format_sensitive_fields(sensitive)

        system_record = prompt_registry.get_default_prompt("auditor_system")
        system_prompt = system_record.text
        user_prompt = _load_prompt("auditor_user.txt").format(
            task=task,
            sql=sql_query,
            runtime_context=runtime_context.build_runtime_context(),
            schema_context=schema_context[:6000],
            allowed_objects=rag_adapter.format_allowed_objects(allowed_columns or {}),
            security_context=security_context,
            sensitive_fields=sensitive_text,
            guard_findings=_format_guard_findings(rule_findings),
        )

        client = llm_provider.get_llm("auditor")
        # Phase 0.6 — response_format=json_object форсирует валидный JSON
        # у провайдеров, которые это поддерживают (OpenAI, OpenRouter,
        # OpenAI-compat сервера). Это снижает частоту AUDIT_UNCERTAIN.
        # CLI-обёртки игнорируют параметр.
        response = client.invoke(
            system_prompt,
            user_prompt,
            response_format={"type": "json_object"},
        )
        response_usage = response.usage_norm or llm_provider.extract_usage(response.raw)
        response_generation_id = (response.raw or {}).get("id")

        model_findings: list[Vulnerability] = []
        model_summary = ""
        model_overall = 0.0
        parse_error: str | None = None
        try:
            payload = llm_provider.parse_json_response(response.text)
            model_findings, model_summary, model_overall = _parse_model_vulnerabilities(payload)
            model_findings = _filter_model_findings(sql_query, model_findings)
            if not model_findings:
                model_summary = ""
                model_overall = 0.0
        except ValueError as exc:
            parse_error = str(exc)

        # Если модель вернула не-JSON, мы не имеем права считать ее ответ
        # за молчаливое одобрение. Подмешиваем AUDIT_UNCERTAIN с риском
        # выше порога - это гарантирует отказ и понятную причину.
        if parse_error:
            rule_findings.append(_audit_uncertain_vuln(parse_error, self.RISK_THRESHOLD))

        merged = _merge(rule_findings, model_findings)
        overall = max(_build_overall_risk(merged), 0.0 if parse_error else model_overall)
        security_risk, quality_risk = sql_guard.split_risk_scores(merged)

        severity_blocked = _apply_severity_gate(merged)
        approved = (security_risk < self.RISK_THRESHOLD) and not severity_blocked and not parse_error
        internal_labels = _internal_labels(merged)
        stage4_meta = (classifier_output.stage_outputs.get("stage_4_llm_judge") or {})

        if approved:
            why = self._explain_approval(merged, model_summary, explain_error)
            summary = (model_summary or "Запрос одобрен.") + " " + why
        else:
            top = sorted(merged, key=lambda v: v.risk_score, reverse=True)[:3]
            head = model_summary or "Запрос отклонен."
            details = "; ".join(v.vuln_class for v in top)
            summary = head + " Основные риски: " + details + "."

        result = AuditResult(
            approved=approved,
            vulnerabilities=merged,
            overall_risk_score=overall,
            summary=summary,
        )
        setattr(result, "metadata", {
            "internal_labels": internal_labels,
            "security_risk_score": security_risk,
            "quality_risk_score": quality_risk,
        })

        self.last_call = {
            **_prompt_trace_fields(system_record, user_prompt),
            "security_context": security_context,
            "rag_security_hits": security_hits,
            "sensitive_fields_text": sensitive_text,
            "rag_timings": {
                "security_context": security_context_timing,
                "security_hits": security_hits_timing,
            },
            "classifier_output": {
                "approved_by_classifier": classifier_output.approved_by_classifier,
                "max_severity": classifier_output.max_severity,
                "risk_labels": classifier_output.risk_labels,
                "needs_llm_judge": classifier_output.needs_llm_judge,
                "needs_regeneration": classifier_output.needs_regeneration,
                "stage_outputs": classifier_output.stage_outputs,
            },
            "judge_backend": stage4_meta.get("judge_backend"),
            "judge_model": stage4_meta.get("judge_model"),
            "judge_decision": stage4_meta.get("judge_decision"),
            "judge_latency_sec": stage4_meta.get("judge_latency_sec"),
            "stage4_prompt_meta": stage4_meta.get("prompt_meta"),
            "stage4_prompt_id": stage4_meta.get("prompt_id"),
            "stage4_prompt_version": stage4_meta.get("prompt_version"),
            "stage4_prompt_sha256": stage4_meta.get("prompt_sha256"),
            "stage4_prompt_fallback_reason": stage4_meta.get("prompt_fallback_reason"),
            "judge_prompt_id": stage4_meta.get("prompt_id"),
            "judge_prompt_version": stage4_meta.get("prompt_version"),
            "judge_prompt_sha256": stage4_meta.get("prompt_sha256"),
            "judge_prompt_fallback_reason": stage4_meta.get("prompt_fallback_reason"),
            "response_raw": response.text,
            "llm_call": {
                "prompt_meta": system_record.meta,
                **system_record.meta,
                "prompt": system_prompt,
                "prompt_user": user_prompt,
                "response": response.text,
                "backend": response.backend,
                "model": response.model,
                "usage": response_usage,
                "generation_id": response_generation_id,
                "provider": (response_usage or {}).get("provider"),
                "latency_ms": int(response.walltime_sec * 1000) if response.walltime_sec else None,
                "walltime_sec": response.walltime_sec,
                "retry_log": response.retry_log,
                "response_headers": response.response_headers,
            },
            "rule_findings": [v.__dict__ for v in rule_findings],
            "model_findings": [v.__dict__ for v in model_findings],
            "merged_findings": [v.__dict__ for v in merged],
            "parse_error": parse_error,
            "explain_error": explain_error,
            "backend": response.backend,
            "model": response.model,
            "approved": approved,
            "severity_gate_blocked": severity_blocked,
            "internal_labels": internal_labels,
            "overall_risk_score": overall,
            "security_risk_score": security_risk,
            "quality_risk_score": quality_risk,
            "summary": summary,
        }

        return result

    @staticmethod
    def _explain_approval(
        merged: list[Vulnerability],
        model_summary: str,
        explain_error: str | None,
    ) -> str:
        """Короткое объяснение, почему итог считается безопасным."""
        reasons: list[str] = []
        if not merged:
            reasons.append("Уязвимостей не найдено ни правилами, ни моделью.")
        else:
            top_risk = max(v.risk_score for v in merged)
            reasons.append(
                "Найденные риски не превышают порог " + str(_BaseSecurityAuditor.RISK_THRESHOLD)
                + " (максимум " + ("{:.1f}".format(top_risk)).rstrip("0").rstrip(".") + ")."
            )
        if explain_error is None:
            reasons.append("План запроса EXPLAIN построен без ошибок.")
        return " ".join(reasons)
