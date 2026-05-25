"""Запуск тестового Telegram-бота для команды."""

from __future__ import annotations

import sys
from pathlib import Path

from dotenv import load_dotenv

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from app import telegram_bot


if __name__ == "__main__":
    load_dotenv(_REPO_ROOT / ".env")
    telegram_bot.run_long_polling()
