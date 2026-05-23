from __future__ import annotations

import json
from pathlib import Path

from app import trace as trace_store


def test_trace_uses_request_id_override_and_writes_partial(tmp_path, monkeypatch):
    monkeypatch.setenv("TRACES_DIR", str(tmp_path))

    with trace_store.request_id_override("test_partial_001"):
        trace = trace_store.Trace(task="test task", request_id=None)
        assert trace.request_id == "test_partial_001"
        with trace.step("prompt_check", inputs={"x": 1}) as event:
            event["outputs"] = {"ok": True}

    data = json.loads((tmp_path / "test_partial_001.json").read_text(encoding="utf-8"))
    assert data["request_id"] == "test_partial_001"
    assert data["partial"] is True
    assert data["events"][0]["node"] == "prompt_check"
    assert data["events"][0]["outputs"] == {"ok": True}
    assert "finished_at" not in data
    assert "duration_sec" not in data


def test_save_overwrites_partial_with_final_payload(tmp_path, monkeypatch):
    monkeypatch.setenv("TRACES_DIR", str(tmp_path))
    trace = trace_store.Trace(task="test task", request_id="final_001")

    with trace.step("retrieve"):
        pass
    trace.attach_result({"approved": True, "final_sql": "SELECT 1"})
    saved = trace.save()

    data = json.loads(saved.read_text(encoding="utf-8"))
    assert data["request_id"] == "final_001"
    assert data.get("partial") is not True
    assert data["finished_at"]
    assert data["duration_sec"] >= 0
    assert data["result"]["approved"] is True


def test_flush_partial_returns_none_on_os_error(tmp_path, monkeypatch):
    monkeypatch.setenv("TRACES_DIR", str(tmp_path))
    trace = trace_store.Trace(task="test task", request_id="fail_001")

    def fail_write_text(self: Path, *args, **kwargs):
        raise OSError("disk is unavailable")

    monkeypatch.setattr(Path, "write_text", fail_write_text)

    assert trace.flush_partial() is None
