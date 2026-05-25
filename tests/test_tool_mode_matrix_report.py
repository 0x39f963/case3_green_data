from __future__ import annotations

from pathlib import Path

from app import llm_provider
from scripts import smoke_tool_mode_matrix


def test_matrix_report_has_parseable_structure(tmp_path: Path) -> None:
    path = tmp_path / "matrix.md"
    rows = [
        {
            "key": "local-qwen3-5-9b",
            "backend": "local_openai",
            "provider_model": "qwen3.5:9b",
            "outcome": "passed",
            "supports_tool_mode": "verified",
            "error": "",
        }
    ]

    smoke_tool_mode_matrix.write_report(rows, path)
    text = path.read_text(encoding="utf-8")

    assert text.startswith("Generated at:")
    assert "| key | backend | provider_model | outcome | supports_tool_mode | error |" in text
    assert "| local-qwen3-5-9b | local_openai | qwen3.5:9b | passed | verified |  |" in text


def test_list_model_options_exposes_tool_support_field() -> None:
    options = llm_provider.list_model_options()

    assert options
    assert all(item.get("supports_tool_mode") in {"verified", "unsupported", "unknown"} for item in options)


def test_tool_support_parser_reads_matrix_report(tmp_path: Path, monkeypatch) -> None:
    report = tmp_path / ".cursor/!tmp/!ARTEFACTS/2026-05-21/13-tool_mode_matrix_report.md"
    report.parent.mkdir(parents=True)
    report.write_text(
        "\n".join(
            [
                "Generated at: 2026-05-21 19:00:00 MSK",
                "",
                "| key | backend | provider_model | outcome | supports_tool_mode | error |",
                "|---|---|---|---|---|---|",
                "| local-qwen3-5-9b | local_openai | qwen3.5:9b | passed | verified |  |",
                "| local-qwen3-8b | local_openai | qwen3:8b | model_didnt_call_tool | unsupported |  |",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)

    parsed = llm_provider._tool_support_from_report()

    assert parsed["local-qwen3-5-9b"] == "verified"
    assert parsed["local-qwen3-8b"] == "unsupported"
