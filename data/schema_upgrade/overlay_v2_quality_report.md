Generated at: 2026-05-25 11:49:13 MSK

# Overlay v2 Quality Report

## summary

Canonical report path for `deploy/schema_overlay_v2.json` quality upgrade.
The detailed working report is also stored at `.cursor/!tmp/!ARTEFACTS/2026-05-24/quality-overlay-v2/overlay_v2_quality_report.md`.
This file fixes the acceptance path expected by TZ-06 and keeps integration evidence near `data/schema_upgrade/audit_baseline.md`.

## changed_files

| File | What changed | Why |
|---|---|---|
| `deploy/schema_overlay_v2.json` | 116 weak column descriptions and `nl_phrases` were upgraded in prior TZ-01 work | Reduce broad matches on generic words and add table-level business context |
| `deploy/schema_overlay.schema.v2.json` | V2 JSON Schema added for column-level overlay format | Allow deterministic validation of v2 artifact before RAG rebuild |
| `scripts/validate_schema_overlay.py` | Validator supports `--path` and auto-selects v1 or v2 schema | Make acceptance command machine-checkable |
| `data/schema_upgrade/overlay_v2_quality_report.md` | This canonical report path added | Remove report-path mismatch before integration branch |

## before_after_examples

| # | Field | Before | After | Why |
|---:|---|---|---|---|
| 1 | `credit_contract.sel_curr` | `Валюта` | Валюта кредитного договора with FK context | Avoid generic currency matches |
| 2 | `count_turnover.period_st_end` | `Период` | Period of OSV turnover rows | Bind period to OSV domain |
| 3 | `scp_amd_product.scp_general_amount` | `Сумма` | Requested product amount in SCP application | Separate from other amount columns |
| 4 | `sys_obj_type.note` | `Описание` | CRM object type description | Add table and domain context |
| 5 | `tbs_type.identif` | `Идентификатор` | Business identifier / mnemonic of OSV type | Separate business code from technical id |
| 6 | `scp_collateral_app.testnumber` | Test attribute | Technical test field, not for business SELECT | Prevent noisy SELECT projections |
| 7 | `participant_app.bch_refuse` | Refusal from BKI | BKI refusal flag with value meaning | Explain abbreviation and flag semantics |
| 8 | `offices_psb.feat_org` | `Признак: 1 - ГО` | Head office flag with 1/0 meaning | Make binary field usable for filters |
| 9 | `sys_company.date_of_consent` | Consent date | Consent date for personal-data processing | Add security-sensitive business context |
| 10 | `scp_gov_program_dict.margin_bank` | Margin | Bank margin for state support program | Separate subsidized program metrics |

## validation_summary

| Command | Result | Notes |
|---|---|---|
| `python3 scripts/validate_schema_overlay.py --path deploy/schema_overlay_v2.json` | PASS | 60 tables, v2 schema selected |
| `python3 -m json.tool deploy/schema_overlay_v2.json >/tmp/schema_overlay_v2.validated.json` | PASS | JSON valid |
| `python3 -m json.tool deploy/schema_overlay.schema.v2.json >/tmp/schema_overlay_schema_v2.validated.json` | PASS | JSON Schema file valid JSON |
| Overlay metrics script | PASS | 60 tables, 1872 columns, 0 short descriptions, 0 generic descriptions, 0 empty aliases, 0 empty `nl_phrases` |

## unchanged_security_contract

- PII tags were not changed by TZ-06.
- Column labels/categories were not changed by TZ-06.
- Severity values were not changed by TZ-06.
- Join keys were not changed by TZ-06.

## deferred_items

| Item | Reason |
|---|---|
| Runtime use in `app/rag_adapter.py` | Out of quality-upgrade scope |
| Runtime guard wiring | Out of quality-upgrade scope |
| FAISS/RAG rebuild | Must be done in integration branch after artifact acceptance |
| `sys_employee` PII diff review | Security-sensitive decision from source PR, not changed here |

## verdict

READY_FOR_INTEGRATION for data-artifact handoff.
