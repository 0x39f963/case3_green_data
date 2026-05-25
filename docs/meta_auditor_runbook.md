Generated at: 2026-05-23 03:10 MSK

# Meta-auditor runbook

## Что это

Phase 2 "учится на прогонах": проходит по `benchmark.pipeline_runs`,
читает trace, генерирует короткий урок через Claude CLI Opus 4.7 / Sonnet 4.6,
сохраняет урок в `benchmark.rag_embeddings(index_name='solutions')` и ставит
флаг `pipeline_runs.meta_audited = TRUE`.

В runtime urоки подмешиваются в generator prompt как блок
`=== УРОКИ ИЗ ПОХОЖИХ ЗАДАЧ ===` через `app/rag_adapter.get_solutions_context()`.

## Почему сейчас пусто

В `benchmark.rag_embeddings WHERE index_name='solutions'` ровно **1 запись**
(`20260519T122034_7de279b0`). Cron `scripts/run_meta_audit.py` существует, но
**нигде не запланирован** — ни в host crontab, ни в Docker. Каждый прогон
оставляет `meta_audited=false`, никто их не подбирает.

## Как запустить вручную (smoke)

```bash
docker exec case3_app python scripts/run_meta_audit.py --limit 5 --dry-run
# реально сохранить уроки:
docker exec case3_app python scripts/run_meta_audit.py --limit 5
```

Параметры:
- `--limit N` — сколько trace взять за один заход (default 20).
- `--model` — Claude CLI модель (default из `meta_auditor.DEFAULT_MODEL`).
- `--timeout-sec` — лимит на один trace (default 600).
- `--dry-run` — не писать в БД, только показать lesson.
- `--include-approved` — учить и на успешных прогонах (по умолчанию
  пропускает approved).

Latency пользователю не виден: это backend cron.

## Как настроить cron

### Вариант A: host crontab (рекомендую)

```cron
# каждый час подбирать новые трассы
0 * * * * cd /home/x39963/web/mipt/case3 && \
    docker exec case3_app python scripts/run_meta_audit.py --limit 30 \
    >> /var/log/meta_audit.log 2>&1
```

### Вариант B: docker-compose с расписанием

В `deploy/docker-compose.yml` добавить отдельный сервис `meta-auditor`
с restart-loop:

```yaml
meta-auditor:
  image: case3_app:latest
  command: ["sh", "-c", "while true; do python scripts/run_meta_audit.py --limit 30; sleep 3600; done"]
  env_file: [../.env, benchmark.env]
  depends_on: [postgres]
```

## Что мониторить

- `select count(*) from benchmark.pipeline_runs where meta_audited=false` —
  должно регулярно падать к нулю.
- `select count(*) from benchmark.rag_embeddings where index_name='solutions'`
  — должно расти на N в час.
- Логи `/var/log/meta_audit.log` или `docker logs case3_meta_auditor`.

## Что НЕ делать

- Не запускать без isolation_mode=production (на clean cron бесполезен —
  trace там не сохраняется по `solutions` пути).
- Не отключать `--include-approved` если строишь positive lessons base.
- Не запускать одновременно несколько cron job-ов на одной БД — будут гонки
  на флаге `meta_audited`.

## Связь с TZ-7

Это **Phase 2.4** roadmap: «оживить solutions write-path». До этого индекс
solutions содержит 1 запись из тысяч прогонов, обучение pipeline не работает.
После настройки cron индекс начнёт расти и `app/orchestrator` (если включён
`USE_SOLUTIONS_LESSONS=true` или isolation=production) будет подмешивать
уроки в prompt.

## Acceptance

- 1 проход вручную успешен (smoke).
- Через 6 часов после запуска cron — N≥10 записей в `solutions`.
- Через 24 часа — pipeline_runs `meta_audited=false` < 50.
