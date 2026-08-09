# FLASHIN — runbook контролируемого пилотного запуска

## Неподвижный принцип

Первый реальный заказ разрешён только для **точного immutable release**, который одновременно:

- прошёл полный CI (`backend`, `frontend`, `admin`, `browser-e2e`, `integrated-e2e`, `docker`) от официального GitHub Actions App;
- развёрнут на production-подобном pilot host;
- имеет подписанные provider, live readiness, rollback, lifecycle и repository-governance evidence;
- имеет действующий подписанный admission с пятью именованными владельцами;
- связан с отдельным pilot runtime state;
- открыт только для явного Telegram allowlist;
- ограничен ровно 20 заказами и автоматически закрывается при критической ошибке.

Любое изменение release, production configuration, evidence-файла, admission manifest или GitHub `main` требует остановки runtime и нового полного допуска.

`integrated-e2e` — обязательный internal-stack gate: он проводит подписанный тестовый Telegram WebApp payload через реальный Mini App -> FastAPI -> PostgreSQL -> payment -> canonical YooKassa callback -> stock -> Admin fulfillment/delivery -> return/refund callback -> stock restoration -> notification -> terminal Mini App/Admin state. В нём заменена внешняя HTTP-граница YooKassa. Этот PASS не заменяет реальные Telegram/YooKassa/MoySklad/CDN evidence.

Отдельный обязательный backend provider-spine smoke проводит `customerorder -> demand -> salesreturn` через реальный PostgreSQL provider-command lifecycle и worker, заменяя только удалённую HTTP-границу MoySklad. Он доказывает внутренний outbound mapping/dispatch, но не существование документов в живом аккаунте MoySklad.

## Обязательные внешние входы

До технического допуска должны существовать:

- production/sandbox credentials Telegram, YooKassa, MoySklad, Meilisearch и R2/S3, если функции включены;
- отдельный случайный `PILOT_EVIDENCE_SIGNING_SECRET`;
- отдельный краткоживущий operator-only GitHub token с `Administration: write` **только на время применения branch protection**;
- отдельный краткоживущий operator-only GitHub token с минимально достаточными `Actions: read` и `Administration: read` для чтения protection/ruleset и выпуска repository-governance evidence;
- `PILOT_GITHUB_ACTIONS_APP_ID=15368` для привязки required checks к официальному GitHub Actions App;
- DNS и валидный HTTPS для Mini App, API, Admin и CDN;
- публичные оферта, privacy, consent, returns/refunds и реквизиты продавца;
- адрес возврата и рабочие контакты поддержки;
- именованные business, operations, technical, legal и support owners;
- on-call/escalation route и внешний alert receiver;
- два разных проверяемых immutable release: `current` и `previous`;
- проверенный backup и подписанный production-like rollback drill.

GitHub operator tokens запрещено хранить в root `.env`: Compose передаёт этот файл application containers. Секреты, raw Telegram `initData`, cookies и authorization headers запрещено сохранять в evidence.

## 1. Защитить исходный код

До создания governance evidence настройте для GitHub `main` ruleset или classic branch protection:

1. только pull request;
2. required checks `backend`, `frontend`, `admin`, `browser-e2e`, `integrated-e2e`, `docker`;
3. для каждого required check expected source — **GitHub Actions**, App ID `15368`; `any source`, отсутствующий/другой App ID и legacy name-only contexts запрещены;
4. strict/up-to-date checks;
5. явный запрет force-push;
6. явный запрет deletion;
7. classic `enforce_admins=true` или ruleset без bypass actors;
8. `main` остаётся default branch.

Сначала проверьте exact policy локальным dry-run без токена:

```bash
python3 scripts/configure_main_protection.py
```

Для применения classic branch protection передайте `Administration: write` token **только в процесс этой команды**:

```bash
PILOT_GITHUB_TOKEN="$TOKEN_FROM_OPERATOR_SECRET_MANAGER" \
  python3 scripts/configure_main_protection.py --apply
unset PILOT_GITHUB_TOKEN
```

Команда fail-closed требует ровно шесть checks и GitHub Actions App ID `15368`, включает strict checks, PR-only, `enforce_admins`, conversation resolution и запрещает force-push/deletion. Если используется organization ruleset вместо classic protection, настройте эквивалентную политику и затем докажите её governance collector-ом.

