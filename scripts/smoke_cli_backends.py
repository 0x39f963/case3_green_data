"""Smoke check for Claude Code / Codex CLI backends inside the container.

Verifies that:
- claude and codex binaries are on PATH (Dockerfile installs them via npm).
- list_model_options reports available_by_config=True for both CLI presets.
- Codex can complete a trivial Orchestra-style `codex exec --json` call.
- Claude is not invoked here because quota can be exhausted; version/config
  checks are enough to verify installation and command shape without spending
  a run.

Run as:
    PYTHONPATH=. .venv/bin/python scripts/smoke_cli_backends.py
or inside the container:
    docker exec case3_app python scripts/smoke_cli_backends.py
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from typing import Any

from app import llm_provider
from app.llm_provider import ProviderUnavailable


CHECKS = (
    ("claude", "ANTHROPIC_CLI_PATH"),
    ("codex", "CODEX_CLI_PATH"),
)


def _bin_for(name: str, env_var: str) -> str:
    return os.environ.get(env_var, "").strip() or name


def _version(binary: str) -> dict[str, Any]:
    try:
        proc = subprocess.run(
            [binary, "--version"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except FileNotFoundError:
        return {"ok": False, "reason": "binary_not_found"}
    except subprocess.TimeoutExpired:
        return {"ok": False, "reason": "version_timeout"}
    text = (proc.stdout + " " + proc.stderr).strip()
    return {
        "ok": proc.returncode == 0,
        "returncode": proc.returncode,
        "version_line": text.splitlines()[0] if text else "",
    }


def _codex_runtime_check() -> dict[str, Any]:
    with llm_provider.model_override(llm_mode="codex_cli", llm_generator_model=None):
        client = llm_provider.get_llm("generator")
        try:
            result = client.invoke("Answer exactly OK.", "OK")
        except ProviderUnavailable as exc:
            return {"ok": False, "reason": str(exc)}
    return {
        "ok": result.text.strip().upper() == "OK",
        "text": result.text.strip()[:120],
        "model": result.model,
        "backend": result.backend,
    }


def main() -> int:
    issues: list[str] = []
    summary: dict[str, Any] = {"binaries": {}, "model_options": {}, "runtime": {}}

    for name, env_var in CHECKS:
        binary = _bin_for(name, env_var)
        path = shutil.which(binary)
        version_info = _version(binary) if path else {"ok": False, "reason": "not_in_path"}
        summary["binaries"][name] = {
            "binary": binary,
            "in_path": path,
            **version_info,
        }
        label = "OK" if version_info.get("ok") else "FAIL"
        print(f" - {label:4} {name:8} binary={binary} path={path or '-'} {version_info.get('version_line') or version_info.get('reason') or ''}")
        if not path:
            issues.append(f"{name}: not in PATH (set {env_var} or install via deploy/Dockerfile)")
        elif not version_info.get("ok"):
            issues.append(f"{name}: --version returned {version_info.get('returncode')}, reason={version_info.get('reason')}")

    print()
    options = llm_provider.list_model_options()
    cli_options = [o for o in options if o.get("backend") in {"anthropic_cli", "codex_cli"}]
    for opt in cli_options:
        key = opt["key"]
        summary["model_options"][key] = {
            "backend": opt.get("backend"),
            "available_by_config": opt.get("available_by_config"),
            "config_hint": opt.get("config_hint"),
        }
        label = "OK" if opt.get("available_by_config") else "WARN"
        hint = opt.get("config_hint") or "no hint"
        print(f" - {label:4} preset {key:10} available_by_config={opt.get('available_by_config')} hint={hint}")
        if not opt.get("available_by_config"):
            issues.append(f"{key}: list_model_options reports unavailable ({hint})")

    print()
    codex_runtime = _codex_runtime_check()
    summary["runtime"]["codex-cli"] = codex_runtime
    label = "OK" if codex_runtime.get("ok") else "FAIL"
    print(
        " - "
        + f"{label:4} preset codex-cli runtime "
        + (codex_runtime.get("text") or codex_runtime.get("reason") or "")
    )
    if not codex_runtime.get("ok"):
        issues.append("codex-cli: runtime invocation failed (" + str(codex_runtime.get("reason")) + ")")
    print(" - SKIP preset claude-cli runtime not invoked to avoid consuming exhausted quota")

    print()
    if issues:
        print("FAIL")
        for line in issues:
            print(" - " + line)
        print(json.dumps({"verdict": "FAIL", "issues": issues, **summary}, ensure_ascii=False))
        return 1
    print("OK")
    print(json.dumps({"verdict": "PASS", **summary}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
