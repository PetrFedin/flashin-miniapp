import React, { useState } from "react";

import { AdminApiError, adminJson } from "./api.js";
import { normalizeOrderOperationsTrace } from "./orderOperationsTrace.js";

const STAGE_LABELS = {
  payment: "Оплата",
  inventory: "Склад",
  moysklad: "МойСклад",
  fulfillment: "Фулфилмент",
  refunds: "Возвраты",
  notifications: "Уведомления",
};

const SIGNAL_LABELS = {
  business_events: "BusinessEvent recovery",
};

const STATUS_LABELS = {
  PASS: "PASS · согласовано",
  PENDING: "PENDING · нормальный прогресс",
  REVIEW: "REVIEW · нужна проверка",
  BLOCKED: "BLOCKED · дальнейший шаг остановлен",
};

const REASON_LABELS = {
  payment_review_required: "Оплата переведена в ручную проверку.",
  payment_terminal_failure: "Платёж завершился ошибкой и не подтверждает расчёт.",
  order_paid_without_settled_payment_record: "Заказ помечен оплаченным без согласованной записи платежа.",
  settled_payment_amount_mismatch: "Сумма подтверждённого платежа расходится с суммой заказа.",
  payment_settled: "Подтверждённый платёж согласован с заказом.",
  cancelled_without_captured_payment: "Заказ отменён без захваченной оплаты.",
  payment_not_settled_yet: "Оплата ещё не перешла в подтверждённое состояние.",
  inventory_ledger_invalid: "Inventory ledger нарушает stock/reserve инварианты.",
  cancelled_order_has_unreversed_commit: "У отменённого заказа остался commit без обратного движения.",
  cancelled_order_inventory_reconciled: "Складские движения отменённого заказа согласованы.",
  refunded_order_missing_inventory_return: "Возврат денег завершён, но stock return не подтверждён.",
  refund_inventory_reconciled: "Возврат товара в inventory ledger согласован.",
  inventory_commit_recorded: "Продажа зафиксирована commit-движением склада.",
  fulfilled_order_missing_inventory_commit: "Отгруженный заказ не имеет ожидаемого inventory commit.",
  inventory_reserved_not_committed_yet: "Товар зарезервирован и ждёт следующего этапа fulfillment.",
  paid_order_inventory_not_recorded: "После оплаты ещё нет ожидаемого складского движения.",
  inventory_not_expected_before_payment: "Складская фиксация ожидается после оплаты.",
  moysklad_command_failed: "Команда МойСклад завершилась ошибкой или review state.",
  moysklad_command_status_unknown: "Получен неизвестный статус команды МойСклад.",
  moysklad_command_in_progress: "Команда МойСклад ещё выполняется.",
  terminal_order_moysklad_not_terminal: "Заказ завершён, а команда МойСклад ещё не завершилась.",
  moysklad_commands_terminal_success: "Команды МойСклад завершились успешно.",
  moysklad_not_required_for_unpaid_cancellation: "Для неоплаченной отмены команда МойСклад не требуется.",
  terminal_order_missing_moysklad_command: "Для завершённого заказа нет ожидаемой команды МойСклад.",
  moysklad_command_not_terminal_yet: "Команда МойСклад ещё не сформирована или не дошла до terminal state.",
  fulfillment_not_required_for_cancelled_order: "Для отменённого заказа fulfillment не требуется.",
  fulfillment_held_for_payment_review: "Fulfillment удерживается до решения payment review.",
  fulfillment_task_blocked: "Fulfillment-задача заблокирована и требует проверки.",
  completed_order_missing_fulfillment_task: "Завершённый заказ не имеет ожидаемой fulfillment-задачи.",
  fulfillment_completed: "Fulfillment и доставка согласованы как завершённые.",
  shipment_in_progress: "Отгрузка передана в доставку и ещё не завершена.",
  fulfillment_task_status_unknown: "Получен неизвестный статус fulfillment-задачи.",
  fulfillment_sla_overdue: "Fulfillment вышел за допустимый SLA.",
  fulfillment_in_progress: "Fulfillment выполняется в штатном статусе.",
  paid_order_fulfillment_not_started: "Оплата подтверждена, fulfillment ещё не стартовал.",
  fulfillment_not_expected_before_payment: "Fulfillment ожидается после подтверждения оплаты.",
  no_refund_requested: "Возврат не запрашивался.",
  refund_reconciliation_required: "Возврат требует ручной reconciliation/retry проверки.",
  refund_terminal_failure: "Возврат завершился терминальной ошибкой.",
  refund_in_progress: "Возврат выполняется у провайдера.",
  refunds_settled: "Возвраты завершены и согласованы.",
  refund_status_unknown: "Получен неизвестный статус возврата.",
  notification_delivery_failed: "Доставка уведомления завершилась ошибкой.",
  notification_delivery_in_progress: "Уведомление находится в очереди доставки.",
  notifications_delivered: "Уведомления доставлены транспортом.",
  settled_order_has_no_notification_evidence: "Для подтверждённого/завершённого заказа нет notification evidence.",
  notification_not_expected_yet: "Уведомление ожидается после следующего бизнес-события.",
  business_event_recovery_required: "Есть failed BusinessEvent, который требует recovery-проверки.",
  business_event_processing_in_progress: "BusinessEvent ещё обрабатывается воркером в штатном режиме.",
};

