import React, { useEffect, useRef, useState } from "react";

import { hasAdminPermission, hasAnyAdminPermission } from "./adminPermissions.js";
import { AdminApiError, adminJson } from "./api.js";
import CatalogCommercePanel from "./CatalogCommercePanel.jsx";
import CatalogOperationsPanel from "./CatalogOperationsPanel.jsx";
import CatalogSupportOperationsPanel from "./CatalogSupportOperationsPanel.jsx";
import FulfillmentOperationsPanel from "./FulfillmentOperationsPanel.jsx";
import OrderOperationsTracePanel from "./OrderOperationsTracePanel.jsx";
import PilotOperationsPanel from "./PilotOperationsPanel.jsx";
import ServiceOperationsPanel from "./ServiceOperationsPanel.jsx";
import SupplyChainOperationsPanel from "./SupplyChainOperationsPanel.jsx";
import {
  buildBusinessEventReplayBody,
  canReplayBusinessEvent,
  compactEventError,
  eventStatusLabel,
  formatEventDate,
} from "./businessEvents.js";

const REFRESH_INTERVAL_MS = 30_000;

function statusCount(summary, status) {
  return Number(summary?.counts?.[status] || 0);
}

function payloadPreview(event) {
  if (!event) return "";
  if (event.payload_error) return event.payload_error;
  return JSON.stringify(event.payload || {}, null, 2);
}

function FulfillmentPanelMount({ onUnauthorized, session }) {
  if (session) {
    return <FulfillmentOperationsPanel onUnauthorized={onUnauthorized} session={session} />;
  }
  return <FulfillmentOperationsPanel onUnauthorized={onUnauthorized} />;
}

function ServicePanelMount({ onUnauthorized, session }) {
  if (session) {
    return <ServiceOperationsPanel onUnauthorized={onUnauthorized} session={session} />;
  }
  return <ServiceOperationsPanel onUnauthorized={onUnauthorized} />;
}

