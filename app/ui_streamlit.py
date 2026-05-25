"""
Чат на Streamlit для аналитика.

Поле ввода задачи, развернутая история итераций, финальный SQL и
оценка риска. Ходит к FastAPI на APP_API_URL - это разделяет UI и
движок. Для полной JSON-трассы со всеми промптами в боковой панели
дается ссылка на просмотрщик по TRACE_VIEWER_URL.
"""

from __future__ import annotations

import os
from typing import Any

import requests
import streamlit as st


API_URL = os.environ.get("APP_API_URL", "http://app:8000")
TRACE_URL = os.environ.get("TRACE_VIEWER_URL", "http://localhost:8502")
REQUEST_TIMEOUT_SEC = 180
MODE_CHOICES = {
    "Локальный режим (Ollama)": "local_openai",
    "Облачный режим (OpenRouter)": "prod_demo",
    "Mixed (OpenRouter + Claude CLI auditor)": "mixed",
    "Claude CLI": "claude_cli",
    "Codex CLI": "codex_cli",
}


st.set_page_config(page_title="Case 3 SQL Security", layout="wide")
st.title("Case 3. Проверка SQL по текстовой задаче")
st.caption("Аналитик пишет задачу обычным языком, система отвечает безопасным SQL.")


# История диалога живет в session_state, чтобы переключение между
# вкладками не сбрасывало предыдущие ответы.
if "messages" not in st.session_state:
    st.session_state.messages = []
if "llm_mode_label" not in st.session_state:
    current_mode = os.environ.get("LLM_MODE", "prod_demo")
    st.session_state.llm_mode_label = next(
        (label for label, mode in MODE_CHOICES.items() if mode == current_mode),
        "Облачный режим (OpenRouter)",
    )


def _draw_iterations(iterations: list[dict[str, Any]]) -> None:
    """
    Отрисовать историю итераций цикла. Каждая итерация - отдельный
    expander с SQL, оценкой риска, списком уязвимостей и заметками.
    На вход - список IterationLog в виде словарей из ответа /run.
    """
    for entry in iterations:
        audit = entry.get("audit_result") or {}
        approved = audit.get("approved", False)
        risk = float(audit.get("overall_risk_score", 0.0))
        title = (
            "Итерация " + str(entry.get("iteration", "?"))
            + " - " + ("одобрено" if approved else "отклонено")
            + ", риск " + "{:.1f}".format(risk)
        )
        with st.expander(title, expanded=False):
            st.code(entry.get("sql_query", ""), language="sql")
            st.write("Сводка аудитора: " + str(audit.get("summary", "")))
            _draw_vulnerabilities(audit.get("vulnerabilities", []))
            if entry.get("revision_notes"):
                st.write("Замечания к доработке: " + entry["revision_notes"])


def _draw_response(payload: dict[str, Any]) -> None:
    """Отрисовать ответ pipeline в основном поле чата."""
    verdict = "одобрен" if payload["approved"] else "отклонен"
    st.subheader("Вердикт: " + verdict)
    metadata = payload.get("metadata", {})
    if metadata.get("needs_human"):
        st.warning("Нужна ручная проверка: " + str(metadata.get("human_reason", "abstain decision")))
    st.code(payload.get("final_sql") or "(пусто)", language="sql")

    cols = st.columns(3)
    cols[0].metric("Итераций", payload.get("iterations_used", 0))
    cols[1].metric("Длительность, сек", metadata.get("duration_sec", "?"))
    cols[2].metric("Модель", metadata.get("generator_model", "?"))

    _draw_iterations(payload.get("iterations_log", []))

    st.text_area(
        "Человекочитаемый отчет",
        value=payload.get("audit_log", ""),
        height=240,
    )

    trace_id = metadata.get("trace_id")
    if trace_id:
        st.markdown(
            "Полная трасса pipeline: ["
            + trace_id + "](" + TRACE_URL + "/trace/" + trace_id + ")"
        )


def _draw_vulnerabilities(items: list[dict[str, Any]]) -> None:
    """Показать labels grouped by layer."""
    if not items:
        st.write("Уязвимости не найдены.")
        return
    for layer, group in _group_by_layer(items).items():
        st.markdown("**Layer: " + layer + "**")
        for vuln in group:
            score = float(vuln.get("risk_score", vuln.get("severity", 0)) or 0)
            level = _severity_text(score)
            evidence = str(vuln.get("evidence_span", ""))[:80]
            line = (
                "- " + str(vuln.get("vuln_class", vuln.get("label", "?")))
                + " | severity " + "{:.1f}".format(score)
                + " | " + level
                + ": " + str(vuln.get("description", ""))
            )
            if evidence:
                line += " | evidence: " + evidence
            st.write(line)


def _group_by_layer(items: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for item in items:
        layer = str(item.get("layer") or _infer_layer(str(item.get("vuln_class", item.get("label", "")))))
        grouped.setdefault(layer, []).append(item)
    return grouped


def _infer_layer(label: str) -> str:
    if label.startswith("PROMPT_"):
        return "prompt-risk"
    if label in {"AUDIT_UNCERTAIN"}:
        return "internal"
    return "rule"


def _severity_text(score: float) -> str:
    if score >= 9:
        return "critical"
    if score >= 6:
        return "high"
    if score >= 4:
        return "medium"
    return "low"


# Сначала перерисовываем все, что уже было в сессии.
for message in st.session_state.messages:
    with st.chat_message(message["role"]):
        if message["role"] == "user":
            st.write(message["content"])
        else:
            _draw_response(message["payload"])


task = st.chat_input("Опиши задачу обычным языком")
if task:
    st.session_state.messages.append({"role": "user", "content": task})
    with st.chat_message("user"):
        st.write(task)

    with st.chat_message("assistant"):
        with st.status("Прогоняю цикл генерации и проверки...", expanded=False):
            try:
                response = requests.post(
                    API_URL + "/run",
                    json={
                        "task": task,
                        "llm_mode": MODE_CHOICES[st.session_state.llm_mode_label],
                    },
                    timeout=REQUEST_TIMEOUT_SEC,
                )
                response.raise_for_status()
                payload = response.json()
            except requests.RequestException as exc:
                st.error("Сервис app недоступен или вернул ошибку: " + str(exc))
                payload = None

        if payload is not None:
            _draw_response(payload)
            st.session_state.messages.append({"role": "assistant", "payload": payload})


with st.sidebar:
    st.header("Состояние")
    old_mode = st.session_state.llm_mode_label
    st.selectbox(
        "Demo режим",
        list(MODE_CHOICES),
        key="llm_mode_label",
    )
    if st.session_state.llm_mode_label != old_mode:
        st.session_state.messages = []
        st.rerun()

    try:
        health = requests.get(API_URL + "/health", timeout=5).json()
        st.success("API доступен")
        st.json(health)
    except requests.RequestException as exc:
        st.error("API недоступен: " + str(exc))

    st.markdown("Просмотрщик трасс: [" + TRACE_URL + "](" + TRACE_URL + ")")
