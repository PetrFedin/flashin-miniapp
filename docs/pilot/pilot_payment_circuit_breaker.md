# FLASHIN — автоматическая остановка пилота при денежных расхождениях

## Назначение

Runtime guard ограничивает checkout первыми 20 заказами. Payment circuit breaker дополняет его: критическая ошибка оплаты или возврата у любого заказа, занявшего `pilot_order_slots`, автоматически переводит `pilot_runtime_state` в `stopped`.

После автоматического STOP новые checkout блокируются. Уже созданные заказы, платежи и возвраты не удаляются и остаются доступны для reconciliation, ручной проверки и завершения операций.

Обычные заказы вне пилота не изменяют runtime state.

## События автоматического STOP

### Оплата YooKassa

- сумма или валюта провайдера не совпадает с заказом;
- провайдер вернул невалидную сумму, payment ID или статус;
- provider payment связан с другим order ID;
- локальная запись Payment связана с другим заказом;
- активный платёж не содержит confirmation URL;
- статус платёжной попытки требует ручной проверки;
- успешная оплата пришла после отмены заказа;
- отмена провайдера пришла после settlement;
- provider cancel конфликтует с локальным состоянием заказа.

### Reconciliation

- локальный и provider status не совпадают;
- local amount и provider amount отличаются.

### Возвраты

- provider refund временно не может быть создан или прочитан и заявка переходит в `refund_retry_required`;
- provider refund невалиден либо сумма не совпадает и заявка переходит в `refund_review_required`;
- финальная связь return/refund/order изменилась во время обработки;
- повторная финализация refund вызвала integrity conflict.

## Транзакционная модель

Есть два безопасных режима остановки.

### STOP в основной транзакции

Когда ошибка заканчивается контролируемым статусом review/retry, runtime STOP записывается в той же транзакции, что и:

- `payment.review_required`;
- `PaymentReconciliation.status=mismatch`;
- `refund_retry_required`;
- `refund_review_required`.

Статус операции и остановка пилота либо фиксируются вместе, либо откатываются вместе. Ошибка записи retry/review state не подавляется: endpoint возвращает `503`.

### Durable STOP после rollback

Если provider payload отклоняется исключением, незавершённая payment/refund транзакция сначала откатывается. Затем отдельная короткая DB-транзакция:

1. находит `PilotOrderSlot` по order ID;
2. блокирует singleton `pilot_runtime_state`;
3. переводит `active` или `completed` в `stopped`;
4. сохраняет нормализованный `auto:<reason>`.

Если STOP нельзя надёжно записать, API возвращает `503`, а не маскирует проблему обычным `409`/`502`.

## Release capability v2

Circuit breaker является обязательной частью подписанной capability `pilot_runtime_guard` версии 2. Immutable archive принимается для deploy или rollback только при наличии и wiring следующих файлов:

- `backend/services/pilot_circuit_breaker.py`;
- `backend/api/payments.py`;
- `backend/api/returns.py`;
- `backend/services/payment_reconciliation.py`.

Pilot checkout проверяет capability v2 одновременно у `current` и `previous` release pointer. Версия v1, отсутствующая подпись, несовпадающий release binding или архив без breaker wiring блокируют checkout с `503`.

Перед arm необходимо выполнить два успешных production deploy одной v2-линии. После этого текущий и предыдущий immutable releases должны отдельно пройти:

```bash
python3 scripts/pilot_release_capability.py verify --slot both --env .env
```

Rollback на v1 или на архив без payment/refund circuit breaker запрещён до остановки сервисов.

## Почему completed также может стать stopped

Двадцатый checkout закрывает приём новых заказов статусом `completed`, но денежная ошибка может появиться позже — при webhook, refund или reconciliation. Поэтому критическое событие имеет приоритет и переводит даже completed runtime в `stopped`, сохраняя `accepted_orders=20` и все order slots.

## Работа оператора

Проверить состояние:

```bash
make pilot-runtime-status
```

При автоматической остановке ожидается:

- `status=stopped`;
- `accepted_orders` не изменён;
- `remaining_orders` рассчитан из прежнего счётчика;
- `stop_reason` хранится только в БД и не раскрывает credentials/provider payload.

Дальнейшие действия:

1. остановить любые ручные попытки повторного checkout;
2. сверить order, payment/refund и provider данные;
3. завершить reconciliation или исправить интеграцию;
4. записать сценарий как fail/blocked в 20-order pilot control;
5. создать свежие provider/live evidence и новый signed admission;
6. использовать `--resume` только если причина устранена и остаются свободные slots.

Runtime после 20 принятых заказов или STOP нельзя обнулить штатной командой.

## Публичный API

Circuit breaker не раскрывает Telegram ID, provider payload или секреты.

Для обычных заказов сохраняются прежние HTTP-коды денежных ошибок. Для пилотного заказа дополнительно фиксируется STOP. Если сам safety STOP не удалось сохранить, возвращается `503`.
