# FLASHIN — runtime-защита первых 20 заказов

## Зачем нужен отдельный runtime guard

Подписанный admission разрешает открыть пилот, но сам по себе не ограничивает публичный checkout. Production API поэтому использует отдельное состояние БД, которое проверяется внутри транзакции создания каждого нового заказа.

При `PILOT_RUNTIME_ENFORCED=true` новый checkout закрыт по умолчанию. Он становится доступен только после:

1. двух успешных production-выкладок версии с runtime guard — и текущий, и предыдущий immutable release должны поддерживать безопасный откат;
2. signed admission со статусом GO;
3. инициализации 20-сценарного pilot state;
4. явного arm для известных Telegram ID.

## Состояния

- `closed` — пилот ещё не открывался; новые checkout запрещены;
- `active` — разрешены только allowlisted участники и только пока есть свободные слоты;
- `stopped` — новые checkout и новые внешние payment attempts запрещены после STOP, deploy, rollback или circuit-breaker;
- `completed` — принято 20 новых заказов; двадцать первый checkout невозможен, но уже принятые заказы могут завершить свой payment/fulfillment/refund lifecycle при сохранённой runtime safety.

Состояние хранится в singleton-строке `pilot_runtime_state`. Каждый допущенный заказ получает неизменяемый слот в `pilot_order_slots` с номером 1–20, order ID, customer ID, run ID и хэшем admission.

## Что проверяет каждый новый checkout

Перед чтением корзины и резервированием товара backend блокирует строку runtime через `SELECT ... FOR UPDATE` и проверяет:

- runtime имеет статус `active`;
- лимит равен ровно 20;
- Telegram ID клиента находится в allowlist;
- signed admission-файл не изменён;
- HMAC-подпись admission корректна;
- configuration fingerprint совпадает с текущим production environment;
- admission относится к текущему immutable release и тому previous release, который доступен для отката;
- current и previous release имеют подписанную capability `pilot_runtime_guard`;
- capability связана с точными SHA-256 архива, Git commit и release ID;
- provider, live-gate и rollback evidence имеют прежние SHA-256;
- pilot state не был заменён после arm;
- решение pilot state не равно `STOP`;
- database evidence и operational queues/workers остаются безопасными;
- число строк `pilot_order_slots` совпадает со счётчиком БД;
- остаётся свободный слот.

Слот создаётся после `Order` flush, но до резервирования остатков. Слот, заказ, stock reservation, promo и loyalty изменения входят в одну DB-транзакцию. Любая последующая ошибка откатывает весь набор изменений.

Повтор запроса с уже обработанным `Idempotency-Key` возвращает существующий заказ до runtime-проверки и не занимает второй слот. Это позволяет безопасно получить результат после сетевого таймаута, даже если пилот уже остановлен или завершён.

## Fresh payment guard

Создание заказа и создание YooKassa payment — два разных API-шага. Поэтому runtime safety проверяется повторно непосредственно перед **новым** `POST /payments` в YooKassa.

Для fresh provider payment backend требует:

- `PILOT_RUNTIME_ENFORCED=true` order должен иметь неизменяемый `pilot_order_slots` slot;
- slot должен относиться к тому же `run_id` и admission, что и текущий runtime;
- runtime должен быть `active` или `completed`; `stopped`/`closed` запрещают новый provider payment;
- slot sequence должен уже входить в `accepted_orders` и не выходить за лимит 20;
- signed admission/release/pilot-state binding, database evidence и operational safety должны пройти ту же fail-closed runtime-проверку, что и новый checkout.

Проверка выполняется в отдельной DB-транзакции **до внешнего YooKassa create**. Если runtime был остановлен между созданием Order и нажатием оплаты, новый денежный side effect не создаётся.

При этом recovery не блокируется:

- уже существующий YooKassa payment сначала fetch/reconcile/reuse и может быть завершён даже после STOP;
- provider webhooks продолжают обрабатываться;
- refunds намеренно остаются доступны, чтобы остановленный пилот мог вернуть деньги;
- fulfillment, reconciliation и уведомления продолжают штатно разбирать уже принятые заказы.

Это различает «не создавать новый финансовый риск» и «безопасно завершить/размотать уже начатый финансовый lifecycle».

## Подготовка

В production `.env` обязательны:

```dotenv
PILOT_RUNTIME_ENFORCED=true
PILOT_RUNTIME_MAX_ORDERS=20
```

Backend получает только read-only доступ к:

- `/app/docs` — admission, provider/live/rollback evidence и pilot state;
- `/app/deploy/release` — current/previous immutable release pointers.

