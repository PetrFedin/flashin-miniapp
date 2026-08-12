import React, { useEffect, useMemo, useRef, useState } from "react";

import { hasAdminPermission } from "./adminPermissions.js";
import { AdminApiError, adminJson } from "./api.js";
import {
  PRIVACY_STATUS_LABELS,
  PRIVACY_TYPE_LABELS,
  RETURN_STATUS_LABELS,
  SUPPORT_PRIORITY_LABELS,
  SUPPORT_STATUS_LABELS,
  canApproveReturn,
  canProcessPrivacy,
  normalizeAdminAssignment,
  normalizeRefundAmount,
  serviceAttentionCount,
  supportTransitions,
} from "./serviceOperations.js";

const SERVICE_ENDPOINTS = Object.freeze({
  support: "/api/support/admin/tickets",
  privacy: "/api/privacy/admin/requests",
  returns: "/api/admin/returns",
});

function money(value, currency = "RUB") {
  return new Intl.NumberFormat("ru-RU", {
    style: "currency",
    currency: currency || "RUB",
    maximumFractionDigits: 2,
  }).format(Number(value || 0));
}

function operationError(error) {
  if (error instanceof AdminApiError && error.status === 403) {
    return "Недостаточно прав для этого раздела.";
  }
  return error?.message || "Операция не выполнена.";
}

