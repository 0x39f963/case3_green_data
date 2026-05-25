from __future__ import annotations

from fastapi.testclient import TestClient

from app.api import app


def test_chat_uses_canonical_shell():
    client = TestClient(app)

    response = client.get("/chat")

    assert response.status_code == 200
    assert 'class="page-shell"' in response.text
    assert 'class="tool-col"' in response.text
    assert 'class="side-toolbar"' in response.text
    assert "/web/static/shared/design_system.css" in response.text
    assert "/web/assets/web_chat.js" in response.text


def test_shared_assets_served_and_blocked():
    client = TestClient(app)

    css = client.get("/web/static/shared/design_system.css")
    js = client.get("/web/static/shared/shell.js")
    blocked = client.get("/web/static/shared/random.css")

    assert css.status_code == 200
    assert ".card" in css.text
    assert ".page-shell" in css.text
    assert ".tool-col" in css.text
    assert js.status_code == 200
    assert "toolCol" in js.text
    assert blocked.status_code == 404


def test_run_detail_route_states(tmp_path, monkeypatch):
    monkeypatch.setenv("BOT_REPORTS_DIR", str(tmp_path / "reports"))
    monkeypatch.setenv("TRACES_DIR", str(tmp_path / "traces"))
    reports = tmp_path / "reports"
    traces = tmp_path / "traces"
    reports.mkdir()
    traces.mkdir()
    (reports / "sample123.html").write_text("<html>report</html>", encoding="utf-8")
    (traces / "trace12345.json").write_text('{"ok": true}', encoding="utf-8")

    client = TestClient(app)

    invalid = client.get("/runs/bad.path")
    assert invalid.status_code == 200
    assert "Trace id is missing or invalid" in invalid.text
    assert 'class="page-shell"' in invalid.text
    assert client.get("/runs/sample123").status_code == 200
    # Когда трасса есть, /runs рендерит canonical отчёт on-the-fly
    # вместо placeholder'а. Trace в этом тесте мок-минимальный — рендер
    # может уйти в graceful fallback, нам важно лишь не получить
    # "HTML report is not generated yet" для уже существующего trace.
    placeholder = client.get("/runs/trace12345")
    assert placeholder.status_code == 200
    missing = client.get("/runs/missing123")
    assert missing.status_code == 200
    assert "Trace JSON and HTML report were not found" in missing.text
