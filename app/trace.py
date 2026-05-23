"""
Сборщик JSON-трассы цикла генерации и проверки SQL.

Один прогон pipeline - один JSON-файл в data/traces. Внутри линейный
список событий: имя ноды LangGraph, тайминги, входящее состояние,
исходящее состояние, и details с полными промптами, ответами модели и
найденными уязвимостями. Файлы накапливаются и читаются trace-viewer.
"""

from __future__ import annotations

import contextvars
import json
import os
import time
import uuid
import difflib
import re
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from pglast import parser
from pglast.parser import ParseError


DEFAULT_TRACES_DIR = Path("/app/data/traces")


# Phase 7 — request_id override через ContextVar, чтобы вызывающая сторона
# (например web_chat) могла заранее сгенерить trace_id, передать его UI,
# а Trace(...) внутри pipeline подхватил тот же id вместо построения
# нового. asyncio.to_thread копирует contextvars в воркер автоматически.
_REQUEST_ID_OVERRIDE: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "trace_request_id_override",
    default=None,
)


@contextmanager
def request_id_override(request_id: str) -> Iterator[str]:
    """Привязать конкретный trace_id к Trace, создаваемому внутри блока."""
    token = _REQUEST_ID_OVERRIDE.set(request_id)
    try:
        yield request_id
    finally:
        _REQUEST_ID_OVERRIDE.reset(token)


def _traces_dir() -> Path:
    """Где хранить файлы. Папка из переменной окружения, либо дефолт."""
    raw = os.environ.get("TRACES_DIR", "").strip()
    path = Path(raw) if raw else DEFAULT_TRACES_DIR
    path.mkdir(parents=True, exist_ok=True)
    return path


def _now() -> float:
    return time.time()


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


def _safe(value: Any) -> Any:
    """
    Привести значение к json-сериализуемому виду.

    LangGraph state может содержать вложенные dataclass-объекты, datetime,
    Vulnerability из baseline. Превращаем их в простые типы: чтобы трасса
    оставалась читаемой и переносимой.
    """
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {k: _safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_safe(v) for v in value]
    if hasattr(value, "__dict__"):
        return _safe(vars(value))
    return repr(value)


class Trace:
    """
    Накопитель событий одного прогона.

    Используется как контекстный менеджер: на выходе из with-блока
    финальная структура пишется в файл, даже если внутри произошла
    ошибка - тогда в трассу попадет error_info.
    """

    def __init__(self, task: str, request_id: str | None = None) -> None:
        override = _REQUEST_ID_OVERRIDE.get(None)
        self.request_id = request_id or override or _build_request_id()
        self.task = task
        self.started_at = _now()
        self.finished_at: float | None = None
        self.events: list[dict[str, Any]] = []
        self.error: dict[str, Any] | None = None
        self.result: dict[str, Any] | None = None

    @contextmanager
    def step(
        self,
        node: str,
        inputs: dict[str, Any] | None = None,
    ) -> Iterator[dict[str, Any]]:
        """
        Записать одно событие pipeline. Возвращает мутируемый dict, в
        который нода кладет outputs и details. Время начала и конца
        контекст-менеджер ставит сам.
        """
        event: dict[str, Any] = {
            "node": node,
            "started_at": _iso(_now()),
            "started_ts": _now(),
            "inputs": _safe(inputs or {}),
            "outputs": {},
            "details": {},
        }
        try:
            yield event
        finally:
            event["finished_at"] = _iso(_now())
            event["duration_sec"] = round(_now() - event["started_ts"], 3)
            event.pop("started_ts", None)
            event["outputs"] = _safe(event.get("outputs", {}))
            event["details"] = _safe(event.get("details", {}))
            self.events.append(event)
            self.flush_partial()

    def attach_result(self, result: dict[str, Any]) -> None:
        """
        Прицепить финальный SystemResult к трассе.
        Внутри идет нормализация в простые типы, чтобы запись на диск не
        упала на dataclass-объектах baseline.
        """
        self.result = _safe(result)

    def attach_error(self, exc: BaseException) -> None:
        """
        Зафиксировать падение pipeline для записи в файл трассы.
        Аналитик увидит тип и текст ошибки в просмотрщике, даже если
        процесс рухнул на середине цикла.
        """
        self.error = {
            "type": exc.__class__.__name__,
            "message": str(exc),
        }

    def save(self) -> Path:
        """
        Сериализовать накопленные события и записать JSON на диск.
        Возвращает путь к файлу - его потом подхватывает trace-viewer и
        печатает оркестратор в metadata.trace_id.
        """
        self.finished_at = self.finished_at or _now()
        payload = {
            "request_id": self.request_id,
            "task": self.task,
            "started_at": _iso(self.started_at),
            "finished_at": _iso(self.finished_at),
            "duration_sec": round(self.finished_at - self.started_at, 3),
            "events": self.events,
            "result": self.result,
            "error": self.error,
        }
        out_path = _traces_dir() / (self.request_id + ".json")
        out_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return out_path

    def flush_partial(self) -> Path | None:
        """Live-flush текущего состояния трассы между узлами.

        Используется web-UI чтобы показать прогресс pipeline в реальном
        времени (см. /web/api/chats/{chat_id}/progress). Идентичная
        форма файла, что и финальный save(), но с флагом `partial: true`
        и без finished_at/duration. Save() в конце run'а перезапишет
        этим же путём и удалит флаг. Любые OSError проглатываются,
        чтобы partial-flush не мог уронить pipeline.
        """
        try:
            payload = {
                "request_id": self.request_id,
                "task": self.task,
                "started_at": _iso(self.started_at),
                "events": list(self.events),
                "result": self.result,
                "error": self.error,
                "partial": True,
            }
            out_path = _traces_dir() / (self.request_id + ".json")
            tmp_path = out_path.with_suffix(out_path.suffix + ".tmp")
            tmp_path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            tmp_path.replace(out_path)
            return out_path
        except OSError:
            return None


