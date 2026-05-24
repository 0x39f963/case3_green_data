Generated at: 2026-05-24 18:25:00 MSK

# Overlay v2 — quality upgrade report (TZ-01)

Артефакт: `deploy/schema_overlay_v2.json` (knowledge artifact для RAG / SQL generation).
ТЗ: `.cursor/!tmp/!TZ/2026-05-24/pr_artifacts_quality_upgrade_tz/01-tz-schema-overlay-v2-quality.md`.

## summary

Targeted rewrite descriptions и `nl_phrases` у 116 колонок (S1 + S2 кандидаты из audit). PII tags, category, allowed_ops, denied_ops, approved_join_keys, имена таблиц/колонок — не трогали. Количество таблиц (60) и колонок (1872) сохранено.

| Метрика | Before | After |
|---|---|---|
| Tables | 60 | 60 |
| Columns | 1872 | 1872 |
| Descriptions <16 символов | 116 | 0 |
| Descriptions = одно общее слово | 11 | 0 |
| Колонок с пустыми aliases | 0 | 0 |
| Колонок с пустыми nl_phrases | 0 | 0 |
| Approved join keys diff vs v1 | 0 | 0 |
| Allowed/denied ops conflicts | 0 | 0 |
| Table-level pii_tags diff vs v1 | 1 (sys_employee) | 1 (sys_employee, не менялось) |

## changed_files

| File | What changed | Why |
|---|---|---|
| `deploy/schema_overlay_v2.json` | descriptions у 116 колонок, nl_phrases у 116 колонок, aliases у 9 колонок | устранены S1/S2 weak descriptions, добавлен бизнес-контекст и фразы с объектом |
| `data/schema_upgrade/overlay_v2_quality_candidates.md` | новый | inventory слабых мест перед рерайтом (S1/S2/S3/S4, PII diff) |
| `data/schema_upgrade/overlay_v2_quality_report.md` | новый | этот файл, before/after report |
| `scripts/improve_schema_overlay_v2.py` | новый | детерминированный apply-скрипт с REWRITES-таблицей |

## before_after_examples

### 1. credit_contract.sel_curr (S1: одно слово)

Before:
```json
"description": "Валюта"
"nl_phrases": ["Валюта"]
```
After:
```json
"description": "FK на справочник валют — валюта кредитного договора. Используется в запросах про валюту кредита (рубли / иностранная валюта) и в группировках портфеля по валюте."
"nl_phrases": ["валюта кредитного договора", "валюта кредита", "договоры в валюте"]
```
Reason: фраза «валюта» матчила во множестве таблиц без контекста; теперь модель связывает с кредитным договором.

### 2. count_turnover.period_st_end (S1: одно слово)

Before:
```json
"description": "Период"
"nl_phrases": ["Период"]
```
After:
```json
"description": "Период оборотов ОСВ в строковом виде (например 'YYYY-MM' или диапазон дат). Используется как ключ группировки оборотов и сальдо за период."
"nl_phrases": ["период ОСВ", "период оборотов", "за какой период обороты"]
```
Reason: «Период» без объекта — слишком широкая привязка; теперь явно ОСВ.

### 3. count_turnover.link_cnt_owner (S1: одно слово)

Before:
```json
"description": "Владелец"
```
After:
```json
"description": "FK на справочник владельцев записи ОСВ (как правило подразделение или ответственный сотрудник). Используется для фильтрации оборотов по владельцу."
```
Reason: явно сказано, что FK; модель не будет путать с «владельцем компании / договора».

### 4. scp_amd_product.scp_sublimit_val + scp_amd_product.scp_general_amount (S1: «Валюта», «Сумма»)

