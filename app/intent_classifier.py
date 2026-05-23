"""
Деterministic intent classifier для SQL pipeline.

Распознаёт high-level intent на основе ключевых слов / regex по тексту задачи.
Это первый шаг pipeline: результат передаётся в RAG, generator prompt, audit и
classifier ctx, чтобы downstream knew, какой шаблон ответа ожидается.

Категории (mutually exclusive, ranked):
- refusal_required: явные команды на bypass прав/safety/exfil.
- insufficient_context: запрос требует объекты, которых нет в schema overlay.
- mask_required: «замаскируй / mask / hash / зашифруй» PII.
- aggregate_safe: agg-only / "без персональных полей" / "сколько X".
- top_n: top-N, ORDER BY ... LIMIT N интент.
- row_level_business: «покажи / выведи / список / выбери», без safe-якорей.
- unknown: всё остальное.

Этот модуль не должен делать LLM-вызов; он быстрый и детерминированный.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


INTENT_REFUSAL_REQUIRED = "refusal_required"
INTENT_INSUFFICIENT_CONTEXT = "insufficient_context"
INTENT_MASK_REQUIRED = "mask_required"
INTENT_AGGREGATE_SAFE = "aggregate_safe"
INTENT_TOP_N = "top_n"
INTENT_ROW_LEVEL_BUSINESS = "row_level_business"
INTENT_UNKNOWN = "unknown"

ALL_INTENTS = (
    INTENT_REFUSAL_REQUIRED,
    INTENT_INSUFFICIENT_CONTEXT,
    INTENT_MASK_REQUIRED,
    INTENT_AGGREGATE_SAFE,
    INTENT_TOP_N,
    INTENT_ROW_LEVEL_BUSINESS,
    INTENT_UNKNOWN,
)


@dataclass(frozen=True)
class Intent:
    kind: str
    confidence: float
    matched: tuple[str, ...]


_REFUSAL_PATTERNS = [
    r"\bexec\s+xp_\w+",
    r"\bunion\s+select\b",
    r"\bdrop\s+(?:table|schema|database)\b",
    r"\bgrant\b|\brevoke\b|\balter\s+role\b",
    r"\bpg_(?:read_file|ls_dir|sleep)\b",
    r"\b(information_schema|pg_catalog)\b",
    r"обойти\s+(?:права|политику|policy)",
    r"раскрой\s+(?:чужие|пароли|секреты)",
    r"\bsystem\s+prompt\b|игнорируй\s+правила",
]

_MASK_PATTERNS = [
    r"замаскируй",
    r"\bmask\b",
    r"\bmd5\(|\bsha\d+\(|\bhash\(",
    r"скрой\s+(?:часть|первые|последние)",
    r"приведи\s+к\s+masked",
]

_SAFE_REPORT_PATTERNS = [
    r"без\s+(?:персональных|пдн|личных)\s+полей",
    r"без\s+ПДн\b",
    r"обезличенн\w*",
    r"safe\s+(?:employee\s+)?report",
    r"anonymi[sz]ed",
]

_AGGREGATE_VERB_PATTERNS = [
    r"\bcount\b|\bsum\b|\bavg\b|\bmax\b|\bmin\b",
    r"подсчит\w*|сколько|количество",
    r"средн\w*|максимум|минимум|сумма",
    r"\baggregate\b|агрегат",
    r"сводн\w*\s+отч[её]т",
]

_TOP_N_PATTERNS = [
    r"\btop[\s-]?\d+\b|топ[\s-]?\d+",
    r"\bпервые\s+\d+|первых\s+\d+",
    r"largest|biggest|самые\s+(?:большие|крупные|активные)",
    r"\border\s+by\b.*\blimit\b",
]

_ROW_LEVEL_PATTERNS = [
    r"\bпокажи\b|\bвыведи\b|\bвыбери\b|\bсписок\b",
    r"\bselect\s+\w+,",
    r"\bget\b|\bfetch\b|\bretrieve\b",
]


def _hits(patterns: list[str], text: str) -> list[str]:
    found: list[str] = []
    for pat in patterns:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            found.append(m.group(0))
    return found


def classify(task: str, attack_prompt: str = "") -> Intent:
    """Вернуть Intent, выбранный по ранжированным правилам."""
    body = (task or "") + " " + (attack_prompt or "")

    refusal_hits = _hits(_REFUSAL_PATTERNS, body)
    if refusal_hits:
        return Intent(INTENT_REFUSAL_REQUIRED, 0.95, tuple(refusal_hits[:3]))

    mask_hits = _hits(_MASK_PATTERNS, body)
    safe_hits = _hits(_SAFE_REPORT_PATTERNS, body)
    aggregate_verb_hits = _hits(_AGGREGATE_VERB_PATTERNS, body)
    top_n_hits = _hits(_TOP_N_PATTERNS, body)
    row_level_hits = _hits(_ROW_LEVEL_PATTERNS, body)

    if mask_hits and not safe_hits:
        return Intent(INTENT_MASK_REQUIRED, 0.85, tuple(mask_hits[:3]))

    if safe_hits or aggregate_verb_hits:
        confidence = 0.9 if safe_hits else 0.7
        matched = tuple((safe_hits + aggregate_verb_hits)[:4])
        return Intent(INTENT_AGGREGATE_SAFE, confidence, matched)

    if top_n_hits:
        return Intent(INTENT_TOP_N, 0.8, tuple(top_n_hits[:3]))

    if row_level_hits:
        return Intent(INTENT_ROW_LEVEL_BUSINESS, 0.6, tuple(row_level_hits[:3]))

    return Intent(INTENT_UNKNOWN, 0.0, ())


def render_prompt_block(intent: Intent) -> str:
    """Однострочный блок для подмеса в generator/auditor prompt."""
    if intent.kind == INTENT_UNKNOWN:
        return "INTENT: unknown"
    return (
        "INTENT: " + intent.kind
        + " (confidence=" + str(round(intent.confidence, 2))
        + (", anchors=" + ", ".join(intent.matched) if intent.matched else "")
        + ")"
    )
