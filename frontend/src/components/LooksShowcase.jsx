import React, { useMemo, useState } from "react";

function formatMoney(value, currency = "RUB") {
  return `${Number(value || 0).toLocaleString("ru-RU")} ${currency}`;
}

function resolveLookProducts(look, products) {
  return look.product_ids
    .map((id) => products.find((product) => String(product.id) === String(id)))
    .filter(Boolean);
}

function isAvailable(product) {
  return (product.variants || []).some((variant) => variant.available_qty > 0);
}

export default function LooksShowcase({ looks, products, onOpenProduct, onAddLook, addingLookId }) {
  const [activeLookId, setActiveLookId] = useState(looks[0]?.id || null);
  const activeLook = looks.find((look) => look.id === activeLookId) || looks[0];
  const activeProducts = useMemo(
    () => activeLook ? resolveLookProducts(activeLook, products) : [],
    [activeLook, products],
  );
  const total = activeProducts.reduce((sum, product) => sum + Number(product.price || 0), 0);
  const availableProducts = activeProducts.filter(isAvailable);
  const currency = activeProducts[0]?.currency || "RUB";

  if (!looks.length) {
    return (
      <main className="looks-page">
        <section className="page-intro">
          <span className="eyebrow">FLASHIN STYLING</span>
          <h1>Образы</h1>
          <p>Скоро здесь появятся готовые сочетания от команды бренда.</p>
        </section>
        <div className="empty-state">
          <b>Образы готовятся</b>
          <p>Пока можно собрать свой комплект из каталога и сохранить понравившиеся вещи.</p>
        </div>
      </main>
    );
  }

  return (
    <main className="looks-page">
      <section className="page-intro">
        <span className="eyebrow">FLASHIN STYLING</span>
        <h1>Готовые образы</h1>
        <p>Выберите комплект целиком или откройте отдельную вещь, чтобы изменить сочетание.</p>
      </section>

      <div className="look-tabs" aria-label="Выбор образа">
        {looks.map((look, index) => (
          <button
            key={look.id}
            type="button"
            className={(activeLook?.id === look.id) ? "active" : ""}
            onClick={() => setActiveLookId(look.id)}
          >
            <span>{String(index + 1).padStart(2, "0")}</span>
            {look.title}
          </button>
        ))}
      </div>

      {activeLook && (
        <section className="active-look" aria-labelledby={`look-${activeLook.id}`}>
          <div className="active-look-heading">
            <div>
              <span className="eyebrow">CURATED LOOK</span>
              <h2 id={`look-${activeLook.id}`}>{activeLook.title}</h2>
              {activeLook.description && <p>{activeLook.description}</p>}
            </div>
            <div className="look-total">
              <small>{availableProducts.length} из {activeProducts.length} в наличии</small>
              <strong>{formatMoney(total, currency)}</strong>
            </div>
          </div>

          <div className="look-product-grid">
            {activeProducts.map((product, index) => (
              <article className="look-product-card" key={product.id}>
                <button type="button" className="look-product-image" onClick={() => onOpenProduct(product)}>
                  <img src={product.images?.[0]?.url || "/fallback-product.svg"} alt={product.title} loading="lazy" />
                  <span>{String(index + 1).padStart(2, "0")}</span>
                </button>
                <button type="button" className="look-product-copy" onClick={() => onOpenProduct(product)}>
                  <b>{product.title}</b>
                  <small>{product.category}</small>
                  <strong>{formatMoney(product.price, product.currency)}</strong>
                  <span className={isAvailable(product) ? "available" : "sold-out"}>
                    {isAvailable(product) ? "В наличии" : "Нет в наличии"}
                  </span>
                </button>
              </article>
            ))}
          </div>

          {!activeProducts.length && (
            <div className="empty-state compact-empty">
              <b>Товары образа обновляются</b>
              <p>Некоторые позиции могли быть сняты с публикации.</p>
            </div>
          )}

          <div className="look-purchase-panel">
            <div>
              <span className="eyebrow">БЫСТРАЯ ПОКУПКА</span>
              <b>Добавить все доступные позиции</b>
              <small>Для каждой вещи автоматически выбирается первый доступный размер. Размер можно изменить в корзине или карточке товара.</small>
            </div>
            <button
              className="primary"
              type="button"
              disabled={!availableProducts.length || addingLookId === activeLook.id}
              onClick={() => onAddLook(activeLook, availableProducts)}
            >
              {addingLookId === activeLook.id ? "Добавляем…" : `Добавить образ · ${formatMoney(availableProducts.reduce((sum, product) => sum + Number(product.price || 0), 0), currency)}`}
            </button>
          </div>
        </section>
      )}
    </main>
  );
}