const ACTION_LABELS = {
  none: "Действий не требуется.",
  wait_for_payment_callback: "Ждать callback/reconciliation оплаты; не повторять списание вручную.",
  inspect_payment_review: "Открыть payment review и сверить provider settlement перед продолжением.",
  inspect_inventory_ledger: "Сверить inventory ledger и stock/reserve инварианты.",
  wait_for_fulfillment: "Продолжить штатный fulfillment в пределах SLA.",
  wait_for_provider_command: "Ждать terminal state очереди провайдера; не дублировать команду.",
  inspect_moysklad_command_queue: "Проверить очередь команд МойСклад и связанные recovery-сигналы.",
  inspect_fulfillment: "Проверить fulfillment task, доставку и SLA.",
  wait_for_delivery: "Ждать подтверждение доставки в штатном контуре.",
  inspect_refund_reconciliation: "Проверить refund reconciliation до повторного внешнего действия.",
  wait_for_refund_settlement: "Ждать terminal state возврата у платёжного провайдера.",
  inspect_notification_delivery: "Проверить notification delivery/retry state без раскрытия текста сообщения.",
  wait_for_notification_delivery: "Ждать штатную доставку уведомления.",
  inspect_business_event_recovery: "Открыть BusinessEvent recovery и проверить replay-safety до внешнего действия.",
  wait_for_business_event_worker: "Ждать штатную обработку BusinessEvent; не запускать ручной replay.",
};

function money(value, currency) {
  try {
    return new Intl.NumberFormat("ru-RU", {
      style: "currency",
      currency: currency || "RUB",
      maximumFractionDigits: 2,
    }).format(Number(value || 0));
  } catch {
    return `${Number(value || 0).toFixed(2)} ${currency || "RUB"}`;
  }
}

function lifecycleBadgeClass(status) {
  return status === "BLOCKED" || status === "REVIEW" ? "attention" : "ok";
}

function LifecycleEvidence({ item, title }) {
  return (
    <article className="service-card" data-stage={item.key}>
      <div className="service-item-heading">
        <h3>{title}</h3>
        <span className={`attention-badge ${lifecycleBadgeClass(item.status)}`}>
          {item.status}
        </span>
      </div>
      <p>{REASON_LABELS[item.reason] || "Статус требует сверки по безопасному trace-контракту."}</p>
      <p><strong>Следующий шаг:</strong> {ACTION_LABELS[item.nextAction] || "Остановиться и проверить trace до внешнего действия."}</p>
      {item.evidence.length > 0 && (
        <dl className="pilot-metrics-list">
          {item.evidence.map((entry, index) => (
            <div key={`${item.key}-${index}`}><dt>Evidence</dt><dd>{entry}</dd></div>
          ))}
        </dl>
      )}
    </article>
  );
}