Allowlist хранится только в БД. Команда status показывает её размер, но не Telegram ID.

### Защищённые current и previous releases

После внедрения runtime guard необходимо выполнить минимум две успешные выкладки этого защищённого кода. При каждой успешной выкладке production script:

1. проверяет immutable ZIP;
2. убеждается, что в архиве присутствуют migration, runtime service, checkout wiring, read-only evidence mounts и STOP-логика deploy/rollback;
3. записывает в release pointer HMAC-подписанную capability `pilot_runtime_guard`.

Проверка обоих указателей:

```bash
python3 scripts/pilot_release_capability.py verify --slot both --env .env
```

Ожидается `ok=true` для `current` и `previous`. Если previous относится к версии без runtime guard, открывать пилот нельзя: сначала выполните ещё одну успешную защищённую production-выкладку, затем новый rollback drill и admission.

Capability не доверяет одному JSON-флагу: при создании она формируется только после инспекции immutable release ZIP и затем связывается подписью с SHA-256 архива и Git commit.

## Открытие checkout

Сначала должна быть полностью выполнена admission-последовательность и создан pilot state:

```bash
python3 scripts/pilot_release_capability.py verify --slot both --env .env
make pilot-admission-status
make pilot-runner
```

Затем явно перечислите Telegram ID участников:

```bash
make pilot-runtime-arm ARGS='\
  --telegram-id 123456789 \
  --telegram-id 987654321'

make pilot-runtime-status
```

Ожидаемый результат status:

- `ok=true`;
- `status=active`;
- `accepted_orders=0` для нового пилота;
- `max_orders=20`;
- `remaining_orders=20`;
- `slot_count=0`;
- `allowlist_count` соответствует утверждённому списку;
- `errors=[]`.

## Немедленная остановка

```bash
make pilot-runtime-stop REASON='payment amount mismatch'
make pilot-runtime-status
```

Команда не удаляет заказы и слоты. Она запрещает новые checkout и fresh YooKassa payment attempts. Уже созданные provider payments можно reconcile/reuse; webhooks, fulfillment, refund, notification и reconciliation продолжают безопасно завершать или разматывать уже начатые операции.

Кроме ручной команды, runtime автоматически останавливается перед:

- `make deploy-prod`;
- `make rollback`;
- `make rollback-drill`.

Rollback повторно принудительно переводит восстановленную БД в `stopped` **до запуска публичного backend**, поэтому backup с историческим `active` не создаёт даже короткого окна для нового заказа или fresh payment attempt.

Если backend не может подтвердить остановку существующего active runtime, deploy/rollback завершается ошибкой.

## Возобновление после исправления

После deploy или rollback старый admission недействителен, потому что изменился current release. Для продолжения необходимо:

1. проверить capability current и previous;
2. заново выполнить provider/live evidence и admission;
3. убедиться, что pilot state не заменён, а STOP-причина устранена;
4. выполнить resume:

```bash
make pilot-runtime-arm ARGS='\
  --resume \
  --telegram-id 123456789 \
  --telegram-id 987654321'
```

Resume сохраняет тот же run ID, уже занятые слоты и счётчик. Обнулить принятые заказы или открыть второй «первый пилот» этой командой нельзя.

## Реакция API

- HTTP `423` на checkout — runtime закрыт, остановлен, завершён, клиент не в allowlist или pilot state имеет STOP;
- HTTP `423` на fresh payment — runtime остановлен/закрыт, order не имеет admitted pilot slot либо runtime safety временно не допускает новый денежный side effect;
- HTTP `503` — нарушена целостность evidence/release/runtime/slot binding либо safety state невозможно надёжно проверить;
- успешный idempotent retry ранее созданного заказа возвращает прежний `Order` и не расходует слот;
- существующая provider payment attempt сначала reconciles/reuses и не считается fresh create.

Клиент получает только общий безопасный текст. Конкретная причина доступна оператору через `make pilot-runtime-status` и readiness/incident views; Telegram ID и секреты в ответ API не включаются.

## Запреты

Нельзя:

- отключать `PILOT_RUNTIME_ENFORCED` в production;
- увеличивать лимит выше 20;
- открывать пилот, если current или previous не имеют валидной подписанной capability;
- редактировать release capability, runtime-счётчик или slot rows вручную;
- заменять pilot state после первого arm;
- создавать fresh provider payment после STOP в обход guard;
- продолжать после STOP без нового admission;
- публиковать allowlist, admission/evidence или реальные идентификаторы заказов в GitHub.
