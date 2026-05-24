Generated at: 2026-05-24 18:10:00 MSK

# Overlay v2 — quality candidates (audit)

Audit-проход по `deploy/schema_overlay_v2.json` перед targeted rewrite. Это inventory слабых мест, ещё не правка. Источники: сам overlay, `TASK-3/marina-case3-rag/schema.json` (исходные комментарии), `data/schema_upgrade/audit_baseline.md` (state v1→v2).

## Сводные метрики

| Показатель | Значение |
|---|---|
| Таблиц | 60 |
| Колонок | 1872 |
| Колонок с пустыми aliases | 0 |
| Колонок с пустыми nl_phrases | 0 |
| Таблиц с business_description короче 40 символов | 0 |
| Колонок с description короче 16 символов | 116 |
| Колонок с description = одно общее слово (Сумма / Валюта / Период / Описание / Тип / Статус / Дата / Владелец / Идентификатор) | 11 |
| Колонок-PII (column.category == "pii") | 24 |
| Таблиц с table.pii_tags ≠ v1 | 1 (sys_employee) |
| Таблиц с изменёнными approved_join_keys | 0 |

Всего слабых descriptions (sum, без двойного счёта): **123** (116 + 11 − 4 пересечений).

## Категории слабости

### S1. Description = одно слово без объекта (high impact)

Слова без контекста таблицы. Модель будет матчить «сумма», «период», «валюта» одинаково в десятках колонок и не различит их в WHERE/SELECT. 11 случаев:

| Таблица | Колонка | Description |
|---|---|---|
| sys_obj_type | note | Описание |
| count_turnover | link_cnt_owner | Владелец |
| count_turnover | period_st_end | Период |
| credit_contract | sel_curr | Валюта |
| fs_file | ff_note | Описание |
| product_pricing | scp_sublimit_sum | Сумма |
| scp_amd_product | scp_sublimit_val | Валюта |
| scp_amd_product | scp_general_amount | Сумма |
| scp_decision_quest | scp_sublimit_sum | Сумма |
| scp_decision_quest | scp_sublimit_val | Валюта |
| tbs_type | identif | Идентификатор |

### S2. Description короткое, но с минимальным объектом (medium impact)

Колонка имеет нечто кроме одного слова, но всё ещё <16 символов и без контекста таблицы (например «Сумма транша», «Срок договора», «Дата документа»). 116 случаев в таблицах:

`afhd_ac_trans_link, corp_tech_application, count_turnover, credit_contract, dict_product, fs_file, mler_application, offices_psb, participant_app, prod_change_params, prod_commissions, prod_guarantees, product_pricing, scp_amd_product, scp_application, scp_collateral_app, scp_decision_quest, scp_gov_program_dict, scp_part_sec_expertise, scp_prod_comm_dict, scp_prod_guar_dict, scp_prod_guarant_dict, scp_project_ans, scp_sec_check_res, scp_sec_expertise, sys_algorithm, sys_company, sys_employee, sys_obj_type, tbs_type, yaig_client_gen_agr, yaig_client_guarantee`.

Полный список с column-by-column контекстом сохранён в audit-выгрузке (см. tool-results), сюда не дублируем — большая таблица.

### S3. Дублирующиеся короткие nl_phrases (medium impact)

`nl_phrases` у этих 123 колонок сейчас просто mirror-описание (то же слово в массиве из 1 элемента). Это «съедает» бизнес-смысл и провоцирует false-positive matches при RAG retrieval. Пример паттерна `"description": "Сумма"`, `"nl_phrases": ["Сумма"]` — встречается ~11 раз только в категории S1, плюс десятки в S2.

### S4. Англоязычные описания (low impact, локальный кластер)

Несколько технических полей контактных таблиц имеют англоязычное description, что вырывается из общего русскоязычного контракта overlay:

| Таблица | Колонка | Description |
|---|---|---|
| sys_company | attr_web_site | Website |
| sys_company | company_registrator | Registrar |
| sys_company | company_tax_authority | Tax authority |
| sys_employee | skype | Skype |
| sys_employee | time_zone | Time zone |
| sys_employee | sys_image | Photo |

Эти поля не PII, но описания не помогают модели русскоязычного запроса.

## PII diff vs v1

Изменилась только одна таблица — `sys_employee`:

| Поле | v1 | v2 | Комментарий |
|---|---|---|---|
| `email` | ✅ | ✅ | контакт сотрудника, оставлен |
| `email_confirmed` | ✅ | ❌ | флаг подтверждения e-mail; технический булевый признак, не само значение → разумно исключить из PII |
| `phone` | ✅ | ✅ | контакт сотрудника, оставлен |
| `phone_confirmed` | ✅ | ❌ | флаг подтверждения телефона; технический булевый признак → разумно исключить из PII |
| `inner_emp_phone` | ✅ | ✅ | внутренний рабочий телефон, оставлен |
| `first_name` | ✅ | ✅ | имя |
| `second_name` | ✅ | ✅ | отчество |
| `sur_name` | ✅ | ✅ | фамилия |
| `birthday` | ❌ | ✅ | дата рождения — корректно добавлено (152-ФЗ ПДн) |

Чистый эффект v2: убраны 2 булевых флага подтверждения контакта (которые сами по себе не раскрывают идентичность), добавлена дата рождения (реальная ПДн). PII tags в `sys_employee` стали точнее.

Других таблиц с изменением table-level `pii_tags` нет. На column-level (`category == "pii"`) — 24 поля в 11 таблицах, состав ниже:

```
corp_tech_application: credit_logic_id, cred_depart_opinion_txt
count_turnover: input_balace_credit
credit_contract: credit_contract_number, credit_product, uid_credit
fs_file: ff_inner_name
scp_decision_quest: credit_report_class
scp_dict_product_na: credit_product
scp_project_ans: credit_line_term
scp_sec_check_res: credit_history_comm, sf_credit_history_comm
sys_employee: first_name, second_name, sur_name, email, birthday, phone, email_confirmed, phone_confirmed, inner_emp_phone
sys_company: inn, attr_email, contact_phone
```

Замечание: на column.category некоторые поля (`*_confirmed`) ещё помечены `pii` в коде колонки, хотя из table-level `pii_tags` они убраны. Это локальное расхождение, **в этой задаче не исправляется** (см. границу TZ: «Не менять PII tags, labels, severity ... без отдельного обоснования»). Зафиксировано в deferred.

## План рерайта (для шага 2)

Targeted rewrite затрагивает только descriptions + nl_phrases (опционально aliases, если они дублируют description) у:

1. Все 11 колонок S1 — приоритет 1.
2. Все 116 колонок S2 — приоритет 2.
3. 6 англоязычных колонок S4 — приоритет 3, дополнительно к S2.

Что не трогаем:
- `category` колонок,
- `pii_tags` таблиц,
- `approved_join_keys`, `allowed_ops`, `denied_ops`,
- технические имена таблиц и колонок,
- column.category == "pii" → text может остаться коротким (это PII-поле, описание не критично; не несём в targeted set).

Каждое улучшение строится из:
- бизнес-контекста таблицы (`business_description`),
- имени колонки (`_id` → FK, `_sum` → сумма в рублях, `_str` → строковое представление, `_val` → значение, `_date` → дата, `_num` → номер, `link_*` → FK-связь, `is_*` / `feat_*` → флаг),
- исходного типа из `schema.json` Марины (numeric / bigint / timestamp / character varying / text / smallint).

Никаких бизнес-фактов «из головы» сверх этого.
