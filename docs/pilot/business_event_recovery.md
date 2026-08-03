# FLASHIN — восстановление failed BusinessEvent в пилоте

## Назначение

BusinessEvent после 10 неуспешных попыток переводится в `failed` и больше не обрабатывается автоматически. Это защищает worker от бесконечного poison-message цикла, но требует управляемого действия оператора.

Новый recovery-контур хранит отдельно от события:

- последнюю ошибку и время попытки;
- время перехода в terminal `failed`;
- число ручных replay;
- администратора и время последнего replay;
- время успешного разрешения после повторного запуска.

## Диагностика

```http
GET /api/platform/admin/events/summary
GET /api/platform/admin/events?status=failed&limit=100
GET /api/platform/admin/events/{event_id}
```

Доступ на чтение требует admin permission `orders.read`.

Карточка события возвращает исходный payload только в detail endpoint. Список не дублирует payload, чтобы не раздувать ответ и не раскрывать лишние данные в массовой выдаче.

## Replay

```http
POST /api/platform/admin/events/{event_id}/replay
Content-Type: application/json
Authorization: Bearer <admin-token>

{
  "reason": "Исправлено сопоставление webhook destination",
  "payload": {
    "order_id": 123,
    "status": "paid"
  }
}
```

`payload` необязателен. Без него повторно используется сохранённый payload. Если сохранённый JSON повреждён или имеет неверный тип, replay без исправленного payload отклоняется.

Replay требует permission `orders.write` и разрешён только для статуса `failed`.

## Гарантии

- строка BusinessEvent блокируется `FOR UPDATE`;
- два параллельных replay не могут одновременно вернуть событие в очередь;
- `processed` и уже `pending` события не переотправляются;
- попытки текущего цикла сбрасываются в 0, число ручных replay увеличивается отдельно;
- внешний webhook не вызывается из Admin API;
- событие возвращается в `pending` и обрабатывается штатным worker;
- действие записывается в admin audit log вместе с причиной и состоянием до replay;
- при успешной обработке recovery state получает `resolved_at`.

## Процедура оператора

1. Открыть failed event и прочитать `last_error`.
2. Проверить aggregate, event type и исходный payload.
3. Устранить первопричину: destination, mapping, конфигурацию или данные.
4. Не менять payload без подтверждения бизнес-смысла события.
5. Выполнить один replay с конкретной причиной.
6. Проверить переход `pending` → `processed` и появление `resolved_at`.
7. Проверить ровно один ожидаемый webhook outbox/destination result.
8. При повторном `failed` остановить replay и передать событие разработчику.

## STOP-критерии

Нельзя выполнять массовый replay, если:

- причина ошибки не устранена;
- событие связано с оплатой или возвратом, а сумма/валюта не сверены;
- payload приходится менять без подтверждения владельца процесса;
- destination уже мог выполнить внешний side effect;
- нет понимания, является ли downstream операция идемпотентной.

Webhook delivery остаётся at-least-once относительно аварии процесса после внешнего side effect и до фиксации результата в БД. Stable event ID и idempotency на стороне получателя обязательны для критических интеграций.
