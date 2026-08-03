# FLASHIN — операторский статус контролируемого пилота

## Назначение

Пилот нельзя вести по разрозненным SQL-запросам и ручному чтению evidence-файлов. Защищённый read-only endpoint даёт оператору единый обезличенный снимок runtime, денежных review-сигналов и целостности связей, не раскрывая Telegram ID, provider ID, подписи, хэши evidence или содержимое allowlist.

## Endpoint

```text
GET /api/ops/pilot-runtime
```

Требования доступа:

- действующая admin session;
- permission `security.read`;
- ответ всегда содержит `Cache-Control: no-store, max-age=0` и `Pragma: no-cache`.

Endpoint ничего не изменяет, не arm/resume/stop runtime и не подтверждает provider операции.

## Поля ответа

### `checkout_decision`

- `GO` — только когда runtime enforcement включён, статус `active`, есть свободные slots, DB и artifact integrity полностью подтверждены, а незакрытых денежных review-сигналов нет;
- `NO-GO` — во всех остальных случаях.

Это оперативный индикатор, а не замена signed admission. Runtime checkout всё равно повторно выполняет собственную fail-closed проверку.

### `runtime`

Отображаются только безопасные агрегаты:

- наличие и статус runtime;
- run ID;
- лимит, принято и осталось заказов;
- число фактических slots;
- число исторических slots других run ID;
- количество разрешённых пользователей без самих Telegram ID;
- нормализованная STOP-причина;
- timestamps открытия, остановки, завершения и последнего обновления.

### `database_integrity`

Возвращает `healthy` и стабильные machine-readable codes. Проверяются:

- singleton runtime state;
- лимит ровно 20;
- диапазон accepted counter;
- равенство counter и slot count;
- непрерывная последовательность slots;
- привязка каждого slot к admission;
- наличие run/evidence/release bindings;
- валидность allowlist без раскрытия значений;
- корректность переходов active/completed/stopped.

### `artifact_integrity`

Используется тот же валидатор, что блокирует реальный checkout. Сырые ошибки и пути файлов наружу не возвращаются. Они сворачиваются в безопасные коды:

- `admission_evidence_invalid`;
- `current_release_invalid`;
- `previous_release_invalid`;
- `release_capability_invalid`;
- `pilot_control_invalid`;
- `evidence_file_invalid`;
- `configuration_fingerprint_mismatch`;
- `signing_configuration_invalid`;
- `runtime_artifact_invalid`.

### `money_attention`

Для заказов текущего pilot run агрегируются только количества:

- orders в payment review;
- orders с `refund_retry_required` или `refund_review_required`;
- незакрытые payment reconciliation mismatches;
- общий флаг `attention_required`.

Order ID, customer ID, Telegram ID, provider payment/refund ID и payload не возвращаются.

## Операторская интерпретация

`GO` допускает переход к следующему pilot order только при уже выполненном signed admission и armed runtime.

При `NO-GO`:

1. не пытаться обходить runtime через прямой API/SQL;
2. проверить machine codes и STOP-причину;
3. завершить payment/refund reconciliation;
4. при artifact error повторить capability/evidence verification;
5. после устранения причины выпустить свежие evidence и admission;
6. использовать штатный resume только если run допускает продолжение и slots ещё доступны.

## Запреты

Endpoint не должен:

- возвращать `allowed_telegram_ids`;
- возвращать любые Telegram ID или customer identifiers;
- возвращать provider payment/refund IDs;
- возвращать абсолютные пути private evidence;
- возвращать signing secret, signature или raw admission manifest;
- кэшироваться браузером, CDN или reverse proxy.
