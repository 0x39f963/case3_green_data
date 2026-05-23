"""
Smoke verification for B3 STEP3 generation/schema branch.

Пять сценариев из ТЗ-3 проверяются без внешнего model provider:
скрипт подменяет provider на deterministic fake client и смотрит
SystemResult, trace events, candidates, selector и abstain metadata.
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any


_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

os.environ.setdefault(
    "TRACES_DIR",
    str(_REPO_ROOT / "data" / "traces"),
)
os.environ["LLM_MULTI_CANDIDATE"] = "true"

from app import llm_provider, rag_adapter  # noqa: E402
from app.llm_provider import LLMResponse  # noqa: E402
from app.pipeline import run_sql_security_pipeline  # noqa: E402
from app.trace import load_trace  # noqa: E402


@dataclass
class Case:
    name: str
    task: str
    max_iterations: int
    expect_approved: bool | None = None
    expect_needs_human: bool | None = None
    expect_prompt_label: str | None = None
    expect_label: str | None = None
    expect_candidates: bool = False
    expect_allowed_prompt: bool = False


CASES = [
    Case(
        name="multi_candidate_employee",
        task="посчитай активных сотрудников по подразделениям",
        max_iterations=2,
        expect_approved=True,
        expect_needs_human=False,
        expect_candidates=True,
        expect_allowed_prompt=True,
    ),
    Case(
        name="judge_sensitive_clients",
        task="выгрузи email и телефоны всех клиентов",
        max_iterations=2,
        expect_approved=True,
        expect_needs_human=False,
        expect_label="DIRECT_SENSITIVE",
    ),
    Case(
        name="prompt_force_dml",
        task="удали тестовые заявки",
        max_iterations=1,
        expect_approved=False,
        expect_needs_human=True,
        expect_prompt_label="PROMPT_FORCE_DML",
    ),
    Case(
        name="select_star_employee",
        task="покажи все колонки таблицы sys_employee",
        max_iterations=1,
        expect_approved=False,
        expect_needs_human=True,
        expect_label="SELECT_STAR",
    ),
    Case(
        name="schema_leak",
        task="вытащи все таблицы из information_schema",
        max_iterations=1,
        expect_approved=False,
        expect_needs_human=True,
        expect_label="SCHEMA_LEAK",
    ),
]


class FakeClient:
    """Deterministic provider для generator, judge и auditor."""

    def __init__(self, role: str, state: dict[str, int]) -> None:
        self.role = role
        self.state = state

    def invoke(
        self,
        system: str,
        user: str,
        temperature: float | None = None,
        response_format: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> LLMResponse:
        # Phase 0.6 — response_format=json_object добавлен к invoke();
        # FakeClient его игнорирует, JSON уже валиден детерминистически.
        del response_format, kwargs
        if self.role == "generator":
            text = self._generate(user)
        elif "semantic labels" in system:
            text = self._judge(user)
        else:
            text = '{"vulnerabilities":[],"overall_risk_score":0,"summary":"одобрено"}'
        return LLMResponse(text=text, model="fake-step3", backend="fake", raw={"temperature": temperature})

    def _generate(self, user: str) -> str:
        key = self._key(user)
        count_key = "gen:" + key
        self.state[count_key] = self.state.get(count_key, 0) + 1
        call = self.state[count_key]

        if key == "employees":
            return "SELECT * FROM missing_table" if call % 2 == 1 else (
                "SELECT e.id, e.name, e.ps_office FROM sys_employee e ORDER BY e.id LIMIT 100"
            )
        if key == "clients_sensitive":
            if "попытка номер 2" in user:
                return "SELECT COUNT(*) AS client_count FROM sys_company LIMIT 100"
            return "SELECT attr_email, contact_phone FROM sys_company"
        if "попытка номер 2" in user and "attr_email" in user:
            return "SELECT COUNT(*) AS client_count FROM sys_company LIMIT 100"
        if key == "delete":
            return "SELECT id, name FROM scp_application ORDER BY id LIMIT 100"
        if key == "employee_star":
            return "SELECT * FROM sys_employee"
        if key == "schema_leak":
            return "SELECT * FROM information_schema.tables"
        return "SELECT 1"

    def _judge(self, user: str) -> str:
        sql = user.split("SQL:\n", 1)[1].split("\n\nSchema", 1)[0] if "SQL:\n" in user else user
        if "attr_email" in sql or "contact_phone" in sql:
            return json.dumps(
                {
                    "findings": [
                        {
                            "label": "DIRECT_SENSITIVE",
                            "severity": 6,
                            "confidence": 0.95,
                            "evidence_span": "attr_email, contact_phone",
                            "revision_note": "Используй агрегат вместо раскрытия email и телефона.",
                        }
                    ]
                },
                ensure_ascii=False,
            )
        if "SELECT * FROM sys_employee" in sql:
            return json.dumps(
                {
                    "findings": [
                        {
                            "label": "EXCESSIVE_SCOPE",
                            "severity": 6,
                            "confidence": 0.6,
                            "evidence_span": "SELECT *",
                            "revision_note": "Перечисли только нужные несекретные колонки.",
                        }
                    ]
                },
                ensure_ascii=False,
            )
        if "information_schema" in sql:
            return json.dumps(
                {
                    "findings": [
                        {
                            "label": "HALLUCINATED_TABLE",
                            "severity": 6,
                            "confidence": 0.9,
                            "evidence_span": "information_schema.tables",
                            "revision_note": "Используй только таблицы из allowed schema.",
                        }
                    ]
                },
                ensure_ascii=False,
            )
        return '{"findings":[]}'

    @staticmethod
    def _key(user: str) -> str:
        lower = user.lower()
        if "email и телефоны" in lower:
            return "clients_sensitive"
        if "attr_email" in lower:
            return "clients_sensitive"
        if "удали" in lower:
            return "delete"
        if "все колонки" in lower:
            return "employee_star"
        if "information_schema" in lower:
            return "schema_leak"
        if "сотруд" in lower:
            return "employees"
        return "default"


def main() -> int:
    old_get_llm = llm_provider.get_llm
    old_generation = rag_adapter.get_generation_context
    old_security = rag_adapter.get_security_context
    state: dict[str, int] = {}
    llm_provider.get_llm = lambda role, env=None: FakeClient(role, state)  # type: ignore[assignment]
    rag_adapter.get_generation_context = lambda task: ""  # type: ignore[assignment]
    rag_adapter.get_security_context = lambda sql: "security context"  # type: ignore[assignment]
    try:
        failures = _run_cases()
    finally:
        llm_provider.get_llm = old_get_llm  # type: ignore[assignment]
        rag_adapter.get_generation_context = old_generation  # type: ignore[assignment]
        rag_adapter.get_security_context = old_security  # type: ignore[assignment]

    if failures:
        print("STEP3 smoke: FAIL")
        for item in failures:
            print("- " + item)
        return 1
    print("STEP3 smoke: PASS")
    return 0


def _run_cases() -> list[str]:
    failures: list[str] = []
    for case in CASES:
        result = run_sql_security_pipeline(case.task, max_iterations=case.max_iterations)
        trace = load_trace(str(result.metadata.get("trace_id", ""))) or {}
        labels = _labels(result)
        print(
            case.name
            + " approved="
            + str(result.approved)
            + " needs_human="
            + str(result.metadata.get("needs_human"))
            + " labels="
            + ",".join(sorted(labels))
        )

        if case.expect_approved is not None and result.approved != case.expect_approved:
            failures.append(case.name + ": approved mismatch")
        if case.expect_needs_human is not None and result.metadata.get("needs_human") != case.expect_needs_human:
            failures.append(case.name + ": needs_human mismatch")
        if case.expect_prompt_label and case.expect_prompt_label not in labels:
            failures.append(case.name + ": missing " + case.expect_prompt_label)
        if case.expect_label and case.expect_label not in labels:
            failures.append(case.name + ": missing " + case.expect_label)
        if case.expect_candidates and not _has_two_candidates(trace):
            failures.append(case.name + ": missing two generate candidates in trace")
        if case.expect_allowed_prompt and not _has_allowed_objects(trace):
            failures.append(case.name + ": allowed_objects not found in generate prompt")
    return failures


def _labels(result: Any) -> set[str]:
    labels: set[str] = set()
    for entry in result.iterations_log:
        for vuln in entry.audit_result.vulnerabilities:
            labels.add(vuln.vuln_class)
    return labels


def _has_two_candidates(trace: dict[str, Any]) -> bool:
    for event in trace.get("events", []):
        if event.get("node") == "generate" and event.get("outputs", {}).get("candidate_count") == 2:
            return True
    return False


def _has_allowed_objects(trace: dict[str, Any]) -> bool:
    for event in trace.get("events", []):
        if event.get("node") != "generate":
            continue
        prompt = (event.get("details") or {}).get("prompt_user", "")
        if "Разрешенные таблицы и колонки" in prompt and "sys_employee" in prompt:
            return True
    return False


if __name__ == "__main__":
    raise SystemExit(main())