export default function ServiceOperationsPanel({ onUnauthorized, session }) {
  const canSupport = hasAdminPermission(session, "support.write");
  const canPrivacyRead = hasAdminPermission(session, "privacy.read");
  const canPrivacyWrite = hasAdminPermission(session, "privacy.write");
  const canReturnsRead = hasAdminPermission(session, "orders.read");
  const canReturnsWrite = hasAdminPermission(session, "orders.write");

  const [tickets, setTickets] = useState([]);
  const [privacyRequests, setPrivacyRequests] = useState([]);
  const [returns, setReturns] = useState([]);
  const [sectionErrors, setSectionErrors] = useState({});
  const [supportDrafts, setSupportDrafts] = useState({});
  const [refundAmounts, setRefundAmounts] = useState({});
  const [busyKeys, setBusyKeys] = useState(() => new Set());
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const locks = useRef(new Set());

  const attentionCount = useMemo(() => serviceAttentionCount({
    tickets: canSupport ? tickets : [],
    privacy: canPrivacyRead ? privacyRequests : [],
    returns: canReturnsRead ? returns : [],
  }), [tickets, privacyRequests, returns, canSupport, canPrivacyRead, canReturnsRead]);

  function isBusy(key) {
    return busyKeys.has(key);
  }

  function markBusy(key, active) {
    setBusyKeys((current) => {
      const next = new Set(current);
      if (active) next.add(key);
      else next.delete(key);
      return next;
    });
  }

  async function run(key, operation, successMessage = "") {
    if (locks.current.has(key)) return null;
    locks.current.add(key);
    markBusy(key, true);
    setError("");
    setNotice("");
    try {
      const result = await operation();
      if (successMessage) setNotice(successMessage);
      return result;
    } catch (actionError) {
      if (actionError instanceof AdminApiError && actionError.status === 401) {
        onUnauthorized?.("Сессия администратора истекла. Войдите снова.");
      } else {
        setError(operationError(actionError));
      }
      return null;
    } finally {
      locks.current.delete(key);
      markBusy(key, false);
    }
  }

  async function load() {
    const entries = [];
    if (canSupport) entries.push(["support", SERVICE_ENDPOINTS.support]);
    if (canPrivacyRead) entries.push(["privacy", SERVICE_ENDPOINTS.privacy]);
    if (canReturnsRead) entries.push(["returns", SERVICE_ENDPOINTS.returns]);

    if (!canSupport) setTickets([]);
    if (!canPrivacyRead) setPrivacyRequests([]);
    if (!canReturnsRead) setReturns([]);
    if (!entries.length) {
      setSectionErrors({});
      return;
    }

    const results = await Promise.allSettled(entries.map(([, path]) => adminJson(path)));
    const nextErrors = {};
    let unauthorized = false;

    results.forEach((result, index) => {
      const [section] = entries[index];
      if (result.status === "fulfilled") {
        if (section === "support") setTickets(Array.isArray(result.value) ? result.value : []);
        if (section === "privacy") setPrivacyRequests(Array.isArray(result.value) ? result.value : []);
        if (section === "returns") setReturns(Array.isArray(result.value) ? result.value : []);
      } else if (result.reason instanceof AdminApiError && result.reason.status === 401) {
        unauthorized = true;
      } else {
        nextErrors[section] = operationError(result.reason);
      }
    });

    setSectionErrors(nextErrors);
    if (unauthorized) onUnauthorized?.("Сессия администратора истекла. Войдите снова.");
  }

  useEffect(() => {
    run("initial-service-load", load);
  }, [canSupport, canPrivacyRead, canReturnsRead]);

  function supportDraft(ticket) {
    return supportDrafts[ticket.id] || {
      status: ticket.status,
      priority: ticket.priority,
      assigned_admin_id: ticket.assigned_admin_id ?? "",
    };
  }

  function setSupportDraft(ticketId, patch) {
    setSupportDrafts((current) => ({
      ...current,
      [ticketId]: { ...current[ticketId], ...patch },
    }));
  }

  async function updateTicket(ticket) {
    if (!canSupport) {
      setError("Недостаточно прав: управление обращениями требует support.write.");
      return;
    }
    const draft = supportDraft(ticket);
    const assignment = normalizeAdminAssignment(draft.assigned_admin_id);
    if (assignment.error) {
      setError(assignment.error);
      return;
    }
    if (assignment.value === null && ticket.assigned_admin_id != null) {
      setError("Снятие ответственного не поддерживается. Назначьте другого активного администратора.");
      return;
    }

    const payload = {};
    if (draft.status && draft.status !== ticket.status) payload.status = draft.status;
    if (draft.priority && draft.priority !== ticket.priority) payload.priority = draft.priority;
    if (assignment.value !== null && assignment.value !== ticket.assigned_admin_id) {
      payload.assigned_admin_id = assignment.value;
    }
    if (!Object.keys(payload).length) {
      setError("Выберите новый статус, приоритет или ответственного обращения.");
      return;
    }

    const updated = await run(
      `support-${ticket.id}`,
      () => adminJson(`/api/support/admin/tickets/${ticket.id}`, {
        method: "PATCH",
        body: JSON.stringify(payload),
      }),
      `Обращение #${ticket.id} обновлено.`,
    );
    if (!updated) return;
    setTickets((current) => current.map((item) => item.id === updated.id ? updated : item));
    setSupportDrafts((current) => {
      const next = { ...current };
      delete next[ticket.id];
      return next;
    });
  }

  async function processPrivacy(request) {
    if (!canPrivacyWrite) {
      setError("Недостаточно прав: исполнение privacy-запроса требует privacy.write.");
      return;
    }
    const typeLabel = PRIVACY_TYPE_LABELS[request.request_type] || request.request_type;
    const warning = request.request_type === "delete"
      ? `Исполнить запрос #${request.id} «${typeLabel}»? Данные клиента будут необратимо обезличены.`
      : `Исполнить запрос #${request.id} «${typeLabel}»?`;
    if (!window.confirm(warning)) return;

    const result = await run(
      `privacy-${request.id}`,
      () => adminJson(`/api/privacy/admin/requests/${request.id}/process`, { method: "POST" }),
      `Privacy-запрос #${request.id} исполнен.`,
    );
    if (!result) return;
    await load();
  }

  async function approveReturn(item) {
    if (!canReturnsWrite) {
      setError("Недостаточно прав: подтверждение refund требует orders.write.");
      return;
    }
    const rawAmount = refundAmounts[item.id] ?? item.refundable_balance;
    const validation = normalizeRefundAmount(rawAmount, item.refundable_balance);
    if (validation.error) {
      setError(validation.error);
      return;
    }
    const amount = validation.value;
    if (!window.confirm(
      `Подтвердить возврат #${item.id} по заказу #${item.order_id} на ${money(amount, item.currency)}?`,
    )) return;

    const result = await run(
      `return-${item.id}`,
      () => adminJson("/api/returns/admin/approve", {
        method: "POST",
        body: JSON.stringify({ return_id: item.id, amount }),
      }),
      `Возврат #${item.id} передан платёжному провайдеру.`,
    );
    if (!result) return;
    await load();
  }

  return (
    <section className="service-operations" aria-labelledby="service-operations-title">
      <div className="section-title-row">
        <div>
          <h2 id="service-operations-title">Service Operations</h2>
          <p>Обращения, персональные данные и возвраты до конечного операторского действия.</p>
        </div>
        <div className={`attention-badge ${attentionCount ? "attention" : "ok"}`}>
          Требуют действия: {attentionCount}
        </div>
        <button
          onClick={() => run("refresh-service", load, "Service Operations обновлён.")}
          disabled={isBusy("refresh-service")}
        >
          Обновить сервис
        </button>
      </div>

      {error && <div className="error" role="alert">{error}<button onClick={() => setError("")}>×</button></div>}
      {notice && <div className="notice" role="status">{notice}<button onClick={() => setNotice("")}>×</button></div>}

      <div className="service-grid">
        {canSupport && (
          <article className="service-card" aria-labelledby="support-queue-title">
            <h3 id="support-queue-title">Обращения клиентов</h3>
            {sectionErrors.support && <p className="error-inline">{sectionErrors.support}</p>}
            {!sectionErrors.support && !tickets.length && <p>Открытых обращений нет.</p>}
            {tickets.map((ticket) => {
              const draft = supportDraft(ticket);
              const statuses = [ticket.status, ...supportTransitions(ticket.status)];
              return (
                <div className="service-item" key={ticket.id}>
                  <div className="service-item-heading">
                    <b>#{ticket.id} · {ticket.subject}</b>
                    <span>{SUPPORT_STATUS_LABELS[ticket.status] || ticket.status}</span>
                  </div>
                  <p>{ticket.message}</p>
                  <small>{ticket.order_id ? `Заказ #${ticket.order_id}` : "Без привязки к заказу"}</small>
                  <div className="service-controls">
                    <label>
                      Статус
                      <select
                        aria-label={`Статус обращения ${ticket.id}`}
                        value={draft.status || ticket.status}
                        onChange={(event) => setSupportDraft(ticket.id, { status: event.target.value })}
                        disabled={isBusy(`support-${ticket.id}`)}
                      >
                        {statuses.map((status) => (
                          <option value={status} key={status}>{SUPPORT_STATUS_LABELS[status] || status}</option>
                        ))}
                      </select>
                    </label>
                    <label>
                      Приоритет
                      <select
                        aria-label={`Приоритет обращения ${ticket.id}`}
                        value={draft.priority || ticket.priority}
                        onChange={(event) => setSupportDraft(ticket.id, { priority: event.target.value })}
                        disabled={isBusy(`support-${ticket.id}`)}
                      >
                        {Object.entries(SUPPORT_PRIORITY_LABELS).map(([value, label]) => (
                          <option value={value} key={value}>{label}</option>
                        ))}
                      </select>
                    </label>
                    <label>
                      Ответственный Admin ID
                      <input
                        aria-label={`Ответственный обращения ${ticket.id}`}
                        type="number"
                        min="1"
                        step="1"
                        placeholder="ID активного администратора"
                        value={draft.assigned_admin_id ?? ""}
                        onChange={(event) => setSupportDraft(ticket.id, { assigned_admin_id: event.target.value })}
                        disabled={isBusy(`support-${ticket.id}`)}
                      />
                    </label>
                    <button
                      onClick={() => updateTicket(ticket)}
                      disabled={isBusy(`support-${ticket.id}`)}
                    >
                      Сохранить обращение
                    </button>
                  </div>
                </div>
              );
            })}
          </article>
        )}

        {canPrivacyRead && (
          <article className="service-card" aria-labelledby="privacy-queue-title">
            <h3 id="privacy-queue-title">Privacy-запросы</h3>
            {!canPrivacyWrite && <p className="event-warning">Просмотр без исполнения: нет privacy.write.</p>}
            {sectionErrors.privacy && <p className="error-inline">{sectionErrors.privacy}</p>}
            {!sectionErrors.privacy && !privacyRequests.length && <p>Необработанных запросов нет.</p>}
            {privacyRequests.map((request) => (
              <div className="service-item" key={request.id}>
                <div className="service-item-heading">
                  <b>#{request.id} · {PRIVACY_TYPE_LABELS[request.request_type] || request.request_type}</b>
                  <span>{PRIVACY_STATUS_LABELS[request.status] || request.status}</span>
                </div>
                {request.result_url && <small>Результат: {request.result_url}</small>}
                {canPrivacyWrite && (
                  <button
                    onClick={() => processPrivacy(request)}
                    disabled={!canProcessPrivacy(request.status) || isBusy(`privacy-${request.id}`)}
                  >
                    Исполнить privacy-запрос
                  </button>
                )}
              </div>
            ))}
          </article>
        )}

        {canReturnsRead && (
          <article className="service-card" aria-labelledby="returns-queue-title">
            <h3 id="returns-queue-title">Возвраты и refunds</h3>
            {!canReturnsWrite && <p className="event-warning">Возвраты доступны только для чтения: нет orders.write.</p>}
            {sectionErrors.returns && <p className="error-inline">{sectionErrors.returns}</p>}
            {!sectionErrors.returns && !returns.length && <p>Возвратов на обработку нет.</p>}
            {returns.map((item) => (
              <div className="service-item" key={item.id}>
                <div className="service-item-heading">
                  <b>#{item.id} · Заказ #{item.order_id}</b>
                  <span>{RETURN_STATUS_LABELS[item.status] || item.status}</span>
                </div>
                <p>{item.reason}</p>
                <small>
                  {item.customer_name || item.customer_username || `Клиент #${item.customer_id}`}
                  {` · доступно ${money(item.refundable_balance, item.currency)}`}
                  {` · возвращено ${money(item.refunded_total, item.currency)}`}
                </small>
                {canReturnsWrite && (
                  <div className="service-controls">
                    <label>
                      Сумма возврата
                      <input
                        aria-label={`Сумма возврата ${item.id}`}
                        type="number"
                        min="0.01"
                        step="0.01"
                        max={item.refundable_balance}
                        value={refundAmounts[item.id] ?? item.refundable_balance}
                        onChange={(event) => setRefundAmounts((current) => ({
                          ...current,
                          [item.id]: event.target.value,
                        }))}
                        disabled={!canApproveReturn(item) || isBusy(`return-${item.id}`)}
                      />
                    </label>
                    <button
                      className={item.status.includes("review") || item.status.includes("retry") ? "danger" : ""}
                      onClick={() => approveReturn(item)}
                      disabled={!canApproveReturn(item) || isBusy(`return-${item.id}`)}
                    >
                      Подтвердить refund
                    </button>
                  </div>
                )}
                {item.provider_refund_id && <small>Provider refund: {item.provider_refund_id}</small>}
              </div>
            ))}
          </article>
        )}
      </div>
    </section>
  );
}
