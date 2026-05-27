"""
Подготовка init-скрипта для контейнера PostgreSQL.

Заказчик прислал дамп с шапкой, которую нельзя выполнять внутри initdb.
Первые шесть строк содержат CREATE DATABASE, backslash-команду \\connect
и параметры локали ru_RU.UTF-8, недоступной в образе postgres:16.
Если оставить их, контейнер просто не поднимется.

Этот скрипт берет исходный дамп TASK-3/data_model .sql, отрезает служебную
шапку и записывает безопасную версию в deploy/postgres-init/01-schema.sql,
откуда postgres подхватит ее как обычный CREATE TABLE-скрипт.

Запускается на хосте один раз перед docker compose build. Не зависит ни
от каких сторонних библиотек.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# Корень репозитория относительно файла скрипта. Файл лежит в scripts/,
# значит корень - на один уровень выше.
REPO_ROOT = Path(__file__).resolve().parent.parent

SOURCE_ENV = "INIT_SQL_SOURCE"
TARGET_ENV = "INIT_SQL_TARGET"

SOURCE_DUMPS = (
    REPO_ROOT / "TASK-3" / "data_model .sql",
    REPO_ROOT / "TASK-3" / "data_model.sql",
    REPO_ROOT / "TASK-3" / "marina-case3-rag" / "data_model.sql",
)
TARGET_INIT = REPO_ROOT / "deploy" / "postgres-init" / "01-schema.sql"

HEADER_STOP = "CREATE TABLE "


def env_path(name: str) -> Path | None:
    value = os.environ.get(name)
    if not value:
        return None
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = REPO_ROOT / path
    return path.resolve()


def find_source_dump() -> Path:
    source = env_path(SOURCE_ENV)
    if source is not None:
        if source.exists():
            return source
        raise FileNotFoundError(
            "Не найден исходный дамп схемы из "
            + SOURCE_ENV
            + ": "
            + str(source)
        )

    for path in SOURCE_DUMPS:
        if path.exists():
            return path

    paths = "\n  - ".join(str(path) for path in SOURCE_DUMPS)
    raise FileNotFoundError(
        "Не найден исходный дамп схемы. Проверенные пути:\n  - "
        + paths
        + "\nПроверь, что папка TASK-3 на месте. Если файл лежит в другом "
        + "месте, задай "
        + SOURCE_ENV
        + "=/path/to/data_model.sql."
    )


def strip_dump_header(lines: list[str]) -> str:
    for pos, line in enumerate(lines):
        if line.startswith(HEADER_STOP):
            start = pos - 1 if pos > 0 and not lines[pos - 1].strip() else pos
            return "".join(lines[start:])

    raise ValueError(
        "В исходном дампе не найден первый CREATE TABLE. "
        + "Похоже, файл поврежден или это не DDL-дамп."
    )


def prepare_init_sql() -> Path:
    """
    Прочитать исходный дамп, удалить вредную шапку, записать результат.

    Возвращает путь к подготовленному файлу. Если что-то пошло не так,
    падает с понятной ошибкой, а не молча создает пустой init.sql.
    """

    source_dump = find_source_dump()
    target_init = env_path(TARGET_ENV) or TARGET_INIT

    text = source_dump.read_text(encoding="utf-8")
    lines = text.splitlines(keepends=True)

    # Срезаем верх до первого CREATE TABLE. В исходниках встречались оба
    # имени файла: с пробелом перед .sql и без него.
    safe_body = (
        strip_dump_header(lines)
        .replace("\u0451", "\u0435")
        .replace("\u0401", "\u0415")
    )
    safe_body = safe_body.rstrip("\n") + "\n\n"

    # Защитная проверка: ни одной запретной директивы не должно остаться.
    # Если кто-то изменит исходный дамп и опасные строки окажутся ниже,
    # мы сразу это заметим и не пропустим init.sql в контейнер.
    forbidden = ("CREATE DATABASE", "\\connect", "LC_COLLATE", "LC_CTYPE")
    for token in forbidden:
        if token in safe_body:
            raise ValueError(
                "В подготовленном тексте остался запрещенный фрагмент: "
                + token
                + ". Доработай скрипт под новый исходник."
            )

    target_init.parent.mkdir(parents=True, exist_ok=True)
    if target_init.exists() and target_init.is_dir():
        raise ValueError("Целевой путь является каталогом: " + str(target_init))

    target_init.write_text(safe_body, encoding="utf-8")
    return target_init


def main() -> int:
    try:
        path = prepare_init_sql()
    except (FileNotFoundError, ValueError) as exc:
        # Ошибки понятные и адресные, поэтому показываем их без traceback.
        print("Ошибка подготовки init.sql: " + str(exc), file=sys.stderr)
        return 1

    print("Готов: " + str(path))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
