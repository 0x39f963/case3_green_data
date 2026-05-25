"""
Подготовка init-скрипта для контейнера PostgreSQL.

Заказчик прислал дамп с шапкой, которую нельзя выполнять внутри initdb.
Первые шесть строк содержат CREATE DATABASE, backslash-команду \\connect
и параметры локали ru_RU.UTF-8, недоступной в образе postgres:16.
Если оставить их, контейнер просто не поднимется.

Этот скрипт берет исходный дамп TASK-3/data_model .sql, отрезает первые
шесть строк и записывает безопасную версию в deploy/init.sql, откуда
postgres подхватит ее как обычный CREATE TABLE-скрипт.

Запускается на хосте один раз перед docker compose build. Не зависит ни
от каких сторонних библиотек.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Корень репозитория относительно файла скрипта. Файл лежит в scripts/,
# значит корень - на один уровень выше.
REPO_ROOT = Path(__file__).resolve().parent.parent

SOURCE_DUMP = REPO_ROOT / "TASK-3" / "data_model .sql"
TARGET_INIT = REPO_ROOT / "deploy" / "init.sql"

# Сколько строк отрезаем сверху. Число строго привязано к содержанию
# исходного файла заказчика: SET / CREATE DATABASE / \\connect плюс
# пустые строки между ними. При смене источника проверять заново.
HEAD_STRIP = 6


def prepare_init_sql() -> Path:
    """
    Прочитать исходный дамп, удалить вредную шапку, записать результат.

    Возвращает путь к подготовленному файлу. Если что-то пошло не так,
    падает с понятной ошибкой, а не молча создает пустой init.sql.
    """

    if not SOURCE_DUMP.exists():
        raise FileNotFoundError(
            "Не найден исходный дамп схемы: " + str(SOURCE_DUMP)
            + ". Проверь, что папка TASK-3 на месте."
        )

    text = SOURCE_DUMP.read_text(encoding="utf-8")
    lines = text.splitlines(keepends=True)

    if len(lines) <= HEAD_STRIP:
        raise ValueError(
            "В исходном дампе меньше "
            + str(HEAD_STRIP)
            + " строк. Похоже, файл поврежден."
        )

    # Срезаем верх. Все остальное переносим как есть - там идут CREATE
    # TABLE, комментарии, внешние ключи. Менять содержимое не нужно.
    safe_body = "".join(lines[HEAD_STRIP:])

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

    TARGET_INIT.parent.mkdir(parents=True, exist_ok=True)
    TARGET_INIT.write_text(safe_body, encoding="utf-8")
    return TARGET_INIT


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
