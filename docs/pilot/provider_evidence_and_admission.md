# FLASHIN — подписанный допуск к пилоту

## Назначение

Пилот нельзя начинать только на основании зелёного CI, запущенных контейнеров или устного подтверждения команды. Перед первым заказом система требует единый подписанный admission manifest, который связывает:

- конкретный текущий immutable release;
- предыдущий release, доступный для отката;
- свежие результаты пяти внешних provider probes;
- свежий публичный live gate;
- фактически выполненный rollback drill с восстановлением БД;
- именные подтверждения бизнеса, операций, технологии, юриста и поддержки.

Подпись создаётся HMAC-SHA256 отдельным секретом `PILOT_EVIDENCE_SIGNING_SECRET`. Секрет не должен совпадать с JWT, TOTP или outbox secrets и не хранится в Git.

## Какие файлы создаются

Все рабочие доказательства являются локальными production-артефактами и исключены из Git:

- `docs/pilot/integration_check_report.json` — подписанный результат provider probes;
- `docs/pilot_live_gate_report.json` — публичный live gate;
- `docs/pilot/rollback_drill_report.json` — подписанный результат реального отката;
- `docs/pilot/pilot_admission_manifest.json` — итоговый подписанный допуск;
- соответствующие `.md`-сводки для оператора.

Удаление, изменение, истечение срока или смена production-конфигурации делает доказательство недействительным.

## Обязательная последовательность

### 1. Выполнить две успешные production-выкладки

```bash
make readiness-gate
make deploy-prod

# После следующего проверенного изменения или повторной release-процедуры
make readiness-gate
make deploy-prod
make release-status
```

В `release-status` должны существовать два разных указателя: `current` и `previous`.

### 2. Создать и проверить backup

```bash
make backup
make verify-backup FILE=backups/flashin_YYYYMMDD_HHMMSS.sql.gz
```

### 3. Выполнить реальный rollback drill

```bash
make rollback-drill \
  RELEASE=previous \
  BACKUP=backups/flashin_YYYYMMDD_HHMMSS.sql.gz

make rollback-drill-status
```

Drill останавливает production-сервисы, проверяет backup, восстанавливает БД, проверяет Alembic и транзакционную целостность, запускает весь production-контур и выполняет container smoke. Только после полного успеха создаётся подписанный rollback report.

После drill система работает на предыдущем release. Поэтому необходимо снова развернуть актуальный код:

```bash
make deploy-prod
make release-status
```

После повторной выкладки `current` должен совпадать с исходным release drill, а `previous` — с release, на который выполнялся откат.

### 4. Однократно запустить внешние provider probes

```bash
make provider-probes
```

Команда требует явного подтверждения побочного эффекта и выполняет:

- Telegram `getMe`;
- YooKassa payment probe на 1,00 RUB;
- чтение каталога MoySklad;
- R2/S3 write/read/delete;
- Meilisearch health и проверку непустого product index.

YooKassa использует стабильный idempotence key для текущего release. Повтор команды для того же release не должен создавать новую платёжную сущность. `--force` повторяет проверки, но сохраняет тот же release-scoped idempotence key.

Проверить уже созданное доказательство без внешних побочных эффектов:

```bash
make check-integrations
```

### 5. Выполнить live gate

```bash
make pilot-gate
```

`pilot-gate` больше не создаёт платежи. Он проверяет публичные HTTPS endpoints, readiness, каталог, security headers и свежую подписанную provider evidence.

### 6. Создать именной admission manifest

```bash
make pilot-admit ARGS='\
  --business-owner "Имя Фамилия" \
  --operations-owner "Имя Фамилия" \
  --technical-owner "Имя Фамилия" \
  --legal-owner "Имя Фамилия" \
  --support-owner "Имя Фамилия" \
  --legal-documents-approved \
  --support-process-ready \
  --rollback-drill-completed \
  --provider-probe-side-effect-understood \
  --pilot-scope-limited-to-20-orders'

make pilot-admission-status
```

Флаги являются осознанными подтверждениями, а не формальной галочкой. Нельзя указывать вымышленные имена или одного человека вместо неподтверждённых функций.

### 7. Инициализировать 20-заказный пилот

```bash
make pilot-runner
```

При отсутствии state-файла `pilot-runner` сначала проверяет admission manifest. Если он отсутствует, просрочен, изменён, относится к другому release/configuration или содержит неполные approvals, инициализация блокируется с решением `NO-GO`.

## Срок действия

Сроки задаются в `.env`:

- `PILOT_PROVIDER_EVIDENCE_MAX_AGE_MINUTES` — provider probes;
- `PILOT_LIVE_GATE_MAX_AGE_MINUTES` — live gate;
- `PILOT_ADMISSION_MAX_AGE_MINUTES` — окно начала пилота;
- `PILOT_ROLLBACK_DRILL_MAX_AGE_DAYS` — допустимый возраст rollback drill.

Рекомендуемые значения для пилота: 60, 30, 60 минут и 30 дней соответственно.

## Что автоматически аннулирует допуск

- новый текущий release;
- изменение любого значимого provider/public configuration value или credential;
- изменение или удаление доказательств;
- истечение TTL;
- отсутствие `current` или `previous` release;
- совпадение current и previous;
- rollback drill между другими release;
- отсутствие backup-файла или несовпадение его SHA-256;
- любой provider/live/rollback результат не равен GO;
- отсутствие хотя бы одного владельца или подтверждения.

Admission действует только для запуска контролируемых первых 20 заказов. Он не является разрешением массового трафика.
