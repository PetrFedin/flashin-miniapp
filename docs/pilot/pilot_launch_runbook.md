# FLASHIN — runbook контролируемого пилотного запуска

## Неподвижный принцип

Первый реальный заказ разрешён только для **точного immutable release**, который одновременно:

- прошёл полный CI (`backend`, `frontend`, `admin`, `browser-e2e`, `docker`);
- развёрнут на production-подобном pilot host;
- имеет подписанные provider, live readiness, rollback, lifecycle и repository-governance evidence;
- имеет действующий подписанный admission с пятью именованными владельцами;
- связан с отдельным pilot runtime state;
- открыт только для явного Telegram allowlist;
- ограничен ровно 20 заказами и автоматически закрывается при критической ошибке.

Любое изменение release, production configuration, evidence-файла, admission manifest или GitHub `main` требует остановки runtime и нового полного допуска.

## Обязательные внешние входы

До технического допуска должны существовать:

- production/sandbox credentials Telegram, YooKassa, MoySklad, Meilisearch и R2/S3, если функции включены;
- отдельный случайный `PILOT_EVIDENCE_SIGNING_SECRET`;
- обязательный `PILOT_GITHUB_TOKEN` с доступом к branch protection, Actions runs и полным ruleset bypass data;
- DNS и валидный HTTPS для Mini App, API, Admin и CDN;
- публичные оферта, privacy, consent, returns/refunds и реквизиты продавца;
- адрес возврата и рабочие контакты поддержки;
- именованные business, operations, technical, legal и support owners;
- on-call/escalation route и внешний alert receiver;
- два разных проверяемых immutable release: `current` и `previous`;
- проверенный backup и подписанный production-like rollback drill.

Секреты, raw Telegram `initData`, cookies и authorization headers запрещено сохранять в evidence.

## 1. Защитить исходный код

До создания governance evidence настройте для GitHub `main` ruleset или classic branch protection:

1. только pull request;
2. required checks `backend`, `frontend`, `admin`, `browser-e2e`, `docker`;
3. strict/up-to-date checks;
4. запрет force-push;
5. запрет deletion;
6. classic `enforce_admins=true` или ruleset без bypass actors;
7. `main` остаётся default branch.

После изменения governance не вносите новые коммиты в `main`, пока не будет создан новый release и заново пройдена вся цепочка.

## 2. Проверить production configuration

```bash
make validate-env
make readiness-gate
```

`readiness-gate` должен завершиться GO до deploy. Никакие значения из `.env.production.example` не являются реальными секретами.

## 3. Создать и развернуть два immutable release

Нужны различающиеся `current` и `previous`, чтобы rollback был доказуемым.

```bash
make release-create
make release-verify FILE=deploy/release/builds/flashin_<release>.zip
make deploy-prod

# После следующего проверенного release/deploy должен появиться previous.
make release-create
make release-verify FILE=deploy/release/builds/flashin_<next-release>.zip
make deploy-prod
make release-status
```

Не допускается использовать архив, созданный из другого commit, или вручную менять release pointer.

## 4. Backup и production-like rollback drill

```bash
make backup
make verify-backup FILE=backups/flashin_YYYYMMDD_HHMMSS.sql.gz
make rollback-drill RELEASE=previous BACKUP=backups/flashin_YYYYMMDD_HHMMSS.sql.gz
make rollback-drill-status
make release-status
```

После drill точный `current` release, база и сервисы должны быть восстановлены согласно rollback runbook. Не создавайте новый release только ради возврата после drill: это изменит release binding и обнулит последующие evidence.

## 5. Подписанные provider probes

Это единственный обязательный pre-admission шаг, который может создать контролируемый 1 RUB YooKassa payment.

```bash
make provider-probes
make check-integrations
```

Отчёт должен быть strict, свежим, подписанным и привязанным к точному release/configuration.

## 6. Публичный live readiness gate

```bash
make pilot-gate
```

Gate проверяет развёрнутые HTTPS endpoints и уже созданное provider evidence без нового платежа.

## 7. Baseline signed admission

```bash
make pilot-admit ARGS='\
  --business-owner "Exact Business Owner" \
  --operations-owner "Exact Operations Owner" \
  --technical-owner "Exact Technical Owner" \
  --legal-owner "Exact Legal Owner" \
  --support-owner "Exact Support Owner" \
  --legal-documents-approved \
  --support-process-ready \
  --rollback-drill-completed \
  --provider-probe-side-effect-understood \
  --pilot-scope-limited-to-20-orders'
```

