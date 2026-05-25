"""Smoke CLI candidate parallelism for Codex/Claude backends.

Run examples:
    PYTHONPATH=. python scripts/smoke_cli_parallel_candidates.py --modes codex_cli
    PYTHONPATH=. LLM_CLI_MAX_PARALLEL=2 python scripts/smoke_cli_parallel_candidates.py --modes codex_cli,anthropic_cli
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from typing import Any

from app import generator as generator_module
from app import llm_provider


def _run_mode(mode: str) -> dict[str, Any]:
    os.environ.setdefault("LLM_MULTI_CANDIDATE", "true")
    os.environ.setdefault("LLM_PARALLEL_CANDIDATES", "true")
    os.environ.setdefault("LLM_CLI_PARALLEL_CANDIDATES", "true")
    os.environ.setdefault("LLM_CLI_MAX_PARALLEL", "2")

    temperatures = [0.1, 0.1]
    llm_mode = "claude_cli" if mode == "anthropic_cli" else mode
    with llm_provider.model_override(llm_mode=llm_mode, llm_generator_model=None):
        client = llm_provider.get_llm("generator")
        requested = True
        supported = generator_module._candidate_parallel_supported(client, requested, temperatures)
        started = time.perf_counter()
        responses = generator_module._generate_candidates(
            client,
            "Answer exactly OK.",
            "OK",
            temperatures,
            parallel=requested,
        )
        elapsed = time.perf_counter() - started

    walltimes = [item.walltime_sec for item in responses if item.walltime_sec is not None]
    scheduling = "parallel" if supported else "sequential"
    return {
        "mode": mode,
        "llm_mode": llm_mode,
        "backend": responses[0].backend if responses else getattr(client, "backend", ""),
        "model": responses[0].model if responses else getattr(client, "model", ""),
        "candidate_count": len(responses),
        "candidate_parallel_requested": requested,
        "candidate_parallel_supported": supported,
        "candidate_effective_scheduling": scheduling,
        "candidate_max_parallel": generator_module._candidate_max_parallel(client, supported, temperatures),
        "elapsed_sec": round(elapsed, 3),
        "sum_candidate_walltime_sec": round(sum(walltimes), 3),
        "max_candidate_walltime_sec": round(max(walltimes), 3) if walltimes else 0.0,
        "texts": [item.text.strip()[:120] for item in responses],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--modes",
        default=os.environ.get("LLM_MODE", "codex_cli"),
        help="Comma-separated LLM modes/backends to smoke, e.g. codex_cli,anthropic_cli.",
    )
    args = parser.parse_args()

    modes = [item.strip() for item in args.modes.split(",") if item.strip()]
    results = []
    issues = []
    for mode in modes:
        try:
            result = _run_mode(mode)
        except Exception as exc:
            result = {"mode": mode, "ok": False, "error": str(exc)}
            issues.append(mode + ": " + str(exc))
        else:
            result["ok"] = (
                result["candidate_count"] == 2
                and result["candidate_parallel_supported"] is True
                and result["candidate_effective_scheduling"] == "parallel"
            )
            if not result["ok"]:
                issues.append(mode + ": parallel scheduling was not effective")
        results.append(result)
        print(json.dumps(result, ensure_ascii=False))

    if issues:
        print(json.dumps({"verdict": "FAIL", "issues": issues, "results": results}, ensure_ascii=False))
        return 1
    print(json.dumps({"verdict": "PASS", "results": results}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
