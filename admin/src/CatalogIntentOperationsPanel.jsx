import React, { useEffect, useMemo, useRef, useState } from "react";

import { hasAdminPermission } from "./adminPermissions.js";
import { AdminApiError, adminJson } from "./api.js";

const STATUS_LABELS = {
  requested: "Получена",
  working: "В работе",
  ready: "Готово",
  closed: "Закрыто",
  cancelled: "Отменено",
};

const TYPE_LABELS = {
  preorder: "Предзаказ",
  made_to_order: "Под заказ",
};

function datetimeLocalValue(value) {
  if (!value) return "";
  const raw = String(value);
  const normalized = /(?:Z|[+-]\d\d:\d\d)$/.test(raw) ? raw : `${raw}Z`;
  const date = new Date(normalized);
  if (Number.isNaN(date.getTime())) return "";
  const offset = date.getTimezoneOffset() * 60_000;
  return new Date(date.getTime() - offset).toISOString().slice(0, 16);
}

function buildDraft(item) {
  return {
    status: item.status || "requested",
    quote_amount: item.quote_amount ?? "",
    quote_currency: item.quote_currency || "RUB",
    estimated_ready_at: datetimeLocalValue(item.estimated_ready_at),
    admin_note: item.admin_note || "",
  };
}

export default function CatalogIntentOperationsPanel({ onUnauthorized, session }) {
  const canRead = hasAdminPermission(session, "products.read");
  const canWrite = hasAdminPermission(session, "products.write");
  const [items, setItems] = useState([]);
  const [drafts, setDrafts] = useState({});
  const [statusFilter, setStatusFilter] = useState("");
  const [typeFilter, setTypeFilter] = useState("");
  const [loading, setLoading] = useState(false);
  const [savingId, setSavingId] = useState(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const sequence = useRef(0);

  const query = useMemo(() => {
    const params = new URLSearchParams({ limit: "300" });
    if (statusFilter) params.set("status", statusFilter);
    if (typeFilter) params.set("intent_type", typeFilter);
    return params.toString();
  }, [statusFilter, typeFilter]);

  function handleFailure(actionError) {
    if (actionError instanceof AdminApiError && actionError.status === 401) {
      onUnauthorized?.("Сессия администратора истекла. Войдите снова.");
      return;
    }
    setError(actionError?.message || "Очередь предзаказов недоступна.");
  }

  async function load() {
    if (!canRead) return;
    const requestId = sequence.current + 1;
    sequence.current = requestId;
    setLoading(true);
    setError("");
    try {
      const next = await adminJson(`/api/catalog/admin/intents?${query}`);
      if (sequence.current !== requestId) return;
      const rows = Array.isArray(next) ? next : [];
      setItems(rows);
      setDrafts(Object.fromEntries(rows.map((item) => [item.id, buildDraft(item)])));
    } catch (actionError) {
      if (sequence.current === requestId) handleFailure(actionError);
    } finally {
      if (sequence.current === requestId) setLoading(false);
    }
  }

  useEffect(() => {
    load();
    return () => { sequence.current += 1; };
  }, [canRead, query]);

  function patchDraft(id, patch) {
    setDrafts((current) => ({
      ...current,
      [id]: { ...(current[id] || {}), ...patch },
    }));
  }

  async function save(item) {
    if (!canWrite || savingId) return;
    const draft = drafts[item.id] || buildDraft(item);
    setSavingId(item.id);
    setError("");
    setNotice("");
    try {
      const payload = {
        status: draft.status,
        admin_note: String(draft.admin_note || "").trim(),
        quote_currency: String(draft.quote_currency || "RUB").trim().toUpperCase(),
      };
      if (draft.quote_amount !== "") payload.quote_amount = Number(draft.quote_amount);
      if (draft.estimated_ready_at) payload.estimated_ready_at = new Date(draft.estimated_ready_at).toISOString();

      await adminJson(`/api/catalog/admin/intents/${item.id}`, {
        method: "PATCH",
        body: JSON.stringify(payload),
        dedupeKey: `catalog-intent:${item.id}:${draft.status}:${draft.quote_amount}:${draft.estimated_ready_at}`,
      });
      setNotice(`Заявка #${item.id} обновлена.`);
      await load();
    } catch (actionError) {
      handleFailure(actionError);
    } finally {
      setSavingId(null);
    }
  }

  if (!canRead) return null;

  return (
    <section className="catalog-intent-operations" aria-labelledby="catalog-intent-admin-title">
      <div className="section-heading">
        <div>
          <h2 id="catalog-intent-admin-title">Предзаказ и товары под заказ</h2>
          <p>Операторская очередь клиентского интереса. Это не заказ и не платёж: stock/order/payment создаются только штатным checkout-контуром.</p>
        </div>
        <button type="button" onClick={load} disabled={loading}>{loading ? "Обновление…" : "Обновить"}</button>
      </div>

      <div className="filters">
        <label>
          Статус заявки
          <select aria-label="Статус заявок" value={statusFilter} onChange={(event) => setStatusFilter(event.target.value)}>
            <option value="">Все</option>
            {Object.entries(STATUS_LABELS).map(([value, label]) => <option value={value} key={value}>{label}</option>)}
          </select>
        </label>
        <label>
          Тип
          <select aria-label="Тип заявок" value={typeFilter} onChange={(event) => setTypeFilter(event.target.value)}>
            <option value="">Все</option>
            <option value="preorder">Предзаказ</option>
            <option value="made_to_order">Под заказ</option>
          </select>
        </label>
      </div>

      {error && <div className="error" role="alert">{error}<button type="button" onClick={() => setError("")}>×</button></div>}
      {notice && <div className="notice" role="status">{notice}<button type="button" onClick={() => setNotice("")}>×</button></div>}

      {!loading && !items.length && <p>Заявок по выбранному фильтру нет.</p>}
      <div className="table" aria-label="Очередь предзаказов">
        {items.map((item) => {
          const draft = drafts[item.id] || buildDraft(item);
          return (
            <div className="row catalog-intent-row" key={item.id}>
              <b>#{item.id} · {TYPE_LABELS[item.intent_type] || item.intent_type}</b>
              <span>Product #{item.product_id} · {item.product_title}</span>
              <span>Customer #{item.customer_id} · PII скрыты</span>
              <span>{item.variant_size || "Размер не указан"}{item.variant_color ? ` · ${item.variant_color}` : ""} · ×{item.quantity}</span>
              {item.notes && <span>Клиент: {item.notes}</span>}
              <span>Создано: {new Date(item.created_at).toLocaleString("ru-RU")}</span>
              {item.normal_checkout_available && <strong>Есть локальный остаток — клиент может перейти в штатную корзину</strong>}

              <label>
                Статус
                <select
                  aria-label={`Статус заявки ${item.id}`}
                  value={draft.status}
                  disabled={!canWrite}
                  onChange={(event) => patchDraft(item.id, { status: event.target.value })}
                >
                  {Object.entries(STATUS_LABELS).map(([value, label]) => <option value={value} key={value}>{label}</option>)}
                </select>
              </label>
              <label>
                Предложение
                <input
                  aria-label={`Сумма предложения ${item.id}`}
                  type="number"
                  min="0"
                  step="0.01"
                  value={draft.quote_amount}
                  disabled={!canWrite}
                  onChange={(event) => patchDraft(item.id, { quote_amount: event.target.value })}
                />
              </label>
              <label>
                Валюта
                <input
                  aria-label={`Валюта предложения ${item.id}`}
                  maxLength={8}
                  value={draft.quote_currency}
                  disabled={!canWrite}
                  onChange={(event) => patchDraft(item.id, { quote_currency: event.target.value })}
                />
              </label>
              <label>
                Ориентир готовности
                <input
                  aria-label={`Срок готовности ${item.id}`}
                  type="datetime-local"
                  value={draft.estimated_ready_at}
                  disabled={!canWrite}
                  onChange={(event) => patchDraft(item.id, { estimated_ready_at: event.target.value })}
                />
              </label>
              <label>
                Внутренний комментарий
                <textarea
                  aria-label={`Комментарий оператора ${item.id}`}
                  maxLength={2000}
                  value={draft.admin_note}
                  disabled={!canWrite}
                  onChange={(event) => patchDraft(item.id, { admin_note: event.target.value })}
                />
              </label>
              {canWrite && (
                <button type="button" onClick={() => save(item)} disabled={savingId === item.id}>
                  {savingId === item.id ? "Сохранение…" : "Сохранить заявку"}
                </button>
              )}
            </div>
          );
        })}
      </div>
    </section>
  );
}
