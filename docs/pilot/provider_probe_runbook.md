# FLASHIN — provider probe runbook

## Принцип

Внешние probes имеют реальные побочные эффекты, поэтому они отделены от обычного `pilot-gate`.

- `make provider-probes` — осознанно обращается к production-провайдерам и создаёт подписанное доказательство.
- `make check-integrations` — только проверяет уже существующее доказательство; внешних запросов и платежей не создаёт.
- `make pilot-gate` — проверяет публичный production-контур и валидность свежего подписанного provider report.

Пилот получает **NO-GO**, если доказательство отсутствует, просрочено, изменено, подписано другим secret, относится к другому release/configuration или содержит хотя бы один неуспешный probe.

## Что проверяется

1. **Telegram** — read-only Bot API `getMe`.
2. **YooKassa** — создание платежа на **1,00 RUB** с release-scoped idempotence key. Это подтверждает возможность создания платежа, но не успешную оплату и не webhook; они проверяются в 20 заказах.
3. **MoySklad** — read-only запрос каталога, каталог должен быть непустым.
4. **R2/S3** — запись, чтение и удаление временного объекта.
5. **Meilisearch** — health и непустой product index.

## Подготовка

Должны существовать:

- production `.env` с отдельным `PILOT_EVIDENCE_SIGNING_SECRET`;
- проверенный `current` immutable release;
- работающий backend container;
- реальные production credentials всех пяти провайдеров;
- `MEDIA_STORAGE=r2|s3` и `MEILISEARCH_ENABLED=true`.

## Команды

```bash
# Явный запуск с побочным эффектом YooKassa
make provider-probes

# Повторить сетевые probes для того же release.
# YooKassa idempotence key остаётся тем же.
make provider-probes ARGS='--force'

# Проверить подпись, release/config binding и срок действия без внешних запросов
make check-integrations

# Публичный live gate, также без создания нового платежа
make pilot-gate
```

Диагностика на host Python допускается только при настройке:

```bash
python3 scripts/check_integrations.py run \
  --host-python \
  --acknowledge-side-effects \
  --verbose
```

Не публиковать verbose output, provider IDs, локальные отчёты или confirmation data в issue, PR и публичных чатах.

## Idempotency YooKassa

Ключ строится детерминированно из shop ID и Git commit текущего promoted release. Payload также стабилен для release. Это позволяет безопасно повторить запрос после сетевого timeout без создания серии новых pending payments.

Новый payment probe допустим только после смены текущего release. Провайдер может вернуть ранее созданный pending/succeeded payment для того же idempotence key — это ожидаемое поведение.

## Локальные отчёты

- `docs/pilot/integration_check_report.json`
- `docs/pilot/integration_check_report.md`

JSON подписан HMAC-SHA256 и содержит:

- время создания и истечения;
- current release binding;
- HMAC configuration fingerprint;
- результаты ровно пяти probes;
- безопасную сводку побочных эффектов.

Файлы исключены из Git. Ручное редактирование делает подпись недействительной.

## Решение

- **GO** — все пять probes прошли, подпись корректна, report свежий и соответствует current release/configuration.
- **NO-GO** — любое другое состояние.
