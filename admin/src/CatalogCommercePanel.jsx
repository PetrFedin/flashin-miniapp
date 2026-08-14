import React, { useEffect, useMemo, useRef, useState } from "react";

import { hasAdminPermission } from "./adminPermissions.js";
import { AdminApiError, adminJson, uploadAdminFile } from "./api.js";

const BADGE_OPTIONS = [
  ["bestseller", "Бестселлер"],
  ["exclusive", "Эксклюзив"],
  ["new_season", "Новый сезон"],
  ["sale", "Распродажа"],
  ["outlet", "Аутлет"],
  ["drop", "Drop"],
  ["limited", "Limited"],
];

function emptyForm() {
  return {
    id: null,
    sku: "",
    title: "",
    slug: "",
    brand: "FLASHIN",
    description: "",
    price: "",
    old_price: "",
    currency: "RUB",
    category: "Clothing",
    gender: "unisex",
    active: true,
    is_drop: false,
    is_rare: false,
    moysklad_id: "",
    availability_status: "in_stock",
    material: "",
    season: "",
    badges: [],
    grid_rank: 1000,
    sale_starts_at: "",
    sale_ends_at: "",
    showroom_fitting_enabled: true,
    images: [],
    videos: [],
    external_links: [],
    variants: [{ id: null, size: "", color: "", sku: "", moysklad_id: "", stock_qty: 0 }],
    remove_variant_ids: [],
    recommendation_ids: [],
  };
}

