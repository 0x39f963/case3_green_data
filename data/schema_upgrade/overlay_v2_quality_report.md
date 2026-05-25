Generated at: 2026-05-25 13:32:00 MSK

# Overlay v2 Quality Report

## summary

Канонический отчёт по quality upgrade `deploy/schema_overlay_v2.json` (TZ-01). Расширенный working-отчёт сохранён в `.cursor/!tmp/!ARTEFACTS/2026-05-24/quality-overlay-v2/overlay_v2_quality_report.md` (за пределами repo). Этот файл выполняет acceptance criteria TZ-01 (минимум 20 before/after примеров) и подтверждает overlay v2 как валидный data-артефакт перед integration branch.

Post-quality audit (2026-05-25) дополнительно поправил `column.category` у `sys_employee.email_confirmed` и `sys_employee.phone_confirmed` (`pii` → `status`, см. `.cursor/!tmp/!TZ/2026-05-25/post_quality_audit_tz/00-audit-report.md` F2). Сами `pii_tags` не менялись.

## changed_files

| File | What changed | Why |
|---|---|---|
| `deploy/schema_overlay_v2.json` | 116 weak column descriptions и `nl_phrases` улучшены в TZ-01; в post-audit правки `sys_employee.email_confirmed/phone_confirmed.category` (`pii`→`status`) | Снизить broad matches на generic-словах; восстановить семантическую целостность column.category↔pii_tags |
| `deploy/schema_overlay.schema.v2.json` | Добавлен v2 JSON Schema с `minItems: 1` для aliases/nl_phrases | Детерминированная JSON-валидация v2 формата |
| `scripts/validate_schema_overlay.py` | Поддержка `--path`, auto-select v1/v2 schema; в post-audit добавлены проверки column.category=pii ↔ pii_tags, short/generic descriptions | Acceptance command воспроизводим; quality checks автоматизированы |
| `data/schema_upgrade/overlay_v2_quality_report.md` | Расширен до 20 before/after | Покрыть acceptance criteria TZ-01 |

## before_after_examples

