from __future__ import annotations

import re
from pathlib import Path

from app import prompt_registry


ROOT = Path(__file__).resolve().parent.parent
PROMPTS = ROOT / "app" / "prompts"

SHARED_BLOCKING_LABELS = (
    "DIRECT_SENSITIVE, MASKING_REQUIRED, EXCESSIVE_SCOPE, WRONG_JOIN_PATH, "
    "HALLUCINATED_TABLE, HALLUCINATED_COLUMN, MISSING_REQUIRED_FILTER, "
    "BUSINESS_MISMATCH, LIMIT_BYPASS, HARDCODED_BINDING, BINDINGS_BYPASS, "
    "BROKEN_SQL, SYNTAX_BROKEN, UNBOUND_PLACEHOLDER"
)


def _text(name: str) -> str:
    return (PROMPTS / name).read_text(encoding="utf-8")


def _shared_labels(text: str) -> str:
    match = re.search(
        r"Security labels that every judge must understand:\n(.+?)\.",
        text,
        re.S,
    )
    assert match is not None
    return " ".join(match.group(1).split())


def test_generator_system_vnext_contains_p0_rules() -> None:
    text = _text("generator_system.txt")

    for item in (
        "Step 0 -",
        "`refusal_required`",
        "`insufficient_context`",
        "`aggregate_safe`",
        "`row_level_business`",
        "Join discipline",
        "Type discipline",
        "Minimal projection",
        "Intent fidelity",
    ):
        assert item in text

    assert "status`, `state`, `decision`, `type`" in text
    assert "любые `*_id`" in text
    assert "'ACTIVE'" in text
    assert "boolean" in text
    assert "если нужного пути нет" in text or "Если путь не указан" in text
    assert "INSUFFICIENT_CONTEXT" in text
    assert "\"top by X\" на \"latest by id\"" in text


def test_generator_system_has_id_not_pii_and_anti_laundering_rules() -> None:
    text = _text("generator_system.txt")

    assert "Колонки на `_id` не являются PII только по имени" in text
    assert "credit_logic_id" in text
    assert "schema-tag" in text
    assert "100 тысяч строк" in text
    assert "100 thousand rows" in text
    assert "Excel dump" in text
    assert "Не превращай небезопасный full dump / mass export в COUNT" in text
    assert "Слово \"полный отчет\" само по себе не отказ" in text


def test_revision_prompt_preserves_form_and_repairs_first() -> None:
    text = _text("generator_user_revision.txt")

    assert "Порядок ремонта" in text
    assert "исправь тип литерала" in text
    assert "сохрани форму исходной задачи" in text
    assert "Не переиспользуй запрещенные identifiers" in text
    assert "smallint = boolean" in text
    assert "WRONG_JOIN_PATH" in text
    assert "Если исходная задача была \"top by X\"" in text
    assert "full dump / mass export / row-cap bypass" in text


def test_judge_prompts_share_same_blocking_taxonomy() -> None:
    files = (
        "semantic_judge_system.txt",
        "auditor_system.txt",
        "classifier_judge_system.txt",
    )

    labels = {_shared_labels(_text(name)) for name in files}

    assert labels == {SHARED_BLOCKING_LABELS}


def test_prompt_check_mass_export_is_bilingual_without_full_report_false_positive() -> None:
    text = _text("prompt_check_judge_system.txt")

    for item in (
        "100 тысяч строк",
        "полная история",
        "выгрузка в Excel",
        "100 thousand rows",
        "full history",
        "Excel dump",
        "FETCH ALL",
        "Do not flag \"полный отчет\"",
    ):
        assert item in text


def test_prompt_registry_seeds_v8_defaults() -> None:
    assert prompt_registry.DEFAULT_SEED_VERSION == 8
