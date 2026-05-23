from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
PROMPTS_DIR = ROOT / "app" / "prompts"

PROMPT_FILES: dict[str, str] = {
    "generator_system": "generator_system.txt",
    "generator_tool_mode_system": "generator_tool_mode_system.txt",
    "generator_tools_system": "generator_system_tools.txt",
    "auditor_system": "auditor_system.txt",
    "semantic_judge_system": "semantic_judge_system.txt",
    "quality_reviewer_system": "bench_reviewer_system.txt",
    "bench_reviewer_system": "bench_reviewer_system.txt",
    "bench_reviewer_user": "bench_reviewer_user.txt",
    "classifier_judge_system": "classifier_judge_system.txt",
    "prompt_check_judge_system": "prompt_check_judge_system.txt",
    "case_quality_judge_system": "case_quality_judge_system.txt",
    "judge_audit_hypothesis_system": "judge_audit_hypothesis_system.txt",
}
PROMPT_TYPES = tuple(PROMPT_FILES.keys())
STATUS_VALUES = {"draft", "active", "archived"}
DEFAULT_SEED_VERSION = 7


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS system_prompts (
    id TEXT PRIMARY KEY,
    prompt_type TEXT NOT NULL,
    version INTEGER NOT NULL,
    name TEXT NOT NULL,
    text TEXT NOT NULL,
    text_sha256 TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'draft'
        CHECK (status IN ('draft', 'active', 'archived')),
    is_default BOOLEAN NOT NULL DEFAULT false,
    created_by TEXT,
    notes TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (prompt_type, version)
);

CREATE UNIQUE INDEX IF NOT EXISTS system_prompts_one_default_per_type
ON system_prompts (prompt_type)
WHERE is_default = true AND status = 'active';

