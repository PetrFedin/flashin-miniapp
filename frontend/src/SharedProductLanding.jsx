import React, { useEffect, useMemo, useState } from "react";

import { addToCart, addWishlist, telegramAuth } from "./api";
import { getCatalogProduct } from "./catalogApi.js";
import { useTelegram } from "./hooks/useTelegram";

function money(value, currency = "RUB") {
  return new Intl.NumberFormat("ru-RU", {
    style: "currency",
    currency,
    maximumFractionDigits: 2,
  }).format(Number(value || 0));
}

function positiveProductId(value) {
  const parsed = Number(value);
  return Number.isInteger(parsed) && parsed > 0 ? parsed : null;
}

function productIdFromStartParam(value) {
  const match = String(value || "").trim().match(/^product_([1-9][0-9]*)$/);
  return match ? positiveProductId(match[1]) : null;
}

function sharedProductId() {
  const params = new URLSearchParams(window.location.search);
  const directId = positiveProductId(params.get("product"));
  if (directId) return directId;

  const urlStartId = productIdFromStartParam(params.get("tgWebAppStartParam"));
  if (urlStartId) return urlStartId;

  const telegramStartParam = window.Telegram?.WebApp?.initDataUnsafe?.start_param;
  return productIdFromStartParam(telegramStartParam);
}

export default function SharedProductLanding() {
  const { initData, initialized } = useTelegram();
  const productId = useMemo(() => sharedProductId(), [initialized]);
  const [product, setProduct] = useState(null);
  const [variantId, setVariantId] = useState(null);
  const [loading, setLoading] = useState(Boolean(productId));
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  const variant = useMemo(
    () => product?.variants?.find((item) => item.id === variantId) || null,
    [product, variantId],
  );

  async function ensureAuth() {
    if (localStorage.getItem("flashin_token")) return;
    if (!initialized || !initData) throw new Error("Telegram авторизация ещё не готова.");
    await telegramAuth(initData);
  }

  useEffect(() => {
    if (!productId || !initialized) return;
    let cancelled = false;
    setLoading(true);
    setError("");
    (async () => {
      try {
        await ensureAuth();
        const next = await getCatalogProduct(productId);
        if (cancelled) return;
        setProduct(next);
        setVariantId(next.variants?.find((item) => item.available_qty > 0)?.id || next.variants?.[0]?.id || null);
      } catch (actionError) {
        if (!cancelled) setError(actionError?.message || "Не удалось открыть отправленную карточку.");
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => { cancelled = true; };
  }, [productId, initialized]);

  if (!productId) return null;

  function closeLanding() {
    const url = new URL(window.location.href);
    url.searchParams.delete("product");
    url.searchParams.delete("tgWebAppStartParam");
    window.history.replaceState({}, "", `${url.pathname}${url.search}${url.hash}`);
    window.location.reload();
  }

  async function saveFavorite() {
    if (!product) return;
    setBusy("wishlist");
    setError("");
    try {
      await ensureAuth();
      await addWishlist(product.id);
      setNotice("Карточка добавлена в избранное.");
    } catch (actionError) {
      setError(actionError?.message || "Не удалось добавить в избранное.");
    } finally {
      setBusy("");
    }
  }

  async function addProductToCart() {
    if (!product || !variant || variant.available_qty <= 0) return;
    setBusy("cart");
    setError("");
    try {
      await ensureAuth();
      await addToCart(product.id, variant.id, 1);
      setNotice("Товар добавлен в корзину. Закройте карточку, чтобы перейти к оформлению.");
    } catch (actionError) {
      setError(actionError?.message || "Не удалось добавить товар в корзину.");
    } finally {
      setBusy("");
    }
  }

  return (
    <div className="catalog-plus-overlay shared-product-overlay" role="dialog" aria-modal="true" aria-label="Отправленная карточка товара">
      <div className="catalog-plus-shell">
        <header className="topbar catalog-plus-topbar">
          <div>
            <div className="brand">FLASHIN</div>
            <div className="hello">Отправленная карточка</div>
          </div>
          <button type="button" className="secondary compact" onClick={closeLanding}>Закрыть</button>
        </header>
        {error && <div className="message error" role="alert">{error}</div>}
        {notice && <div className="message success" role="status">{notice}</div>}
        {loading && <main><p>Загрузка карточки…</p></main>}
        {!loading && product && (
          <main>
            <img className="hero" src={product.images?.[0]?.url || "/fallback-product.svg"} alt={product.title} />
            <div className="catalog-plus-badges detail-badges">
              {(product.merchandising?.badges || []).map((badge) => <span className="catalog-plus-badge" key={badge}>{badge}</span>)}
            </div>
            <div className="product-heading">
              <div>
                <div className="meta">{product.brand} · {product.category}</div>
                <h1>{product.title}</h1>
                <div className="meta">{product.merchandising?.material || ""}{product.merchandising?.season ? ` · ${product.merchandising.season}` : ""}</div>
              </div>
              <div className="price">{money(product.price, product.currency)}</div>
            </div>
            {product.description && <p>{product.description}</p>}
            <div className="sizes">
              {(product.variants || []).map((item) => (
                <button
                  type="button"
                  key={item.id}
                  className={`${variantId === item.id ? "size active" : "size"} ${item.available_qty <= 0 ? "unavailable" : ""}`}
                  onClick={() => setVariantId(item.id)}
                >
                  {item.size}{item.color ? ` · ${item.color}` : ""}
                  <small>{item.available_qty > 0 ? `${item.available_qty} шт.` : "нет локально"}</small>
                </button>
              ))}
            </div>
            <div className="actions horizontal">
              {variant?.available_qty > 0 && <button type="button" className="primary" onClick={addProductToCart} disabled={busy === "cart"}>Добавить в корзину</button>}
              <button type="button" className="secondary" onClick={saveFavorite} disabled={busy === "wishlist"}>В избранное</button>
            </div>
            {!!product.external_availability?.length && (
              <section className="panel">
                <h2>Где ещё доступен товар</h2>
                {product.external_availability.map((item) => (
                  <div className="status-row" key={item.id}>
                    <span>{item.source_name}</span>
                    <a href={item.url} target="_blank" rel="noreferrer">Открыть</a>
                  </div>
                ))}
              </section>
            )}
          </main>
        )}
      </div>
    </div>
  );
}
