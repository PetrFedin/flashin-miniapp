# FLASHIN — runbook пилотного запуска

## Решение о допуске

Первый заказ разрешён только после последовательного прохождения пяти уровней:

1. `make readiness-gate` — production configuration и predeploy checks.
2. `make deploy-prod` — immutable release, backup, migration, production smoke.
3. `make provider-probes` — подписанные внешние provider checks.
4. `make pilot-gate` — публичные HTTPS/readiness/security checks без побочных платежей.
5. `make pilot-admit ...` — подписанный именной бизнес- и операционный допуск.

`make pilot-runner` не создаёт новый 20-order state без действующего admission manifest.

## Обязательные входные данные

- реальные Telegram, YooKassa, MoySklad, R2/S3 и Meilisearch credentials;
- отдельный `PILOT_EVIDENCE_SIGNING_SECRET`;
- DNS/HTTPS для Mini App, Admin и API;
- финальные реквизиты, оферта, privacy и returns;
- контакты поддержки и адрес возврата;
- владельцы бизнеса, операций, технологии, юридического блока и поддержки;
- два разных promoted release (`current` и `previous`);
- проверенный backup и фактически выполненный rollback drill.

## Последовательность запуска

```bash
# 1. Конфигурация и первая выкладка
make readiness-gate
make deploy-prod

# 2. Вторая успешная release-процедура, чтобы появился previous
make readiness-gate
make deploy-prod
make release-status

# 3. Backup и реальный rollback drill
make backup
make verify-backup FILE=backups/flashin_YYYYMMDD_HHMMSS.sql.gz
make rollback-drill RELEASE=previous BACKUP=backups/flashin_YYYYMMDD_HHMMSS.sql.gz
make rollback-drill-status

# 4. Вернуть точный исходный immutable release после drill
# Не использовать deploy-prod: он создаст новый release ID/SHA.
make rollback RELEASE=previous BACKUP=backups/flashin_YYYYMMDD_HHMMSS.sql.gz
make release-status

# 5. Внешние probes — единственный шаг, создающий 1 RUB YooKassa payment
make provider-probes
make check-integrations

# 6. Публичный live gate — без создания нового payment
make pilot-gate

# 7. Именной подписанный допуск
make pilot-admit ARGS='\
  --business-owner "..." \
  --operations-owner "..." \
  --technical-owner "..." \
  --legal-owner "..." \
  --support-owner "..." \
  --legal-documents-approved \
  --support-process-ready \
  --rollback-drill-completed \
  --provider-probe-side-effect-understood \
  --pilot-scope-limited-to-20-orders'
make pilot-admission-status

# 8. Контроль первых 20 заказов
make pilot-sheet
make pilot-runner
```

Подробная логика evidence/admission: `docs/pilot/provider_evidence_and_admission.md`.

## Волны пилота

1. **Заказы 1–3:** базовая оплата, доставка/самовывоз, ручная сверка order/payment/stock/notification.
2. **Заказы 4–7:** промокод, бонусы, повторный webhook, отмена неоплаченного заказа.
3. **Заказы 8–12:** multi-item, пограничный остаток, поздний платёж, support, fulfillment.
4. **Заказы 13–20:** полный/частичный refund, повторный callback, review, MoySklad conflict, recovery и обычный SOP flow.

После каждого сценария:

```bash
make pilot-record ARGS='--number N --result pass ... --evidence ...'
make pilot-status
```

Финальное решение:

```bash
make pilot-final
```

## Автоматический STOP

- критический сценарий отмечен `fail`;
- повторный order/payment/refund ID;
- сумма отличается более чем на 0,01;
- валюта не совпадает;
- остаток отрицательный или delta неверна;
- повторный webhook/callback создал больше одного доменного эффекта;
- потерян payment/refund/business event без retry/review;
- `/ready`, Admin или audit trail недоступны;
- раскрыт secret/PII/internal port;
- backup/rollback недоступен.

После STOP новые заказы запрещены до исправления, полного CI, новых provider/live checks и нового admission manifest, если изменились release или configuration.

## Финальный GO после 20 заказов

- `make pilot-final` возвращает `0` и **GO**;
- order/payment/refund reconciliation сходится;
- локальные остатки согласованы с MoySklad;
- все review cases имеют владельца и решение;
- нет критических инцидентов;
- business, operations и technology подписали итоговый acceptance sign-off.

Даже полностью зелёный технический контур не заменяет реальные credentials, юридическое утверждение и действия назначенных владельцев.
