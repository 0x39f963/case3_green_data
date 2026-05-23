from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import prompt_registry  # noqa: E402


def main() -> int:
    args = parse_args()
    try:
        rows = prompt_registry.seed_defaults(dry_run=args.dry_run, created_by=args.created_by)
    except prompt_registry.PromptRegistryError as exc:
        print(json.dumps({"status": "ERROR", "error": str(exc)}, ensure_ascii=False))
        return 2
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"status": "ERROR", "error": str(exc)}, ensure_ascii=False))
        return 2

    payload: dict[str, Any] = {
        "status": "DRY_RUN" if args.dry_run else "OK",
        "count": len(rows),
        "items": rows,
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Seed system prompt registry from app/prompts files.")
    parser.add_argument("--dry-run", action="store_true", help="Print seed rows without touching Postgres.")
    parser.add_argument("--created-by", default="seed_system_prompts.py")
    return parser.parse_args()


if __name__ == "__main__":
    raise SystemExit(main())
