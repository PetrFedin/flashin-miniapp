# flashin-miniapp — обязательные доработки / zero-new-cost execution

Snapshot: 2026-10-12. State: REQUIRED, not proof of implementation.

Canon: `README.md`, `Makefile`, existing readiness-gate/pilot-gate instructions.

| ID | P | Требуемое действие | Сквозная связь | Definition of Done |
|---|---|---|---|---|
| FLA-ZC-01 | P0 | Доказать Telegram storefront/customer journey | Telegram auth → catalog → sizes → cart → order → admin | E2E mock/sandbox, permission tests, real-device screenshots |
| FLA-ZC-02 | P0 | Fail-closed платежи и интеграции | orders → inventory lock → payment provider → returns | no real charge in tests, idempotency, verification signatures, no secret leaks |
| FLA-ZC-03 | P0 | Бесплатное восстановление данных | own DB → backup → restore → order reconciliation | isolated drill, no extra paid provider/storage |
| FLA-ZC-04 | P1 | Retention и аналитика | consent → purchase → repeat/CRM → admin insight | real attribution only, opt-out, cost and no fake data |

## Обязательный нулевой бюджет и безопасная передача
- Дополнительная стоимость внедрения/эксплуатации нового контура: **0 ₽**. Запрещено включать биллинг, pay-as-you-go, платные CI minutes/API/AI/БД/storage, платные публикации, платные провайдеры, приобретать серверы/домены или выполнять реальные платежи. Уже существующая инфраструктура не доказывает нулевой совокупной стоимости; фиксировать только **incremental cost**.
- Только локальные/open-source инструменты и **доказанные** свободные остатки бесплатных тарифов. Если бесплатные лимиты/учётная запись не проверены: `BLOCKED_NO_FREE_CAPACITY`.
- Не заменять production demo-приложением; не подключать чужие БД; не записывать секреты в GitHub; не обходить CI, review, migrations и RBAC.
- Сначала проверить latest main/PR/CI, затем взять первое доступное P0. Для каждой задачи: implementation files, exact SHA, passing unit+negative+browser/device tests, persistence/restore evidence где применимо, live deployed SHA где нужен, ledger стоимости, статус `PLANNED/IN_CODE/CI_PASS/LIVE_VERIFIED/BLOCKED`.
- Этот файл — **дополнение** к существующему authoritative technical specification / AGENTS.md, а не замена. Не утверждать внедрение на основании создания документа.

Следующий агент: продолжай с первого открытого P0, **делай код и тесты**, не создавай одни планы. В конце обнови issue/PR с evidence и источник истины проекта; merge — только по существующим политикам.
