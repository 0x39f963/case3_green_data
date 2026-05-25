from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any
from urllib import request as url_request


ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def main() -> int:
    args = _args()
    started = time.perf_counter()
    data = _call(args)
    elapsed = time.perf_counter() - started
    text = _text(data)
    print(
        json.dumps(
            {
                "status": "ok",
                "base_url": args.base_url,
                "model": args.model,
                "latency_sec": round(elapsed, 3),
                "response_chars": len(text),
                "text": text[:1200],
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )
    return 0


def _args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default=os.environ.get("LOCAL_LLM_BASE_URL", "http://localhost:11434/v1"))
    parser.add_argument("--api-key", default=os.environ.get("LOCAL_LLM_API_KEY", "not-needed"))
    parser.add_argument("--model", default=os.environ.get("LOCAL_LLM_TEST_MODEL", "qwen3:8b"))
    parser.add_argument("--max-tokens", type=int, default=160)
    parser.add_argument(
        "--prompt",
        default="Сгенерируй один безопасный PostgreSQL SELECT для подсчета активных клиентов по статусам.",
    )
    return parser.parse_args()


def _call(args: argparse.Namespace) -> dict[str, Any]:
    if _use_native(args.base_url):
        return _call_ollama(args)
    from openai import OpenAI

    client = OpenAI(base_url=args.base_url, api_key=args.api_key)
    response = client.chat.completions.create(
        model=args.model,
        messages=[
            {"role": "system", "content": "Return concise SQL and a one sentence note. Keep the answer under 80 tokens."},
            {"role": "user", "content": args.prompt},
        ],
        temperature=0.1,
        max_tokens=args.max_tokens,
        extra_body={"think": False},
        timeout=120,
    )
    return response.model_dump()


def _call_ollama(args: argparse.Namespace) -> dict[str, Any]:
    base = args.base_url.rstrip("/")
    if base.endswith("/v1"):
        base = base[:-3]
    payload = {
        "model": args.model,
        "messages": [
            {"role": "system", "content": "Return concise SQL and a one sentence note. Keep the answer under 80 tokens."},
            {"role": "user", "content": args.prompt},
        ],
        "stream": False,
        "think": False,
        "options": {"temperature": 0.1, "num_predict": args.max_tokens},
    }
    req = url_request.Request(
        base + "/api/chat",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    with url_request.urlopen(req, timeout=120) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    data["choices"] = [{"message": data.get("message") or {}}]
    return data


def _text(data: dict[str, Any]) -> str:
    choices = data.get("choices") or []
    if not choices:
        return ""
    message = choices[0].get("message") or {}
    return str(message.get("content") or "").strip()


def _use_native(base_url: str) -> bool:
    raw = os.environ.get("LOCAL_LLM_USE_NATIVE_OLLAMA", "").strip().lower()
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    return "11434" in base_url or "ollama" in base_url.lower()


if __name__ == "__main__":
    raise SystemExit(main())