function BusinessEventsRecoveryPanel({ onUnauthorized, canReplayPermission }) {
  const [summary, setSummary] = useState(null);
  const [events, setEvents] = useState([]);
  const [statusFilter, setStatusFilter] = useState("failed");
  const [selectedEvent, setSelectedEvent] = useState(null);
  const [reason, setReason] = useState("");
  const [replacementPayload, setReplacementPayload] = useState("");
  const [loading, setLoading] = useState(false);
  const [detailLoading, setDetailLoading] = useState(false);
  const [replayingId, setReplayingId] = useState(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const requestSequence = useRef(0);
  const replayLocks = useRef(new Set());
  const selectedEventRef = useRef(selectedEvent);
  const unauthorizedHandler = useRef(onUnauthorized);

  useEffect(() => {
    unauthorizedHandler.current = onUnauthorized;
  }, [onUnauthorized]);

  useEffect(() => {
    selectedEventRef.current = selectedEvent;
  }, [selectedEvent]);

  function handleFailure(actionError) {
    if (actionError instanceof AdminApiError && actionError.status === 401) {
      unauthorizedHandler.current?.("Сессия администратора истекла. Войдите снова.");
      return;
    }
    setError(actionError.message || "Не удалось загрузить события.");
  }

  async function loadEvents(filter = statusFilter, { silent = false } = {}) {
    const sequence = requestSequence.current + 1;
    requestSequence.current = sequence;
    if (!silent) setLoading(true);
    setError("");
    try {
      const query = filter ? `?status=${encodeURIComponent(filter)}&limit=100` : "?limit=100";
      const [nextSummary, nextEvents] = await Promise.all([
        adminJson("/api/platform/admin/events/summary"),
        adminJson(`/api/platform/admin/events${query}`),
      ]);
      if (requestSequence.current !== sequence) return;
      setSummary(nextSummary);
      setEvents(nextEvents);

      const currentSelected = selectedEventRef.current;
      if (currentSelected) {
        const listVersion = nextEvents.find((event) => event.id === currentSelected.id);
        if (listVersion) {
          setSelectedEvent((current) => current ? { ...current, ...listVersion } : current);
        } else {
          setSelectedEvent(null);
          setReason("");
          setReplacementPayload("");
        }
      }
    } catch (actionError) {
      if (requestSequence.current === sequence) handleFailure(actionError);
    } finally {
      if (!silent && requestSequence.current === sequence) setLoading(false);
    }
  }

  useEffect(() => {
    loadEvents(statusFilter);
    const timer = window.setInterval(
      () => loadEvents(statusFilter, { silent: true }),
      REFRESH_INTERVAL_MS,
    );
    return () => {
      window.clearInterval(timer);
      requestSequence.current += 1;
    };
  }, [statusFilter]);

  async function openEvent(eventId) {
    setDetailLoading(true);
    setError("");
    setNotice("");
    try {
      const event = await adminJson(`/api/platform/admin/events/${eventId}`);
      setSelectedEvent(event);
      setReason("");
      setReplacementPayload("");
    } catch (actionError) {
      handleFailure(actionError);
    } finally {
      setDetailLoading(false);
    }
  }

  async function replaySelectedEvent() {
    const event = selectedEvent;
    if (!canReplayPermission || !canReplayBusinessEvent(event) || replayLocks.current.has(event.id)) return;

    let body;
    try {
      body = buildBusinessEventReplayBody(reason, replacementPayload);
    } catch (validationError) {
      setError(validationError.message);
      return;
    }

    const confirmed = window.confirm(
      `Вернуть BusinessEvent #${event.id} (${event.event_type}) в очередь? `
      + "Повторная обработка может вызвать внешний webhook.",
    );
    if (!confirmed) return;

    replayLocks.current.add(event.id);
    setReplayingId(event.id);
    setError("");
    setNotice("");
    try {
      const replayed = await adminJson(`/api/platform/admin/events/${event.id}/replay`, {
        method: "POST",
        body: JSON.stringify(body),
        dedupeKey: `business-event-replay:${event.id}`,
      });
      selectedEventRef.current = replayed;
      setSelectedEvent(replayed);
      setReason("");
      setReplacementPayload("");
      setNotice(`Событие #${event.id} возвращено в очередь. Контролируйте переход в processed.`);
      setStatusFilter("");
      await loadEvents("", { silent: true });
    } catch (actionError) {
      handleFailure(actionError);
    } finally {
      replayLocks.current.delete(event.id);
      setReplayingId(null);
    }
  }

  return (
    <section className="event-recovery">
      <div className="section-heading">
        <div>
          <h2>BusinessEvent recovery</h2>
          <p>Диагностика terminal-ошибок и контролируемый возврат события в worker queue.</p>
        </div>
        <button
          type="button"
          onClick={() => loadEvents(statusFilter)}
          disabled={loading}
        >
          {loading ? "Обновление…" : "Обновить события"}
        </button>
      </div>

      {error && <div className="error" role="alert">{error}<button type="button" onClick={() => setError("")}>×</button></div>}
      {notice && <div className="notice" role="status">{notice}<button type="button" onClick={() => setNotice("")}>×</button></div>}

      <div className="kpis event-kpis" aria-label="Статусы BusinessEvent">
        <button type="button" className={statusFilter === "failed" ? "active" : ""} onClick={() => setStatusFilter("failed")}>
          <span>Требуют вмешательства</span>
          <strong>{statusCount(summary, "failed")}</strong>
        </button>
        <button type="button" className={statusFilter === "pending" ? "active" : ""} onClick={() => setStatusFilter("pending")}>
          <span>В очереди</span>
          <strong>{statusCount(summary, "pending")}</strong>
        </button>
        <button type="button" className={statusFilter === "processed" ? "active" : ""} onClick={() => setStatusFilter("processed")}>
          <span>Обработаны</span>
          <strong>{statusCount(summary, "processed")}</strong>
        </button>
        <button type="button" className={statusFilter === "" ? "active" : ""} onClick={() => setStatusFilter("")}>
          <span>Все</span>
          <strong>{statusCount(summary, "failed") + statusCount(summary, "pending") + statusCount(summary, "processed")}</strong>
        </button>
      </div>

      {summary?.oldest_failed_at && (
        <p className="event-warning">
          Самая старая необработанная ошибка: <b>{formatEventDate(summary.oldest_failed_at)}</b>
        </p>
      )}

      <div className="event-layout">
        <div className="event-list" aria-busy={loading}>
          {!loading && events.length === 0 && (
            <p>{statusFilter === "failed" ? "Terminal-ошибок нет." : "События не найдены."}</p>
          )}
          {events.map((event) => (
            <button
              type="button"
              key={event.id}
              className={`event-row ${selectedEvent?.id === event.id ? "selected" : ""}`}
              onClick={() => openEvent(event.id)}
              disabled={detailLoading}
            >
              <span className={`event-status ${event.status}`}>{eventStatusLabel(event.status)}</span>
              <strong>#{event.id} · {event.event_type}</strong>
              <span>{event.aggregate_type || "—"} {event.aggregate_id || ""}</span>
              <span>Попыток: {event.attempts} · Replay: {event.replay_count}</span>
              <span>{formatEventDate(event.failed_at || event.processed_at || event.created_at)}</span>
              {event.status === "failed" && <small>{compactEventError(event.last_error)}</small>}
            </button>
          ))}
        </div>

        <aside className="event-detail">
          {detailLoading && <p>Загрузка события…</p>}
          {!detailLoading && !selectedEvent && <p>Выберите событие для просмотра диагностики.</p>}
          {!detailLoading && selectedEvent && (
            <>
              <div className="section-heading compact">
                <div>
                  <h3>Событие #{selectedEvent.id}</h3>
                  <p>{selectedEvent.event_type}</p>
                </div>
                <span className={`event-status ${selectedEvent.status}`}>
                  {eventStatusLabel(selectedEvent.status)}
                </span>
              </div>
              <dl className="event-metadata">
                <div><dt>Aggregate</dt><dd>{selectedEvent.aggregate_type || "—"} {selectedEvent.aggregate_id || ""}</dd></div>
                <div><dt>Попытки</dt><dd>{selectedEvent.attempts}</dd></div>
                <div><dt>Replay</dt><dd>{selectedEvent.replay_count}</dd></div>
                <div><dt>Последняя попытка</dt><dd>{formatEventDate(selectedEvent.last_attempt_at)}</dd></div>
                <div><dt>Failed</dt><dd>{formatEventDate(selectedEvent.failed_at)}</dd></div>
                <div><dt>Resolved</dt><dd>{formatEventDate(selectedEvent.resolved_at)}</dd></div>
              </dl>

              <h4>Последняя ошибка</h4>
              <pre className="event-error-detail">{selectedEvent.last_error || "Ошибка не зафиксирована"}</pre>
              <h4>Сохранённый payload</h4>
              <pre className="event-payload">{payloadPreview(selectedEvent)}</pre>

              {canReplayPermission && canReplayBusinessEvent(selectedEvent) ? (
                <div className="event-replay-form">
                  <h4>Вернуть в очередь</h4>
                  <label>
                    Причина и выполненное исправление
                    <textarea
                      value={reason}
                      maxLength={500}
                      onChange={(event) => setReason(event.target.value)}
                      placeholder="Например: исправлено сопоставление destination, проверен downstream idempotency key"
                    />
                  </label>
                  <label>
                    Исправленный payload — необязательно
                    <textarea
                      className="event-payload-input"
                      value={replacementPayload}
                      onChange={(event) => setReplacementPayload(event.target.value)}
                      placeholder='Оставьте пустым для повторного использования сохранённого payload. Иначе укажите JSON-объект: {"order_id": 123}'
                    />
                  </label>
                  <p className="event-warning">
                    Перед replay устраните первопричину и убедитесь, что downstream операция идемпотентна.
                  </p>
                  <button
                    type="button"
                    className="danger"
                    onClick={replaySelectedEvent}
                    disabled={replayingId === selectedEvent.id}
                  >
                    {replayingId === selectedEvent.id ? "Возврат в очередь…" : "Подтвердить replay"}
                  </button>
                </div>
              ) : canReplayBusinessEvent(selectedEvent) ? (
                <p>Просмотр доступен, но replay требует permission events.replay.</p>
              ) : (
                <p>Replay доступен только для terminal-статуса failed.</p>
              )}
            </>
          )}
        </aside>
      </div>
    </section>
  );
}

export default function BusinessEventsPanel({ onUnauthorized, session }) {
  const canSecurityRead = hasAdminPermission(session, "security.read");
  const canProductsRead = hasAdminPermission(session, "products.read");
  const canShowroomRead = hasAdminPermission(session, "showroom.read");
  const canOrdersRead = hasAdminPermission(session, "orders.read");
  const canEventsRead = hasAdminPermission(session, "events.read");
  const canEventsReplay = hasAdminPermission(session, "events.replay");
  const canService = hasAnyAdminPermission(session, ["support.write", "privacy.read", "orders.read"]);

  return (
    <>
      {canSecurityRead && <PilotOperationsPanel onUnauthorized={onUnauthorized} />}
      {(canProductsRead || canShowroomRead) && <CatalogSupportOperationsPanel onUnauthorized={onUnauthorized} session={session} />}
      {canProductsRead && <CatalogCommercePanel onUnauthorized={onUnauthorized} session={session} />}
      {canProductsRead && <CatalogOperationsPanel onUnauthorized={onUnauthorized} session={session} />}
      {canProductsRead && <SupplyChainOperationsPanel onUnauthorized={onUnauthorized} session={session} />}
      {canOrdersRead && <OrderOperationsTracePanel onUnauthorized={onUnauthorized} />}
      {canOrdersRead && <FulfillmentPanelMount onUnauthorized={onUnauthorized} session={session} />}
      {canService && <ServicePanelMount onUnauthorized={onUnauthorized} session={session} />}
      {canEventsRead && (
        <BusinessEventsRecoveryPanel
          onUnauthorized={onUnauthorized}
          canReplayPermission={canEventsReplay}
        />
      )}
    </>
  );
}
