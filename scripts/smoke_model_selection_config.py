"""Smoke check for UI model selection config.

Prints the runtime presets exposed via app.llm_provider.list_model_options and
verifies invariants required by TZ §5 / §8: no duplicate keys, CLI presets
carry no llm_generator_model, OpenRouter Qwen3.5 and real local Ollama Qwen
are distinct options, and every entry carries the required UI metadata.

Run as:
    .venv/bin/python scripts/smoke_model_selection_config.py
"""

from __future__ import annotations

import json
import sys
from typing import Any

from app import llm_provider


REQUIRED_FIELDS = (
    "key",
    "label",
    "llm_mode",
    "llm_generator_model",
    "backend",
    "provider_model",
    "description",
    "available_by_config",
    "config_hint",
)


def main() -> int:
    options = llm_provider.list_model_options()
    by_key: dict[str, dict[str, Any]] = {}
    errors: list[str] = []

    print("Discovered " + str(len(options)) + " runtime presets:")
    for option in options:
        key = option.get("key") or "?"
        line = " - " + key.ljust(28) + " " + str(option.get("backend") or "?").ljust(14)
        line += " mode=" + str(option.get("llm_mode") or "?").ljust(13)
        line += " generator=" + str(option.get("llm_generator_model") or "<empty>")
        print(line)
        if key in by_key:
            errors.append("duplicate key: " + key)
        by_key[key] = option

    for option in options:
        for field in REQUIRED_FIELDS:
            if field not in option:
                errors.append("missing field " + field + " in " + str(option.get("key")))

    for option in options:
        backend = option.get("backend")
        llm_mode = option.get("llm_mode")
        if backend == "openrouter" and llm_mode != "prod_demo":
            errors.append("invalid pair: " + option["key"] + " backend openrouter + mode " + str(llm_mode))
        if backend == "local_openai" and llm_mode != "local_openai":
            errors.append("invalid pair: " + option["key"] + " backend local_openai + mode " + str(llm_mode))
        if backend in {"anthropic_cli", "codex_cli"} and option.get("llm_generator_model"):
            errors.append(
                "CLI preset " + option["key"] + " carries llm_generator_model="
                + repr(option.get("llm_generator_model"))
            )

    for key in ("or-qwen3-5-9b", "local-qwen3-5-9b", "claude-cli", "codex-cli"):
        if key not in by_key:
            errors.append("missing expected preset: " + key)

    or_qwen = by_key.get("or-qwen3-5-9b")
    local_qwen = by_key.get("local-qwen3-5-9b")
    if or_qwen and local_qwen and or_qwen.get("backend") == local_qwen.get("backend"):
        errors.append("or-qwen3-5-9b and local-qwen3-5-9b share the same backend")

    print()
    if errors:
        print("FAIL")
        for line in errors:
            print(" - " + line)
        print(json.dumps({"verdict": "FAIL", "errors": errors}, ensure_ascii=False))
        return 1

    print("OK")
    print(json.dumps(
        {
            "verdict": "PASS",
            "total_presets": len(options),
            "by_backend": _group_by(options, "backend"),
            "by_mode": _group_by(options, "llm_mode"),
        },
        ensure_ascii=False,
    ))
    return 0


def _group_by(options: list[dict[str, Any]], field: str) -> dict[str, int]:
    counter: dict[str, int] = {}
    for opt in options:
        key = str(opt.get(field) or "?")
        counter[key] = counter.get(key, 0) + 1
    return counter


if __name__ == "__main__":
    sys.exit(main())
