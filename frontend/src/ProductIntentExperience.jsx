import React, { useEffect, useMemo, useState } from "react";

import {
  createProductIntent,
  listIntentEligibleProducts,
  listMyProductIntents,
} from "./catalogApi";

const TYPE_LABEL = {
  preorder: "Предзаказ",
  made_to_order: "Под заказ",
};

const STATUS_LABEL = {
  requested: "Заявка получена",
  working: "В работе",
  ready: "Готово / доступно",
  closed: "Закрыто",
  cancelled: "Отменено",
};

function money(value, currency = "RUB") {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "—";
  try {
    return new Intl.NumberFormat("ru-RU", {
      style: "currency",
      currency: String(currency || "RUB").toUpperCase(),
      maximumFractionDigits: 0,
    }).format(Number(value));
  } catch {
    return `${Number(value).toLocaleString("ru-RU")} ${currency || "RUB"}`;
  }
}

function utcDate(value) {
  if (!value) return "—";
  const raw = String(value);
  const normalized = /(?:Z|[+-]\d\d:\d\d)$/.test(raw) ? raw : `${raw}Z`;
  const parsed = new Date(normalized);
  return Number.isNaN(parsed.getTime()) ? raw : parsed.toLocaleString("ru-RU");
}

export default function ProductIntentExperience() {
  const [open, setOpen] = useState(false);
  const [products, setProducts] = useState([]);
  const [requests, setRequests] = useState([]);
  const [selectedProductId, setSelectedProductId] = useState("");
  const [selectedVariantId, setSelectedVariantId] = useState("");
  const [quantity, setQuantity] = useState(1);
  const [requestedSize, setRequestedSize] = useState("");
  const [requestedColor, setRequestedColor] = useState("");
  const [notes, setNotes] = useState("");
  const [loading, setLoading] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  const selectedProduct = useMemo(
    () => products.find((product) => String(product.id) === String(selectedProductId)) || null,
    [products, selectedProductId],
  );
  const eligibleVariants = useMemo(
    () => (selectedProduct?.variants || []).filter((variant) => variant.intent_eligible),
    [selectedProduct],
  );

  async function load() {
    setLoading(true);
    setError("");
    try {
      const eligible = await listIntentEligibleProducts();
      const nextProducts = Array.isArray(eligible) ? eligible : [];
      setProducts(nextProducts);
      setSelectedProductId((current) => (
        current && nextProducts.some((product) => String(product.id) === String(current))
          ? current
          : String(nextProducts[0]?.id || "")
      ));

      if (localStorage.getItem("flashin_token")) {
        const mine = await listMyProductIntents();
        setRequests(Array.isArray(mine) ? mine : []);
      } else {
        setRequests([]);
      }
    } catch (actionError) {
      setError(actionError?.message || "Не удалось загрузить заявки на предзаказ.");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    if (open) load();
  }, [open]);

  useEffect(() => {
    if (!selectedProduct) {
      setSelectedVariantId("");
      return;
    }
    if (!eligibleVariants.some((variant) => String(variant.id) === String(selectedVariantId))) {
      setSelectedVariantId(String(eligibleVariants[0]?.id || ""));
    }
  }, [selectedProduct, eligibleVariants, selectedVariantId]);

  async function submit(event) {
    event.preventDefault();
    setError("");
    setNotice("");
    if (!selectedProduct) {
      setError("Выберите товар.");
      return;
    }
    if ((selectedProduct.variants || []).length > 0 && !selectedVariantId) {
      setError("Выберите недоступный размер / цвет для заявки.");
      return;
    }
    if (!localStorage.getItem("flashin_token")) {
      setError("Для заявки откройте Mini App из Telegram и авторизуйтесь.");
      return;
    }

    setSubmitting(true);
    try {
      const created = await createProductIntent({
        product_id: selectedProduct.id,
        variant_id: selectedVariantId || null,
        quantity,
        requested_size: requestedSize,
        requested_color: requestedColor,
        notes,
      });
      setNotice(`Заявка #${created.id} создана. Оплата не списывается — команда сначала подтвердит возможность и сроки.`);
      setNotes("");
      setRequestedSize("");
      setRequestedColor("");
      const mine = await listMyProductIntents();
      setRequests(Array.isArray(mine) ? mine : []);
    } catch (actionError) {
      setError(actionError?.message || "Не удалось создать заявку.");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <>
      <button
        type="button"
        className="intent-fab"
        onClick={() => setOpen(true)}
        aria-haspopup="dialog"
      >
        Предзаказ / под заказ
      </button>

      {open && (
        <div className="intent-backdrop" role="presentation" onMouseDown={(event) => {
          if (event.target === event.currentTarget) setOpen(false);
        }}>
          <section className="intent-panel" role="dialog" aria-modal="true" aria-labelledby="intent-title">
            <div className="intent-heading">
              <div>
                <span className="intent-kicker">FLASHIN SERVICE</span>
                <h2 id="intent-title">Предзаказ и индивидуальный заказ</h2>
                <p>Заявка фиксирует ваш интерес. Она не резервирует склад и не запускает оплату.</p>
              </div>
              <button type="button" className="intent-close" onClick={() => setOpen(false)} aria-label="Закрыть">×</button>
            </div>

            {error && <div className="intent-error" role="alert">{error}</div>}
            {notice && <div className="intent-notice" role="status">{notice}</div>}

            {loading ? (
              <p>Загрузка…</p>
            ) : (
              <div className="intent-layout">
                <form className="intent-form" onSubmit={submit}>
                  <h3>Новая заявка</h3>
                  {!products.length ? (
                    <p>Сейчас нет активных товаров со статусом «предзаказ» или «под заказ».</p>
                  ) : (
                    <>
                      <label>
                        Товар
                        <select
                          aria-label="Товар для предзаказа"
                          value={selectedProductId}
                          onChange={(event) => setSelectedProductId(event.target.value)}
                        >
                          {products.map((product) => (
                            <option key={product.id} value={product.id}>
                              {TYPE_LABEL[product.intent_type] || product.intent_type} · {product.brand} · {product.title}
                            </option>
                          ))}
                        </select>
                      </label>

                      {selectedProduct && (
                        <article className="intent-product-preview">
                          {selectedProduct.image_url && <img src={selectedProduct.image_url} alt="" />}
                          <div>
                            <b>{selectedProduct.title}</b>
                            <span>{TYPE_LABEL[selectedProduct.intent_type] || selectedProduct.intent_type}</span>
                            <strong>{money(selectedProduct.price, selectedProduct.currency)}</strong>
                          </div>
                        </article>
                      )}

                      {eligibleVariants.length > 0 ? (
                        <label>
                          Размер / цвет
                          <select
                            aria-label="Вариант для предзаказа"
                            value={selectedVariantId}
                            onChange={(event) => setSelectedVariantId(event.target.value)}
                          >
                            {eligibleVariants.map((variant) => (
                              <option key={variant.id} value={variant.id}>
                                {variant.size || "—"} · {variant.color || "без цвета"}
                              </option>
                            ))}
                          </select>
                        </label>
                      ) : (
                        <div className="intent-two-fields">
                          <label>Желаемый размер<input value={requestedSize} maxLength={32} onChange={(event) => setRequestedSize(event.target.value)} /></label>
                          <label>Желаемый цвет<input value={requestedColor} maxLength={64} onChange={(event) => setRequestedColor(event.target.value)} /></label>
                        </div>
                      )}

                      <label>
                        Количество
                        <input
                          type="number"
                          min="1"
                          max="5"
                          value={quantity}
                          onChange={(event) => setQuantity(Math.max(1, Math.min(5, Number(event.target.value) || 1)))}
                        />
                      </label>
                      <label>
                        Комментарий
                        <textarea
                          value={notes}
                          maxLength={2000}
                          onChange={(event) => setNotes(event.target.value)}
                          placeholder="Размер, пожелания, срок, удобный способ связи"
                        />
                      </label>
                      <button type="submit" disabled={submitting}>{submitting ? "Отправка…" : "Отправить заявку без оплаты"}</button>
                    </>
                  )}
                </form>

                <div className="intent-history" aria-label="Мои заявки на предзаказ">
                  <div className="intent-history-heading">
                    <h3>Мои заявки</h3>
                    <button type="button" onClick={load}>Обновить</button>
                  </div>
                  {!localStorage.getItem("flashin_token") && <p>История появится после авторизации в Mini App.</p>}
                  {localStorage.getItem("flashin_token") && !requests.length && <p>Заявок пока нет.</p>}
                  {requests.map((request) => (
                    <article className="intent-request" key={request.id}>
                      <div><b>#{request.id} · {request.product_title}</b><span>{TYPE_LABEL[request.intent_type] || request.intent_type}</span></div>
                      <p>{request.variant_size || "Размер не указан"}{request.variant_color ? ` · ${request.variant_color}` : ""} · ×{request.quantity}</p>
                      <strong>{STATUS_LABEL[request.status] || request.status}</strong>
                      {request.quote_amount !== null && request.quote_amount !== undefined && (
                        <p>Предложение: {money(request.quote_amount, request.quote_currency)} <small>без автоматического списания</small></p>
                      )}
                      {request.estimated_ready_at && <p>Ориентир готовности: {utcDate(request.estimated_ready_at)}</p>}
                      {request.normal_checkout_available && <p className="intent-ready-note">Появился локальный остаток — покупку можно оформить через обычную корзину.</p>}
                    </article>
                  ))}
                </div>
              </div>
            )}
          </section>
        </div>
      )}
    </>
  );
}