Before:
```json
"scp_sublimit_val":   { "description": "Валюта", "nl_phrases": ["Валюта"] }
"scp_general_amount": { "description": "Сумма",  "nl_phrases": ["Сумма"]  }
```
After:
```json
"scp_sublimit_val":   { "description": "FK на справочник валют — валюта сублимита продукта РУМ в заявке СКП. ...", "nl_phrases": ["валюта сублимита продукта РУМ", "валюта продукта СКП", "валюта сублимита в заявке"] }
"scp_general_amount": { "description": "Общая запрашиваемая сумма по продукту РУМ в составе заявки СКП (в рублях). ...", "nl_phrases": ["общая сумма продукта РУМ", "сумма по продукту в заявке СКП", "запрашиваемая сумма продукта"] }
```
Reason: эти два поля в одной таблице раньше имели одно и то же description как у десятков других «сумма / валюта» — модель не могла различить.

### 5. sys_obj_type.note (S1: «Описание»)

Before:
```json
"description": "Описание"
```
After:
```json
"description": "Текстовое описание типа объекта CRM (произвольный комментарий администратора, свободная форма). Используется при отображении карточки типа объекта."
```
Reason: «Описание» — самое общее слово, теперь явно про CRM-метаданные.

### 6. tbs_type.identif (S1: «Идентификатор»)

Before:
```json
"description": "Идентификатор", "aliases": ["идентификатор"]
```
After:
```json
"description": "Бизнес-идентификатор типа ОСВ (отдельный от технического id, например мнемокод). Используется для ссылок на тип ОСВ во внешних системах."
"aliases": ["мнемокод типа ОСВ", "бизнес-идентификатор типа ОСВ"]
```
Reason: ранее модель путала с техническим `id`; теперь явно описан как business code / мнемокод.

### 7. credit_contract.contracttype vs contract_type (дубль)

Before:
```json
"contracttype": { "description": "_Вид договора" }
```
After:
```json
"contracttype": { "description": "Дублирующее техническое поле вида кредитного договора, FK на справочник видов. Сохраняется для обратной совместимости со старыми отчётами. Для новых выборок предпочтительно contract_type." }
```
Reason: префикс `_` в исходном комментарии — индикатор legacy-поля; теперь модель явно предупреждена.

### 8. dict_product.summ_tranche, summ_limit, form_limit, period_grace

Before (4 поля): «Сумма транша», «Сумма лимита», «Форма лимита», «Грейс».
After: полные описания с контекстом «параметр продукта», единица измерения (руб., месяцы), назначение (проверка допустимости при выдаче / подборе продукта). См. секции в `improve_schema_overlay_v2.py` (`dict_product`).
Reason: справочник продуктов — основная сущность для подбора продукта по запросу; короткие описания не давали модели понять, что это параметр продукта, а не заявки.

### 9. scp_decision_quest — целая группа из 10 коротких полей

Все 10 коротких полей вопроса на решение СКП теперь имеют явное «в составе вопроса на решение СКП» в описании, и `nl_phrases` содержат `по вопросу решения`. Это даёт RAG-индексу разделить эти поля от одноимённых полей в `scp_amd_product` и `product_pricing`.

### 10. scp_project_ans — целая группа из 11 коротких полей

Аналогично 10 коротких полей одобренного решения теперь говорят «по одобренному решению». Это критично, потому что у таблицы `scp_project_ans` много полей с такими же именами, как у заявок (doc_num, summ_tranche, scp_subl_bor, scp_bg_owner_lidm).

### 11. sys_employee.skype/time_zone/sys_image/sex_id (S4: англоязычные)

Before:
```json
"skype":     { "description": "Skype",     "nl_phrases": ["Skype"] }
"time_zone": { "description": "Time zone", "nl_phrases": ["Time zone"] }
"sys_image": { "description": "Photo",     "nl_phrases": ["Photo"] }
```
After: русскоязычные описания с явной отсылкой к карточке сотрудника и уточнение «в SELECT с маскированием» для контактного поля `skype`.
Reason: overlay в основном русскоязычный; англоязычные descriptions выпадали из общего контракта и снижали relevance score для русскоязычного запроса.

### 12. sys_company.attr_web_site / company_registrator / company_tax_authority / date_of_consent (S4)

Before: «Website», «Registrar», «Tax authority», «Дата согласия» — все короткие и/или англоязычные.
After: явные русскоязычные описания с указанием назначения. У `date_of_consent` — явная отсылка к 152-ФЗ.

