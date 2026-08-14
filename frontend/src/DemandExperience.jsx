import React, { useEffect, useMemo, useState } from "react";

import { telegramAuth } from "./api";
import {
  cancelDemandRequest,
  createDemandRequest,
  listCatalogProducts,
  listMyDemandRequests,
} from "./catalogApi.js";
import { useTelegram } from "./hooks/useTelegram";

const TYPE_LABELS = {
  preorder: "Предзаказ",
  made_to_order: "Под заказ",
};

const STATUS_LABELS = {
  requested: "Заявка получена",
  contacted: "Менеджер связался",
  confirmed: "Заявка подтверждена",
  cancelled: "Отменена",
};

function productImage(product) {
  return product?.images?.[0]?.url || "/fallback-product.svg";
}

function money(value, currency = "RUB") {
  return new Intl.NumberFormat("ru-RU", {
    style: "currency",
    currency,
    maximumFractionDigits: 0,
  }).format(Number(value || 0));
}

export default function DemandExperience() {
  const { initData, initialized } = useTelegram();
  const [open, setOpen] = useState(false);
  const [products, setProducts] = useState([]);
  const [requests, setRequests] = useState([]);
  const [selectedId, setSelectedId] = useState(null);
  const [variantId, setVariantId] = useState(null);
  const [quantity, setQuantity] = useState(1);
  const [notes, setNotes] = useState("");
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  const selected = useMemo(
    () => products.find((item) => Number(item.id) === Number(selectedId)) || null,
    [products, selectedId],
  );

  async function ensureAuth() {
    if (localStorage.getItem("flashin_token")) return;
    if (!initialized || !initData) throw new Error("Telegram авторизация ещё не готова.");
    await telegramAuth(initData);
  }

  async function load() {
    setLoading(true);
    setError("");
    try {
      await ensureAuth();
      const [preorder, madeToOrder, mine] = await Promise.all([
        listCatalogProducts({ availability_status: "preorder", sort: "grid" }),
        listCatalogProducts({ availability_status: "made_to_order", sort: "grid" }),
        listMyDemandRequests(),
      ]);
      const eligible = [...preorder, ...madeToOrder]
        .filter((item, index, rows) => rows.findIndex((other) => other.id === item.id) === index)
        .filter((item) => Number(item.merchandising?.local_available_qty || 0) === 0);
      setProducts(eligible);
      setRequests(Array.isArray(mine) ? mine : []);
      setSelectedId((current) => eligible.some((item) => item.id === current) ? current : eligible[0]?.id || null);
    } catch (actionError) {
      setError(actionError?.message || "Не удалось загрузить заявки.");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    if (open && initialized) load();
  }, [open, initialized]);

  useEffect(() => {
    if (!selected) {
      setVariantId(null);
      return;
    }
    setVariantId(selected.variants?.[0]?.id || null);
    setQuantity(1);
    setNotes("");
  }, [selectedId]);

  async function submit() {
    if (!selected || busy) return;
    setBusy(true);
    setError("");
    setNotice("");
    try {
      await ensureAuth();
      const row = await createDemandRequest(selected, variantId, quantity, notes);
      setNotice(`${TYPE_LABELS[row.request_type] || "Заявка"} #${row.id} сохранена. Оплата и склад не затронуты.`);
      await load();
    } catch (actionError) {
      setError(actionError?.message || "Не удалось отправить заявку.");
    } finally {
      setBusy(false);
    }
  }

  async function cancel(row) {
    if (busy || row.status === "cancelled") return;
    setBusy(true);
    setError("");
    try {
      await cancelDemandRequest(row.id);
      setNotice(`Заявка #${row.id} отменена.`);
      await load();
    } catch (actionError) {
      setError(actionError?.message || "Не удалось отменить заявку.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <button
        type="button"
        className="catalog-plus-launcher"
        style={{ bottom: "78px" }}
        onClick={() => setOpen(true)}
      >
        Предзаказ / под заказ
      </button>

      {open && (
        <div className="catalog-plus-overlay" role="dialog" aria-modal="true" aria-label="Предзаказ и товары под заказ">
          <div className="catalog-plus-shell">
            <header className="topbar catalog-plus-topbar">
              <div>
                <div className="brand">FLASHIN</div>
                <div className="hello">Предзаказ / под заказ</div>
              </div>
              <button type="button" className="secondary compact" onClick={() => setOpen(false)}>Закрыть</button>
            </header>

            {error && <div className="message error" role="alert">{error}</div>}
            {notice && <div className="message success" role="status">{notice}</div>}

            <main>
              <p>Здесь оформляется только заявка на спрос. Она не создаёт оплаченный заказ и не резервирует склад.</p>
              {loading && <p>Загрузка…</p>}
              {!loading && !products.length && <p>Сейчас нет активных товаров для предзаказа или изготовления под заказ.</p>}

              {!!products.length && (
                <div className="catalog-plus-layout">
                  <div className="catalog-plus-grid">
                    {products.map((product) => (
                      <button
                        type="button"
                        className={`catalog-plus-card ${selectedId === product.id ? "selected" : ""}`}
                        key={product.id}
                        onClick={() => setSelectedId(product.id)}
                      >
                        <img src={productImage(product)} alt={product.title} />
                        <strong>{product.title}</strong>
                        <span>{TYPE_LABELS[product.merchandising?.availability_status] || product.merchandising?.availability_status}</span>
                        <span>{money(product.price, product.currency)}</span>
                      </button>
                    ))}
                  </div>

                  {selected && (
                    <section className="panel" aria-label="Форма заявки на товар">
                      <h2>{selected.title}</h2>
                      <p>{TYPE_LABELS[selected.merchandising?.availability_status]}</p>
                      <label>
                        Вариант
                        <select aria-label="Вариант для заявки" value={variantId || ""} onChange={(event) => setVariantId(Number(event.target.value) || null)}>
                          {(selected.variants || []).map((variant) => (
                            <option key={variant.id} value={variant.id}>
                              {variant.size || "—"}{variant.color ? ` · ${variant.color}` : ""}
                            </option>
                          ))}
                        </select>
                      </label>
                      <label>
                        Количество
                        <input aria-label="Количество для заявки" type="number" min="1" max="10" value={quantity} onChange={(event) => setQuantity(Math.min(10, Math.max(1, Number(event.target.value) || 1)))} />
                      </label>
                      <label>
                        Комментарий
                        <textarea value={notes} maxLength={2000} onChange={(event) => setNotes(event.target.value)} placeholder="Например: нужна примерка, уточнить срок и цвет" />
                      </label>
                      <button type="button" className="primary" disabled={busy} onClick={submit}>
                        {selected.merchandising?.availability_status === "preorder" ? "Оставить заявку на предзаказ" : "Оставить заявку под заказ"}
                      </button>
                    </section>
                  )}
                </div>
              )}

              <section className="panel" aria-label="Мои заявки на спрос">
                <h2>Мои заявки</h2>
                {!requests.length && <p>Заявок пока нет.</p>}
                {requests.map((row) => (
                  <div className="status-row" key={row.id}>
                    <span>#{row.id} · {row.product_title || `Product #${row.product_id}`} · {TYPE_LABELS[row.request_type]}</span>
                    <span>{row.requested_size || "размер не указан"}{row.requested_color ? ` · ${row.requested_color}` : ""}</span>
                    <b>{STATUS_LABELS[row.status] || row.status}</b>
                    {row.status !== "cancelled" && <button type="button" className="secondary compact" disabled={busy} onClick={() => cancel(row)}>Отменить</button>}
                  </div>
                ))}
              </section>
            </main>
          </div>
        </div>
      )}
    </>
  );
}