Имена далее используются как identity boundary для lifecycle и governance evidence. Admission имеет короткий срок жизни; при истечении создайте новый, не изменяйте timestamp вручную.

## 8. Реальные deployed lifecycle scenarios

Выполните все обязательные сценарии из `docs/pilot/live_lifecycle_evidence.md`:

- real Telegram signed authentication;
- YooKassa redirect и return;
- duplicate webhook idempotency;
- sandbox refund/reconciliation;
- live MoySklad sync;
- Telegram notification delivery;
- Meilisearch indexing, если включён;
- R2/S3/CDN delivery, если включён.

Сохраните только sanitized evidence под `docs/pilot/evidence`, затем:

```bash
make pilot-lifecycle-create ARGS='--input docs/pilot/live_lifecycle_input.json'
make pilot-lifecycle-attach
make pilot-lifecycle-status
```

Каждый scenario owner должен дословно совпадать с одним из владельцев signed admission.

## 9. Signed repository-governance evidence

Убедитесь, что защищённый `main` указывает на тот же commit, что `current_release.json`, и полный CI этого commit завершён success.

```bash
make pilot-governance-create ARGS='--owner "Exact Technical Owner"'
make pilot-governance-status
make pilot-governance-attach
```

Collector обязан видеть полное поле `bypass_actors`. Скрытое поле, отсутствующий token, неполные checks, другой head SHA или иной workflow дают NO-GO.

## 10. Финальная проверка допуска

```bash
make pilot-admission-status
```

Только `go: true` разрешает инициализацию runtime. Эта команда проверяет baseline evidence, lifecycle, governance, release capability, signatures, checksums, freshness и owner identity.

## 11. Инициализировать и открыть runtime только allowlist

```bash
make pilot-sheet
make pilot-init ARGS='\
  --operator-role operations_owner \
  --operator "Exact Operations Owner" \
  --reason "Initialize controlled first-20-order pilot"'

make pilot-runtime-arm ARGS='\
  --telegram-id 123456789 \
  --telegram-id 987654321'

make pilot-runtime-status
```

Запрещено открывать runtime без явного allowlist, использовать массовый список или повышать лимит выше 20.

## 12. Волны первых 20 заказов

1. **1–3:** базовая оплата, доставка/самовывоз, ручная сверка order/payment/stock/notification.
2. **4–7:** промокод, бонусы, duplicate webhook, отмена неоплаченного заказа.
3. **8–12:** multi-item, пограничный остаток, поздний платёж, support и fulfillment.
4. **13–20:** полный/частичный refund, duplicate callback, review, MoySklad conflict, recovery и обычный SOP flow.

После каждого сценария:

```bash
make pilot-record ARGS='--number N --result pass --operator-role operations_owner --operator "Exact Operations Owner" --reason "Verified scenario" --evidence ...'
make pilot-status
make pilot-runtime-status
```

Не отмечайте `pass` до сверки PostgreSQL, provider IDs, inventory ledger, notification/fulfillment outcome и evidence checksum.

## Немедленный STOP

```bash
make pilot-runtime-stop REASON='Precise incident reason'
```

STOP обязателен при любом из условий:

- критический сценарий `fail`;
- duplicate order/payment/refund ID;
- money delta более 0,01 или неверная валюта;
- отрицательный остаток или неверный inventory delta;
- duplicate webhook/callback создал повторный финансовый/доменный эффект;
- потерян payment/refund/business event без retry/review;
- reconciliation, `/ready`, Admin, audit trail или alerts недоступны;
- evidence/admission/release/governance checksum изменился;
- обнаружен secret, raw initData или лишний доступ;
- backup/rollback недоступен.

После STOP не используйте `--resume`, пока incident не закрыт, полный CI не зелёный и все затронутые evidence/admission не выпущены заново.

## 13. Финальная сверка и решение после заказа 20

```bash
make pilot-status
make pilot-runtime-status
make pilot-final
```

Финальный GO возможен только когда:

- все 20 slots имеют проверенный результат и evidence;
- PostgreSQL order/payment/refund/inventory totals сходятся;
- provider settlements/refunds подтверждены Finance;
- локальные остатки согласованы с MoySklad;
- notifications, fulfillment, delivery, returns и support outcomes подтверждены Operations;
- все review/incidents имеют владельца и terminal resolution;
- Legal/privacy/retention handling подтверждены;
- backup и rollback evidence сохранены вне application host;
- business, operations и technical owners подписали итоговый acceptance;
- отдельное решение о mass launch принято после пилота.

Успешные 20 заказов не являются автоматическим разрешением массовых продаж.
