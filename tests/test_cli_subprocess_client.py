"""Tests for CLISubprocessClient env allowlist + auth detection (Phase 7)."""

from __future__ import annotations

import asyncio
import subprocess
import time
from pathlib import Path

import pytest

from app import llm_provider
from app.llm_provider import CLISubprocessClient, LLMConfigError, ProviderUnavailable


def test_filter_subprocess_env_allows_basic_and_prefixed(monkeypatch):
    monkeypatch.setenv("PATH", "/usr/local/bin:/usr/bin")
    monkeypatch.setenv("HOME", "/root")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-anthropic-key")
    monkeypatch.setenv("OPENAI_API_KEY", "fake-openai-key")
    monkeypatch.setenv("CODEX_HOME", "/root/.codex")
    monkeypatch.setenv("RANDOM_SECRET", "must-not-leak")
    env = llm_provider._filter_subprocess_env()
    assert env["PATH"] == "/usr/local/bin:/usr/bin"
    assert env["HOME"] == "/root"
    assert env["ANTHROPIC_API_KEY"] == "fake-anthropic-key"
    assert env["OPENAI_API_KEY"] == "fake-openai-key"
    assert env["CODEX_HOME"] == "/root/.codex"
    assert "RANDOM_SECRET" not in env


def test_looks_like_auth_failure_detects_keywords():
    assert llm_provider._looks_like_auth_failure("", "please login", 1) is True
    assert llm_provider._looks_like_auth_failure("Token expired, run claude login", "", 1) is True
    assert llm_provider._looks_like_auth_failure("", "ok done", 0) is False
    assert llm_provider._looks_like_auth_failure("", "warning: cache miss", 0) is False


def test_looks_like_auth_failure_detects_status_codes():
    assert llm_provider._looks_like_auth_failure("", "", 401) is True
    assert llm_provider._looks_like_auth_failure("", "", 403) is True


def test_looks_like_quota_failure_detects_cli_limit_text():
    assert llm_provider._looks_like_quota_failure("You've hit your limit", "") is True
    assert llm_provider._looks_like_quota_failure("", "ok done") is False


def test_claude_command_uses_orchestra_flags():
    client = CLISubprocessClient(binary="claude", model="claude-sonnet-4-6", backend="anthropic_cli")
    cmd = client.build_command("system text", "user task", "merged", None)
    assert cmd[0] == "claude"
    assert "-p" in cmd
    assert cmd[cmd.index("-p") + 1] == "user task"
    assert "--output-format" in cmd
    assert cmd[cmd.index("--output-format") + 1] == "json"
    assert "--dangerously-skip-permissions" in cmd
    assert "--append-system-prompt" in cmd
    assert cmd[cmd.index("--append-system-prompt") + 1] == "system text"
    assert "--model" in cmd
    assert cmd[cmd.index("--model") + 1] == "claude-sonnet-4-6"


def test_codex_command_includes_bypass_flag(monkeypatch):
    monkeypatch.delenv("CODEX_GENERATOR_REASONING_EFFORT", raising=False)
    client = CLISubprocessClient(binary="codex", model="gpt-5.5", backend="codex_cli")
    cmd = client.build_command("system", "user", "[Инструкция]\nsystem\n\n[Запрос]\nuser", "/tmp/out.txt")
    assert cmd[:2] == ["codex", "exec"]
    assert "--dangerously-bypass-approvals-and-sandbox" in cmd
    assert "--skip-git-repo-check" in cmd
    assert "--json" in cmd
    assert "--output-last-message" not in cmd
    assert cmd[cmd.index("--model") + 1] == "gpt-5.5"
    assert "model_reasoning_effort=\"medium\"" not in cmd
    assert cmd[-1] == "-"


def test_codex_generator_command_sets_medium_reasoning(monkeypatch):
    monkeypatch.delenv("CODEX_GENERATOR_REASONING_EFFORT", raising=False)
    client = CLISubprocessClient(binary="codex", model="gpt-5.5", backend="codex_cli", role="generator")

    cmd = client.build_command("system", "user", "prompt", None)

    assert "-c" in cmd
    assert "model_reasoning_effort=\"medium\"" in cmd
    assert cmd.index("model_reasoning_effort=\"medium\"") < len(cmd) - 1
    assert cmd[-1] == "-"