export default function OrderOperationsTracePanel({ onUnauthorized }) {
  const [orderId, setOrderId] = useState("");
  const [trace, setTrace] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  async function loadTrace() {
    const numericOrderId = Number(orderId);
    if (!Number.isInteger(numericOrderId) || numericOrderId <= 0) {
      setTrace(null);
      setError("Введите положительный ID заказа.");
      return;
    }
    setLoading(true);
    setError("");
    setTrace(null);
    try {
      const payload = await adminJson(`/api/ops/orders/${numericOrderId}/trace`, {
        headers: { "Cache-Control": "no-cache" },
      });
      const normalized = normalizeOrderOperationsTrace(payload);
      if (!normalized.valid) {
        throw new Error("Сервер вернул некорректный incident-trace. Состояние считается требующим внимания.");
      }
      setTrace(normalized);
    } catch (actionError) {
      if (actionError instanceof AdminApiError && actionError.status === 401) {
        onUnauthorized?.("Сессия администратора истекла. Войдите снова.");
        return;
      }
      if (actionError instanceof AdminApiError && actionError.status === 403) {
        setError("Недостаточно прав: incident trace требует orders.read.");
      } else if (actionError instanceof AdminApiError && actionError.status === 404) {
        setError(`Заказ #${numericOrderId} не найден.`);
      } else {
        setError(actionError?.message || "Incident trace заказа недоступен. Не считайте операцию подтверждённой до восстановления диагностики.");
      }
    } finally {
      setLoading(false);
    }
  }

  return (
    <section className="service-operations" aria-labelledby="order-operations-trace-title">
      <div className="section-title-row">
        <div>
          <h2 id="order-operations-trace-title">Диагностика сделки</h2>
          <p>Единый read-only trace заказа: lifecycle verdict, деньги, inventory ledger, провайдеры, fulfillment, события, уведомления и SLA.</p>
        </div>
        {trace && (
          <span className={`attention-badge ${lifecycleBadgeClass(trace.reconciliation.overallStatus)}`}>
            {trace.reconciliation.valid
              ? STATUS_LABELS[trace.reconciliation.overallStatus]
              : "REVIEW · lifecycle evidence недоступен"}
          </span>
        )}
      </div>

      <div className="form-grid">
        <input
          aria-label="ID заказа для диагностики"
          type="number"
          min="1"
          step="1"
          value={orderId}
          onChange={(event) => setOrderId(event.target.value)}
          onKeyDown={(event) => event.key === "Enter" && loadTrace()}
          placeholder="ID заказа"
        />
        <button type="button" onClick={loadTrace} disabled={loading}>
          {loading ? "Проверка…" : "Открыть trace"}
        </button>
      </div>

      {error && <div className="error" role="alert">{error}<button type="button" onClick={() => setError("")}>×</button></div>}

      {trace && (
        <>
          <dl className="event-metadata">
            <div><dt>Заказ</dt><dd>#{trace.order.id}</dd></div>
            <div><dt>Order status</dt><dd>{trace.order.status}</dd></div>
            <div><dt>Payment</dt><dd>{trace.order.paymentStatus}</dd></div>
            <div><dt>Delivery</dt><dd>{trace.order.deliveryStatus}</dd></div>
            <div><dt>Сумма</dt><dd>{money(trace.order.totalAmount, trace.order.currency)}</dd></div>
            {trace.requestId && <div><dt>Request ID</dt><dd>{trace.requestId}</dd></div>}
          </dl>

          <div className="section-title-row">
            <div>
              <h3>Lifecycle reconciliation</h3>
              <p>PENDING — штатное ожидание и не требует ручного действия. REVIEW/BLOCKED — операторская проверка до следующего рискованного шага.</p>
            </div>
            <span className={`attention-badge ${trace.reconciliation.requiresOperatorAction ? "attention" : "ok"}`}>
              {trace.reconciliation.requiresOperatorAction ? "Нужно действие оператора" : "Ручное действие не требуется"}
            </span>
          </div>

          {trace.reconciliation.valid ? (
            <>
              <div className="service-grid" data-testid="order-lifecycle-reconciliation">
                {trace.reconciliation.stages.map((stage) => (
                  <LifecycleEvidence
                    key={stage.key}
                    item={stage}
                    title={STAGE_LABELS[stage.key] || stage.key}
                  />
                ))}
              </div>
              {trace.reconciliation.operationalSignals.length > 0 && (
                <div data-testid="order-lifecycle-operational-signals">
                  <div className="section-title-row">
                    <div>
                      <h3>Сквозные recovery-сигналы</h3>
                      <p>Worker/recovery state влияет на общий verdict, но не подменяет шесть бизнес-этапов сделки.</p>
                    </div>
                  </div>
                  <div className="service-grid">
                    {trace.reconciliation.operationalSignals.map((signal) => (
                      <LifecycleEvidence
                        key={signal.key}
                        item={signal}
                        title={SIGNAL_LABELS[signal.key] || signal.key}
                      />
                    ))}
                  </div>
                </div>
              )}
            </>
          ) : (
            <p className="event-warning" role="alert">
              Lifecycle evidence отсутствует или неполно. Состояние считается REVIEW; не выполняйте ручные provider/money действия до восстановления trace.
            </p>
          )}

          <div className="service-grid">
            <article className="service-card">
              <div className="service-item-heading"><h3>Деньги и возвраты</h3></div>
              <dl className="pilot-metrics-list">
                <div><dt>Payment records</dt><dd>{trace.counts.payments}</dd></div>
                <div><dt>Payment events</dt><dd>{trace.counts.paymentEvents}</dd></div>
                <div><dt>Returns/refunds</dt><dd>{trace.counts.returns}</dd></div>
              </dl>
            </article>

            <article className="service-card">
              <div className="service-item-heading"><h3>Inventory ledger</h3></div>
              <dl className="pilot-metrics-list">
                <div><dt>Movements</dt><dd>{trace.counts.inventoryMovements}</dd></div>
                <div><dt>Нарушения инвариантов</dt><dd>{trace.attention.inventoryInvalidRows}</dd></div>
              </dl>
            </article>

            <article className="service-card">
              <div className="service-item-heading"><h3>Провайдеры</h3></div>
              <dl className="pilot-metrics-list">
                <div><dt>Команд всего</dt><dd>{trace.counts.providerCommands}</dd></div>
                <div><dt>В работе</dt><dd>{trace.attention.providerCommandsActionable}</dd></div>
                <div><dt>Failed/review</dt><dd>{trace.attention.providerFailures}</dd></div>
              </dl>
            </article>

            <article className="service-card">
              <div className="service-item-heading"><h3>Операции</h3></div>
              <dl className="pilot-metrics-list">
                <div><dt>Fulfillment tasks</dt><dd>{trace.counts.fulfillment}</dd></div>
                <div><dt>Business events</dt><dd>{trace.counts.businessEvents}</dd></div>
                <div><dt>Unresolved events</dt><dd>{trace.attention.businessEventsUnresolved}</dd></div>
                <div><dt>Failed events</dt><dd>{trace.attention.businessEventsFailed}</dd></div>
              </dl>
            </article>

            <article className="service-card">
              <div className="service-item-heading"><h3>Клиентский контур</h3></div>
              <dl className="pilot-metrics-list">
                <div><dt>Notifications</dt><dd>{trace.counts.notifications}</dd></div>
                <div><dt>Failed notifications</dt><dd>{trace.attention.failedNotifications}</dd></div>
                <div><dt>SLA events</dt><dd>{trace.counts.sla}</dd></div>
                <div><dt>Overdue SLA</dt><dd>{trace.attention.overdueSla}</dd></div>
              </dl>
            </article>
          </div>

          <p className="event-warning">
            Trace показывает только безопасные статусы, счётчики и stock-инварианты. Provider payload, inventory source, idempotency keys, Telegram IDs и тексты уведомлений в Admin UI не выводятся.
          </p>
        </>
      )}
    </section>
  );
}