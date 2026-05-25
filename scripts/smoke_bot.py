"""
Локальная smoke-проверка тестового Telegram-бота без Telegram и HTTP.

Покрывает whitelist, callback сохранения модели, contextvars override
и полный flow: fake /run -> load_trace -> render HTML -> save report.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any


_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from app import llm_provider, telegram_bot  # noqa: E402


class FakeResponse:
    def __init__(self, status_code: int, data: dict[str, Any]) -> None:
        self.status_code = status_code
        self._data = data
        self.text = json.dumps(data, ensure_ascii=False)

    def json(self) -> dict[str, Any]:
        return self._data


def _write_trace(folder: Path, trace_id: str, result: dict[str, Any]) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    events = [
        {
            "node": "prompt_check",
            "started_at": "2026-05-18T00:00:00+00:00",
            "finished_at": "2026-05-18T00:00:00+00:00",
            "duration_sec": 0.001,
            "inputs": {"task_length": 24},
            "outputs": {"vuln_count": 0},
            "details": {"findings": []},
        },
        {
            "node": "retrieve",
            "started_at": "2026-05-18T00:00:01+00:00",
            "finished_at": "2026-05-18T00:00:01+00:00",
            "duration_sec": 0.002,
            "inputs": {"task": "Покажи сотрудников"},
            "outputs": {"context_length": 12, "allowed_tables": ["sys_employee"]},
            "details": {"generation_context": "sys_employee", "allowed_columns": {"sys_employee": ["id", "name"]}},
        },
        {
            "node": "generate",
            "started_at": "2026-05-18T00:00:02+00:00",
            "finished_at": "2026-05-18T00:00:02+00:00",
            "duration_sec": 0.003,
            "inputs": {"iteration": 1, "task": "Покажи сотрудников"},
            "outputs": {"sql": result["final_sql"], "candidate_count": 1, "selected_index": 0},
            "details": {
                "prompt_system": "system prompt",
                "prompt_user": "user prompt",
                "response_raw": ["SELECT id, name FROM sys_employee LIMIT 10"],
                "response_sql": [result["final_sql"]],
                "ast_tree": {"ok": True, "tree": []},
                "diff": "",
            },
        },
        {
            "node": "sql_guard",
            "started_at": "2026-05-18T00:00:03+00:00",
            "finished_at": "2026-05-18T00:00:03+00:00",
            "duration_sec": 0.001,
            "inputs": {"sql_length": len(result["final_sql"])},
            "outputs": {"vuln_count": 0},
            "details": {"findings": [], "ast_tree": {"ok": True, "tree": []}},
        },
        {
            "node": "explain_sandbox",
            "started_at": "2026-05-18T00:00:04+00:00",
            "finished_at": "2026-05-18T00:00:04+00:00",
            "duration_sec": 0.001,
            "inputs": {"sql_length": len(result["final_sql"])},
            "outputs": {"ok": True, "skipped": False},
            "details": {"plan": "Seq Scan on sys_employee", "error": None},
        },
        {
            "node": "audit",
            "started_at": "2026-05-18T00:00:05+00:00",
            "finished_at": "2026-05-18T00:00:05+00:00",
            "duration_sec": 0.003,
            "inputs": {"iteration": 1, "sql_length": len(result["final_sql"])},
            "outputs": {"approved": True, "overall_risk_score": 0.0, "vuln_count": 0},
            "details": {
                "prompt_system": "audit system",
                "prompt_user": "audit user",
                "response_raw": "{\"vulnerabilities\":[]}",
                "merged_findings": [],
                "summary": "approved",
            },
        },
        {
            "node": "decide",
            "started_at": "2026-05-18T00:00:06+00:00",
            "finished_at": "2026-05-18T00:00:06+00:00",
            "duration_sec": 0.001,
            "inputs": {"approved": True, "iteration": 1, "max_iterations": 5},
            "outputs": {"decision": "approve", "needs_human": False, "human_reason": ""},
            "details": {},
        },
        {
            "node": "revise",
            "started_at": "2026-05-18T00:00:07+00:00",
            "finished_at": "2026-05-18T00:00:07+00:00",
            "duration_sec": 0.001,
            "inputs": {"iteration": 1},
            "outputs": {"notes": "not used"},
            "details": {},
        },
    ]
    payload = {
        "request_id": trace_id,
        "task": "Покажи сотрудников",
        "started_at": "2026-05-18T00:00:00+00:00",
        "finished_at": "2026-05-18T00:00:08+00:00",
        "duration_sec": 8.0,
        "events": events,
        "result": result,
        "error": None,
    }
    (folder / (trace_id + ".json")).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


async def _check_contextvars() -> None:
    async def worker(mode: str, model: str) -> dict[str, str]:
        with llm_provider.model_override(mode, model):
            await asyncio.sleep(0)
            return llm_provider.describe_current_mode()

    a, b = await asyncio.gather(
        worker("prod_demo", "qwen3-5-9b"),
        worker("prod_demo", "claude-haiku-4-5"),
    )
    assert a["mode"] == "prod_demo"
    assert a["generator_model_key"] == "qwen3-5-9b"
    assert b["mode"] == "prod_demo"
    assert b["generator_model_key"] == "claude-haiku-4-5"


async def _check_full_flow(trace_id: str) -> Path:
    async def fake_post(api_url: str, payload: dict[str, Any], timeout_sec: int) -> FakeResponse:
        assert api_url == "http://fake-api"
        assert timeout_sec == 660
        assert payload["llm_mode"] == "prod_demo"
        assert payload["llm_generator_model"] == "qwen3-5-9b"
        result = {
            "final_sql": "SELECT id, name FROM sys_employee LIMIT 10",
            "approved": True,
            "iterations_used": 1,
            "iterations_log": [],
            "audit_log": "approved",
            "metadata": {
                "trace_id": trace_id,
                "decision": "approve",
                "needs_human": False,
                "mode": "prod_demo",
            },
        }
        return FakeResponse(200, result)

    run = await telegram_bot.build_test_run(
        "Покажи сотрудников",
        user_id=101,
        user_name="Smoke User",
        post_run=fake_post,
    )
    path, url = telegram_bot.save_and_deliver_report(run)
    assert url is None
    html = path.read_text(encoding="utf-8")
    assert (_REPO_ROOT / "app" / "templates" / "test_report.css").exists()
    assert (_REPO_ROOT / "app" / "templates" / "test_report.js").exists()
    assert "AI SQL Security Pipeline Report" in html
    assert "Original User Request" in html
    assert "Pipeline Timeline" in html
    assert "Events Stream" in html
    assert "RAG Retrieval" in html
    assert "report-drawer-data" in html
    assert 'class="page-shell"' in html
    assert 'class="side-toolbar"' in html
    assert 'class="report-grid' in html
    assert 'id="stepDrawer"' in html
    assert 'id="detailDrawer"' in html
    assert "prompt_system" in html
    assert "response_raw" in html
    assert "EXPLAIN" in html
    assert "AST" in html
    assert "vanilla-jsoneditor@3.12.0/standalone.js" in html
    assert "readOnly: true" in html
    assert "json-mode" in html
    assert '"tree"' in html
    assert '"text"' in html
    assert 'href="test_report.css"' not in html
    assert 'src="test_report.js"' not in html
    assert "jsoneditor@10.1.3" not in html
    assert "Вердикт: approved" in telegram_bot.caption_for_run(run)
    return path


def main() -> int:
    old_env = os.environ.copy()
    try:
        with tempfile.TemporaryDirectory() as tmp_raw:
            tmp = Path(tmp_raw)
            os.environ["BOT_USER_PREFS_PATH"] = str(tmp / "user_prefs.json")
            os.environ["BOT_REPORT_DIR"] = str(tmp / "reports")
            os.environ["BOT_MODELS_CONFIG"] = str(_REPO_ROOT / "deploy" / "bot_models.json")
            os.environ["BOT_API_URL"] = "http://fake-api"
            os.environ["TRACES_DIR"] = str(tmp / "traces")
            os.environ["BOT_REPORT_DELIVERY_MODE"] = "local"
            os.environ.pop("LLM_BACKEND_GENERATOR", None)
            os.environ.pop("LLM_MODEL_GENERATOR", None)

            allowed = telegram_bot.parse_allowed_users("101,202")
            assert telegram_bot.is_user_allowed(101, allowed)
            assert not telegram_bot.is_user_allowed(303, allowed)

            model = telegram_bot.set_user_model(101, "or-qwen3-5-9b")
            assert model["llm_generator_model"] == "qwen3-5-9b"
            assert "qwen/qwen3.5-9b" in telegram_bot.current_text(101)
            assert (tmp / "user_prefs.json").exists()

            asyncio.run(_check_contextvars())

            trace_id = "smoke_bot_trace"
            result = {
                "final_sql": "SELECT id, name FROM sys_employee LIMIT 10",
                "approved": True,
                "iterations_used": 1,
                "iterations_log": [],
                "audit_log": "approved",
                "metadata": {"trace_id": trace_id, "decision": "approve", "needs_human": False},
            }
            _write_trace(tmp / "traces", trace_id, result)
            report_path = asyncio.run(_check_full_flow(trace_id))
            assert report_path.exists()
    except Exception as exc:
        print("smoke_bot FAIL: " + exc.__class__.__name__ + ": " + str(exc))
        return 1
    finally:
        os.environ.clear()
        os.environ.update(old_env)

    print("smoke_bot PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
