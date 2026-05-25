/* Generated at: 2026-05-23 11:55:41 MSK */
(function (root, factory) {
  const specs = factory();
  if (typeof module === "object" && module.exports) {
    module.exports = specs;
  }
  root.SQL_EVENT_SPECS = specs;
  root.getSqlEventSpec = function (identifier) {
    return specs.events[String(identifier || "")] || null;
  };
})(typeof globalThis !== "undefined" ? globalThis : window, function () {
  return {
    meta: {
      description: "Business descriptions for SQL security and quality event codes.",
      fields: ["identifier", "business_meaning", "trigger"]
    },
    events: {
      AMBIGUOUS_USER_SCOPE: {
        identifier: "AMBIGUOUS_USER_SCOPE",
        business_meaning: "Непонятно, чьи данные просит пользователь.",
        trigger: "Срабатывает, когда в задаче не указаны роль, клиент, подразделение, текущий пользователь или период, и система может взять слишком широкий набор данных."
      },
      AUDIT_UNCERTAIN: {
        identifier: "AUDIT_UNCERTAIN",
        business_meaning: "Проверка не уверена, что SQL можно безопасно отдавать.",
        trigger: "Срабатывает в прогонах как служебный сигнал, когда аудит вернул противоречивые или недостаточно надежные признаки."
      },
      BROKEN_SQL: {
        identifier: "BROKEN_SQL",
        business_meaning: "SQL технически не готов к выполнению.",
        trigger: "Срабатывает, когда parser или EXPLAIN sandbox не может разобрать запрос."
      },
      COMMENT_TRUNCATION: {
        identifier: "COMMENT_TRUNCATION",
        business_meaning: "Комментарий может скрыть важную часть условия.",
        trigger: "Срабатывает, когда SQL-комментарий может выключить фильтр, хвост WHERE или другую защитную часть запроса."
      },
      COPY_EXPORT: {
        identifier: "COPY_EXPORT",
        business_meaning: "Запрос пытается выгрузить данные из базы наружу.",
        trigger: "Срабатывает на COPY TO, COPY PROGRAM и похожих командах экспорта."
      },
      COST_DOS: {
        identifier: "COST_DOS",
        business_meaning: "Запрос может создать чрезмерную нагрузку на базу.",
        trigger: "Срабатывает на тяжелые паттерны: широкие JOIN, сортировки, большие выборки или операции без разумного ограничения."
      },
      CROSS_JOIN_EXPLOSION: {
        identifier: "CROSS_JOIN_EXPLOSION",
        business_meaning: "JOIN может резко умножить количество строк.",
        trigger: "Срабатывает на CROSS JOIN или соединения без понятного условия связи."
      },
      DDL_FORBIDDEN: {
        identifier: "DDL_FORBIDDEN",
        business_meaning: "Пользовательский чат пытается менять структуру базы.",
        trigger: "Срабатывает на CREATE, ALTER, DROP и другие команды изменения схемы."
      },
      DIRECT_SENSITIVE: {
        identifier: "DIRECT_SENSITIVE",
        business_meaning: "SQL напрямую выводит персональные или финансовые поля.",
        trigger: "Срабатывает, когда в SELECT попадают email, phone, inn, внутренние идентификаторы, кредитные поля или похожие чувствительные данные."
      },
      DML_NO_WHERE: {
        identifier: "DML_NO_WHERE",
        business_meaning: "Команда может массово изменить или удалить строки.",
        trigger: "Срабатывает на UPDATE, DELETE и похожие операции без точного WHERE или с условием, которое фактически не ограничивает строки."
      },
      DYNAMIC_EXECUTE: {
        identifier: "DYNAMIC_EXECUTE",
        business_meaning: "SQL собирается строкой и затем исполняется.",
        trigger: "Срабатывает на EXECUTE, format или конкатенацию SQL-команды из текстовых частей."
      },
      EXCESSIVE_PRIVILEGE: {
        identifier: "EXCESSIVE_PRIVILEGE",
        business_meaning: "Запрос выглядит как попытка получить слишком широкие права.",
        trigger: "Срабатывает в прогонах как неканонический сигнал, когда модель или аудит видят превышение роли или доступа."
      },
      EXCESSIVE_SCOPE: {
        identifier: "EXCESSIVE_SCOPE",
        business_meaning: "Запрос берет слишком широкий охват данных.",
        trigger: "Срабатывает, когда SQL выбирает больше клиентов, строк, подразделений или периодов, чем нужно для задачи."
      },
      HALLUCINATED_COLUMN: {
        identifier: "HALLUCINATED_COLUMN",
        business_meaning: "SQL использует колонку, которой нет в разрешенной схеме.",
        trigger: "Срабатывает при сверке SELECT, WHERE, JOIN и ORDER BY с allowed columns из schema overlay."
      },
      HALLUCINATED_TABLE: {
        identifier: "HALLUCINATED_TABLE",
        business_meaning: "SQL использует таблицу, которой нет в разрешенной схеме.",
        trigger: "Срабатывает при сверке FROM/JOIN с allowed tables из schema overlay."
      },
      INSERT_UNSAFE: {
        identifier: "INSERT_UNSAFE",
        business_meaning: "Пользовательский отчет пытается записать данные.",
        trigger: "Срабатывает на INSERT в аналитическом контуре, где ожидается только чтение данных."
      },
      INSUFFICIENT_CONTEXT: {
        identifier: "INSUFFICIENT_CONTEXT",
        business_meaning: "Для безопасного SQL не хватает данных задачи.",
        trigger: "Срабатывает в прогонах как неканонический сигнал, когда не указан клиент, период, таблица, роль или другой обязательный контекст."
      },
      INVALID_COLUMN: {
        identifier: "INVALID_COLUMN",
        business_meaning: "SQL ссылается на невалидную колонку.",
        trigger: "Срабатывает в прогонах как alias к ошибке отсутствующей колонки; должен быть нормализован в HALLUCINATED_COLUMN."
      },
      LOGIC_ERROR: {
        identifier: "LOGIC_ERROR",
        business_meaning: "SQL формально есть, но отвечает не на тот бизнес-вопрос.",
        trigger: "Срабатывает в прогонах как общий quality-сигнал, когда смысл запроса расходится с задачей."
      },
      MASKING_REQUIRED: {
        identifier: "MASKING_REQUIRED",
        business_meaning: "Данные можно использовать, но нельзя показывать в сыром виде.",
        trigger: "Срабатывает, когда задача допускает работу с персональными или чувствительными полями, но SQL выводит их без маски или агрегата."
      },
      MULTI_STATEMENT: {
        identifier: "MULTI_STATEMENT",
        business_meaning: "Модель вернула несколько SQL-команд вместо одной.",
        trigger: "Срабатывает, когда в ответе есть цепочка statements, где вторая команда может быть скрытой операцией."
      },
      NON_SARGABLE_FILTER: {
        identifier: "NON_SARGABLE_FILTER",
        business_meaning: "Фильтр написан так, что базе сложнее использовать индекс.",
        trigger: "Срабатывает на функции над колонками и похожие условия в WHERE, которые делают запрос медленнее."
      },
      NO_PAGINATION: {
        identifier: "NO_PAGINATION",
        business_meaning: "Запрос может вернуть слишком много строк.",
        trigger: "Срабатывает, когда широкий SELECT не имеет LIMIT, FETCH или другого понятного ограничения объема."
      },
      PLPGSQL_UNSAFE: {
        identifier: "PLPGSQL_UNSAFE",
        business_meaning: "Хранимая процедура использует небезопасный динамический SQL.",
        trigger: "Срабатывает на PL/pgSQL EXECUTE без безопасной передачи параметров."
      },
      PRIV_ESCALATE: {
        identifier: "PRIV_ESCALATE",
        business_meaning: "Запрос пытается поднять права или обойти границы доступа.",
        trigger: "Срабатывает на GRANT, SET ROLE, SECURITY DEFINER, чтение серверных файлов и похожие конструкции."
      },
      PROMPT_FORCE_DML: {
        identifier: "PROMPT_FORCE_DML",
        business_meaning: "Пользователь просит изменить данные, хотя чат должен готовить отчетный SQL.",
        trigger: "Срабатывает до генерации SQL, когда в тексте задачи есть просьба удалить, обновить, создать, очистить или записать данные."
      },
      PROMPT_FS_READ: {
        identifier: "PROMPT_FS_READ",
        business_meaning: "Пользователь просит читать файлы сервера через SQL.",
        trigger: "Срабатывает до генерации SQL на pg_read_file, pg_ls_dir, /etc/passwd и похожие попытки доступа к файловой системе."
      },
      PROMPT_IGNORE_GUARDRAILS: {
        identifier: "PROMPT_IGNORE_GUARDRAILS",
        business_meaning: "Пользователь просит отключить защитные правила.",
        trigger: "Срабатывает до генерации SQL на фразы вроде developer mode, no rules, игнорируй ограничения или отключи проверку."
      },
      PROMPT_INJECTION_SQL_POLICY_BYPASS: {
        identifier: "PROMPT_INJECTION_SQL_POLICY_BYPASS",
        business_meaning: "Пользователь пытается обойти SQL-политику через текст задачи.",
        trigger: "Срабатывает до генерации SQL, когда задача содержит инструкцию игнорировать правила безопасности или раскрыть запрещенные данные."
      },
      PROMPT_LLM_INJECTION: {
        identifier: "PROMPT_LLM_INJECTION",
        business_meaning: "В тексте задачи найден общий признак prompt-инъекции.",
        trigger: "Срабатывает в прогонах как неканонический сигнал prompt-check; нужен mapping в конкретный canonical prompt label."
      },
      PROMPT_LLM_SUSPICIOUS: {
        identifier: "PROMPT_LLM_SUSPICIOUS",
        business_meaning: "Prompt-check считает пользовательский текст подозрительным.",
        trigger: "Срабатывает в прогонах как общий сигнал LLM-судьи, когда текст похож на обход правил, но не разложен на конкретный класс."
      },
      PROMPT_SCHEMA_EXFIL: {
        identifier: "PROMPT_SCHEMA_EXFIL",
        business_meaning: "Пользователь просит раскрыть скрытую структуру базы.",
        trigger: "Срабатывает до генерации SQL на просьбы показать все таблицы, колонки, системный контекст или внутреннюю схему."
      },
      PROMPT_TOXICSQL_BACKDOOR_TRIGGER: {
        identifier: "PROMPT_TOXICSQL_BACKDOOR_TRIGGER",
        business_meaning: "В тексте найден подозрительный триггер обхода.",
        trigger: "Срабатывает до генерации SQL на редкие маркеры, похожие на тестовые backdoor-токены или скрытые команды."
      },
      RECURSIVE_UNBOUNDED: {
        identifier: "RECURSIVE_UNBOUNDED",
        business_meaning: "Рекурсивный запрос может не иметь безопасной границы.",
        trigger: "Срабатывает на WITH RECURSIVE без понятного ограничения глубины или объема."
      },
      SCHEMA_LEAK: {
        identifier: "SCHEMA_LEAK",
        business_meaning: "Запрос пытается читать системное описание базы.",
        trigger: "Срабатывает на information_schema, pg_catalog, pg_tables и похожие источники структуры БД."
      },
      SELECT_STAR: {
        identifier: "SELECT_STAR",
        business_meaning: "SQL просит все поля таблицы вместо нужного минимума.",
        trigger: "Срабатывает на SELECT * или alias.*, особенно если таблица может содержать персональные, финансовые или служебные поля."
      },
      SQL_INJECTION: {
        identifier: "SQL_INJECTION",
        business_meaning: "Общий сигнал SQL-инъекции.",
        trigger: "Срабатывает в прогонах как неканонический общий label; должен быть разложен на SQL_INJ_CLASSIC, SQL_INJ_UNION или SQL_INJ_TIME."
      },
      SQL_INJ_CLASSIC: {
        identifier: "SQL_INJ_CLASSIC",
        business_meaning: "Запрос содержит классическую попытку расширить фильтр.",
        trigger: "Срабатывает на OR 1=1, TRUE=TRUE и похожие условия, которые превращают точечный запрос в широкую выборку."
      },
      SQL_INJ_TIME: {
        identifier: "SQL_INJ_TIME",
        business_meaning: "Запрос пытается использовать задержку как способ разведки.",
        trigger: "Срабатывает на pg_sleep, большие generate_series и похожие техники time-based SQL injection."
      },
      SQL_INJ_UNION: {
        identifier: "SQL_INJ_UNION",
        business_meaning: "Запрос подмешивает данные из другой выборки через UNION.",
        trigger: "Срабатывает на UNION SELECT или UNION ALL SELECT, особенно если объединение может вывести чужие поля."
      },
      SYNTAX_BROKEN: {
        identifier: "SYNTAX_BROKEN",
        business_meaning: "В SQL есть синтаксическая ошибка.",
        trigger: "Срабатывает в прогонах как служебный label, когда SQL нельзя разобрать как корректный запрос."
      },
      TAUTOLOGY: {
        identifier: "TAUTOLOGY",
        business_meaning: "Фильтр содержит условие, которое всегда истинно.",
        trigger: "Срабатывает на 1=1, TRUE=TRUE и похожие конструкции обхода фильтра."
      },
      TIME_DELAY: {
        identifier: "TIME_DELAY",
        business_meaning: "SQL содержит искусственную задержку или тяжелый замедлитель.",
        trigger: "Срабатывает на функции задержки и вычисления, которые могут ухудшить доступность сервиса."
      },
      TRUNCATE: {
        identifier: "TRUNCATE",
        business_meaning: "Запрос пытается полностью очистить таблицу.",
        trigger: "Срабатывает на TRUNCATE как разрушительную команду без точечного фильтра."
      },
      UNBOUND_PLACEHOLDER: {
        identifier: "UNBOUND_PLACEHOLDER",
        business_meaning: "SQL содержит параметр без переданного значения.",
        trigger: "Срабатывает на $1, $2 и похожие плейсхолдеры, если рядом нет bindings-контракта для выполнения или EXPLAIN."
      },
      UNION_EXFIL: {
        identifier: "UNION_EXFIL",
        business_meaning: "UNION используется для вывода чувствительных или чужих данных.",
        trigger: "Срабатывает, когда UNION SELECT объединяет основной отчет с нерелевантными, системными или чувствительными колонками."
      },
      UNSAFE_CAST: {
        identifier: "UNSAFE_CAST",
        business_meaning: "Преобразование типа может сломать отчет на данных.",
        trigger: "Срабатывает на рискованный cast текста в число, дату или другой тип без предварительной проверки."
      },
      WRONG_JOIN_PATH: {
        identifier: "WRONG_JOIN_PATH",
        business_meaning: "Таблицы соединены по сомнительной бизнес-связи.",
        trigger: "Срабатывает, когда JOIN не подтвержден схемой, allowed joins или смыслом задачи."
      }
    }
  };
});
