import React, { useMemo, useRef, useState } from "react";

function formatMoney(value, currency = "RUB") {
  return `${Number(value || 0).toLocaleString("ru-RU")} ${currency}`;
}

function availableQuantity(product) {
  return (product.variants || []).reduce((total, variant) => total + Math.max(0, variant.available_qty || 0), 0);
}

function ProductCard({ product, isFavorite, onOpen, onToggleWishlist }) {
  const available = availableQuantity(product);
  const discount = product.old_price > product.price
    ? Math.round(((product.old_price - product.price) / product.old_price) * 100)
    : 0;

  return (
    <article className="product-card">
      <div className="product-image-wrap">
        <button className="product-image-button" type="button" onClick={() => onOpen(product)} aria-label={`Открыть ${product.title}`}>
          <img src={product.images?.[0]?.url || "/fallback-product.svg"} alt={product.title} loading="lazy" />
        </button>
        <div className="product-badges" aria-label="Особенности товара">
          {product.is_drop && <span>DROP</span>}
          {product.is_rare && <span>LIMITED</span>}
          {discount > 0 && <span>-{discount}%</span>}
        </div>
        <button
          className={`favorite-button ${isFavorite ? "active" : ""}`}
          type="button"
          aria-label={isFavorite ? `Убрать ${product.title} из избранного` : `Добавить ${product.title} в избранное`}
          onClick={() => onToggleWishlist(product)}
        >
          {isFavorite ? "♥" : "♡"}
        </button>
      </div>
      <button className="product-copy" type="button" onClick={() => onOpen(product)}>
        <span className="product-brand">{product.brand || "FLASHIN"}</span>
        <span className="product-title">{product.title}</span>
        <span className="product-price-row">
          <strong>{formatMoney(product.price, product.currency)}</strong>
          {product.old_price > product.price && <del>{formatMoney(product.old_price, product.currency)}</del>}
        </span>
        <span className={`stock-note ${available > 0 && available <= 3 ? "low" : ""}`}>
          {available <= 0 ? "Нет в наличии" : available <= 3 ? `Осталось ${available}` : "В наличии"}
        </span>
      </button>
    </article>
  );
}

function LookPreview({ look, products, onOpenProduct, onOpenLooks }) {
  const lookProducts = look.product_ids
    .map((id) => products.find((product) => String(product.id) === String(id)))
    .filter(Boolean)
    .slice(0, 3);

  return (
    <article className="look-preview">
      <button type="button" className="look-preview-gallery" onClick={onOpenLooks} aria-label={`Открыть образ ${look.title}`}>
        {lookProducts.length ? lookProducts.map((product) => (
          <img key={product.id} src={product.images?.[0]?.url || "/fallback-product.svg"} alt={product.title} loading="lazy" />
        )) : <div className="look-placeholder">FLASHIN LOOK</div>}
      </button>
      <div className="look-preview-copy">
        <div>
          <span className="eyebrow">ГОТОВЫЙ ОБРАЗ</span>
          <h3>{look.title}</h3>
          {look.description && <p>{look.description}</p>}
        </div>
        <div className="look-preview-actions">
          <button type="button" className="text-action" onClick={onOpenLooks}>Смотреть образ</button>
          {lookProducts[0] && <button type="button" className="text-action muted" onClick={() => onOpenProduct(lookProducts[0])}>Первый товар</button>}
        </div>
      </div>
    </article>
  );
}

export default function CommerceHome({
  products,
  looks,
  wishlistIds,
  searchQuery,
  onSearchQueryChange,
  onSearch,
  onOpenProduct,
  onToggleWishlist,
  onOpenLooks,
}) {
  const catalogRef = useRef(null);
  const [filter, setFilter] = useState("all");

  const categories = useMemo(() => Array.from(new Set(products.map((product) => product.category).filter(Boolean))).slice(0, 5), [products]);
  const filters = [
    { id: "all", label: "Все" },
    { id: "drop", label: "Drops" },
    { id: "rare", label: "Limited" },
    ...categories.map((category) => ({ id: `category:${category}`, label: category })),
  ];

  const visibleProducts = useMemo(() => products.filter((product) => {
    if (filter === "drop") return product.is_drop;
    if (filter === "rare") return product.is_rare;
    if (filter.startsWith("category:")) return product.category === filter.slice(9);
    return true;
  }), [filter, products]);

  function scrollToCatalog() {
    catalogRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  return (
    <main className="commerce-home">
      <section className="editorial-hero" aria-labelledby="home-title">
        <span className="eyebrow">FLASHIN · NEW SEASON</span>
        <h1 id="home-title">Соберите образ, который работает за вас</h1>
        <p>Новые модели, готовые сочетания и быстрый выбор размера — без лишних шагов.</p>
        <div className="hero-actions">
          <button className="primary compact" type="button" onClick={scrollToCatalog}>Смотреть коллекцию</button>
          <button className="ghost-button" type="button" onClick={onOpenLooks}>Собрать образ</button>
        </div>
      </section>

      {looks.length > 0 && (
        <section className="home-section" aria-labelledby="looks-heading">
          <div className="section-heading">
            <div>
              <span className="eyebrow">СТИЛИЗОВАНО FLASHIN</span>
              <h2 id="looks-heading">Готовые образы</h2>
            </div>
            <button className="text-action" type="button" onClick={onOpenLooks}>Все образы</button>
          </div>
          <div className="look-preview-list">
            {looks.slice(0, 2).map((look) => (
              <LookPreview key={look.id} look={look} products={products} onOpenProduct={onOpenProduct} onOpenLooks={onOpenLooks} />
            ))}
          </div>
        </section>
      )}

      <section className="home-section catalog-section" ref={catalogRef} aria-labelledby="catalog-heading">
        <div className="section-heading">
          <div>
            <span className="eyebrow">КОЛЛЕКЦИЯ</span>
            <h2 id="catalog-heading">Найдите свою вещь</h2>
          </div>
          <span className="result-count">{visibleProducts.length}</span>
        </div>

        <div className="search premium-search">
          <input
            aria-label="Поиск по каталогу"
            placeholder="Название, категория или артикул"
            value={searchQuery}
            onChange={(event) => onSearchQueryChange(event.target.value)}
            onKeyDown={(event) => event.key === "Enter" && onSearch()}
          />
          <button className="secondary search-button" type="button" onClick={onSearch}>Найти</button>
        </div>

        <div className="filter-rail" aria-label="Фильтры каталога">
          {filters.map((item) => (
            <button key={item.id} type="button" className={filter === item.id ? "active" : ""} onClick={() => setFilter(item.id)}>
              {item.label}
            </button>
          ))}
        </div>

        {!visibleProducts.length && (
          <div className="empty-state">
            <b>В этой подборке пока нет товаров</b>
            <p>Выберите другой фильтр или очистите поисковый запрос.</p>
            <button className="ghost-button" type="button" onClick={() => setFilter("all")}>Показать всё</button>
          </div>
        )}

        <div className="product-grid">
          {visibleProducts.map((product) => (
            <ProductCard
              key={product.id}
              product={product}
              isFavorite={wishlistIds.has(product.id)}
              onOpen={onOpenProduct}
              onToggleWishlist={onToggleWishlist}
            />
          ))}
        </div>
      </section>
    </main>
  );
}
