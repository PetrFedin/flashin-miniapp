import React, { useEffect, useMemo, useRef, useState } from "react";

import { AdminApiError, adminJson } from "./api.js";
import {
  FULFILLMENT_STATUS_LABELS,
  SHIPMENT_STATUS_LABELS,
  fulfillmentAction,
  fulfillmentAttentionCount,
  isPicklistComplete,
  normalizeTracking,
} from "./fulfillmentOperations.js";

const DATASETS = Object.freeze([
  ["tasks", "/api/fulfillment/tasks"],
  ["shipments", "/api/delivery-providers/shipments"],
  ["sla", "/api/fulfillment/sla"],
]);

function operationError(error) {
  if (error instanceof AdminApiError && error.status === 403) {
    return "Недостаточно прав для управления сборкой и доставкой.";
  }
  return error?.message || "Операция fulfillment не выполнена.";
}

function slaLabel(event) {
  if (!event) return "SLA не создан";
  const dueAt = new Date(event.due_at);
  const due = Number.isNaN(dueAt.getTime()) ? event.due_at : dueAt.toLocaleString("ru-RU");
  return `${event.event_type}: ${event.status} · ${due}`;
}

export default function FulfillmentOperationsPanel({ onUnauthorized }) {
  const [tasks, setTasks] = useState([]);
  const [shipments, setShipments] = useState([]);
  const [slaEvents, setSlaEvents] = useState([]);
  const [trackingDrafts, setTrackingDrafts] = useState({});
  const [sectionErrors, setSectionErrors] = useState({});
  const [busyKeys, setBusyKeys] = useState(() => new Set());
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const locks = useRef(new Set());

  const shipmentsByOrder = useMemo(() => new Map(
    shipments.map((shipment) => [Number(shipment.order_id), shipment]),
  ), [shipments]);
  const slaByOrder = useMemo(() => {
    const result = new Map();
    slaEvents.forEach((event) => {
      if (!result.has(Number(event.order_id)) || event.status === "open") {
        result.set(Number(event.order_id), event);
      }
    });
    return result;
  }, [slaEvents]);
  const attentionCount = useMemo(
    () => fulfillmentAttentionCount(tasks, shipments),
    [tasks, shipments],
  );

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

  function handleFailure(actionError) {
    if (actionError instanceof AdminApiError && actionError.status === 401) {
      onUnauthorized?.("Сессия администратора истекла. Войдите снова.");
      return;
    }
    setError(operationError(actionError));
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
      handleFailure(actionError);
      return null;
    } finally {
      locks.current.delete(key);
      markBusy(key, false);
    }
  }

  async function load() {
    const results = await Promise.allSettled(
      DATASETS.map(([, path]) => adminJson(path)),
    );
    const nextErrors = {};
    let unauthorized = false;
    results.forEach((result, index) => {
      const [name] = DATASETS[index];
      if (result.status === "fulfilled") {
        const value = Array.isArray(result.value) ? result.value : [];
        if (name === "tasks") setTasks(value);
        if (name === "shipments") setShipments(value);
        if (name === "sla") setSlaEvents(value);
      } else if (result.reason instanceof AdminApiError && result.reason.status === 401) {
        unauthorized = true;
      } else {
        nextErrors[name] = operationError(result.reason);
      }
    });
    setSectionErrors(nextErrors);
    if (unauthorized) onUnauthorized?.("Сессия администратора истекла. Войдите снова.");
  }

  useEffect(() => {
    run("initial-fulfillment-load", load);
  }, []);

  async function updateTask(task, status, message) {
    const updated = await run(
      `task-${task.id}`,
      () => adminJson(`/api/fulfillment/tasks/${task.id}`, {
        method: "PATCH",
        body: JSON.stringify({ status, comment: "" }),
      }),
      message,
    );
    if (updated) await load();
  }

  async function pickAndPack(task) {
    const result = await run(`pick-pack-${task.id}`, async () => {
      const picklist = await adminJson(`/api/fulfillment/tasks/${task.id}/picklist`);
      const items = Array.isArray(picklist?.items) ? picklist.items : [];
      if (!items.length) throw new Error("В picklist нет позиций заказа.");

      const completed = [];
      for (const item of items) {
        if (item.status === "picked" && Number(item.picked_qty) === Number(item.quantity)) {
          completed.push(item);
          continue;
        }
        const updated = await adminJson(
          `/api/fulfillment/task-items/${item.task_item_id}`
          + `?picked_qty=${encodeURIComponent(item.quantity)}&status=picked`,
          { method: "PATCH" },
        );
        completed.push({
          ...item,
          status: updated.status,
          picked_qty: updated.picked_qty,
        });
      }
      if (!isPicklistComplete(completed)) {
        throw new Error("Не все позиции picklist подтверждены полностью.");
      }
      await adminJson(`/api/fulfillment/tasks/${task.id}`, {
        method: "PATCH",
        body: JSON.stringify({ status: "packed", comment: "Все позиции собраны" }),
      });
      return true;
    }, `Заказ #${task.order_id} полностью собран и упакован.`);
    if (result) await load();
  }

  async function createShipment(task) {
    const shipment = await run(
      `shipment-create-${task.order_id}`,
      () => adminJson(
        `/api/delivery-providers/orders/${task.order_id}/shipment?provider_code=courier`,
        { method: "POST" },
      ),
      `Отгрузка заказа #${task.order_id} создана.`,
    );
    if (shipment) await load();
  }

  async function ship(task, shipment) {
    const validation = normalizeTracking(trackingDrafts[shipment.id]);
    if (validation.error) {
      setError(validation.error);
      return;
    }
    const updated = await run(
      `shipment-ship-${shipment.id}`,
      () => adminJson(
        `/api/delivery-providers/shipments/${shipment.id}`
        + `?tracking_number=${encodeURIComponent(validation.value)}&status=shipped`,
        { method: "PATCH" },
      ),
      `Заказ #${task.order_id} передан в доставку.`,
    );
    if (updated) await load();
  }

  async function deliver(task, shipment) {
    if (!window.confirm(`Подтвердить доставку заказа #${task.order_id}?`)) return;
    const updated = await run(
      `shipment-deliver-${shipment.id}`,
      () => adminJson(
        `/api/delivery-providers/shipments/${shipment.id}`
        + `?tracking_number=${encodeURIComponent(shipment.tracking_number || "")}&status=delivered`,
        { method: "PATCH" },
      ),
      `Заказ #${task.order_id} доставлен и завершён.`,
    );
    if (updated) await load();
  }

  async function handleAction(task, shipment) {
    const action = fulfillmentAction(task, shipment);
    if (!action) return;
    if (action.type === "task") {
      await updateTask(
        task,
        action.status,
        action.status === "picking"
          ? `Сборка заказа #${task.order_id} начата.`
          : `Заказ #${task.order_id} готов к передаче в доставку.`,
      );
      return;
    }
    if (action.type === "pick_pack") await pickAndPack(task);
    if (action.type === "create_shipment") await createShipment(task);
    if (action.type === "ship") await ship(task, shipment);
    if (action.type === "deliver") await deliver(task, shipment);
  }

  return (
    <section className="service-operations" aria-labelledby="fulfillment-operations-title">
      <div className="section-title-row">
        <div>
          <h2 id="fulfillment-operations-title">Fulfillment & Delivery</h2>
          <p>Сборка по picklist, упаковка, готовность, отгрузка и подтверждение доставки.</p>
        </div>
        <div className={`attention-badge ${attentionCount ? "attention" : "ok"}`}>
          В работе: {attentionCount}
        </div>
        <button
          type="button"
          onClick={() => run("refresh-fulfillment", load, "Fulfillment обновлён.")}
          disabled={isBusy("refresh-fulfillment")}
        >
          Обновить fulfillment
        </button>
      </div>

      {error && <div className="error" role="alert">{error}<button type="button" onClick={() => setError("")}>×</button></div>}
      {notice && <div className="notice" role="status">{notice}<button type="button" onClick={() => setNotice("")}>×</button></div>}
      {sectionErrors.tasks && <p className="error-inline">Задачи: {sectionErrors.tasks}</p>}
      {sectionErrors.shipments && <p className="error-inline">Отгрузки: {sectionErrors.shipments}</p>}
      {sectionErrors.sla && <p className="error-inline">SLA: {sectionErrors.sla}</p>}

      {!sectionErrors.tasks && !tasks.length && <p>Активных fulfillment-задач нет.</p>}
      <div className="service-grid">
        {tasks.map((task) => {
          const shipment = shipmentsByOrder.get(Number(task.order_id)) || null;
          const action = fulfillmentAction(task, shipment);
          const busy = isBusy(`task-${task.id}`)
            || isBusy(`pick-pack-${task.id}`)
            || isBusy(`shipment-create-${task.order_id}`)
            || isBusy(`shipment-ship-${shipment?.id}`)
            || isBusy(`shipment-deliver-${shipment?.id}`);
          return (
            <article
              className="service-card"
              key={task.id}
              aria-labelledby={`fulfillment-task-${task.id}`}
            >
              <div className="service-item-heading">
                <h3 id={`fulfillment-task-${task.id}`}>Заказ #{task.order_id}</h3>
                <span>{FULFILLMENT_STATUS_LABELS[task.status] || task.status}</span>
              </div>
              <p>Задача #{task.id} · Ответственный Admin {task.assigned_admin_id || "не назначен"}</p>
              <small>{slaLabel(slaByOrder.get(Number(task.order_id)))}</small>
              {shipment && (
                <div className="service-item">
                  <b>Отгрузка #{shipment.id}</b>
                  <span>{SHIPMENT_STATUS_LABELS[shipment.status] || shipment.status}</span>
                  <small>{shipment.provider_code}{shipment.tracking_number ? ` · ${shipment.tracking_number}` : ""}</small>
                </div>
              )}
              {shipment?.status === "created" && (
                <label>
                  Трек-номер
                  <input
                    aria-label={`Трек-номер заказа ${task.order_id}`}
                    value={trackingDrafts[shipment.id] || ""}
                    onChange={(event) => setTrackingDrafts((current) => ({
                      ...current,
                      [shipment.id]: event.target.value,
                    }))}
                    disabled={busy}
                    placeholder="TRACK-123"
                  />
                </label>
              )}
              {action ? (
                <button
                  type="button"
                  onClick={() => handleAction(task, shipment)}
                  disabled={busy}
                >
                  {action.label}
                </button>
              ) : (
                <p><b>Цикл завершён.</b></p>
              )}
            </article>
          );
        })}
      </div>
    </section>
  );
}
