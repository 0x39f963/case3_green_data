from __future__ import annotations

import json
import os
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any


name = "codex_cli"


def invoke(
    system: str,
    user: str,
    *,
    timeout_sec: int,
    max_tokens: int,
    model: str,
    retry_attempts: int = 1,
    retry_backoff_sec: float = 0.0,
) -> dict[str, Any]:
    del max_tokens
    attempts = max(int(retry_attempts), 1)
    last_error = ""
    for attempt in range(attempts):
        try:
            return _invoke_once(system, user, timeout_sec=timeout_sec, model=model)
        except FileNotFoundError:
            # PATH miss — постоянная ошибка, retry бессмыслен.
            raise
        except RuntimeError as exc:
            last_error = str(exc)
            if attempt >= attempts - 1:
                raise
            time.sleep(max(retry_backoff_sec, 0.0) * (2 ** attempt))
    raise RuntimeError(
        "codex CLI failed after " + str(attempts) + " attempts: " + last_error
    )


def _invoke_once(
    system: str,
    user: str,
    *,
    timeout_sec: int,
    model: str,
) -> dict[str, Any]:
    binary = os.environ.get("CODEX_CLI_PATH", "codex")
    prompt = system + "\n\n" + user
    # Long prompts (~30k+ chars) go through a temp-file fed to stdin as a
    # regular file descriptor, not a pipe. PIPE has a buffer cap (~64KB on
    # Linux, smaller on WSL) and can deadlock when the child has not started
    # reading yet; a regular file has no such limit.
    with tempfile.TemporaryDirectory() as tmp:
        prompt_path = Path(tmp) / "prompt.txt"
        prompt_path.write_bytes(prompt.encode("utf-8"))
        cmd = [
            binary,
            "exec",
            "--json",
            "--dangerously-bypass-approvals-and-sandbox",
            "--skip-git-repo-check",
        ]
        if model:
            cmd.extend(["--model", model])
        cmd.append("-")
        started = time.perf_counter()
        stdin_fd = os.open(str(prompt_path), os.O_RDONLY)
        try:
            result = subprocess.run(
                cmd,
                stdin=stdin_fd,
                capture_output=True,
                text=True,
                timeout=timeout_sec,
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError("codex CLI timeout after " + str(timeout_sec) + " seconds") from exc
        finally:
            try:
                os.close(stdin_fd)
            except OSError:
                pass
        latency = round(time.perf_counter() - started, 3)
        if result.returncode != 0:
            raise RuntimeError("codex CLI exited with code " + str(result.returncode) + ": " + result.stderr[-500:])
        text = _extract_agent_message(result.stdout)
    return {
        "text": text,
        "raw": {"stdout": result.stdout, "stderr": result.stderr},
        "usage": None,
        "latency_sec": latency,
    }


def _extract_agent_message(stdout: str) -> str:
    last_text = ""
    for line in stdout.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        item = event.get("item") if isinstance(event, dict) else None
        if isinstance(item, dict) and item.get("type") == "agent_message":
            text = item.get("text")
            if isinstance(text, str):
                last_text = text
    return last_text.strip() or stdout.strip()
