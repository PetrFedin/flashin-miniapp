import React, { useEffect, useMemo, useState } from "react";

import {
  addToCart,
  addWishlist,
  getCart,
  listWishlist,
  removeWishlist,
  telegramAuth,
} from "./api";
import {
  createShowroomAppointment,
  getCatalogProduct,
  listCatalogProducts,
  listMyShowroomAppointments,
  listProductFeedback,
  submitProductFeedback,
} from "./catalogApi.js";
import { useTelegram } from "./hooks/useTelegram";

const DEFAULT_FILTERS = {
  q: "",
  brand: "",
  category: "",
  material: "",
  season: "",
  availability_status: "",
  badge: "",
  size: "",
  color: "",
  min_price: "",
  max_price: "",
  sort: "grid",
};

const STATUS_LABELS = {
  in_stock: "В наличии",
  preorder: "Предзаказ",
  made_to_order: "Под заказ",
  out_of_stock: "Нет в наличии",
};

const BADGE_LABELS = {
  bestseller: "Бестселлер",
  exclusive: "Эксклюзив",
  new_season: "Новый сезон",
  sale: "Распродажа",
  outlet: "Аутлет",
  drop: "Drop",
  limited: "Limited",
};

function money(value, currency = "RUB") {
  return new Intl.NumberFormat("ru-RU", {
    style: "currency",
    currency,
    maximumFractionDigits: 2,
  }).format(Number(value || 0));
}

function imageUrl(product) {
  return product?.images?.[0]?.url || "/fallback-product.svg";
}

function CatalogCard({ product, onOpen }) {
  const merch = product.merchandising || {};
  return (
    <article className="product-card catalog-plus-card">
      <button className="product-open" type="button" onClick={() => onOpen(product)}>
        <img src={imageUrl(product)} alt={product.title} loading="lazy" />
        <span className="catalog-plus-badges">
          {(merch.badges || []).slice(0, 3).map((badge) => (
            <small className="catalog-plus-badge" key={badge}>{BADGE_LABELS[badge] || badge}</small>
          ))}
        </span>
        <span className="title">{product.title}</span>
        <span className="meta">{product.brand} · {product.category}</span>
        {merch.material && <span className="meta">{merch.material}{merch.season ? ` · ${merch.season}` : ""}</span>}
        <span className="meta">{STATUS_LABELS[merch.availability_status] || merch.availability_status}</span>
        <span className="price">{money(product.price, product.currency)}</span>
        {product.old_price > product.price && <span className="meta"><s>{money(product.old_price, product.currency)}</s></span>}
        <span className="meta">★ {Number(product.rating?.average || 0).toFixed(1)} · {product.rating?.count || 0} отзывов</span>
      </button>
    </article>
  );
}

