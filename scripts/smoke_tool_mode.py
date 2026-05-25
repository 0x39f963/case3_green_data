"""
Deterministic smoke for Phase 5 tool-mode through fake /run.

No live provider is used. The fake generator supports OpenAI-style
tool_calls, the fake auditor returns a stable JSON approval, and EXPLAIN
tool execution is marked skipped so the smoke does not depend on a local
PostgreSQL DSN.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ["GENERATOR_TOOL_MODE"] = "true"
os.environ["GENERATOR_TOOL_MAX_STEPS"] = os.environ.get("GENERATOR_TOOL_MAX_STEPS", "5")
os.environ["LLM_MULTI_CANDIDATE"] = "false"
os.environ["STAGE_3_ENABLED"] = os.environ.get("STAGE_3_ENABLED", "false")
os.environ["STAGE_4_ENABLED"] = "false"
os.environ["LATENCY_SOFT_SEC"] = os.environ.get("LATENCY_SOFT_SEC", "300")
os.environ["LATENCY_HARD_SEC"] = os.environ.get("LATENCY_HARD_SEC", "600")

from fastapi.testclient import TestClient  # noqa: E402

from app import llm_provider, tools  # noqa: E402
from app.api import app  # noqa: E402
from app.llm_provider import LLMResponse  # noqa: E402


class FakeGeneratorClient:
    supports_tools = True
    backend = "fake_tool"
    model = "fake-tool-generator"

    def __init__(self) -> None:
        self.step = 0

    def invoke(
        self,
        system: str,
        user: str,
        temperature: float | None = None,
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str | dict | None = None,
        messages_override: list[dict[str, Any]] | None = None,
        **_: Any,
    ) -> LLMResponse:
        del system, user, temperature, tool_choice, messages_override
        self.step += 1
        if self.step == 1:
            assert tools, "tool specs must be sent in tool-mode"
            sql = "SELECT id, name FROM sys_employee ORDER BY id LIMIT 10"
            return LLMResponse(
                text="",
                model=self.model,
                backend=self.backend,
                raw={"id": "fake-tool-step-1"},
                tool_calls=[
                    {"id": "call-hallucination", "name": "check_hallucination", "arguments": {"sql": sql}},
                    {"id": "call-sensitive", "name": "get_sensitive_fields", "arguments": {"tables": ["sys_employee"]}},
                    {"id": "call-explain", "name": "explain_query", "arguments": {"sql": sql}},
                ],
            )
        return LLMResponse(
            text="SELECT id, name FROM sys_employee ORDER BY id LIMIT 10",
            model=self.model,
            backend=self.backend,
            raw={"id": "fake-tool-final"},
            walltime_sec=0.01,
        )


class FakeAuditorClient:
    supports_tools = False
    backend = "fake"
    model = "fake-auditor"

    def invoke(self, system: str, user: str, response_format: dict[str, Any] | None = None, **_: Any) -> LLMResponse:
        del system, user, response_format
        return LLMResponse(
            text='{"vulnerabilities":[],"overall_risk_score":0,"summary":"approved"}',
            model=self.model,
            backend=self.backend,
            raw={"id": "fake-audit"},
            walltime_sec=0.01,
        )


def _patch_runtime() -> None:
    original_dispatch = tools.dispatch

    def fake_dispatch(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if name == "explain_query":
            return {"ok": True, "skipped": True, "plan": None, "error": None, "sub_timings": {}}
        return original_dispatch(name, arguments)

    tools.dispatch = fake_dispatch  # type: ignore[assignment]
    llm_provider.validate_current_config = lambda: {  # type: ignore[assignment]
        "contour": "fake",
        "generator_backend": "fake_tool",
        "auditor_backend": "fake",
    }
    llm_provider.get_llm = lambda role, env=None: (  # type: ignore[assignment]
        FakeGeneratorClient() if role == "generator" else FakeAuditorClient()
    )


def main() -> int:
    _patch_runtime()
    client = TestClient(app)
    response = client.post("/run", json={"task": "Покажи сотрудников", "max_iterations": 1})
    if response.status_code != 200:
        print("smoke_tool_mode FAIL: HTTP " + str(response.status_code))
        print(response.text)
        return 1

    data = response.json()
    sql = str(data.get("final_sql") or "")
    metadata = data.get("metadata") or {}
    trace_id = str(metadata.get("trace_id") or "")
    if "sys_employee" not in sql:
        print("smoke_tool_mode FAIL: unexpected final_sql=" + sql)
        return 1
    if not trace_id:
        print("smoke_tool_mode FAIL: missing trace_id")
        return 1

    trace_path = Path(os.environ.get("TRACES_DIR", "data/traces")) / (trace_id + ".json")
    trace = json.loads(trace_path.read_text(encoding="utf-8"))
    generate = next(item for item in trace["events"] if item.get("node") == "generate")
    details = generate.get("details") or {}
    compliance = details.get("tool_compliance") or {}
    if details.get("tool_mode") is not True or details.get("tool_mode_degraded") is True:
        print("smoke_tool_mode FAIL: tool_mode flags invalid")
        return 1
    if compliance.get("ok") is not True:
        print("smoke_tool_mode FAIL: compliance=" + json.dumps(compliance, ensure_ascii=False))
        return 1
    tool_llm_calls = details.get("tool_llm_calls") or []
    candidates = details.get("candidates") or []
    if len(tool_llm_calls) < 2:
        print("smoke_tool_mode FAIL: missing tool_llm_calls")
        return 1
    if len(candidates) != len(tool_llm_calls):
        print("smoke_tool_mode FAIL: legacy candidates must mirror tool_llm_calls")
        return 1
    if not all(item.get("legacy_tool_loop_candidate") for item in candidates):
        print("smoke_tool_mode FAIL: candidates are not marked as legacy tool-loop rows")
        return 1

    print("smoke_tool_mode PASS")
    print(json.dumps({"trace_id": trace_id, "final_sql": sql}, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
