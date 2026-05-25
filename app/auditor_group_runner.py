"""Grouped auditor framework for MVP.

The module loads Ksenia group definitions and runs small group checks through
``asyncio.gather``. G1-G3 are active under MVP via deterministic label-scoped
rules. G4-G7 stay explicit stubs until full prompts/RAG data arrive.
"""

from __future__ import annotations

import asyncio
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app import sql_guard

ROOT = Path(__file__).resolve().parent.parent
GROUPS_PATH = ROOT / "data" / "audit_groups" / "groups.yaml"
ACTIVE_GROUPS = frozenset({"G1", "G2", "G3"})
STUB_GROUPS = frozenset({"G4", "G5", "G6", "G7"})


@dataclass
class GroupResult:
    """One grouped-auditor result."""

    group_id: str
    labels: list[str]
    findings: list[Any]
    latency_ms: float
    used_stub: bool
    prompt_path: str = ""
    examples_count: int = 0
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "group_id": self.group_id,
            "labels": self.labels,
            "findings": [getattr(item, "__dict__", item) for item in self.findings],
            "latency_ms": self.latency_ms,
            "used_stub": self.used_stub,
            "prompt_path": self.prompt_path,
            "examples_count": self.examples_count,
            "error": self.error,
        }


def grouped_auditor_enabled() -> bool:
    raw = os.environ.get("AUDITOR_GROUPED_ENABLED", "").strip().lower()
    return raw in {"1", "true", "yes", "on"}


async def run_grouped_audit(
    sql: str,
    context: dict[str, Any] | None = None,
    groups_path: Path = GROUPS_PATH,
) -> list[GroupResult]:
    """Run all configured groups concurrently."""
    groups = _load_groups(groups_path)
    tasks = [run_group(str(item.get("id", "")), item, sql, context or {}) for item in groups]
    return await asyncio.gather(*tasks)


def run_grouped_audit_blocking(
    sql: str,
    context: dict[str, Any] | None = None,
    groups_path: Path = GROUPS_PATH,
) -> list[GroupResult]:
    """Sync wrapper for the current synchronous SecurityAuditor contract."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(run_grouped_audit(sql, context, groups_path))

    # If audit is called from an active loop, isolate the async run in a short
    # helper thread instead of nesting event loops.
    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(lambda: asyncio.run(run_grouped_audit(sql, context, groups_path)))
        return future.result()


async def run_group(
    group_id: str,
    group_def: dict[str, Any],
    sql: str,
    context: dict[str, Any] | None = None,
) -> GroupResult:
    """Run one group. G4-G7 are explicit MVP stubs."""
    started = time.perf_counter()
    labels = [str(item) for item in group_def.get("labels", [])]
    prompt_path = str(group_def.get("prompt_file") or "")
    examples_path = str(group_def.get("examples_file") or "")

    if group_id in STUB_GROUPS or group_id not in ACTIVE_GROUPS:
        return GroupResult(
            group_id=group_id,
            labels=labels,
            findings=[],
            latency_ms=(time.perf_counter() - started) * 1000,
            used_stub=True,
            prompt_path=prompt_path,
            examples_count=_count_examples(examples_path),
        )

    findings = sql_guard.check_by_labels(sql, labels, context or {})
    return GroupResult(
        group_id=group_id,
        labels=labels,
        findings=findings,
        latency_ms=(time.perf_counter() - started) * 1000,
        used_stub=False,
        prompt_path=prompt_path,
        examples_count=_count_examples(examples_path),
    )


def flatten_findings(results: list[GroupResult]) -> list[Any]:
    """Return deduped findings from group results, preserving strongest severity."""
    by_label: dict[str, Any] = {}
    for result in results:
        for item in result.findings:
            label = getattr(item, "vuln_class", None) or getattr(item, "label", "")
            current = by_label.get(str(label))
            score = float(getattr(item, "risk_score", getattr(item, "severity", 0.0)) or 0.0)
            old_score = float(getattr(current, "risk_score", getattr(current, "severity", -1.0)) or -1.0) if current else -1.0
            if current is None or score > old_score:
                by_label[str(label)] = item
    return list(by_label.values())


def _load_groups(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return _stub_groups()
    try:
        import yaml

        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        return _stub_groups(error=str(exc))
    if isinstance(data, dict):
        raw = data.get("groups", [])
    else:
        raw = data
    if not isinstance(raw, list):
        return _stub_groups(error="groups.yaml must be a list")
    groups = [item for item in raw if isinstance(item, dict) and item.get("id")]
    return groups or _stub_groups()


def _stub_groups(error: str | None = None) -> list[dict[str, Any]]:
    groups = []
    for group_id in ("G1", "G2", "G3", "G4", "G5", "G6", "G7"):
        groups.append({"id": group_id, "labels": [], "error": error})
    return groups


def _count_examples(path_text: str) -> int:
    if not path_text:
        return 0
    path = ROOT / path_text if not Path(path_text).is_absolute() else Path(path_text)
    if not path.exists():
        return 0
    try:
        return sum(1 for line in path.read_text(encoding="utf-8").splitlines() if line.strip())
    except OSError:
        return 0