export default function CatalogExperience() {
  const { initData, initialized } = useTelegram();
  const [open, setOpen] = useState(false);
  const [products, setProducts] = useState([]);
  const [filters, setFilters] = useState(DEFAULT_FILTERS);
  const [selected, setSelected] = useState(null);
  const [selectedVariantId, setSelectedVariantId] = useState(null);
  const [wishlistIds, setWishlistIds] = useState(new Set());
  const [feedback, setFeedback] = useState([]);
  const [feedbackForm, setFeedbackForm] = useState({ rating: 5, comment: "" });
  const [appointmentForm, setAppointmentForm] = useState({ starts_at: "", notes: "" });
  const [appointments, setAppointments] = useState([]);
  const [cartCount, setCartCount] = useState(0);
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [dirtyLegacyState, setDirtyLegacyState] = useState(false);

  const selectedVariant = useMemo(
    () => selected?.variants?.find((variant) => variant.id === selectedVariantId) || null,
    [selected, selectedVariantId],
  );
  const isFavorite = selected ? wishlistIds.has(selected.id) : false;

  async function ensureAuth() {
    if (localStorage.getItem("flashin_token")) return;
    if (!initialized || !initData) throw new Error("Telegram авторизация ещё не готова.");
    await telegramAuth(initData);
  }

  async function refreshSessionState() {
    const [wishlist, cart, nextAppointments] = await Promise.all([
      listWishlist(),
      getCart().catch(() => null),
      listMyShowroomAppointments().catch(() => []),
    ]);
    setWishlistIds(new Set((wishlist || []).map((item) => item.id)));
    setCartCount((cart?.items || []).reduce((sum, item) => sum + Number(item.quantity || 0), 0));
    setAppointments(nextAppointments || []);
  }

  async function loadCatalog(nextFilters = filters) {
    setLoading(true);
    setError("");
    try {
      await ensureAuth();
      const [rows] = await Promise.all([
        listCatalogProducts(nextFilters),
        refreshSessionState(),
      ]);
      setProducts(rows || []);
    } catch (actionError) {
      setError(actionError?.message || "Каталог временно недоступен.");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    if (!open) return;
    loadCatalog(filters);
  }, [open]);

  async function openProduct(product) {
    setBusy(`product-${product.id}`);
    setError("");
    try {
      await ensureAuth();
      const [detail, rows] = await Promise.all([
        getCatalogProduct(product.id),
        listProductFeedback(product.id),
      ]);
      setSelected(detail);
      setSelectedVariantId(detail.variants?.find((item) => item.available_qty > 0)?.id || detail.variants?.[0]?.id || null);
      setFeedback(rows || []);
      setFeedbackForm({ rating: 5, comment: "" });
      setAppointmentForm({ starts_at: "", notes: "" });
    } catch (actionError) {
      setError(actionError?.message || "Не удалось открыть карточку.");
    } finally {
      setBusy("");
    }
  }

  async function toggleFavorite() {
    if (!selected) return;
    setBusy("wishlist");
    setError("");
    try {
      await ensureAuth();
      if (isFavorite) await removeWishlist(selected.id);
      else await addWishlist(selected.id);
      setWishlistIds((current) => {
        const next = new Set(current);
        if (isFavorite) next.delete(selected.id); else next.add(selected.id);
        return next;
      });
      setDirtyLegacyState(true);
      setNotice(isFavorite ? "Удалено из избранного." : "Добавлено в избранное.");
    } catch (actionError) {
      setError(actionError?.message || "Не удалось изменить избранное.");
    } finally {
      setBusy("");
    }
  }

  async function addSelectedToCart() {
    if (!selected || !selectedVariant || selectedVariant.available_qty <= 0) return;
    setBusy("cart");
    setError("");
    try {
      await ensureAuth();
      const cart = await addToCart(selected.id, selectedVariant.id, 1);
      setCartCount((cart?.items || []).reduce((sum, item) => sum + Number(item.quantity || 0), 0));
      setDirtyLegacyState(true);
      setNotice(`${selected.title}, размер ${selectedVariant.size}, добавлен в корзину.`);
    } catch (actionError) {
      setError(actionError?.message || "Не удалось добавить товар в корзину.");
    } finally {
      setBusy("");
    }
  }

  async function sendFeedback() {
    if (!selected) return;
    setBusy("feedback");
    setError("");
    try {
      await ensureAuth();
      await submitProductFeedback(selected.id, feedbackForm.rating, feedbackForm.comment);
      setFeedback(await listProductFeedback(selected.id));
      const refreshed = await getCatalogProduct(selected.id);
      setSelected(refreshed);
      setFeedbackForm({ rating: 5, comment: "" });
      setNotice("Оценка сохранена.");
    } catch (actionError) {
      setError(actionError?.message || "Не удалось сохранить оценку.");
    } finally {
      setBusy("");
    }
  }

  async function bookShowroom() {
    if (!selected || !appointmentForm.starts_at) {
      setError("Выберите дату и время примерки.");
      return;
    }
    setBusy("showroom");
    setError("");
    try {
      await ensureAuth();
      await createShowroomAppointment(selected.id, appointmentForm.starts_at, appointmentForm.notes);
      setAppointments(await listMyShowroomAppointments());
      setAppointmentForm({ starts_at: "", notes: "" });
      setNotice("Запрос на примерку отправлен. Статус будет доступен в списке записей.");
    } catch (actionError) {
      setError(actionError?.message || "Не удалось создать запись на примерку.");
    } finally {
      setBusy("");
    }
  }

  function closeExperience() {
    setOpen(false);
    setSelected(null);
    setError("");
    if (dirtyLegacyState) {
      window.location.reload();
    }
  }

  if (!open) {
    return (
      <button
        type="button"
        className="catalog-plus-launcher"
        onClick={() => setOpen(true)}
        aria-label="Открыть каталог с фильтрами"
      >
        Catalog+ · фильтры и примерка
      </button>
    );
  }

  return (
    <div className="catalog-plus-overlay" role="dialog" aria-modal="true" aria-label="Расширенный каталог FLASHIN">
      <div className="catalog-plus-shell">
        <header className="topbar catalog-plus-topbar">
          <div>
            <div className="brand">FLASHIN · Catalog+</div>
            <div className="hello">Карточки, фильтры, отзывы и примерка</div>
          </div>
          <div className="catalog-plus-top-actions">
            <span>Корзина · {cartCount}</span>
            <button type="button" className="secondary compact" onClick={closeExperience}>К покупкам/оформлению</button>
          </div>
        </header>

        {error && <div className="message error" role="alert">{error}<button type="button" onClick={() => setError("")}>×</button></div>}
        {notice && <div className="message success" role="status">{notice}<button type="button" onClick={() => setNotice("")}>×</button></div>}

        {!selected ? (
          <main>
            <div className="section-heading"><div><h1>Расширенный каталог</h1><p>Фильтруйте по merchandising-признакам и реальной доступности.</p></div></div>
            <div className="catalog-plus-filters">
              <input aria-label="Catalog+ поиск" value={filters.q} onChange={(event) => setFilters({ ...filters, q: event.target.value })} placeholder="Название, бренд, SKU" />
              <input value={filters.brand} onChange={(event) => setFilters({ ...filters, brand: event.target.value })} placeholder="Бренд" />
              <input value={filters.category} onChange={(event) => setFilters({ ...filters, category: event.target.value })} placeholder="Категория" />
              <input value={filters.material} onChange={(event) => setFilters({ ...filters, material: event.target.value })} placeholder="Материал" />
              <input value={filters.season} onChange={(event) => setFilters({ ...filters, season: event.target.value })} placeholder="Сезон" />
              <input value={filters.size} onChange={(event) => setFilters({ ...filters, size: event.target.value })} placeholder="Размер" />
              <input value={filters.color} onChange={(event) => setFilters({ ...filters, color: event.target.value })} placeholder="Цвет" />
              <select value={filters.availability_status} onChange={(event) => setFilters({ ...filters, availability_status: event.target.value })}>
                <option value="">Любая доступность</option>
                <option value="in_stock">В наличии</option>
                <option value="preorder">Предзаказ</option>
                <option value="made_to_order">Под заказ</option>
                <option value="out_of_stock">Нет в наличии</option>
              </select>
              <select value={filters.badge} onChange={(event) => setFilters({ ...filters, badge: event.target.value })}>
                <option value="">Любой статус</option>
                {Object.entries(BADGE_LABELS).map(([value, label]) => <option value={value} key={value}>{label}</option>)}
              </select>
              <input type="number" min="0" value={filters.min_price} onChange={(event) => setFilters({ ...filters, min_price: event.target.value })} placeholder="Цена от" />
              <input type="number" min="0" value={filters.max_price} onChange={(event) => setFilters({ ...filters, max_price: event.target.value })} placeholder="Цена до" />
              <select value={filters.sort} onChange={(event) => setFilters({ ...filters, sort: event.target.value })}>
                <option value="grid">Порядок магазина</option>
                <option value="price_asc">Цена: сначала ниже</option>
                <option value="price_desc">Цена: сначала выше</option>
                <option value="newest">Сначала новые</option>
                <option value="rating_desc">По рейтингу</option>
              </select>
            </div>
            <div className="actions horizontal">
              <button type="button" className="primary" onClick={() => loadCatalog(filters)} disabled={loading}>{loading ? "Загрузка…" : "Применить фильтры"}</button>
              <button type="button" className="secondary" onClick={() => { setFilters(DEFAULT_FILTERS); loadCatalog(DEFAULT_FILTERS); }} disabled={loading}>Сбросить</button>
            </div>
            {!loading && !products.length && <div className="empty-state"><h2>Ничего не найдено</h2><p>Измените фильтры каталога.</p></div>}
            <div className="grid">
              {products.map((product) => <CatalogCard product={product} onOpen={openProduct} key={product.id} />)}
            </div>

            {!!appointments.length && (
              <section className="panel">
                <h2>Мои записи на примерку</h2>
                {appointments.slice(0, 10).map((appointment) => (
                  <div className="status-row" key={appointment.id}>
                    <span>Товар #{appointment.product_id} · {new Date(appointment.starts_at).toLocaleString("ru-RU")}</span>
                    <b className="status neutral">{appointment.status}</b>
                  </div>
                ))}
              </section>
            )}
          </main>
        ) : (
          <main>
            <button type="button" className="link" onClick={() => setSelected(null)}>← К результатам</button>
            <div className="catalog-plus-media">
              <img className="hero" src={imageUrl(selected)} alt={selected.title} />
              {(selected.videos || []).map((video) => (
                <video key={video.id || video.url} className="catalog-plus-video" src={video.url} controls preload="metadata" aria-label={video.title || `Видео ${selected.title}`} />
              ))}
            </div>
            <div className="catalog-plus-badges detail-badges">
              {(selected.merchandising?.badges || []).map((badge) => <span className="catalog-plus-badge" key={badge}>{BADGE_LABELS[badge] || badge}</span>)}
            </div>
            <div className="product-heading">
              <div>
                <div className="meta">{selected.brand} · {selected.category}</div>
                <h1>{selected.title}</h1>
                <div className="meta">{selected.merchandising?.material || "Материал не указан"}{selected.merchandising?.season ? ` · ${selected.merchandising.season}` : ""}</div>
                <div className="meta">{STATUS_LABELS[selected.merchandising?.availability_status] || selected.merchandising?.availability_status}</div>
                <div className="meta">★ {Number(selected.rating?.average || 0).toFixed(1)} · {selected.rating?.count || 0} отзывов</div>
              </div>
              <div className="price">{money(selected.price, selected.currency)}</div>
            </div>
            {selected.old_price > selected.price && <p><s>{money(selected.old_price, selected.currency)}</s></p>}
            {selected.description && <p>{selected.description}</p>}

            <h3>Размер / цвет</h3>
            <div className="sizes">
              {(selected.variants || []).map((variant) => (
                <button
                  type="button"
                  key={variant.id}
                  className={`${selectedVariantId === variant.id ? "size active" : "size"} ${variant.available_qty <= 0 ? "unavailable" : ""}`}
                  onClick={() => setSelectedVariantId(variant.id)}
                >
                  {variant.size}{variant.color ? ` · ${variant.color}` : ""}
                  <small>{variant.available_qty > 0 ? `${variant.available_qty} шт.` : "нет локально"}</small>
                </button>
              ))}
            </div>

            <div className="actions horizontal">
              {selectedVariant?.available_qty > 0 && <button type="button" className="primary" onClick={addSelectedToCart} disabled={busy === "cart"}>Добавить в корзину</button>}
              <button type="button" className="secondary" onClick={toggleFavorite} disabled={busy === "wishlist"}>{isFavorite ? "Убрать из избранного" : "В избранное"}</button>
              {selected.share?.telegram_share_url && <a className="secondary button-link" href={selected.share.telegram_share_url} target="_blank" rel="noreferrer">Отправить в Telegram</a>}
            </div>

            {!!selected.external_availability?.length && (
              <section className="panel">
                <h2>Где ещё доступен товар</h2>
                {selected.external_availability.map((item) => (
                  <div className="status-row" key={item.id}>
                    <span>{item.source_name} · {STATUS_LABELS[item.availability_status] || item.availability_status}{item.price != null ? ` · ${money(item.price, item.currency)}` : ""}</span>
                    <a href={item.url} target="_blank" rel="noreferrer">Открыть</a>
                  </div>
                ))}
              </section>
            )}

            {selected.merchandising?.showroom_fitting_enabled && (
              <section className="panel">
                <h2>Примерка в шоуруме</h2>
                <p>Выберите время с шагом 30 минут. Запись сначала получает статус requested.</p>
                <div className="form-grid">
                  <input type="datetime-local" aria-label="Дата и время примерки" value={appointmentForm.starts_at} onChange={(event) => setAppointmentForm({ ...appointmentForm, starts_at: event.target.value })} />
                  <input value={appointmentForm.notes} onChange={(event) => setAppointmentForm({ ...appointmentForm, notes: event.target.value })} placeholder="Комментарий к визиту" />
                </div>
                <button type="button" className="secondary" onClick={bookShowroom} disabled={busy === "showroom"}>Записаться на примерку</button>
              </section>
            )}

            <section className="panel">
              <h2>Оценки и отзывы</h2>
              <div className="form-grid">
                <select aria-label="Оценка товара" value={feedbackForm.rating} onChange={(event) => setFeedbackForm({ ...feedbackForm, rating: Number(event.target.value) })}>
                  {[5, 4, 3, 2, 1].map((rating) => <option value={rating} key={rating}>{rating} ★</option>)}
                </select>
                <input value={feedbackForm.comment} maxLength={2000} onChange={(event) => setFeedbackForm({ ...feedbackForm, comment: event.target.value })} placeholder="Ваш комментарий" />
              </div>
              <button type="button" className="secondary" onClick={sendFeedback} disabled={busy === "feedback"}>Сохранить оценку</button>
              <div className="catalog-plus-feedback">
                {feedback.map((item) => <div className="result-card" key={item.id}><b>{item.rating} ★</b><p>{item.comment || "Без комментария"}</p></div>)}
              </div>
            </section>

            {!!selected.recommendations?.length && (
              <section>
                <h2>Complete the look</h2>
                <div className="grid">
                  {selected.recommendations.map((product) => <CatalogCard key={product.id} product={product} onOpen={openProduct} />)}
                </div>
              </section>
            )}
          </main>
        )}
      </div>
    </div>
  );
}
