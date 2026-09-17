import React, { useRef, useState } from "react";

import { AdminApiError, adminJson } from "./api.js";
import {
  PHYSICAL_DISPOSITION_LABELS,
  PHYSICAL_RETURN_STATUS_LABELS,
  clearPhysicalIdempotencyKey,
  getPhysicalIdempotencyKey,
  normalizePhysicalQuantity,
  physicalMutationSignature,
  physicalRemaining,
} from "./physicalReturns.js";

function defaultDraft(item) {
  return {
    authorizeQty: physicalRemaining(item, "authorize") ? String(physicalRemaining(item, "authorize")) : "",
    receiveQty: physicalRemaining(item, "receive") ? String(physicalRemaining(item, "receive")) : "",
    inspectQty: physicalRemaining(item, "inspect") ? String(physicalRemaining(item, "inspect")) : "",
    disposition: "resalable",
    reason: "",
  };
}

function physicalError(error) {
  if (error instanceof AdminApiError && error.status === 403) {
    return "Недостаточно прав для изменения физического возврата.";
  }
  return error?.message || "Операция физического возврата не выполнена.";
}

export default function PhysicalReturnPanel({ returnItem, canWrite, onChanged, onUnauthorized }) {
  const [open, setOpen] = useState(false);
  const [detail, setDetail] = useState(null);
  const [drafts, setDrafts] = useState({});
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const lock = useRef(new Set());

  function draftFor(item) {
    return { ...defaultDraft(item), ...(drafts[item.order_item_id] || {}) };
  }

  function setDraft(item, patch) {
    setDrafts((current) => ({
      ...current,
      [item.order_item_id]: {
        ...defaultDraft(item),
        ...(current[item.order_item_id] || {}),
        ...patch,
      },
    }));
  }

  async function run(key, operation, successMessage = "") {
    if (lock.current.has(key)) return null;
    lock.current.add(key);
    setBusy(key);
    setError("");
    setNotice("");
    try {
      const value = await operation();
      if (successMessage) setNotice(successMessage);
      return value;
    } catch (actionError) {
      if (actionError instanceof AdminApiError && actionError.status === 401) {
        onUnauthorized?.("Сессия администратора истекла. Войдите снова.");
      } else {
        setError(physicalError(actionError));
      }
      return null;
    } finally {
      lock.current.delete(key);
      setBusy((current) => current === key ? "" : current);
    }
  }

  async function loadDetail() {
    const value = await adminJson(`/api/admin/returns/${returnItem.id}/physical`);
    setDetail(value);
    return value;
  }

  async function toggle() {
    if (open) {
      setOpen(false);
      return;
    }
    const loaded = await run(`physical-load-${returnItem.id}`, loadDetail);
    if (loaded) setOpen(true);
  }

  async function mutateItem(item, operation) {
    if (!canWrite) {
      setError("Недостаточно прав: физический возврат требует returns.physical.write.");
      return;
    }
    const draft = draftFor(item);
    const phase = operation === "authorize" ? "authorize" : operation === "receive" ? "receive" : "inspect";
    const rawQuantity = operation === "authorize"
      ? draft.authorizeQty
      : operation === "receive"
        ? draft.receiveQty
        : draft.inspectQty;
    const limit = physicalRemaining(item, phase);
    const validation = normalizePhysicalQuantity(rawQuantity, limit, "Количество");
    if (validation.error) {
      setError(validation.error);
      return;
    }

    const quantity = validation.value;
    const reason = String(draft.reason || "").trim();
    const disposition = operation === "inspect" ? draft.disposition : "";
    const signature = physicalMutationSignature({
      operation,
      returnId: returnItem.id,
      orderItemId: item.order_item_id,
      quantity,
      disposition,
      reason,
    });
    let idempotencyKey;
    try {
      idempotencyKey = getPhysicalIdempotencyKey(signature);
    } catch {
      setError("Не удалось создать устойчивый ключ операции. Обновите страницу и повторите действие.");
      return;
    }

    const payload = {
      order_item_id: item.order_item_id,
      quantity,
      reason,
      ...(operation === "inspect" ? { disposition } : {}),
    };
    const key = `physical-${operation}-${returnItem.id}-${item.order_item_id}`;
    const result = await run(
      key,
      () => adminJson(`/api/admin/returns/${returnItem.id}/physical/${operation}`, {
        method: "POST",
        headers: { "Idempotency-Key": idempotencyKey },
        body: JSON.stringify(payload),
      }),
      operation === "authorize"
        ? "Позиция добавлена в авторизованный физический возврат."
        : operation === "receive"
          ? "Фактическая приёмка зафиксирована."
          : "Осмотр и disposition зафиксированы.",
    );
    if (!result) return;

    clearPhysicalIdempotencyKey(signature);
    setDrafts((current) => {
      const next = { ...current };
      delete next[item.order_item_id];
      return next;
    });
    await run(`physical-refresh-${returnItem.id}`, loadDetail);
    await onChanged?.();
  }

  async function markTransit() {
    if (!canWrite) {
      setError("Недостаточно прав: физический возврат требует returns.physical.write.");
      return;
    }
    const result = await run(
      `physical-transit-${returnItem.id}`,
      () => adminJson(`/api/admin/returns/${returnItem.id}/physical/in-transit`, { method: "POST" }),
      "Физический возврат отмечен как находящийся в пути.",
    );
    if (!result) return;
    await run(`physical-refresh-${returnItem.id}`, loadDetail);
    await onChanged?.();
  }

  const status = detail?.physical_status || returnItem.physical_status || "not_started";

  return (
    <div className="physical-return-panel">
      <div className="physical-return-heading">
        <span>
          Физический возврат: {PHYSICAL_RETURN_STATUS_LABELS[status] || status}
        </span>
        <button
          type="button"
          onClick={toggle}
          disabled={busy === `physical-load-${returnItem.id}`}
        >
          {open ? "Скрыть физический возврат" : "Открыть физический возврат"}
        </button>
      </div>

      {open && (
        <div className="physical-return-body">
          {!canWrite && (
            <p className="event-warning">Только чтение: для действий требуется returns.physical.write.</p>
          )}
          {error && <p className="error-inline" role="alert">{error}</p>}
          {notice && <p className="notice" role="status">{notice}</p>}

          {detail?.items?.map((item) => {
            const draft = draftFor(item);
            const authorizeRemaining = physicalRemaining(item, "authorize");
            const receiveRemaining = physicalRemaining(item, "receive");
            const inspectRemaining = physicalRemaining(item, "inspect");
            const canAuthorize = canWrite
              && ["not_started", "requested", "authorized"].includes(status)
              && Number(item.authorized_qty || 0) === 0
              && authorizeRemaining > 0;
            const canReceive = canWrite && status === "in_transit" && receiveRemaining > 0;
            const canInspect = canWrite && ["received", "inspected"].includes(status) && inspectRemaining > 0;

            return (
              <div className="physical-return-item" key={item.order_item_id}>
                <div className="service-item-heading">
                  <b>{item.title || `Order item #${item.order_item_id}`} {item.size ? `· ${item.size}` : ""}</b>
                  <span>SKU line #{item.order_item_id}</span>
                </div>
                <div className="physical-metrics">
                  <small>Заказано: {item.ordered_qty}</small>
                  <small>Авторизовано: {item.authorized_qty}</small>
                  <small>Принято: {item.received_qty}</small>
                  <small>Осмотрено: {item.inspected_qty}</small>
                  <small>В продажу: {item.resalable_qty}</small>
                  <small>Повреждено: {item.damaged_qty}</small>
                  <small>Карантин: {item.quarantine_qty}</small>
                </div>

                {canAuthorize && (
                  <div className="physical-action-grid">
                    <label>
                      Авторизовать, шт.
                      <input
                        type="number"
                        min="1"
                        max={authorizeRemaining}
                        step="1"
                        value={draft.authorizeQty ?? ""}
                        onChange={(event) => setDraft(item, { authorizeQty: event.target.value })}
                        disabled={Boolean(busy)}
                      />
                    </label>
                    <label>
                      Основание / комментарий
                      <input
                        value={draft.reason ?? ""}
                        maxLength={2000}
                        onChange={(event) => setDraft(item, { reason: event.target.value })}
                        disabled={Boolean(busy)}
                      />
                    </label>
                    <button
                      type="button"
                      onClick={() => mutateItem(item, "authorize")}
                      disabled={Boolean(busy)}
                    >
                      Авторизовать позицию
                    </button>
                  </div>
                )}

                {canReceive && (
                  <div className="physical-action-grid">
                    <label>
                      Фактически принять, шт.
                      <input
                        type="number"
                        min="1"
                        max={receiveRemaining}
                        step="1"
                        value={draft.receiveQty ?? ""}
                        onChange={(event) => setDraft(item, { receiveQty: event.target.value })}
                        disabled={Boolean(busy)}
                      />
                    </label>
                    <label>
                      Комментарий приёмки
                      <input
                        value={draft.reason ?? ""}
                        maxLength={2000}
                        onChange={(event) => setDraft(item, { reason: event.target.value })}
                        disabled={Boolean(busy)}
                      />
                    </label>
                    <button
                      type="button"
                      onClick={() => mutateItem(item, "receive")}
                      disabled={Boolean(busy)}
                    >
                      Зафиксировать приёмку
                    </button>
                  </div>
                )}

                {canInspect && (
                  <div className="physical-action-grid">
                    <label>
                      Осмотреть, шт.
                      <input
                        type="number"
                        min="1"
                        max={inspectRemaining}
                        step="1"
                        value={draft.inspectQty ?? ""}
                        onChange={(event) => setDraft(item, { inspectQty: event.target.value })}
                        disabled={Boolean(busy)}
                      />
                    </label>
                    <label>
                      Disposition
                      <select
                        value={draft.disposition || "resalable"}
                        onChange={(event) => setDraft(item, { disposition: event.target.value })}
                        disabled={Boolean(busy)}
                      >
                        {Object.entries(PHYSICAL_DISPOSITION_LABELS).map(([value, label]) => (
                          <option value={value} key={value}>{label}</option>
                        ))}
                      </select>
                    </label>
                    <label>
                      Комментарий осмотра
                      <input
                        value={draft.reason ?? ""}
                        maxLength={2000}
                        onChange={(event) => setDraft(item, { reason: event.target.value })}
                        disabled={Boolean(busy)}
                      />
                    </label>
                    <button
                      type="button"
                      onClick={() => mutateItem(item, "inspect")}
                      disabled={Boolean(busy)}
                    >
                      Зафиксировать disposition
                    </button>
                  </div>
                )}
              </div>
            );
          })}

          {canWrite && status === "authorized" && (
            <button
              type="button"
              onClick={markTransit}
              disabled={Boolean(busy)}
            >
              Зафиксировать передачу в пути
            </button>
          )}
        </div>
      )}
    </div>
  );
}
