import React, { useState } from "react";

import { AdminApiError, adminJson } from "./api.js";
import { normalizeOrderOperationsTrace } from "./orderOperationsTrace.js";

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
          <p>Единый read-only trace заказа: деньги, inventory ledger, провайдеры, fulfillment, события, уведомления и SLA.</p>
        </div>
        {trace && (
          <span className={`attention-badge ${trace.attention.required ? "attention" : "ok"}`}>
            {trace.attention.required ? "Требует вмешательства" : "Критических сигналов нет"}
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
