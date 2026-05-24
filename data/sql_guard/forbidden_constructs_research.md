# Исследование и таксономия SQL Guard

## 1. Общий обзор

Этот документ описывает таксономию правил SQL Guard — детерминированного слоя безопасности SQL-запросов,
который работает **до LLM и до RAG**, только на основе AST и строкового анализа.

Цели:
- блокировка SQL-инъекций
- защита от утечек данных
- запрет опасных PostgreSQL-конструкций
- контроль привилегий
- защита от перегрузки базы (DoS на уровне SQL)

---

## 2. Основные источники

### OWASP SQL Injection Cheat Sheet
https://cheatsheetseries.owasp.org/cheatsheets/SQL_Injection_Prevention_Cheat_Sheet.html

### CWE-89: SQL Injection
https://cwe.mitre.org/data/definitions/89.html

### PortSwigger Web Security Academy (SQL Injection)
https://portswigger.net/web-security/sql-injection

### HackTricks PostgreSQL Injection
https://book.hacktricks.xyz/pentesting-web/sql-injection/postgresql-injection

### sqlmap (реальные техники атак)
https://github.com/sqlmapproject/sqlmap

### PostgreSQL официальная документация
https://www.postgresql.org/docs/

### MITRE ATT&CK
- https://attack.mitre.org/techniques/T1190/
- https://attack.mitre.org/techniques/T1505/001/

---

## 3. Карта правил по категориям

---

## 3.1 Инъекции и blind-инъекции

### blind_inj
Источники:
- OWASP SQL Injection
- PortSwigger Blind SQL Injection
- sqlmap time-based техники

Сюда входят:
- OR 1=1
- TRUE = TRUE
- тавтологии

---

### pg_sleep_long
Источники:
- sqlmap time-based SQLi
- PortSwigger Time-based SQL Injection

PostgreSQL:
- pg_sleep()

---

### blind_string_concat
Источники:
- OWASP SQLi
- PortSwigger логические атаки через строки

---

### comment_payload_bypass
Источники:
- OWASP SQLi (обход через комментарии)
- sqlmap tamper payloads

---

## 3.2 UNION-эксфильтрация

### union_cast_exfil
Источники:
- PortSwigger UNION-based SQLi
- CWE-89
- sqlmap UNION атаки

PostgreSQL:
- CAST()

---

### union_null_padding
Источники:
- sqlmap выравнивание колонок
- PortSwigger UNION enumeration

---

## 3.3 Утечка структуры базы

### information_schema_access
Источники:
- OWASP reconnaissance этап SQLi
- PostgreSQL information_schema

---

### pg_catalog_access
Источники:
- PostgreSQL системный каталог

---

## 3.4 Доступ к файловой системе PostgreSQL

### pg_read_file / pg_read_binary_file / pg_ls_dir
Источники:
- PostgreSQL документация (server file access)
- HackTricks PostgreSQL exploitation

---

## 3.5 RCE (удалённое выполнение кода)

### copy_from_program
Источники:
- PostgreSQL COPY ... PROGRAM
- HackTricks PostgreSQL RCE
- MITRE ATT&CK T1190

---

### dblink_usage
Источники:
- PostgreSQL расширение dblink
- HackTricks lateral connection abuse

---

### lo_import / lo_export
Источники:
- PostgreSQL large object subsystem
- HackTricks file exfiltration

---

## 3.6 DDL (изменение структуры БД)

### create_object / drop / truncate / alter_*
Источники:
- PostgreSQL DDL документация
- OWASP (риск разрушения данных)

---

## 3.7 DML (опасные операции записи)

### insert_foreign_table
### update_statement
### delete_statement

Источники:
- PostgreSQL DML документация
- OWASP: нарушение модели доступа
- модель read-only аналитического контура

---

## 3.8 Привилегии

### set_role / reset_role
### grant / revoke

Источники:
- PostgreSQL система ролей
- OWASP authorization bypass

---

## 3.9 Перегрузка базы (DoS)

### cost_dos
Источники:
- PostgreSQL EXPLAIN / cost model
- PortSwigger heavy query attacks

---

### cross_join_explosion
Источники:
- теория реляционных баз данных (декартово произведение)
- PostgreSQL JOIN документация

---

## 3.10 Multi-statement

### multi_statement
Источники:
- OWASP stacked queries
- sqlmap stacked injection

---

## 4. Что сознательно НЕ блокируем жёстко

### WITH RECURSIVE
Статус: WARNING

Причина:
- используется в аналитике (иерархии, графы)
- может быть легитимным

---

### GROUP BY / ORDER BY / агрегаты
Статус: разрешено

Причина:
- базовая аналитическая функциональность

---

## 5. Исследовательская зона (пока не блокируем)

- JIT оптимизация атак
- манипуляции cost planner
- обход индексов через планировщик
- параллельные execution edge cases

---

## 6. Принципы системы

- основной слой: AST (pglast)
- fallback: regex только для обфускации
- никаких LLM в enforcement path
- режим read-only как базовый
- deterministic rules engine

---