def _build_request_id() -> str:
    """
    Имя файла трассы. Складываем штамп времени и короткий случайный
    хвост, чтобы файлы из одной секунды не перетирали друг друга, и
    чтобы список в trace-viewer сортировался по времени без отдельной
    колонки.
    """
    stamp = datetime.now(tz=timezone.utc).strftime("%Y%m%dT%H%M%S")
    suffix = uuid.uuid4().hex[:8]
    return stamp + "_" + suffix


def list_traces() -> list[dict[str, Any]]:
    """
    Прочитать список существующих трасс с короткими шапками.

    Используется на главной странице просмотрщика. Файлы сортируются
    по убыванию имени - имена начинаются со штампа времени, поэтому
    это эквивалентно сортировке по времени старта.
    """
    items: list[dict[str, Any]] = []
    folder = _traces_dir()
    for file in sorted(folder.glob("*.json"), reverse=True):
        try:
            data = json.loads(file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            # Пропускаем битый файл, но не валим листинг.
            continue
        items.append(
            {
                "request_id": data.get("request_id", file.stem),
                "task": data.get("task", ""),
                "started_at": data.get("started_at", ""),
                "duration_sec": data.get("duration_sec"),
                "iterations": len([e for e in data.get("events", []) if e.get("node") == "generate"]),
                "approved": (data.get("result") or {}).get("approved"),
                "error": data.get("error"),
            }
        )
    return items


def load_trace(request_id: str) -> dict[str, Any] | None:
    """
    Прочитать одну трассу по идентификатору запроса.
    Возвращает разобранный JSON или None, если файла нет на диске.
    Используется детальной страницей trace-viewer.
    """
    path = _traces_dir() / (request_id + ".json")
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def save_trace_payload(
    payload: dict[str, Any],
    request_id: str | None = None,
    overwrite: bool = False,
) -> Path:
    """
    Save an externally produced trace JSON into the common traces folder.

    Used by web import endpoints so Telegram or benchmark run JSON can be
    opened later in the existing trace-viewer.
    """
    trace_id = _safe_request_id(request_id or str(payload.get("request_id") or ""))
    if not trace_id:
        trace_id = _build_request_id()
    data = dict(payload)
    data["request_id"] = trace_id
    out_path = _traces_dir() / (trace_id + ".json")
    if out_path.exists() and not overwrite:
        return out_path
    out_path.write_text(
        json.dumps(_safe(data), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return out_path


def ast_tree(sql: str) -> dict[str, Any]:
    """Вернуть pglast AST как JSON-friendly dict."""
    if not sql.strip():
        return {"ok": False, "error": "empty sql", "tree": None}
    try:
        return {"ok": True, "error": None, "tree": json.loads(parser.parse_sql_json(sql))}
    except (ParseError, ValueError) as exc:
        return {"ok": False, "error": str(exc), "tree": None}


def sql_diff(previous_sql: str, current_sql: str) -> str:
    """Вернуть unified diff между двумя SQL-итерациями."""
    if not previous_sql.strip() or previous_sql.strip() == current_sql.strip():
        return ""
    return "\n".join(
        difflib.unified_diff(
            previous_sql.splitlines(),
            current_sql.splitlines(),
            fromfile="previous.sql",
            tofile="current.sql",
            lineterm="",
        )
    )


def _safe_request_id(value: str) -> str:
    text = value.strip()
    if not text or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,120}", text):
        return ""
    return text