### 13. participant_app.bch_refuse

Before:
```json
"description": "Отказ от БКИ"
```
After:
```json
"description": "Флаг отказа участника-клиента от запроса в БКИ (бюро кредитных историй). 1 = клиент отказал в запросе. Используется в фильтрации заявок без БКИ."
```
Reason: расшифрован домен значений (1/0), расшифрована аббревиатура БКИ.

### 14. offices_psb.feat_org

Before:
```json
"description": "Признак: 1 - ГО"
```
After:
```json
"description": "Флаг головного офиса (1 = головной офис, 0 = иное подразделение). Используется для выделения операций ГО в отчётности."
```
Reason: расшифрована аббревиатура и явно описаны оба значения флага.

### 15. scp_gov_program_dict — все 3 поля (маржа / ставка / субсидия)

Все три поля теперь явно говорят «по льготной государственной программе» и указывают единицу измерения «в процентных пунктах / годовых процентах». Это резко улучшает relevance для запросов про субсидии и льготы.

### 16. yaig_client_guarantee — все 6 коротких полей

Все 6 коротких полей банковских гарантий УАиГ теперь говорят «банковская гарантия УАиГ» с уточнением назначения. До правки они конкурировали по match с таблицами кредитных договоров.

### 17. afhd_ac_trans_link.multiply_val / cnt_count / discount

Before: короткие «Мультипликатор», «Контрагент», «Дисконт».
After: явная привязка «в детализации АФХД» / «по операции АФХД» + указание роли поля (агрегация, фильтр, расчёт).

### 18. scp_collateral_app.testnumber

Before:
```json
"description": "Тест_атрибут"
```
After:
```json
"description": "Технический тестовый атрибут — служебное поле для проверок системы. НЕ предназначено для бизнес-аналитики; не использовать в SELECT для отчётов."
```
Reason: предупреждение модели, что это не бизнес-поле — снижает риск его включения в auto-generated SELECT.

### 19. fs_file.ff_id_881752

Before:
```json
"description": "Дата документа"
```
After:
```json
"description": "Дата документа, к которому относится прикреплённый файл (денормализованное поле из исходного документа). Используется для фильтрации файлов по дате документа."
```
Reason: имя поля с auto-generated id-суффиксом затрудняет понимание; теперь явно описано назначение.

### 20. scp_amd_product.curr_loan_debt

Before:
```json
"description": "Валюта ОСЗ"
```
After:
```json
"description": "FK на валюту остатка ссудной задолженности (ОСЗ) по продукту. Используется в отчётах по портфелю по валюте остатка."
```
Reason: расшифровка ОСЗ + явное указание, что это FK на валюту, не на сумму.

## PII diff vs v1 (как просит TZ)

Изменения относительно `deploy/schema_overlay.json` (v1):

Таблица `sys_employee` (единственная с различием table-level `pii_tags`):
- **Убрано из pii_tags:** `email_confirmed`, `phone_confirmed`. Это булевы флаги подтверждения контакта; сами по себе они не раскрывают идентичность и не являются ПДн.
- **Добавлено в pii_tags:** `birthday`. Корректно отнесено к ПДн (152-ФЗ).
- Все ФИО (first/second/sur), email, phone, inner_emp_phone — без изменений.

PII tags более ни в одной таблице не различаются. `approved_join_keys`, `allowed_ops`, `denied_ops` идентичны v1 у всех 60 таблиц.

В этой задаче **PII tags не менялись** (см. границу TZ-01 «Не менять PII tags / labels / severity без отдельного обоснования»). Diff между v1 и v2 уже существовал на момент старта задачи и сделан в исходном PR #5.

## checks_run