| # | Поле | Before | After (срез) | Эффект |
|---:|---|---|---|---|
| 1 | `credit_contract.sel_curr` | `"Валюта"` | `"FK на справочник валют — валюта кредитного договора..."` | разделение от других «валюта» в SCP/УАиГ |
| 2 | `count_turnover.period_st_end` | `"Период"` | `"Период оборотов ОСВ в строковом виде (например 'YYYY-MM'..."` | привязка к ОСВ |
| 3 | `count_turnover.link_cnt_owner` | `"Владелец"` | `"FK на справочник владельцев записи ОСВ (подразделение или ответственный сотрудник)..."` | разделение от «владелец компании/договора» |
| 4 | `scp_amd_product.scp_general_amount` | `"Сумма"` | `"Общая запрашиваемая сумма по продукту РУМ в составе заявки СКП (в рублях)..."` | разделение от ~10 других «Сумма» |
| 5 | `sys_obj_type.note` | `"Описание"` | `"Текстовое описание типа объекта CRM..."` | привязка к CRM-метаданным |
| 6 | `tbs_type.identif` | `"Идентификатор"` | `"Бизнес-идентификатор типа ОСВ (отдельный от технического id, например мнемокод)..."` | разделение от технического id |
| 7 | `credit_contract.contracttype` | `"_Вид договора"` | `"Дублирующее техническое поле вида кредитного договора... Для новых выборок предпочтительно contract_type."` | модель предупреждена о legacy |
| 8 | `dict_product.summ_tranche` | `"Сумма транша"` | `"Параметр продукта — допустимая сумма транша по продукту (в рублях, как правило максимальная)..."` | параметр продукта, не заявки |
| 9 | `scp_decision_quest.*` (10 полей) | короткие | везде «в составе вопроса на решение СКП» | разделение от scp_amd_product/product_pricing |
| 10 | `scp_project_ans.*` (11 полей) | короткие | везде «по одобренному решению» | разделение от заявочных таблиц |
| 11 | `sys_employee.skype/time_zone/sys_image` | англоязычные | русскоязычные + указание PII handling | контракт overlay восстановлен |
| 12 | `sys_company.attr_web_site/.../date_of_consent` | англ./короткие | русскоязычные + у `date_of_consent` явная отсылка к 152-ФЗ | risk-критичный контекст |
| 13 | `participant_app.bch_refuse` | `"Отказ от БКИ"` | `"Флаг отказа участника-клиента от запроса в БКИ... 1 = клиент отказал в запросе..."` | расшифровка БКИ + домен значений 1/0 |
| 14 | `offices_psb.feat_org` | `"Признак: 1 - ГО"` | `"Флаг головного офиса (1 = головной офис, 0 = иное подразделение)..."` | расшифровка ГО + оба значения флага |
| 15 | `scp_gov_program_dict.*` (3 поля) | «Маржа», «Ставка клиента», «Субсидия» | привязка к госпрограмме + единица измерения (п.п. / годовых) | разделение от обычной маржи/ставки |
| 16 | `yaig_client_guarantee.*` (6 полей) | короткие | привязка к банковской гарантии УАиГ | разделение от кредитных договоров |
| 17 | `afhd_ac_trans_link.multiply_val/cnt_count/discount` | «Мультипликатор», «Контрагент», «Дисконт» | привязка к АФХД-детализации | разделение от scp/credit таблиц |
| 18 | `scp_collateral_app.testnumber` | `"Тест_атрибут"` | `"Технический тестовый атрибут... НЕ предназначено для бизнес-аналитики; не использовать в SELECT для отчётов."` | защита от попадания в SELECT |
| 19 | `fs_file.ff_id_881752` | `"Дата документа"` | пояснение про auto-generated id-имя поля + назначение (фильтрация по дате документа) | модель понимает странное имя |
| 20 | `scp_amd_product.curr_loan_debt` | `"Валюта ОСЗ"` | `"FK на валюту остатка ссудной задолженности (ОСЗ) по продукту..."` | расшифровка ОСЗ + явное FK |
| 21 (post-audit) | `sys_employee.email_confirmed` | `category: "pii"` + description «Чувствительные данные...» | `category: "status"` + description «Булев флаг подтверждения email...» | устранение column.category↔pii_tags inconsistency |
| 22 (post-audit) | `sys_employee.phone_confirmed` | `category: "pii"` + description «Чувствительные данные...» | `category: "status"` + description «Булев флаг подтверждения телефона...» | то же |

## validation_summary

| Command | Result | Notes |
|---|---|---|
| `python3 scripts/validate_schema_overlay.py --path deploy/schema_overlay_v2.json` | PASS | 60 tables, v2 schema selected; post-audit добавлены quality-проверки category/pii + short/generic descriptions |
| `python3 -m json.tool deploy/schema_overlay_v2.json` | PASS | JSON valid |
| `python3 -m json.tool deploy/schema_overlay.schema.v2.json` | PASS | V2 schema JSON valid |
| Overlay metrics script | PASS | 60 tables, 1872 columns, 0 short descriptions, 0 generic descriptions, 0 empty aliases, 0 empty nl_phrases, 0 column.category=pii outside pii_tags |

## unchanged_security_contract

- `pii_tags` не менялись post-audit раундом. Diff v1→v2 (унаследованный из PR #5) задокументирован отдельно: `.cursor/!tmp/!ARTEFACTS/2026-05-25/post_quality_audit/01-pii-tag-diff-v1-v2.md`.
- `allowed_ops`, `denied_ops`, `approved_join_keys` не менялись.
- `column.category` менялся только для двух колонок (TASK 2 post-audit), оба случая семантически корректны.

## deferred_items

| Item | Reason |
|---|---|
| Runtime use в `app/rag_adapter.py` | Out of quality-upgrade scope |
| Runtime guard wiring | Out of quality-upgrade scope |
| FAISS/RAG rebuild | Integration branch step после artifact acceptance |
| Security review PII tag drift v1→v2 (34 колонки) | Security-sensitive решение от исходного PR #5; см. отдельный отчёт |
| Single-word generic nl_phrases вне watchlist (23 случая) | Низкий приоритет; не блокирует integration |

## verdict

READY_FOR_INTEGRATION для data-artifact handoff. Перед runtime integration требуется security-review PII tag drift (см. отдельный отчёт).
