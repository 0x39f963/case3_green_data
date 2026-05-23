"""
Рендер человекочитаемого отчета для аналитика.

На входе - история итераций цикла, финальный SQL, вердикт и сводка по
текущему режиму LLM. На выходе - текст на русском с пояснением, что
изменилось между итерациями и почему итог считается безопасным или
почему отказали. Никакой markdown-разметки: отчет должен одинаково
читаться в чате, в HTTP-ответе и в просмотрщике трасс.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

_TASK3_ROOT = Path(__file__).resolve().parent.parent / "TASK-3"
if str(_TASK3_ROOT) not in sys.path:
    sys.path.insert(0, str(_TASK3_ROOT))

from baseline1 import IterationLog, SecurityAuditor  # noqa: E402

RISK_THRESHOLD = SecurityAuditor.RISK_THRESHOLD


def render(
    task: str,
    iterations_log: list[IterationLog],
    approved: bool,
    final_sql: str,
    mode_info: dict[str, Any],
    include_sql: bool = True,
) -> str:
    """
    Собрать читаемый человеком отчет о прогоне.

    Структура: задача, вердикт, перечень итераций с пояснением что
    изменилось по сравнению с предыдущей попыткой, финальный SQL, в
    конце - явное обоснование почему итог безопасен или почему нет.
    """

    lines: list[str] = []
    lines.append("Задача: " + task.strip())
    lines.append("")

    verdict = "одобрен" if approved else "отклонен"
    lines.append("Вердикт: " + verdict + ". Использовано итераций: " + str(len(iterations_log)) + ".")
    lines.append("Режим модели: " + mode_info.get("mode", "?")
                 + ", генератор: " + mode_info.get("generator_model", "?")
                 + ", аудитор: " + mode_info.get("auditor_model", "?") + ".")
    lines.append("")

    if not iterations_log:
        lines.append("Цикл не отработал ни одной итерации. Подробности в трассе.")
        return "\n".join(lines)

    prior_sql = ""
    for entry in iterations_log:
        lines.append("Итерация " + str(entry.iteration) + ":")
        if include_sql:
            lines.append("  SQL: " + _one_line(entry.sql_query))
        else:
            lines.append("  SQL: скрыт, потому что запрос не одобрен.")

        if include_sql:
            diff = _diff_summary(prior_sql, entry.sql_query)
            if diff:
                lines.append("  Что изменилось: " + diff)

        audit = entry.audit_result
        lines.append(
            "  Аудит: " + ("одобрено" if audit.approved else "отклонено")
            + ", риск " + _fmt_risk(audit.overall_risk_score)
            + ", уязвимостей: " + str(len(audit.vulnerabilities))
        )
        for vuln in audit.vulnerabilities:
            lines.append(
                "    - " + vuln.vuln_class + " (риск " + _fmt_risk(vuln.risk_score) + "): "
                + vuln.description
            )
        if audit.summary:
            lines.append("  Сводка аудитора: " + audit.summary)
        if entry.revision_notes:
            lines.append("  Замечания к доработке: " + entry.revision_notes)
        lines.append("")
        prior_sql = entry.sql_query

    lines.append("Финальный SQL:")
    if include_sql:
        lines.append(final_sql.strip() or "(пусто)")
    else:
        lines.append("(скрыт: запрос не одобрен)")
    lines.append("")
    lines.append(_closing_reason(approved, iterations_log))

    return "\n".join(lines)


def _diff_summary(prior_sql: str, current_sql: str) -> str:
    """
    Объяснить, что поменялось в SQL по сравнению с предыдущей итерацией.
    Сравниваем нормализованные множества токенов, чтобы поймать
    появление/исчезновение LIMIT, WHERE, перечня колонок.
    """
    if not prior_sql:
        return ""
    if prior_sql.strip() == current_sql.strip():
        return "запрос идентичен предыдущей попытке."

    prior_tokens = set(prior_sql.upper().split())
    current_tokens = set(current_sql.upper().split())
    added = current_tokens - prior_tokens
    removed = prior_tokens - current_tokens
    notes: list[str] = []
    interesting = {"LIMIT", "WHERE", "*", "ORDER", "JOIN", "UNION", "GROUP", "PG_SLEEP"}
    added_hits = sorted(t for t in added if t in interesting)
    removed_hits = sorted(t for t in removed if t in interesting)
    if added_hits:
        notes.append("добавлено " + ", ".join(added_hits))
    if removed_hits:
        notes.append("убрано " + ", ".join(removed_hits))
    if not notes:
        notes.append("изменены имена колонок или условия фильтрации.")
    return "; ".join(notes)


def _closing_reason(approved: bool, iterations_log: list[IterationLog]) -> str:
    """
    Развернутая причина итога. Для одобрения - перечисляем какие
    проверки пройдены и почему риск ниже порога. Для отказа - какие
    уязвимости остались критичными.
    """
    last_audit = iterations_log[-1].audit_result
    if approved:
        if not last_audit.vulnerabilities:
            return ("Итог считается безопасным: ни правила, ни модель не нашли уязвимостей, "
                    "EXPLAIN-план построен без ошибок. Запрос можно передать на ручное ревью.")
        max_risk = max(v.risk_score for v in last_audit.vulnerabilities)
        return ("Итог считается безопасным: итоговый риск "
                + _fmt_risk(last_audit.overall_risk_score)
                + " ниже порога " + _fmt_risk(RISK_THRESHOLD)
                + ", максимум по найденным " + _fmt_risk(max_risk)
                + ". Запрос можно передать на ручное ревью.")

    top = sorted(last_audit.vulnerabilities, key=lambda v: v.risk_score, reverse=True)[:3]
    if not top:
        return "Запрос не прошел проверку. Подробности в трассе pipeline."
    head = "Запрос не прошел проверку. "
    details = "Основные риски на последней итерации: " + "; ".join(
        v.vuln_class + " (" + _fmt_risk(v.risk_score) + ")" for v in top
    ) + "."
    return head + details


def _one_line(text: str) -> str:
    """Свернуть SQL в одну строку для краткого предпросмотра в отчете."""
    one = " ".join(text.split())
    if len(one) > 160:
        return one[:160] + "..."
    return one


def _fmt_risk(value: float) -> str:
    """Аккуратное форматирование риска без лишних нулей."""
    return ("{:.1f}".format(float(value))).rstrip("0").rstrip(".")
