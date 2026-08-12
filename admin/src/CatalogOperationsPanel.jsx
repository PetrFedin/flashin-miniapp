import React, { useEffect, useMemo, useRef, useState } from "react";

import { AdminApiError, adminJson } from "./api.js";
import {
  availableQty,
  catalogAttentionCount,
  normalizeCatalogPrice,
  normalizeCatalogStock,
  normalizeCatalogText,
} from "./catalogOperations.js";

function operationError(error) {
  if (error instanceof AdminApiError && error.status === 403) {
    return "Недостаточно прав для управления каталогом или остатками.";
  }
  return error?.message || "Операция с каталогом не выполнена.";
}

function initialProductDraft(product) {
  return {
    title: product.title || "",
    brand: product.brand || "",
    category: product.category || "",
    description: product.description || "",
    price: String(product.price ?? ""),
  };
}

export default function CatalogOperationsPanel({ products, onReload, onUnauthorized }) {
  const ownsProducts = !Array.isArray(products);
  const [ownedProducts, setOwnedProducts] = useState([]);
  const [productDrafts, setProductDrafts] = useState({});
  const [stockDrafts, setStockDrafts] = useState({});
  const [busyKeys, setBusyKeys] = useState(() => new Set());
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const locks = useRef(new Set());

  const safeProducts = ownsProducts ? ownedProducts : products;
  const attentionCount = useMemo(() => catalogAttentionCount(safeProducts), [safeProducts]);

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

  async function loadCatalog() {
    const nextProducts = await adminJson("/api/admin/products");
    setOwnedProducts(Array.isArray(nextProducts) ? nextProducts : []);
    return nextProducts;
  }

  useEffect(() => {
    if (!ownsProducts) return undefined;
    run("catalog-initial", loadCatalog);
    return undefined;
  }, [ownsProducts]);

  function productDraft(product) {
    return productDrafts[product.id] || initialProductDraft(product);
  }

  function updateProductDraft(product, field, value) {
    setProductDrafts((current) => ({
      ...current,
      [product.id]: {
        ...(current[product.id] || initialProductDraft(product)),
        [field]: value,
      },
    }));
  }

  function clearProductDraft(productId) {
    setProductDrafts((current) => {
      const next = { ...current };
      delete next[productId];
      return next;
    });
  }

  async function reload() {
    if (onReload) {
      await onReload();
      return;
    }
    if (ownsProducts) await loadCatalog();
  }

  async function saveProduct(product) {
    const draft = productDraft(product);
    const title = normalizeCatalogText(draft.title, "Название", 255);
    const brand = normalizeCatalogText(draft.brand, "Бренд", 120);
    const category = normalizeCatalogText(draft.category, "Категория", 120);
    const price = normalizeCatalogPrice(draft.price);
    const validationError = title.error || brand.error || category.error || price.error;
    if (validationError) {
      setError(validationError);
      return;
    }

    const description = String(draft.description ?? "").trim();
    const payload = {};
    if (title.value !== product.title) payload.title = title.value;
    if (brand.value !== product.brand) payload.brand = brand.value;
    if (category.value !== product.category) payload.category = category.value;
    if (price.value !== Number(product.price)) payload.price = price.value;
    if (description !== String(product.description || "")) payload.description = description;

    if (!Object.keys(payload).length) {
      setError(`В товаре ${product.sku} нет изменений для сохранения.`);
      return;
    }

    const updated = await run(
      `product-${product.id}`,
      () => adminJson(`/api/admin/products/${product.id}`, {
        method: "PATCH",
        body: JSON.stringify(payload),
      }),
      `Товар ${product.sku} обновлён.`,
    );
    if (!updated) return;
    clearProductDraft(product.id);
    await reload();
  }

  async function toggleProduct(product) {
    const nextActive = !product.active;
    if (!nextActive) {
      const confirmed = window.confirm(
        `Скрыть товар ${product.sku}? Он исчезнет из Mini App, а checkout активных корзин с ним будет заблокирован.`,
      );
      if (!confirmed) return;
    }

    const updated = await run(
      `active-${product.id}`,
      () => adminJson(
        `/api/admin/products/${product.id}/active?active=${nextActive ? "true" : "false"}`,
        { method: "PATCH" },
      ),
      nextActive ? `Товар ${product.sku} снова опубликован.` : `Товар ${product.sku} скрыт из каталога.`,
    );
    if (!updated) return;
    clearProductDraft(product.id);
    await reload();
  }

  async function updateStock(product, variant) {
    const rawValue = stockDrafts[variant.id] ?? String(variant.stock_qty);
    const validation = normalizeCatalogStock(rawValue, variant.reserved_qty);
    if (validation.error) {
      setError(`${variant.sku}: ${validation.error}`);
      return;
    }
    if (validation.value === Number(variant.stock_qty)) {
      setError(`Остаток ${variant.sku} не изменился.`);
      return;
    }

    const nextAvailable = validation.value - Number(variant.reserved_qty || 0);
    const confirmed = window.confirm(
      `Изменить физический остаток ${variant.sku}: ${variant.stock_qty} → ${validation.value}? `
      + `Зарезервировано: ${variant.reserved_qty}; доступно после изменения: ${nextAvailable}.`,
    );
    if (!confirmed) return;

    const updated = await run(
      `stock-${variant.id}`,
      () => adminJson(
        `/api/admin/variants/${variant.id}/stock`
        + `?stock_qty=${encodeURIComponent(validation.value)}`
        + `&reason=${encodeURIComponent("manual admin catalog adjustment")}`,
        { method: "PATCH" },
      ),
      `Остаток ${variant.sku} обновлён.`,
    );
    if (!updated) return;
    setStockDrafts((current) => {
      const next = { ...current };
      delete next[variant.id];
      return next;
    });
    await reload();
  }

  return (
    <section className="service-operations" aria-labelledby="catalog-operations-title">
      <div className="section-title-row">
        <div>
          <h2 id="catalog-operations-title">Каталог и остатки</h2>
          <p>Master-data, публикация и физические остатки SKU с контролем резервов.</p>
        </div>
        <div className={`attention-badge ${attentionCount ? "attention" : "ok"}`}>
          Активных с sold-out SKU: {attentionCount}
        </div>
        <button type="button" onClick={() => run("catalog-refresh", reload, "Каталог обновлён.")} disabled={isBusy("catalog-refresh")}>
          Обновить каталог
        </button>
      </div>

      {error && <div className="error" role="alert">{error}<button type="button" onClick={() => setError("")}>×</button></div>}
      {notice && <div className="notice" role="status">{notice}<button type="button" onClick={() => setNotice("")}>×</button></div>}

      {!safeProducts.length && <p>Товары не найдены.</p>}
      <div className="service-grid">
        {safeProducts.map((product) => {
          const draft = productDraft(product);
          const productBusy = isBusy(`product-${product.id}`) || isBusy(`active-${product.id}`);
          return (
            <article className="service-card" key={product.id} aria-labelledby={`catalog-product-${product.id}`}>
              <div className="service-item-heading">
                <h3 id={`catalog-product-${product.id}`}>{product.title}</h3>
                <span>{product.active ? "Опубликован" : "Скрыт"}</span>
              </div>
              <p><b>{product.sku}</b> · {product.currency}</p>

              <div className="service-controls">
                <label>
                  Название
                  <input
                    aria-label={`Название ${product.sku}`}
                    value={draft.title}
                    maxLength={255}
                    disabled={productBusy}
                    onChange={(event) => updateProductDraft(product, "title", event.target.value)}
                  />
                </label>
                <label>
                  Бренд
                  <input
                    aria-label={`Бренд ${product.sku}`}
                    value={draft.brand}
                    maxLength={120}
                    disabled={productBusy}
                    onChange={(event) => updateProductDraft(product, "brand", event.target.value)}
                  />
                </label>
                <label>
                  Категория
                  <input
                    aria-label={`Категория ${product.sku}`}
                    value={draft.category}
                    maxLength={120}
                    disabled={productBusy}
                    onChange={(event) => updateProductDraft(product, "category", event.target.value)}
                  />
                </label>
                <label>
                  Цена
                  <input
                    aria-label={`Цена ${product.sku}`}
                    type="number"
                    min="0.01"
                    step="0.01"
                    value={draft.price}
                    disabled={productBusy}
                    onChange={(event) => updateProductDraft(product, "price", event.target.value)}
                  />
                </label>
                <label>
                  Описание
                  <textarea
                    aria-label={`Описание ${product.sku}`}
                    value={draft.description}
                    disabled={productBusy}
                    onChange={(event) => updateProductDraft(product, "description", event.target.value)}
                  />
                </label>
                <button type="button" onClick={() => saveProduct(product)} disabled={productBusy}>
                  Сохранить товар {product.sku}
                </button>
                <button
                  type="button"
                  className={product.active ? "danger" : ""}
                  onClick={() => toggleProduct(product)}
                  disabled={productBusy}
                >
                  {product.active ? `Скрыть товар ${product.sku}` : `Вернуть товар ${product.sku}`}
                </button>
              </div>

              <h4>SKU и остатки</h4>
              {!product.variants?.length && <p className="error-inline">У товара нет вариантов.</p>}
              {(product.variants || []).map((variant) => {
                const variantBusy = isBusy(`stock-${variant.id}`);
                return (
                  <div className="service-item" key={variant.id}>
                    <div className="service-item-heading">
                      <b>{variant.sku}</b>
                      <span>{availableQty(variant) > 0 ? "В наличии" : "Sold out"}</span>
                    </div>
                    <small>
                      Размер {variant.size || "—"}{variant.color ? ` · ${variant.color}` : ""}
                      {` · stock ${variant.stock_qty} · reserved ${variant.reserved_qty} · available ${availableQty(variant)}`}
                    </small>
                    <div className="service-controls">
                      <label>
                        Физический остаток
                        <input
                          aria-label={`Остаток ${variant.sku}`}
                          type="number"
                          min={variant.reserved_qty || 0}
                          step="1"
                          value={stockDrafts[variant.id] ?? String(variant.stock_qty)}
                          disabled={variantBusy}
                          onChange={(event) => setStockDrafts((current) => ({
                            ...current,
                            [variant.id]: event.target.value,
                          }))}
                        />
                      </label>
                      <button type="button" onClick={() => updateStock(product, variant)} disabled={variantBusy}>
                        Обновить остаток {variant.sku}
                      </button>
                    </div>
                  </div>
                );
              })}
            </article>
          );
        })}
      </div>
    </section>
  );
}
