from __future__ import annotations

import json
import threading
import time

from app import llm_provider


class _FakeResponse:
    def __init__(self, payload: dict):
        self._payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self) -> bytes:
        return json.dumps(self._payload).encode("utf-8")


def test_ollama_native_sends_keep_alive(monkeypatch):
    captured: dict[str, object] = {}

    def fake_urlopen(req, timeout=0):
        captured["timeout"] = timeout
        captured["payload"] = json.loads(req.data.decode("utf-8"))
        return _FakeResponse({
            "message": {"content": "ok"},
            "prompt_eval_count": 2,
            "eval_count": 1,
        })

    monkeypatch.setenv("OLLAMA_REQUEST_KEEP_ALIVE", "0s")
    monkeypatch.setattr(llm_provider.url_request, "urlopen", fake_urlopen)
    client = llm_provider.OllamaNativeClient(
        model="qwen3.5:9b",
        base_url="http://ollama:11434/v1",
        backend="local_openai",
    )

    response = client.invoke("system", "user")

    payload = captured["payload"]
    assert payload["model"] == "qwen3.5:9b"
    assert payload["keep_alive"] == "0s"
    assert payload["think"] is False
    assert response.raw["_client_keep_alive"] == "0s"
    assert response.raw["_client_serialized_by_model"] is True


def test_ollama_native_serializes_same_model(monkeypatch):
    active = 0
    max_active = 0
    guard = threading.Lock()

    def fake_urlopen(req, timeout=0):
        nonlocal active, max_active
        with guard:
            active += 1
            max_active = max(max_active, active)
        time.sleep(0.05)
        with guard:
            active -= 1
        return _FakeResponse({"message": {"content": "ok"}})

    monkeypatch.setenv("OLLAMA_REQUEST_KEEP_ALIVE", "0s")
    monkeypatch.setattr(llm_provider.url_request, "urlopen", fake_urlopen)
    client = llm_provider.OllamaNativeClient(
        model="qwen3.5:9b",
        base_url="http://ollama:11434/v1",
        backend="local_openai",
    )

    threads = [
        threading.Thread(target=client.invoke, args=("system", "user")),
        threading.Thread(target=client.invoke, args=("system", "user")),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=2)

    assert max_active == 1