После изменения governance не вносите новые коммиты в `main`, пока не будет создан новый release и заново пройдена вся цепочка. Если API GitHub показывает `main` как unprotected, этот шаг нельзя считать выполненным по одному только зелёному CI.

## 2. Проверить production configuration

```bash
make validate-env
python3 scripts/provider_wiring_preflight.py --env .env
make readiness-gate
```

Все три шага должны завершиться GO до deploy. Provider preflight fail-closed проверяет production HTTPS URLs, точный YooKassa callback `${API_PUBLIC_URL}/api/webhooks/yookassa`, точный return URL `${MINI_APP_URL}/payment-result`, Telegram token wiring, YooKassa credentials, MoySklad credentials/organization/agent/store IDs, outbound enablement, scheduler и pilot runtime guard. Никакие значения из `.env.production.example` не являются реальными секретами. В production `.env` должны находиться только не-секретные GitHub governance settings; строка `PILOT_GITHUB_TOKEN` там запрещена.

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

Отчёт должен быть strict, свежим, подписанным и привязанным к точному release/configuration. Для Telegram/YooKassa/MoySklad PASS должен отражать реальный ответ внешнего provider, а не CI fake/stub.

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

Выполните все обязательные сценарии из `docs/pilot/live_lifecycle_evidence.md` и все P01-P20 шаги `docs/pilot/live_pilot_runner.json`:

- real Telegram signed authentication;
- YooKassa redirect, canonical `/api/webhooks/yookassa` callback и return;
- duplicate payment/refund webhook idempotency;
- sandbox/live refund и authoritative reconciliation;
- live MoySklad sync и outbound `customerorder`/`demand`/`salesreturn`;
- Telegram notification delivery;
- Meilisearch indexing, если включён;
- R2/S3/CDN delivery, если включён.

Guarded runners для deployed pilot environment:

```bash
RUN_REAL_E2E=1 python -m pytest -q backend/tests/e2e/test_real_order_flow_runner.py
RUN_REAL_LIFECYCLE_E2E=1 python -m pytest -q backend/tests/e2e/test_order_payment_refund_flow.py
```

Первый runner создаёт/проводит реальный order/payment/fulfillment путь; второй является terminal verifier и проверяет финальные order/payment/delivery/return/stock/refund-notification/diagnostics состояния. Не включайте эти флаги в обычный CI.

Сохраните только sanitized evidence под `docs/pilot/evidence`, затем:

```bash
make pilot-lifecycle-create ARGS='--input docs/pilot/live_lifecycle_input.json'
make pilot-lifecycle-attach
make pilot-lifecycle-status
```

Каждый scenario owner должен дословно совпадать с одним из владельцев signed admission. Ни один обязательный P01-P20 шаг не может оставаться `todo` или `failed` к моменту финального допуска.

## 9. Signed repository-governance evidence

Убедитесь, что защищённый `main` указывает на тот же commit, что `current_release.json`, полный **push CI** этого commit завершён success, а все шесть required checks (`backend`, `frontend`, `admin`, `browser-e2e`, `integrated-e2e`, `docker`) имеют `app_id`/`integration_id=15368`.

Для evidence используйте read-capable governance token; write-права после применения protection здесь не нужны. Токен подаётся только в процесс создания отчёта. Не записывайте его в `.env`, Compose secret или контейнер:

```bash
# Предпочтительно: secret-manager запускает одну команду с ephemeral env.
PILOT_GITHUB_TOKEN="$TOKEN_FROM_OPERATOR_SECRET_MANAGER" \
  make pilot-governance-create ARGS='--owner "Exact Technical Owner"'
unset PILOT_GITHUB_TOKEN
make pilot-governance-status
make pilot-governance-attach
```

Не вставляйте raw token в интерактивную shell history; используйте process injection секрет-менеджера или краткоживущий GitHub App installation token.

Collector обязан видеть полное поле `bypass_actors`. Скрытое поле, отсутствующий token, неполные checks, недоверенный source, другой head SHA, PR-only run вместо успешного `push` run или иной workflow дают NO-GO. Проверка уже подписанного отчёта токен не требует. Governance tooling fail-closed требует наличие всех шести checks даже при отсутствии `PILOT_GITHUB_REQUIRED_CHECKS` в env.

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
- любой из шести required checks потерял binding к GitHub Actions App ID `15368`;
- GitHub operator token обнаружен в `.env`, container environment, logs или evidence;
- обнаружен provider secret, raw initData или лишний доступ;
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
