"""
Тестовый Telegram-бот для интерактивных прогонов pipeline.

Бот работает локально через long polling, пускает только user_id из
whitelist, хранит выбор модели по пользователю и отправляет HTML-отчет
как файл после вызова FastAPI /run.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable

from app import llm_provider, test_report
from app.trace import load_trace


LOGGER = logging.getLogger("case3.test_bot")
_PREFS_LOCK = threading.Lock()

PostRun = Callable[[str, dict[str, Any], int], Awaitable[Any]]


class BotUserError(RuntimeError):
    """Ошибка, которую можно показать пользователю бота."""


def _models_path() -> Path:
    return Path(os.environ.get("BOT_MODELS_CONFIG", "deploy/bot_models.json"))


def _prefs_path() -> Path:
    return Path(os.environ.get("BOT_USER_PREFS_PATH", "data/bot/user_prefs.json"))


def parse_allowed_users(raw: str | None = None) -> set[int]:
    """Разобрать TELEGRAM_ALLOWED_USERS как comma-separated список int."""
    text = os.environ.get("TELEGRAM_ALLOWED_USERS", "") if raw is None else raw
    users: set[int] = set()
    for part in text.split(","):
        item = part.strip()
        if not item:
            continue
        users.add(int(item))
    return users


def is_user_allowed(user_id: int, allowed: set[int]) -> bool:
    return user_id in allowed


def load_models(path: Path | None = None) -> dict[str, Any]:
    """Прочитать и проверить deploy/bot_models.json."""
    file = path or _models_path()
    data = json.loads(file.read_text(encoding="utf-8"))
    models = data.get("models")
    default = data.get("default_model_key")
    if not isinstance(models, list) or not models:
        raise ValueError("models должен быть непустым списком.")
    if not isinstance(default, str) or not default:
        raise ValueError("default_model_key должен быть строкой.")

    seen: set[str] = set()
    for item in models:
        if not isinstance(item, dict):
            raise ValueError("Каждая модель должна быть объектом.")
        key = str(item.get("key") or "").strip()
        mode = str(item.get("llm_mode") or "").strip()
        model = str(item.get("llm_generator_model") or "").strip()
        if not key:
            raise ValueError("key модели пуст.")
        if key in seen:
            raise ValueError("Дубликат key модели: " + key)
        if mode not in llm_provider.CONTOURS:
            raise ValueError("Неизвестный llm_mode для " + key + ": " + mode)
        if not llm_provider.is_known_generator_model(model, mode):
            raise ValueError("Неизвестный llm_generator_model для " + key + ": " + model)
        seen.add(key)
    if default not in seen:
        raise ValueError("default_model_key не найден среди models: " + default)
    data["models_by_key"] = {item["key"]: item for item in models}
    return data


def load_prefs(path: Path | None = None) -> dict[str, Any]:
    """Прочитать per-user preferences. Если файла нет, вернуть пустой dict."""
    file = path or _prefs_path()
    if not file.exists():
        return {}
    data = json.loads(file.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("user_prefs должен быть JSON-объектом.")
    return data


def save_prefs(prefs: dict[str, Any], path: Path | None = None) -> None:
    """Атомарно сохранить per-user preferences."""
    file = path or _prefs_path()
    file.parent.mkdir(parents=True, exist_ok=True)
    tmp = file.with_suffix(file.suffix + ".tmp")
    tmp.write_text(json.dumps(prefs, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(file)


def set_user_model(user_id: int, model_key: str) -> dict[str, Any]:
    """Сохранить выбранную модель пользователя и вернуть ее config."""
    config = load_models()
    model = config["models_by_key"].get(model_key)
    if model is None:
        raise BotUserError("Неизвестная модель: " + model_key)
    with _PREFS_LOCK:
        prefs = load_prefs()
        prefs[str(user_id)] = {
            "model_key": model_key,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        save_prefs(prefs)
    return model


def get_user_model(user_id: int) -> dict[str, Any]:
    """Вернуть выбранную модель пользователя или default из конфига."""
    config = load_models()
    default_key = config["default_model_key"]
    with _PREFS_LOCK:
        prefs = load_prefs()
    item = prefs.get(str(user_id)) if isinstance(prefs, dict) else None
    key = item.get("model_key") if isinstance(item, dict) else default_key
    return config["models_by_key"].get(key) or config["models_by_key"][default_key]


def start_text() -> str:
    return (
        "Привет. Это тестовый бот Case 3 для прогона SQL Security pipeline.\n"
        "Команды: /help, /models, /current.\n"
        "Любой обычный текст будет обработан как задача для pipeline."
    )


def help_text() -> str:
    return (
        "/start - краткое описание.\n"
        "/help - эта справка.\n"
        "/models - выбрать модель через inline-кнопки.\n"
        "/current - показать текущую модель.\n"
        "Обычный текст запускает /run и возвращает HTML-отчет файлом.\n"
        "Доступ выдается админом через TELEGRAM_ALLOWED_USERS."
    )


def current_text(user_id: int) -> str:
    model = get_user_model(user_id)
    return (
        "Сейчас выбрана: "
        + str(model["label"])
        + " ("
        + str(model["llm_mode"])
        + ", "
        + str(model["llm_generator_model"])
        + ")."
    )


async def _post_run(api_url: str, payload: dict[str, Any], timeout_sec: int) -> Any:
    import httpx

    url = api_url.rstrip("/") + "/run"
    async with httpx.AsyncClient(timeout=timeout_sec) as client:
        return await client.post(url, json=payload)


def _response_detail(response: Any) -> str:
    try:
        data = response.json()
    except Exception:
        data = None
    if isinstance(data, dict):
        detail = data.get("detail") or data.get("error")
        if detail:
            return str(detail)
    return str(getattr(response, "text", "") or "")


def _fallback_trace(run_id: str, task: str, result: dict[str, Any]) -> dict[str, Any]:
    return {
        "request_id": run_id,
        "task": task,
        "started_at": "",
        "finished_at": "",
        "duration_sec": None,
        "events": [],
        "result": result,
        "error": None,
    }


async def build_test_run(
    task: str,
    user_id: int,
    user_name: str,
    post_run: PostRun | None = None,
) -> test_report.TestRun:
    """Вызвать FastAPI /run, подтянуть trace и собрать TestRun."""
    text = task.strip()
    if not text:
        raise BotUserError("Пустой текст задачи.")

    model = get_user_model(user_id)
    payload = {
        "task": text,
        "llm_mode": model["llm_mode"],
        "llm_generator_model": model["llm_generator_model"],
    }
    api_url = os.environ.get("BOT_API_URL", "http://localhost:8000")
    timeout_sec = int(os.environ.get("BOT_RUN_TIMEOUT_SEC", "660"))
    started = datetime.now(timezone.utc)
    sender = post_run or _post_run
    response = await sender(api_url, payload, timeout_sec)
    status = int(getattr(response, "status_code", 0))

    if status == 503:
        raise BotUserError("Провайдер недоступен, попробуй позже.")
    if status == 504:
        raise BotUserError("Запрос превысил бюджет, попробуй проще.")
    if status != 200:
        detail = _response_detail(response)
        raise BotUserError("Pipeline вернул HTTP " + str(status) + ": " + detail)

    result = response.json()
    if not isinstance(result, dict):
        raise BotUserError("Pipeline вернул неожиданный JSON.")
    meta = result.get("metadata") or {}
    trace_id = str(meta.get("trace_id") or "").strip()
    run_id = trace_id or datetime.now(timezone.utc).strftime("telegram_%Y%m%dT%H%M%S")
    trace = load_trace(run_id) if trace_id else None
    finished = datetime.now(timezone.utc)

    return test_report.TestRun(
        run_id=run_id,
        user_id=user_id,
        user_name=user_name,
        task=text,
        model_key=str(model["key"]),
        model_label=str(model["label"]),
        llm_mode=str(model["llm_mode"]),
        llm_generator_model=str(model["llm_generator_model"]),
        started_at=started,
        finished_at=finished,
        system_result=result,
        trace=trace or _fallback_trace(run_id, text, result),
    )


def save_and_deliver_report(run: test_report.TestRun) -> tuple[Path, str | None]:
    html = test_report.render(run)
    html_path = test_report.save_report(html, run.run_id)
    url = test_report.upload_to_php_server(html_path, run.user_id, run.run_id)
    return html_path, url


def caption_for_run(run: test_report.TestRun) -> str:
    data = test_report.build_report_data(run)
    risks = data["risk_items"]
    risk_text = ", ".join(str(item["label"]) for item in risks) if risks else "none"
    return (
        "Вердикт: "
        + str(data["verdict"])
        + "\nИтераций: "
        + str(data["iterations_used"])
        + "\nДлительность: "
        + str(data["duration_sec"])
        + " sec\nTop risk labels: "
        + risk_text
    )[:1000]


def _build_router(allowed: set[int]) -> Any:
    from aiogram import F, Router
    from aiogram.dispatcher.middlewares.base import BaseMiddleware
    from aiogram.filters import Command
    from aiogram.types import CallbackQuery, FSInputFile, InlineKeyboardButton, InlineKeyboardMarkup, Message

    class WhitelistMiddleware(BaseMiddleware):
        async def __call__(self, handler: Any, event: Any, data: dict[str, Any]) -> Any:
            user = getattr(event, "from_user", None)
            user_id = int(getattr(user, "id", 0) or 0)
            text = str(getattr(event, "text", "") or getattr(event, "data", "") or "")
            LOGGER.info("update user_id=%s text=%s", user_id, text[:80])
            if not is_user_allowed(user_id, allowed):
                await event.answer("Доступ запрещен. Обратись к админу.")
                return None
            return await handler(event, data)

    router = Router()
    router.message.middleware(WhitelistMiddleware())
    router.callback_query.middleware(WhitelistMiddleware())

    @router.message(Command("start"))
    async def cmd_start(message: Message) -> None:
        await message.answer(start_text())

    @router.message(Command("help"))
    async def cmd_help(message: Message) -> None:
        await message.answer(help_text())

    @router.message(Command("models"))
    async def cmd_models(message: Message) -> None:
        config = load_models()
        rows = [
            [InlineKeyboardButton(text=str(item["label"]), callback_data="model:" + str(item["key"]))]
            for item in config["models"]
        ]
        await message.answer(
            "Выберите модель для своих прогонов:",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
        )

    @router.callback_query(F.data.startswith("model:"))
    async def model_callback(callback: CallbackQuery) -> None:
        key = str(callback.data or "").split(":", 1)[1]
        model = set_user_model(int(callback.from_user.id), key)
        await callback.answer("Выбрана модель: " + str(model["label"]))
        if callback.message:
            await callback.message.answer("Выбрана модель: " + str(model["label"]) + ". Можно слать задачи.")

    @router.message(Command("current"))
    async def cmd_current(message: Message) -> None:
        await message.answer(current_text(int(message.from_user.id)))

    @router.message(F.text)
    async def handle_task(message: Message) -> None:
        text = str(message.text or "").strip()
        if text.startswith("/"):
            return
        user = message.from_user
        user_id = int(user.id)
        user_name = str(user.full_name or user.first_name or "")
        LOGGER.info("incoming user_id=%s text=%s", user_id, text[:80])
        await message.answer("Запрос принят, обрабатываю...")
        await message.bot.send_chat_action(chat_id=message.chat.id, action="typing")
        try:
            run = await build_test_run(text, user_id, user_name)
            html_path, url = save_and_deliver_report(run)
            caption = caption_for_run(run)
            if url:
                await message.answer(caption + "\nОтчет: " + url)
            else:
                await message.answer_document(
                    document=FSInputFile(html_path),
                    caption=caption,
                )
            LOGGER.info("sent report user_id=%s run_id=%s", user_id, run.run_id)
        except BotUserError as exc:
            await message.answer(str(exc))
            LOGGER.warning("user error user_id=%s error=%s", user_id, exc)
        except NotImplementedError as exc:
            await message.answer(str(exc))
            LOGGER.error("delivery error user_id=%s error=%s", user_id, exc)

    return router


async def _run_long_polling() -> None:
    from aiogram import Bot, Dispatcher

    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        raise SystemExit("TELEGRAM_BOT_TOKEN пуст.")
    allowed = parse_allowed_users()
    if not allowed:
        raise SystemExit("TELEGRAM_ALLOWED_USERS пуст.")

    logging.basicConfig(level=os.environ.get("BOT_LOG_LEVEL", "INFO"))
    bot = Bot(token=token)
    dp = Dispatcher()
    dp.include_router(_build_router(allowed))
    LOGGER.info("starting long polling, allowed_users=%s", sorted(allowed))
    await dp.start_polling(bot)


def run_long_polling() -> None:
    asyncio.run(_run_long_polling())