def test_codex_auditor_command_inherits_reasoning_config(monkeypatch):
    monkeypatch.delenv("CODEX_GENERATOR_REASONING_EFFORT", raising=False)
    client = CLISubprocessClient(binary="codex", model="gpt-5.5", backend="codex_cli", role="auditor")

    cmd = client.build_command("system", "user", "prompt", None)

    assert "model_reasoning_effort=\"medium\"" not in cmd
    assert "-c" not in cmd


def test_invoke_raises_llm_config_error_when_binary_missing(monkeypatch):
    def fake_run(*args, **kwargs):
        raise FileNotFoundError("no such file")

    monkeypatch.setattr(subprocess, "run", fake_run)
    client = CLISubprocessClient(binary="claude", model="x", backend="anthropic_cli")
    with pytest.raises(LLMConfigError) as exc_info:
        client.invoke("sys", "user")
    assert "не найден" in str(exc_info.value)


def test_invoke_raises_provider_unavailable_on_auth_failure(monkeypatch):
    def fake_run(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="error: please login first")

    monkeypatch.setattr(subprocess, "run", fake_run)
    client = CLISubprocessClient(binary="claude", model="x", backend="anthropic_cli")
    with pytest.raises(ProviderUnavailable) as exc_info:
        client.invoke("sys", "user")
    assert "auth failure" in str(exc_info.value)
    assert "Залогинься" in str(exc_info.value)


def test_invoke_raises_provider_unavailable_on_generic_failure(monkeypatch):
    def fake_run(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 2, stdout="", stderr="something else broke")

    monkeypatch.setattr(subprocess, "run", fake_run)
    client = CLISubprocessClient(binary="claude", model="x", backend="anthropic_cli")
    with pytest.raises(ProviderUnavailable) as exc_info:
        client.invoke("sys", "user")
    assert "auth failure" not in str(exc_info.value)
    assert "вернул код 2" in str(exc_info.value)