CREATE OR REPLACE FUNCTION set_system_prompts_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS system_prompts_updated_at ON system_prompts;
CREATE TRIGGER system_prompts_updated_at
BEFORE UPDATE ON system_prompts
FOR EACH ROW EXECUTE FUNCTION set_system_prompts_updated_at();
"""


class PromptRegistryError(RuntimeError):
    pass


class PromptRegistryUnavailable(PromptRegistryError):
    pass


class PromptNotFound(PromptRegistryError):
    pass


class PromptConflict(PromptRegistryError):
    pass


@dataclass(frozen=True)
class PromptRecord:
    id: str
    prompt_type: str
    version: int | None
    name: str
    text: str
    text_sha256: str
    status: str
    is_default: bool
    created_by: str | None = None
    notes: str | None = None
    created_at: Any = None
    updated_at: Any = None
    source: str = "db"
    fallback_reason: str | None = None

    @property
    def meta(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "prompt_id": self.id,
            "prompt_type": self.prompt_type,
            "prompt_version": self.version,
            "prompt_sha256": self.text_sha256,
            "prompt_source": self.source,
        }
        if self.fallback_reason:
            data["fallback_reason"] = self.fallback_reason
            data["prompt_fallback_reason"] = self.fallback_reason
        return data

    def to_dict(self, include_text: bool = True) -> dict[str, Any]:
        data = {
            "id": self.id,
            "prompt_id": self.id,
            "prompt_type": self.prompt_type,
            "version": self.version,
            "prompt_version": self.version,
            "name": self.name,
            "text_sha256": self.text_sha256,
            "prompt_sha256": self.text_sha256,
            "status": self.status,
            "is_default": self.is_default,
            "created_by": self.created_by,
            "notes": self.notes,
            "created_at": _json_time(self.created_at),
            "updated_at": _json_time(self.updated_at),
            "prompt_source": self.source,
            "fallback_reason": self.fallback_reason,
        }
        if include_text:
            data["text"] = self.text
        return data


def get_default_prompt(prompt_type: str) -> PromptRecord:
    """Return active default prompt; use file fallback if registry is not ready."""
    _check_type(prompt_type)
    if _env_bool("PROMPT_REGISTRY_DISABLE_DB", False):
        return file_prompt(prompt_type, "registry_disabled")
    dsn = _dsn()
    if not dsn:
        return file_prompt(prompt_type, "db_not_configured")
    driver = _psycopg2()
    if driver is None:
        return file_prompt(prompt_type, "driver_missing")

    try:
        with driver.connect(dsn, connect_timeout=3) as conn:
            with conn.cursor(cursor_factory=driver.extras.RealDictCursor) as cur:
                cur.execute(
                    """
                    SELECT *
                    FROM system_prompts
                    WHERE prompt_type = %s
                      AND status = 'active'
                      AND is_default = true
                    ORDER BY version DESC
                    LIMIT 1
                    """,
                    (prompt_type,),
                )
                row = cur.fetchone()
    except driver.Error:
        return file_prompt(prompt_type, "db_unavailable")

    if not row:
        return file_prompt(prompt_type, "default_not_found")
    return _record(row, source="db")


def get_default(prompt_type: str) -> dict[str, Any]:
    """Compatibility API expected by older TZ handoffs."""
    record = get_default_prompt(prompt_type)
    data = record.to_dict(include_text=True)
    data["sha256"] = record.text_sha256
    data["version"] = record.version
    if record.fallback_reason:
        data["prompt_fallback_reason"] = record.fallback_reason
    return data


def record_use(prompt_id: str | None, version: int | None) -> None:
    """Best-effort analytics hook kept for compatibility.

    Current benchmark ingest reads prompt metadata from traces, so this
    function intentionally has no side effect.
    """
    del prompt_id, version


def file_prompt(prompt_type: str, reason: str) -> PromptRecord:
    _check_type(prompt_type)
    name = PROMPT_FILES[prompt_type]
    path = PROMPTS_DIR / name
    if not path.exists():
        raise PromptRegistryUnavailable("fallback prompt file is missing: " + name)
    text = path.read_text(encoding="utf-8")
    return PromptRecord(
        id="file:" + name,
        prompt_type=prompt_type,
        version=None,
        name=name,
        text=text,
        text_sha256=sha256_text(text),
        status="active",
        is_default=True,
        source="file",
        fallback_reason=reason,
    )


def prompt_meta(record: PromptRecord) -> dict[str, Any]:
    return record.meta


def ensure_schema() -> None:
    driver = _require_driver()
    dsn = _require_dsn()
    with driver.connect(dsn, connect_timeout=5) as conn:
        with conn.cursor() as cur:
            cur.execute(SCHEMA_SQL)


def list_prompts(prompt_type: str | None = None) -> list[dict[str, Any]]:
    if prompt_type:
        _check_type(prompt_type)
    driver = _require_driver()
    dsn = _require_dsn()
    params: tuple[Any, ...] = ()
    where = ""
    if prompt_type:
        where = "WHERE prompt_type = %s"
        params = (prompt_type,)
    try:
        with driver.connect(dsn, connect_timeout=5) as conn:
            with conn.cursor(cursor_factory=driver.extras.RealDictCursor) as cur:
                cur.execute(
                    """
                    SELECT *
                    FROM system_prompts
                    """ + where + """
                    ORDER BY prompt_type ASC, version DESC
                    """,
                    params,
                )
                return [_record(row).to_dict(include_text=False) for row in cur.fetchall()]
    except driver.Error as exc:
        raise PromptRegistryUnavailable(_db_error_text(exc)) from exc


def get_prompt(prompt_id: str) -> dict[str, Any]:
    return _get_prompt_record(prompt_id).to_dict(include_text=True)


def create_prompt(
    *,
    prompt_type: str,
    name: str,
    text: str,
    created_by: str | None = None,
    notes: str | None = None,
    status: str = "draft",
    is_default: bool = False,
    version: int | None = None,
) -> dict[str, Any]:
    _check_type(prompt_type)
    _check_status(status)
    if is_default and status != "active":
        raise PromptConflict("default prompt must be active")
    driver = _require_driver()
    dsn = _require_dsn()
    clean_name = (name or prompt_type).strip() or prompt_type
    text = text or ""
    try:
        with driver.connect(dsn, connect_timeout=5) as conn:
            with conn.cursor(cursor_factory=driver.extras.RealDictCursor) as cur:
                if version is None:
                    cur.execute(
                        "SELECT COALESCE(MAX(version), 0) + 1 AS next_version FROM system_prompts WHERE prompt_type = %s",
                        (prompt_type,),
                    )
                    version = int(cur.fetchone()["next_version"])
                prompt_id = _make_id(prompt_type, version)
                cur.execute(
                    """
                    INSERT INTO system_prompts (
                        id, prompt_type, version, name, text, text_sha256,
                        status, is_default, created_by, notes
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    RETURNING *
                    """,
                    (
                        prompt_id,
                        prompt_type,
                        version,
                        clean_name,
                        text,
                        sha256_text(text),
                        status,
                        is_default,
                        created_by,
                        notes,
                    ),
                )
                row = cur.fetchone()
    except driver.Error as exc:
        raise PromptRegistryUnavailable(_db_error_text(exc)) from exc
    return _record(row).to_dict(include_text=True)


def clone_prompt(prompt_id: str, created_by: str | None = None) -> dict[str, Any]:
    source = _get_prompt_record(prompt_id)
    return create_prompt(
        prompt_type=source.prompt_type,
        name=source.name + " draft",
        text=source.text,
        created_by=created_by or source.created_by,
        notes="Cloned from " + source.id,
        status="draft",
        is_default=False,
    )


def update_prompt(
    prompt_id: str,
    *,
    name: str | None = None,
    text: str | None = None,
    notes: str | None = None,
    created_by: str | None = None,
) -> dict[str, Any]:
    driver = _require_driver()
    dsn = _require_dsn()
    try:
        with driver.connect(dsn, connect_timeout=5) as conn:
            with conn.cursor(cursor_factory=driver.extras.RealDictCursor) as cur:
                cur.execute("SELECT * FROM system_prompts WHERE id = %s FOR UPDATE", (prompt_id,))
                row = cur.fetchone()
                if not row:
                    raise PromptNotFound("prompt not found: " + prompt_id)
                if row["status"] != "draft":
                    raise PromptConflict("only draft prompts can be edited")
                values = {
                    "name": row["name"] if name is None else (name.strip() or row["name"]),
                    "text": row["text"] if text is None else text,
                    "notes": row["notes"] if notes is None else notes,
                    "created_by": row["created_by"] if created_by is None else created_by,
                }
                cur.execute(
                    """
                    UPDATE system_prompts
                    SET name = %s,
                        text = %s,
                        text_sha256 = %s,
                        notes = %s,
                        created_by = %s
                    WHERE id = %s
                    RETURNING *
                    """,
                    (
                        values["name"],
                        values["text"],
                        sha256_text(values["text"]),
                        values["notes"],
                        values["created_by"],
                        prompt_id,
                    ),
                )
                updated = cur.fetchone()
    except driver.Error as exc:
        raise PromptRegistryUnavailable(_db_error_text(exc)) from exc
    return _record(updated).to_dict(include_text=True)


def save_as_default_version(
    prompt_id: str,
    *,
    name: str | None = None,
    text: str | None = None,
    notes: str | None = None,
    created_by: str | None = None,
) -> dict[str, Any]:
    """Create an immutable active version from an existing prompt and make it default."""
    driver = _require_driver()
    dsn = _require_dsn()
    try:
        with driver.connect(dsn, connect_timeout=5) as conn:
            with conn.cursor(cursor_factory=driver.extras.RealDictCursor) as cur:
                cur.execute("SELECT * FROM system_prompts WHERE id = %s FOR UPDATE", (prompt_id,))
                row = cur.fetchone()
                if not row:
                    raise PromptNotFound("prompt not found: " + prompt_id)
                if row["status"] == "archived":
                    raise PromptConflict("archived prompt cannot become a new default version")

                prompt_type = row["prompt_type"]
                cur.execute(
                    "SELECT COALESCE(MAX(version), 0) + 1 AS next_version FROM system_prompts WHERE prompt_type = %s",
                    (prompt_type,),
                )
                version = int(cur.fetchone()["next_version"])
                new_text = row["text"] if text is None else text
                new_name = row["name"] if name is None else (name.strip() or row["name"])
                new_notes = notes if notes is not None else row["notes"]
                new_created_by = created_by if created_by is not None else row["created_by"]
                new_id = _make_id(prompt_type, version)

                cur.execute(
                    """
                    INSERT INTO system_prompts (
                        id, prompt_type, version, name, text, text_sha256,
                        status, is_default, created_by, notes
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, 'active', false, %s, %s)
                    RETURNING *
                    """,
                    (
                        new_id,
                        prompt_type,
                        version,
                        new_name,
                        new_text,
                        sha256_text(new_text),
                        new_created_by,
                        new_notes,
                    ),
                )
                cur.fetchone()
                cur.execute(
                    "UPDATE system_prompts SET is_default = false WHERE prompt_type = %s AND is_default = true",
                    (prompt_type,),
                )
                cur.execute(
                    "UPDATE system_prompts SET is_default = true WHERE id = %s RETURNING *",
                    (new_id,),
                )
                updated = cur.fetchone()
    except driver.Error as exc:
        raise PromptRegistryUnavailable(_db_error_text(exc)) from exc
    return _record(updated).to_dict(include_text=True)


def activate_prompt(prompt_id: str) -> dict[str, Any]:
    driver = _require_driver()
    dsn = _require_dsn()
    try:
        with driver.connect(dsn, connect_timeout=5) as conn:
            with conn.cursor(cursor_factory=driver.extras.RealDictCursor) as cur:
                cur.execute("SELECT * FROM system_prompts WHERE id = %s FOR UPDATE", (prompt_id,))
                row = cur.fetchone()
                if not row:
                    raise PromptNotFound("prompt not found: " + prompt_id)
                if row["status"] == "archived":
                    raise PromptConflict("archived prompt cannot be activated")
                cur.execute(
                    "UPDATE system_prompts SET status = 'active' WHERE id = %s RETURNING *",
                    (prompt_id,),
                )
                updated = cur.fetchone()
    except driver.Error as exc:
        raise PromptRegistryUnavailable(_db_error_text(exc)) from exc
    return _record(updated).to_dict(include_text=True)


def make_default(prompt_id: str) -> dict[str, Any]:
    driver = _require_driver()
    dsn = _require_dsn()
    try:
        with driver.connect(dsn, connect_timeout=5) as conn:
            with conn.cursor(cursor_factory=driver.extras.RealDictCursor) as cur:
                cur.execute("SELECT * FROM system_prompts WHERE id = %s FOR UPDATE", (prompt_id,))
                row = cur.fetchone()
                if not row:
                    raise PromptNotFound("prompt not found: " + prompt_id)
                if row["status"] != "active":
                    raise PromptConflict("only active prompt can become default")
                cur.execute(
                    "UPDATE system_prompts SET is_default = false WHERE prompt_type = %s AND is_default = true",
                    (row["prompt_type"],),
                )
                cur.execute(
                    "UPDATE system_prompts SET is_default = true WHERE id = %s RETURNING *",
                    (prompt_id,),
                )
                updated = cur.fetchone()
    except driver.Error as exc:
        raise PromptRegistryUnavailable(_db_error_text(exc)) from exc
    return _record(updated).to_dict(include_text=True)


def archive_prompt(prompt_id: str) -> dict[str, Any]:
    driver = _require_driver()
    dsn = _require_dsn()
    try:
        with driver.connect(dsn, connect_timeout=5) as conn:
            with conn.cursor(cursor_factory=driver.extras.RealDictCursor) as cur:
                cur.execute("SELECT * FROM system_prompts WHERE id = %s FOR UPDATE", (prompt_id,))
                row = cur.fetchone()
                if not row:
                    raise PromptNotFound("prompt not found: " + prompt_id)
                if row["is_default"]:
                    raise PromptConflict("default prompt cannot be archived")
                cur.execute(
                    "UPDATE system_prompts SET status = 'archived', is_default = false WHERE id = %s RETURNING *",
                    (prompt_id,),
                )
                updated = cur.fetchone()
    except driver.Error as exc:
        raise PromptRegistryUnavailable(_db_error_text(exc)) from exc
    return _record(updated).to_dict(include_text=True)


def seed_defaults(dry_run: bool = False, created_by: str = "seed_system_prompts.py") -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not dry_run:
        ensure_schema()
    for prompt_type, file_name in PROMPT_FILES.items():
        path = PROMPTS_DIR / file_name
        if not path.exists():
            rows.append({"prompt_type": prompt_type, "file": file_name, "status": "missing_file"})
            continue
        text = path.read_text(encoding="utf-8")
        item = {
            "id": _make_id(prompt_type, DEFAULT_SEED_VERSION),
            "prompt_type": prompt_type,
            "version": DEFAULT_SEED_VERSION,
            "name": file_name,
            "text_sha256": sha256_text(text),
            "status": "active",
            "is_default": True,
            "file": file_name,
        }
        if dry_run:
            rows.append({**item, "seed_status": "dry_run"})
            continue
        rows.append(_seed_one(item, text, created_by))
    return rows


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _seed_one(item: dict[str, Any], text: str, created_by: str) -> dict[str, Any]:
    driver = _require_driver()
    dsn = _require_dsn()
    try:
        with driver.connect(dsn, connect_timeout=5) as conn:
            with conn.cursor(cursor_factory=driver.extras.RealDictCursor) as cur:
                cur.execute(
                    "SELECT * FROM system_prompts WHERE prompt_type = %s AND version = %s",
                    (item["prompt_type"], item["version"]),
                )
                existing = cur.fetchone()
                if existing:
                    cur.execute(
                        "UPDATE system_prompts SET is_default = false WHERE prompt_type = %s AND is_default = true",
                        (item["prompt_type"],),
                    )
                    cur.execute(
                        "UPDATE system_prompts SET status = 'active', is_default = true WHERE id = %s RETURNING *",
                        (existing["id"],),
                    )
                    existing = cur.fetchone()
                    data = _record(existing).to_dict(include_text=False)
                    data["seed_status"] = "exists_made_default"
                    return data
                cur.execute(
                    "UPDATE system_prompts SET is_default = false WHERE prompt_type = %s AND is_default = true",
                    (item["prompt_type"],),
                )
                cur.execute(
                    """
                    INSERT INTO system_prompts (
                        id, prompt_type, version, name, text, text_sha256,
                        status, is_default, created_by, notes
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, 'active', true, %s, %s)
                    RETURNING *
                    """,
                    (
                        item["id"],
                        item["prompt_type"],
                        item["version"],
                        item["name"],
                        text,
                        item["text_sha256"],
                        created_by,
                        "Seeded from app/prompts/" + item["file"],
                    ),
                )
                row = cur.fetchone()
    except driver.Error as exc:
        raise PromptRegistryUnavailable(_db_error_text(exc)) from exc
    data = _record(row).to_dict(include_text=False)
    data["seed_status"] = "created"
    return data


def _get_prompt_record(prompt_id: str) -> PromptRecord:
    driver = _require_driver()
    dsn = _require_dsn()
    try:
        with driver.connect(dsn, connect_timeout=5) as conn:
            with conn.cursor(cursor_factory=driver.extras.RealDictCursor) as cur:
                cur.execute("SELECT * FROM system_prompts WHERE id = %s", (prompt_id,))
                row = cur.fetchone()
    except driver.Error as exc:
        raise PromptRegistryUnavailable(_db_error_text(exc)) from exc
    if not row:
        raise PromptNotFound("prompt not found: " + prompt_id)
    return _record(row)


def _record(row: dict[str, Any], source: str = "db") -> PromptRecord:
    return PromptRecord(
        id=str(row["id"]),
        prompt_type=str(row["prompt_type"]),
        version=int(row["version"]) if row.get("version") is not None else None,
        name=str(row.get("name") or row["id"]),
        text=str(row.get("text") or ""),
        text_sha256=str(row.get("text_sha256") or sha256_text(str(row.get("text") or ""))),
        status=str(row.get("status") or ""),
        is_default=bool(row.get("is_default")),
        created_by=row.get("created_by"),
        notes=row.get("notes"),
        created_at=row.get("created_at"),
        updated_at=row.get("updated_at"),
        source=source,
    )


def _json_time(value: Any) -> str | None:
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def _db_error_text(exc: BaseException) -> str:
    text = str(exc).strip().splitlines()[0] if str(exc).strip() else exc.__class__.__name__
    return "prompt registry DB unavailable: " + text


def _make_id(prompt_type: str, version: int) -> str:
    return prompt_type + "_v" + str(version)


def _check_type(prompt_type: str) -> None:
    if prompt_type not in PROMPT_FILES:
        raise PromptNotFound("unknown prompt_type: " + str(prompt_type))


def _check_status(status: str) -> None:
    if status not in STATUS_VALUES:
        raise PromptConflict("unknown prompt status: " + str(status))


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name, "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


def _dsn() -> str:
    return os.environ.get("POSTGRES_DSN", "").strip()


def _require_dsn() -> str:
    dsn = _dsn()
    if not dsn:
        raise PromptRegistryUnavailable("POSTGRES_DSN is not configured")
    return dsn


def _require_driver():
    driver = _psycopg2()
    if driver is None:
        raise PromptRegistryUnavailable("psycopg2 is not installed")
    return driver


def _psycopg2():
    try:
        import psycopg2
        import psycopg2.extras
    except ImportError:
        return None
    return psycopg2
