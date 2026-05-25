"""
Отдельный сервис для просмотра JSON-трасс в браузере.

Поднимается на порту 8502, читает data/traces/*.json и рендерит две
страницы: список всех прогонов и подробную карточку одного прогона со
всеми событиями LangGraph, промптами и ответами модели. Без JavaScript -
обычный HTML, который можно сохранить и переслать.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from jinja2 import Environment, FileSystemLoader, select_autoescape
from markupsafe import Markup, escape

from app import trace as trace_module


TEMPLATES_DIR = Path(__file__).parent / "templates"

_env = Environment(
    loader=FileSystemLoader(str(TEMPLATES_DIR)),
    autoescape=select_autoescape(["html"]),
    trim_blocks=True,
    lstrip_blocks=True,
)


def _highlight_span(sql: str, span: str) -> Markup:
    """Подсветить evidence span внутри SQL."""
    if not sql or not span or span not in sql:
        return Markup("<pre>" + escape(sql or "") + "</pre>")
    before, after = sql.split(span, 1)
    html = (
        "<pre>"
        + escape(before)
        + "<mark>"
        + escape(span)
        + "</mark>"
        + escape(after)
        + "</pre>"
    )
    return Markup(html)


_env.filters["highlight_span"] = _highlight_span


app = FastAPI(
    title="Case 3 trace viewer",
    description="Просмотр истории прогонов pipeline.",
)


@app.get("/", response_class=HTMLResponse)
def index() -> HTMLResponse:
    """
    Главная страница: список всех прогонов pipeline.
    Шапки берутся из data/traces/*.json, сортировка по времени убывания,
    каждая строка - ссылка на детальную карточку.
    """
    traces = trace_module.list_traces()
    template = _env.get_template("trace_index.html")
    html = template.render(traces=traces)
    return HTMLResponse(html)


@app.get("/trace/{request_id}", response_class=HTMLResponse)
def show_trace(request_id: str) -> HTMLResponse:
    """
    Детальная карточка одного прогона по идентификатору запроса.
    Возвращает HTTP 404 если файла трассы с таким id нет на диске.
    Шаблон рендерит каждое событие LangGraph как развернутую панель.
    """
    data = trace_module.load_trace(request_id)
    if data is None:
        raise HTTPException(status_code=404, detail="Трасса не найдена")

    template = _env.get_template("trace_detail.html")
    html = template.render(trace=data)
    return HTMLResponse(html)


@app.get("/health")
def health() -> dict[str, str]:
    """
    Проверка живости просмотрщика. Не делает обращений к диску, поэтому
    отвечает мгновенно. Используется healthcheck-ом docker и скриптами
    мониторинга для подтверждения, что сервис поднялся.
    """
    return {"status": "ok"}