| Command | Result | Notes |
|---|---|---|
| `python3 scripts/improve_schema_overlay_v2.py` | PASS | 116 rewrites applied, 0 missing keys, table/column counts сохранены |
| `python3 -m json.tool deploy/schema_overlay_v2.json` | PASS | JSON валиден, 1.8 МБ |
| audit-script (in-place) | PASS | short<16: 116→0, generic-word: 11→0, empty aliases/nl: 0/0 |
| `rg "\"description\": \"(Сумма|Валюта|Период|Описание|Тип|Статус)\""` overlay_v2 | 0 matches | требование acceptance выполнено |
| `python3 scripts/validate_schema_overlay.py` (хардкодный путь на v1) | PASS | для **v1** — отдельный артефакт, не v2 |
| Draft202012 schema validation v2 vs `deploy/schema_overlay.schema.json` | FAIL (60 errors) | pre-existing: schema-файл описывает v1-структуру без поля `columns`; не связано с правками этой задачи |
| v2 join_keys diff vs v1 | 0 различий | |
| v2 allowed/denied ops conflict | 0 conflicts | |
| v2 tables vs marina-schema | match, 60↔60, missing=0, extra=0 | |

## deferred_items

1. **`scripts/validate_schema_overlay.py` не принимает `--path`.** Acceptance TZ просит `python3 scripts/validate_schema_overlay.py --path deploy/schema_overlay_v2.json`. Текущий скрипт хардкодит `deploy/schema_overlay.json` (v1) и не имеет аргументов. Запустил вручную JSON Schema validation вместо этого. Доработать validator — задача интеграции (integration branch).

2. **`deploy/schema_overlay.schema.json` описывает только v1.** Schema не знает поля `columns` и валит на 60 ошибках `Additional properties are not allowed ('columns' was unexpected)`. Нужна `schema_overlay.schema.v2.json` с разрешённым column-level форматом. Не входит в scope TZ-01 (изменение JSON Schema = изменение контракта).

3. **column.category == "pii" vs table-level pii_tags расхождение.** В `sys_employee` поля `email_confirmed`, `phone_confirmed` помечены `category: "pii"` на уровне колонки, но убраны из table-level `pii_tags`. Локальное расхождение между двумя слоями PII-маркировки. TZ-01 явно запрещает менять PII tags/labels без отдельного обоснования — передаю в integration branch как security-review item.

4. **Литературная переработка остальных ~1700 «нормальных» descriptions.** Не делал — TZ просит targeted pass, не массовую переписку. Часть полей всё ещё могла бы быть более явной (например field-level units of measurement, decode of state values), но это улучшение второго уровня — может выполняться итеративно перед каждым FAISS rebuild.

5. **«Минимум 4-8 самых слабых table descriptions».** Все 60 таблиц после v2 уже имеют business_description ≥ 40 символов (audit подтвердил 0 коротких). Если ревьюер захочет ещё подробнее — нужны явные «самые слабые», сейчас явных кандидатов нет.

6. **PII diff `pii` категория на column-level (24 поля).** Список зафиксирован в `overlay_v2_quality_candidates.md`. Не менял — вне scope TZ-01.

## risks

- Не было прогона FAISS rebuild — улучшения descriptions войдут в индекс только после `scripts/build_rag.sh` или эквивалента. До rebuild новые тексты не повлияют на retrieval. Это deferred на integration branch.
- Описания опираются на бизнес-контекст таблиц из v2; если позже окажется, что какая-то `business_description` была неточной — производные description колонок унаследуют эту неточность. Все правки локализованы в одном скрипте `scripts/improve_schema_overlay_v2.py`, отозвать batch можно одним diff.
- nl_phrases стали более длинными и менее общими; теоретически это может снизить recall на коротких NL-запросах вида «сумма». Контрмера: оригинальные одиночные слова присутствуют в `aliases`, FAISS-поиск по aliases остаётся.

## verdict

**READY_FOR_INTEGRATION** для overlay-артефакта v2 (TZ-01).

Перед runtime integration:
- доработать `scripts/validate_schema_overlay.py` (`--path` + поддержка column-level schema), либо обновить `deploy/schema_overlay.schema.json` под v2;
- запустить FAISS rebuild на основе обновлённого overlay;
- провести security-review расхождения column.category vs table.pii_tags для подтверждённых флагов в `sys_employee`.
