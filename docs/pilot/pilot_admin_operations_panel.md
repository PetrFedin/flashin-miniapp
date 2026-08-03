# FLASHIN — pilot operations panel в Admin

## Назначение

Панель переводит защищённый `GET /api/ops/pilot-runtime` в однозначный операторский экран. Она предназначена для наблюдения за первыми 20 заказами и не предоставляет действий, способных arm, resume, stop, reset или обойти runtime guard.

Панель отображается перед BusinessEvent recovery в операционном разделе Admin.

## Доступ

Backend endpoint требует:

- действующую admin session;
- permission `security.read`;
- no-cache policy.

Поведение UI:

- `401` завершает admin session через общий logout handler;
- `403` показывает обезличенное сообщение «Нет доступа»;
- сетевые и серверные ошибки отображаются фиксированным текстом без вывода response body;
- статус обновляется вручную и каждые 30 секунд, когда вкладка видима;
- параллельные запросы не запускаются.

## Что видит оператор

### Решение

Главный блок показывает `GO` или `NO-GO` для следующего checkout.

Клиент оставляет `GO` только когда одновременно подтверждены:

- backend response contract версии 1;
- runtime enforcement;
- статус `active`;
- лимит ровно 20;
- `accepted + remaining = 20`;
- число slots равно accepted counter;
- DB integrity;
- artifact integrity;
- отсутствие payment/refund/reconciliation review-сигналов.

Любое противоречие принудительно отображается как `NO-GO`, даже если поле `checkout_decision` в ответе ошибочно равно `GO`.

### Runtime

Показываются:

- принято заказов и лимит;
- оставшиеся slots;
- фактическое число slots;
- количество пользователей allowlist без идентификаторов;
- необратимый `run_ref`, но не внутренний run ID;
- число исторических slots;
- безопасная STOP-категория;
- время последнего изменения.

### Integrity

Отдельные карточки показывают:

- целостность DB state и slots;
- admission/evidence/current/previous release capability;
- агрегированные payment review, refund retry/review и reconciliation mismatch.

В UI существует конечный словарь допустимых integrity- и STOP-кодов. Неизвестное значение не выводится и превращает контракт в недоверенный `NO-GO`.

## Защита от утечек

Клиент формирует собственную view model и выбирает только ожидаемые поля. Он никогда не сохраняет в состоянии панели и не выводит:

- Telegram ID или содержимое allowlist;
- customer ID и order ID;
- raw run ID;
- provider payment/refund ID;
- произвольный STOP-текст;
- absolute evidence paths;
- manifest, hashes, signatures или secrets;
- неизвестные backend error codes.

Неверные типы чисел, timestamps, boolean flags, status, `run_ref`, STOP reason или integrity codes делают response contract недействительным.

## Запрет на управление runtime

В панели отсутствуют mutation endpoints и кнопки:

- arm;
- resume;
- stop;
- reset;
- изменение allowlist;
- ручное изменение accepted counter;
- подтверждение payment/refund.

Штатные runtime-команды выполняются только по production runbook после provider/live evidence и signed admission.

## Проверка перед пилотом

Перед допуском пользователей оператор должен увидеть:

- `GO`;
- Runtime: `Активен`;
- Enforcement: `включён`;
- лимит `0 / 20` или ожидаемый текущий accepted counter;
- DB integrity: `Подтверждено`;
- Evidence и releases: `Подтверждено`;
- Денежные операции: `Без сигналов`.

Экран является дополнительным контролем и не заменяет checkout runtime guard, capability v2 verification, rollback drill и signed admission.