function localDateTime(value) {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  const pad = (part) => String(part).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

function apiDateTime(value) {
  return value ? new Date(value).toISOString() : null;
}

function formFromProduct(product) {
  const merch = product?.merchandising || {};
  return {
    ...emptyForm(),
    id: product.id,
    sku: product.sku || "",
    title: product.title || "",
    slug: product.slug || "",
    brand: product.brand || "FLASHIN",
    description: product.description || "",
    price: String(product.price ?? ""),
    old_price: product.old_price == null ? "" : String(product.old_price),
    currency: product.currency || "RUB",
    category: product.category || "Clothing",
    gender: product.gender || "unisex",
    active: product.active !== false,
    is_drop: product.is_drop === true,
    is_rare: product.is_rare === true,
    moysklad_id: product.moysklad_id || "",
    availability_status: merch.configured_availability_status || merch.availability_status || "in_stock",
    material: merch.material || "",
    season: merch.season || "",
    badges: Array.isArray(merch.badges) ? merch.badges : [],
    grid_rank: Number(merch.grid_rank ?? 1000),
    sale_starts_at: localDateTime(merch.sale_starts_at),
    sale_ends_at: localDateTime(merch.sale_ends_at),
    showroom_fitting_enabled: merch.showroom_fitting_enabled !== false,
    images: Array.isArray(product.images) ? product.images.map((item) => item.url) : [],
    videos: Array.isArray(product.videos)
      ? product.videos.map((item) => ({ url: item.url, title: item.title || "", sort_order: item.sort_order || 0, active: true }))
      : [],
    external_links: Array.isArray(product.external_availability)
      ? product.external_availability.map((item) => ({
        source_name: item.source_name || "",
        url: item.url || "",
        availability_status: item.availability_status || "in_stock",
        price: item.price == null ? "" : String(item.price),
        currency: item.currency || "RUB",
        active: true,
        sort_order: item.sort_order || 0,
      }))
      : [],
    variants: Array.isArray(product.variants) && product.variants.length
      ? product.variants.map((item) => ({
        id: item.id,
        size: item.size || "",
        color: item.color || "",
        sku: item.sku || "",
        moysklad_id: item.moysklad_id || "",
        stock_qty: Number(item.stock_qty || 0),
      }))
      : [{ id: null, size: "", color: "", sku: "", moysklad_id: "", stock_qty: 0 }],
    remove_variant_ids: [],
    recommendation_ids: Array.isArray(product.recommendation_ids) ? product.recommendation_ids : [],
  };
}

function payloadFromForm(form, canInventoryWrite) {
  return {
    sku: form.sku.trim(),
    title: form.title.trim(),
    slug: form.slug.trim(),
    brand: form.brand.trim(),
    description: form.description.trim(),
    price: Number(form.price),
    old_price: form.old_price === "" ? null : Number(form.old_price),
    currency: form.currency.trim().toUpperCase(),
    category: form.category.trim(),
    gender: form.gender.trim(),
    active: form.active,
    is_drop: form.is_drop,
    is_rare: form.is_rare,
    moysklad_id: form.moysklad_id.trim(),
    availability_status: form.availability_status,
    material: form.material.trim(),
    season: form.season.trim(),
    badges: form.badges,
    grid_rank: Number(form.grid_rank || 0),
    sale_starts_at: apiDateTime(form.sale_starts_at),
    sale_ends_at: apiDateTime(form.sale_ends_at),
    showroom_fitting_enabled: form.showroom_fitting_enabled,
    images: form.images,
    videos: form.videos.map((item, index) => ({
      url: item.url.trim(),
      title: item.title.trim(),
      sort_order: index,
      active: true,
    })).filter((item) => item.url),
    external_links: form.external_links.map((item, index) => ({
      source_name: item.source_name.trim(),
      url: item.url.trim(),
      availability_status: item.availability_status,
      price: item.price === "" ? null : Number(item.price),
      currency: item.currency.trim().toUpperCase(),
      active: true,
      sort_order: index,
    })).filter((item) => item.source_name && item.url),
    variants: form.variants.map((item) => ({
      id: item.id || null,
      size: item.size.trim(),
      color: item.color.trim(),
      sku: item.sku.trim(),
      moysklad_id: item.moysklad_id.trim(),
      stock_qty: canInventoryWrite ? Number(item.stock_qty || 0) : Number(item.stock_qty || 0),
    })),
    ...(form.id ? { remove_variant_ids: form.remove_variant_ids } : {}),
  };
}

function moveItem(items, index, delta) {
  const target = index + delta;
  if (target < 0 || target >= items.length) return items;
  const next = [...items];
  [next[index], next[target]] = [next[target], next[index]];
  return next;
}

export default function CatalogCommercePanel({ onUnauthorized, session }) {
  const canWrite = hasAdminPermission(session, "products.write");
  const canInventoryWrite = hasAdminPermission(session, "inventory.write");
  const canMediaWrite = hasAdminPermission(session, "media.write");
  const canShowroomRead = hasAdminPermission(session, "showroom.read");
  const canShowroomWrite = hasAdminPermission(session, "showroom.write");
  const [products, setProducts] = useState([]);
  const [form, setForm] = useState(() => emptyForm());
  const [appointments, setAppointments] = useState([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const requestSequence = useRef(0);

  const selectedProduct = useMemo(
    () => products.find((item) => item.id === form.id) || null,
    [products, form.id],
  );

  function handleFailure(actionError) {
    if (actionError instanceof AdminApiError && actionError.status === 401) {
      onUnauthorized?.("Сессия администратора истекла. Войдите снова.");
      return;
    }
    setError(actionError?.message || "Операция каталога не выполнена.");
  }

  async function loadAll({ preserveSelection = true } = {}) {
    const sequence = requestSequence.current + 1;
    requestSequence.current = sequence;
    setLoading(true);
    setError("");
    try {
      const [nextProducts, nextAppointments] = await Promise.all([
        adminJson("/api/catalog/admin/products"),
        canShowroomRead ? adminJson("/api/catalog/admin/showroom/appointments") : Promise.resolve([]),
      ]);
      if (requestSequence.current !== sequence) return;
      setProducts(Array.isArray(nextProducts) ? nextProducts : []);
      setAppointments(Array.isArray(nextAppointments) ? nextAppointments : []);
      if (preserveSelection && form.id) {
        const refreshed = nextProducts.find((item) => item.id === form.id);
        if (refreshed) setForm(formFromProduct(refreshed));
      }
    } catch (actionError) {
      if (requestSequence.current === sequence) handleFailure(actionError);
    } finally {
      if (requestSequence.current === sequence) setLoading(false);
    }
  }

  useEffect(() => {
    loadAll({ preserveSelection: false });
    return () => { requestSequence.current += 1; };
  }, [canShowroomRead]);

  function selectProduct(product) {
    setError("");
    setNotice("");
    setForm(formFromProduct(product));
  }

  function startNew() {
    setError("");
    setNotice("");
    setForm(emptyForm());
  }

  function patch(field, value) {
    setForm((current) => ({ ...current, [field]: value }));
  }

  function patchVariant(index, field, value) {
    setForm((current) => ({
      ...current,
      variants: current.variants.map((item, itemIndex) => itemIndex === index ? { ...item, [field]: value } : item),
    }));
  }

  function removeVariant(index) {
    setForm((current) => {
      const item = current.variants[index];
      if (current.variants.length === 1) return current;
      return {
        ...current,
        variants: current.variants.filter((_, itemIndex) => itemIndex !== index),
        remove_variant_ids: item?.id
          ? [...new Set([...current.remove_variant_ids, item.id])]
          : current.remove_variant_ids,
      };
    });
  }

  function toggleBadge(badge) {
    setForm((current) => ({
      ...current,
      badges: current.badges.includes(badge)
        ? current.badges.filter((item) => item !== badge)
        : [...current.badges, badge],
    }));
  }

  async function uploadPhoto(file) {
    if (!canMediaWrite || !file) return;
    setUploading(true);
    setError("");
    try {
      const asset = await uploadAdminFile("/api/media/upload", file);
      setForm((current) => ({ ...current, images: [...current.images, asset.url] }));
      setNotice("Фото загружено и добавлено в галерею. Сохраните карточку.");
    } catch (actionError) {
      handleFailure(actionError);
    } finally {
      setUploading(false);
    }
  }

  function validateForm() {
    if (!form.sku.trim() || !form.title.trim() || !form.slug.trim()) return "SKU, название и slug обязательны.";
    if (!(Number(form.price) > 0)) return "Цена должна быть больше нуля.";
    if (!form.category.trim() || !form.brand.trim()) return "Бренд и категория обязательны.";
    if (!form.variants.length || form.variants.some((item) => !item.size.trim() || !item.sku.trim())) {
      return "У каждого варианта должны быть размер и SKU.";
    }
    if (!canInventoryWrite && form.variants.some((item) => item.id == null && Number(item.stock_qty || 0) > 0)) {
      return "Новый вариант с остатком требует inventory.write.";
    }
    return "";
  }

  async function saveProduct() {
    if (!canWrite) return;
    const validationError = validateForm();
    if (validationError) {
      setError(validationError);
      return;
    }
    setSaving(true);
    setError("");
    setNotice("");
    try {
      const payload = payloadFromForm(form, canInventoryWrite);
      const saved = await adminJson(
        form.id ? `/api/catalog/admin/products/${form.id}` : "/api/catalog/admin/products",
        {
          method: form.id ? "PUT" : "POST",
          body: JSON.stringify(payload),
          dedupeKey: `catalog-product-save:${form.id || form.sku.trim().toUpperCase()}`,
        },
      );
      const recommendationIds = [...new Set(form.recommendation_ids.map(Number).filter((value) => Number.isInteger(value) && value > 0 && value !== saved.id))];
      await adminJson(`/api/catalog/admin/products/${saved.id}/recommendations`, {
        method: "PUT",
        body: JSON.stringify({ product_ids: recommendationIds }),
        dedupeKey: `catalog-recommendations-save:${saved.id}`,
      });
      setForm(formFromProduct({ ...saved, recommendation_ids: recommendationIds }));
      setNotice(`Карточка #${saved.id} сохранена. Изменения доступны клиентскому catalog API.`);
      await loadAll();
    } catch (actionError) {
      handleFailure(actionError);
    } finally {
      setSaving(false);
    }
  }

  async function updateAppointment(appointment, status) {
    if (!canShowroomWrite) return;
    setError("");
    try {
      await adminJson(`/api/catalog/admin/showroom/appointments/${appointment.id}`, {
        method: "PATCH",
        body: JSON.stringify({ status }),
        dedupeKey: `showroom-appointment:${appointment.id}:${status}`,
      });
      setNotice(`Запись #${appointment.id}: ${status}.`);
      await loadAll();
    } catch (actionError) {
      handleFailure(actionError);
    }
  }

  return (
    <section className="catalog-commerce" aria-labelledby="catalog-commerce-title">
      <div className="section-heading">
        <div>
          <h2 id="catalog-commerce-title">Каталог и merchandising</h2>
          <p>Полная карточка товара: контент, цены, статусы, медиа, stock/MoySklad, рекомендации, внешняя доступность и сетка.</p>
        </div>
        <div>
          {canWrite && <button type="button" onClick={startNew}>Новая карточка</button>}
          <button type="button" onClick={() => loadAll()} disabled={loading}>{loading ? "Обновление…" : "Обновить"}</button>
        </div>
      </div>

      {error && <div className="error" role="alert">{error}<button type="button" onClick={() => setError("")}>×</button></div>}
      {notice && <div className="notice" role="status">{notice}<button type="button" onClick={() => setNotice("")}>×</button></div>}

      <div className="event-layout">
        <div className="event-list">
          {products.map((product) => (
            <button
              type="button"
              key={product.id}
              className={`event-row ${form.id === product.id ? "selected" : ""}`}
              onClick={() => selectProduct(product)}
            >
              <strong>#{product.id} · {product.title}</strong>
              <span>{product.sku} · {product.brand}</span>
              <span>{product.price} {product.currency} · {product.merchandising?.availability_status}</span>
              <span>Grid {product.merchandising?.grid_rank ?? 1000} · {product.active ? "Включена" : "Скрыта"}</span>
              <small>{(product.merchandising?.badges || []).join(" · ") || "без badges"}</small>
            </button>
          ))}
        </div>

        <div className="event-detail">
          <div className="section-heading compact">
            <div>
              <h3>{form.id ? `Карточка #${form.id}` : "Новая карточка"}</h3>
              <p>{canWrite ? "Редактирование разрешено" : "Только чтение"}</p>
            </div>
            {selectedProduct?.share?.telegram_share_url && (
              <a href={selectedProduct.share.telegram_share_url} target="_blank" rel="noreferrer">Отправить в Telegram</a>
            )}
          </div>

          <div className="form-grid">
            <input aria-label="SKU карточки" value={form.sku} onChange={(event) => patch("sku", event.target.value)} placeholder="SKU" disabled={!canWrite} />
            <input aria-label="Название карточки" value={form.title} onChange={(event) => patch("title", event.target.value)} placeholder="Название" disabled={!canWrite} />
            <input aria-label="Slug карточки" value={form.slug} onChange={(event) => patch("slug", event.target.value)} placeholder="slug" disabled={!canWrite} />
            <input aria-label="Бренд карточки" value={form.brand} onChange={(event) => patch("brand", event.target.value)} placeholder="Бренд" disabled={!canWrite} />
            <input aria-label="Категория карточки" value={form.category} onChange={(event) => patch("category", event.target.value)} placeholder="Категория" disabled={!canWrite} />
            <input aria-label="Материал карточки" value={form.material} onChange={(event) => patch("material", event.target.value)} placeholder="Материал" disabled={!canWrite} />
            <input aria-label="Сезон карточки" value={form.season} onChange={(event) => patch("season", event.target.value)} placeholder="Сезон, например FW26" disabled={!canWrite} />
            <input aria-label="MoySklad ID товара" value={form.moysklad_id} onChange={(event) => patch("moysklad_id", event.target.value)} placeholder="MoySklad product ID" disabled={!canWrite} />
            <input type="number" min="0" step="0.01" aria-label="Цена карточки" value={form.price} onChange={(event) => patch("price", event.target.value)} placeholder="Цена" disabled={!canWrite} />
            <input type="number" min="0" step="0.01" aria-label="Старая цена карточки" value={form.old_price} onChange={(event) => patch("old_price", event.target.value)} placeholder="Старая цена" disabled={!canWrite} />
            <input type="number" aria-label="Позиция карточки в сетке" value={form.grid_rank} onChange={(event) => patch("grid_rank", event.target.value)} placeholder="Grid rank: меньше = выше" disabled={!canWrite} />
            <select aria-label="Статус доступности карточки" value={form.availability_status} onChange={(event) => patch("availability_status", event.target.value)} disabled={!canWrite}>
              <option value="in_stock">В наличии</option>
              <option value="preorder">Предзаказ</option>
              <option value="made_to_order">Под заказ</option>
              <option value="out_of_stock">Нет в наличии</option>
            </select>
            <input type="datetime-local" aria-label="Начало распродажи" value={form.sale_starts_at} onChange={(event) => patch("sale_starts_at", event.target.value)} disabled={!canWrite} />
            <input type="datetime-local" aria-label="Окончание распродажи" value={form.sale_ends_at} onChange={(event) => patch("sale_ends_at", event.target.value)} disabled={!canWrite} />
          </div>
          <textarea aria-label="Описание карточки" value={form.description} onChange={(event) => patch("description", event.target.value)} placeholder="Полное описание" disabled={!canWrite} />

          <div className="form-grid">
            <label><input type="checkbox" checked={form.active} onChange={(event) => patch("active", event.target.checked)} disabled={!canWrite} /> Карточка включена</label>
            <label><input type="checkbox" checked={form.showroom_fitting_enabled} onChange={(event) => patch("showroom_fitting_enabled", event.target.checked)} disabled={!canWrite} /> Доступна примерка</label>
            <label><input type="checkbox" checked={form.is_drop} onChange={(event) => patch("is_drop", event.target.checked)} disabled={!canWrite} /> Drop</label>
            <label><input type="checkbox" checked={form.is_rare} onChange={(event) => patch("is_rare", event.target.checked)} disabled={!canWrite} /> Rare/Limited</label>
          </div>

          <h4>Badges / фильтры</h4>
          <div className="form-grid">
            {BADGE_OPTIONS.map(([badge, label]) => (
              <label key={badge}>
                <input type="checkbox" checked={form.badges.includes(badge)} onChange={() => toggleBadge(badge)} disabled={!canWrite} /> {label}
              </label>
            ))}
          </div>

          <h4>Фото</h4>
          {canMediaWrite && (
            <input
              aria-label="Загрузить фото в карточку"
              type="file"
              accept="image/jpeg,image/png,image/webp"
              disabled={uploading || !canWrite}
              onChange={(event) => {
                const file = event.target.files?.[0];
                event.target.value = "";
                if (file) uploadPhoto(file);
              }}
            />
          )}
          <div className="table">
            {form.images.map((url, index) => (
              <div className="row" key={`${url}-${index}`}>
                <img src={url} alt={`Фото ${index + 1}`} style={{ width: 72, height: 72, objectFit: "cover" }} />
                <span>{url}</span>
                {canWrite && <button type="button" onClick={() => patch("images", moveItem(form.images, index, -1))}>↑</button>}
                {canWrite && <button type="button" onClick={() => patch("images", moveItem(form.images, index, 1))}>↓</button>}
                {canWrite && <button type="button" onClick={() => patch("images", form.images.filter((_, itemIndex) => itemIndex !== index))}>Удалить</button>}
              </div>
            ))}
          </div>

          <h4>Видео</h4>
          <p className="event-warning">Видео хранится как HTTPS media-link/CDN URL. Upload pipeline изображений намеренно не ослабляется для произвольных файлов.</p>
          {form.videos.map((video, index) => (
            <div className="form-grid" key={index}>
              <input aria-label={`Видео URL ${index + 1}`} value={video.url} onChange={(event) => patch("videos", form.videos.map((item, itemIndex) => itemIndex === index ? { ...item, url: event.target.value } : item))} placeholder="https://.../video.mp4" disabled={!canWrite} />
              <input value={video.title} onChange={(event) => patch("videos", form.videos.map((item, itemIndex) => itemIndex === index ? { ...item, title: event.target.value } : item))} placeholder="Название видео" disabled={!canWrite} />
              {canWrite && <button type="button" onClick={() => patch("videos", form.videos.filter((_, itemIndex) => itemIndex !== index))}>Удалить видео</button>}
            </div>
          ))}
          {canWrite && <button type="button" onClick={() => patch("videos", [...form.videos, { url: "", title: "", sort_order: form.videos.length, active: true }])}>Добавить видео</button>}

          <h4>Размеры / цвета / stock / MoySklad</h4>
          {!canInventoryWrite && <p className="event-warning">Stock только для чтения: изменение физического остатка требует inventory.write.</p>}
          {form.variants.map((variant, index) => (
            <div className="form-grid" key={variant.id || `new-${index}`}>
              <input value={variant.size} onChange={(event) => patchVariant(index, "size", event.target.value)} placeholder="Размер" disabled={!canWrite} />
              <input value={variant.color} onChange={(event) => patchVariant(index, "color", event.target.value)} placeholder="Цвет" disabled={!canWrite} />
              <input value={variant.sku} onChange={(event) => patchVariant(index, "sku", event.target.value)} placeholder="SKU варианта" disabled={!canWrite} />
              <input value={variant.moysklad_id} onChange={(event) => patchVariant(index, "moysklad_id", event.target.value)} placeholder="MoySklad variant ID" disabled={!canWrite} />
              <input type="number" min="0" value={variant.stock_qty} onChange={(event) => patchVariant(index, "stock_qty", Number(event.target.value))} placeholder="Stock" disabled={!canWrite || !canInventoryWrite} />
              {canWrite && <button type="button" onClick={() => removeVariant(index)} disabled={form.variants.length === 1}>Удалить вариант</button>}
            </div>
          ))}
          {canWrite && <button type="button" onClick={() => patch("variants", [...form.variants, { id: null, size: "", color: "", sku: "", moysklad_id: "", stock_qty: 0 }])}>Добавить вариант</button>}

          <h4>Где ещё есть товар</h4>
          {form.external_links.map((link, index) => (
            <div className="form-grid" key={index}>
              <input value={link.source_name} onChange={(event) => patch("external_links", form.external_links.map((item, itemIndex) => itemIndex === index ? { ...item, source_name: event.target.value } : item))} placeholder="Ресурс / магазин" disabled={!canWrite} />
              <input value={link.url} onChange={(event) => patch("external_links", form.external_links.map((item, itemIndex) => itemIndex === index ? { ...item, url: event.target.value } : item))} placeholder="https://..." disabled={!canWrite} />
              <select value={link.availability_status} onChange={(event) => patch("external_links", form.external_links.map((item, itemIndex) => itemIndex === index ? { ...item, availability_status: event.target.value } : item))} disabled={!canWrite}>
                <option value="in_stock">В наличии</option>
                <option value="preorder">Предзаказ</option>
                <option value="made_to_order">Под заказ</option>
                <option value="out_of_stock">Нет в наличии</option>
              </select>
              <input type="number" min="0" step="0.01" value={link.price} onChange={(event) => patch("external_links", form.external_links.map((item, itemIndex) => itemIndex === index ? { ...item, price: event.target.value } : item))} placeholder="Цена там" disabled={!canWrite} />
              {canWrite && <button type="button" onClick={() => patch("external_links", form.external_links.filter((_, itemIndex) => itemIndex !== index))}>Удалить ссылку</button>}
            </div>
          ))}
          {canWrite && <button type="button" onClick={() => patch("external_links", [...form.external_links, { source_name: "", url: "", availability_status: "in_stock", price: "", currency: "RUB", active: true, sort_order: form.external_links.length }])}>Добавить внешний ресурс</button>}

          <h4>Связанные карточки / complete the look</h4>
          <input
            aria-label="ID связанных карточек"
            value={form.recommendation_ids.join(", ")}
            onChange={(event) => patch("recommendation_ids", event.target.value.split(",").map((value) => value.trim()).filter(Boolean))}
            placeholder="ID через запятую: 12, 15, 41"
            disabled={!canWrite}
          />

          {canWrite && (
            <button type="button" className="primary" onClick={saveProduct} disabled={saving || uploading}>
              {saving ? "Сохранение…" : form.id ? "Сохранить карточку" : "Создать карточку"}
            </button>
          )}
        </div>
      </div>

      {canShowroomRead && (
        <div>
          <h3>Записи на примерку</h3>
          {!appointments.length && <p>Записей пока нет.</p>}
          <div className="table">
            {appointments.map((appointment) => (
              <div className="row" key={appointment.id}>
                <b>#{appointment.id}</b>
                <span>Product #{appointment.product_id}</span>
                <span>Customer #{appointment.customer_id}</span>
                <span>{new Date(appointment.starts_at).toLocaleString("ru-RU")}</span>
                <span>{appointment.status}</span>
                {canShowroomWrite && appointment.status === "requested" && <button type="button" onClick={() => updateAppointment(appointment, "confirmed")}>Подтвердить</button>}
                {canShowroomWrite && !["cancelled", "completed"].includes(appointment.status) && <button type="button" onClick={() => updateAppointment(appointment, "cancelled")}>Отменить</button>}
                {canShowroomWrite && appointment.status === "confirmed" && <button type="button" onClick={() => updateAppointment(appointment, "completed")}>Завершить</button>}
              </div>
            ))}
          </div>
        </div>
      )}
    </section>
  );
}