def test_invoke_passes_filtered_env(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-test-key")
    monkeypatch.setenv("RANDOM_THING_THAT_SHOULD_NOT_LEAK", "should-be-dropped")
    captured = {}

    def fake_run(cmd, **kwargs):
        captured["env"] = dict(kwargs.get("env") or {})
        assert Path(captured["env"]["TMPDIR"]).is_dir()
        return subprocess.CompletedProcess(cmd, 0, stdout='{"result":"ok"}', stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    client = CLISubprocessClient(binary="claude", model="m", backend="anthropic_cli")
    client.invoke("sys", "user")
    assert "ANTHROPIC_API_KEY" in captured["env"]
    assert "RANDOM_THING_THAT_SHOULD_NOT_LEAK" not in captured["env"]
    assert Path(captured["env"]["TMPDIR"]).name.startswith("cli_call_")
    assert not Path(captured["env"]["TMPDIR"]).exists()


def test_cli_parallel_support_respects_env(monkeypatch):
    client = CLISubprocessClient(binary="codex", model="gpt-5.5", backend="codex_cli")

    monkeypatch.setenv("LLM_CLI_PARALLEL_CANDIDATES", "true")
    monkeypatch.setenv("LLM_CLI_MAX_PARALLEL", "2")
    monkeypatch.setenv("LLM_CLI_PARALLEL_BACKENDS", "codex_cli,anthropic_cli")
    assert client.candidate_parallel_supported() is True
    assert client.candidate_max_parallel() == 2

    monkeypatch.setenv("LLM_CLI_MAX_PARALLEL", "1")
    assert client.candidate_parallel_supported() is False
    assert client.candidate_max_parallel() == 1

    monkeypatch.setenv("LLM_CLI_MAX_PARALLEL", "2")
    monkeypatch.setenv("LLM_CLI_PARALLEL_BACKENDS", "anthropic_cli")
    assert client.candidate_parallel_supported() is False
    assert client.candidate_max_parallel() == 1

    monkeypatch.setenv("LLM_CLI_PARALLEL_BACKENDS", "codex_cli")
    monkeypatch.setenv("LLM_CLI_PARALLEL_CANDIDATES", "false")
    assert client.candidate_parallel_supported() is False
    assert client.candidate_max_parallel() == 1


def test_cli_invoke_async_runs_subprocesses_in_parallel(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("LLM_CLI_PARALLEL_CANDIDATES", "true")
    monkeypatch.setenv("LLM_CLI_MAX_PARALLEL", "2")
    monkeypatch.setenv("LLM_CLI_PARALLEL_BACKENDS", "codex_cli")
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append((cmd, kwargs.get("env", {}).get("TMPDIR")))
        time.sleep(0.2)
        stdout = (
            '{"type":"item.completed","item":{"type":"agent_message","text":"SELECT '
            + str(len(calls))
            + ';"}}\n'
        )
        return subprocess.CompletedProcess(cmd, 0, stdout=stdout, stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    client = CLISubprocessClient(binary="codex", model="gpt-5.5", backend="codex_cli")

    started = time.perf_counter()
    async def run_pair():
        return await asyncio.gather(
            client.invoke_async("sys", "user 1"),
            client.invoke_async("sys", "user 2"),
        )

    first, second = asyncio.run(run_pair())
    elapsed = time.perf_counter() - started

    assert elapsed < 0.35
    assert first.backend == "codex_cli"
    assert second.backend == "codex_cli"
    assert len(calls) == 2
    assert calls[0][1] != calls[1][1]


def test_cli_invoke_async_respects_max_parallel_one(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("LLM_CLI_PARALLEL_CANDIDATES", "true")
    monkeypatch.setenv("LLM_CLI_MAX_PARALLEL", "1")
    monkeypatch.setenv("LLM_CLI_PARALLEL_BACKENDS", "codex_cli")

    def fake_run(cmd, **kwargs):
        time.sleep(0.18)
        return subprocess.CompletedProcess(
            cmd,
            0,
            stdout='{"type":"item.completed","item":{"type":"agent_message","text":"SELECT 1;"}}\n',
            stderr="",
        )

    monkeypatch.setattr(subprocess, "run", fake_run)
    client = CLISubprocessClient(binary="codex", model="gpt-5.5", backend="codex_cli")

    started = time.perf_counter()
    async def run_pair():
        await asyncio.gather(
            client.invoke_async("sys", "user 1"),
            client.invoke_async("sys", "user 2"),
        )

    asyncio.run(run_pair())
    elapsed = time.perf_counter() - started

    assert elapsed >= 0.34


def test_codex_invoke_reads_agent_message_from_jsonl(monkeypatch):
    captured = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["input"] = kwargs.get("input")
        stdout = (
            '{"type":"thread.started","thread_id":"t"}\n'
            '{"type":"item.completed","item":{"type":"agent_message","text":"SELECT 1;"}}\n'
            '{"type":"turn.completed","usage":{"input_tokens":10,"output_tokens":3}}\n'
        )
        return subprocess.CompletedProcess(cmd, 0, stdout=stdout, stderr="warning")

    monkeypatch.setattr(subprocess, "run", fake_run)
    client = CLISubprocessClient(binary="codex", model="gpt-5.5", backend="codex_cli")
    result = client.invoke("Return SQL only.", "Show active applications.")

    assert captured["cmd"][:2] == ["codex", "exec"]
    assert captured["cmd"][-1] == "-"
    assert "Return SQL only." in captured["input"]
    assert result.text == "SELECT 1;"
    assert result.usage_norm and result.usage_norm["total_tokens"] == 13


def test_claude_invoke_reads_json_result(monkeypatch):
    def fake_run(cmd, **kwargs):
        return subprocess.CompletedProcess(
            cmd,
            0,
            stdout='{"type":"result","is_error":false,"result":"SELECT 1;","usage":{"input_tokens":4,"output_tokens":2}}',
            stderr="",
        )

    monkeypatch.setattr(subprocess, "run", fake_run)
    client = CLISubprocessClient(binary="claude", model="sonnet", backend="anthropic_cli")
    result = client.invoke("Return SQL only.", "Show active applications.")
    assert result.text == "SELECT 1;"
    assert result.usage_norm and result.usage_norm["total_tokens"] == 6
