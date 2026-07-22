import React, { useEffect, useMemo, useRef, useState } from "react";

const RECENT_STORAGE_KEY = "flashin_recent_products";

const OCCASIONS = [
  { id: "everyday", label: "На каждый день", hint: "Универсальные вещи и спокойные сочетания", keywords: ["shirt", "top", "t-shirt", "джемпер", "рубаш", "брюк", "джин", "топ"] },
  { id: "evening", label: "На вечер", hint: "Выразительные модели и акцентные детали", keywords: ["dress", "плать", "юбк", "жакет", "jacket", "silk", "шелк", "вечер"] },
  { id: "travel", label: "Для поездки", hint: "Комфортный комплект, который легко сочетать", keywords: ["hood", "knit", "трикот", "кардиган", "брюк", "shirt", "рубаш", "coat", "куртк"] },
];

const BUDGETS = [
  { id: "all", label: "Любой бюджет", max: Infinity },
  { id: "15000", label: "До 15 000", max: 15000 },
  { id: "30000", label: "До 30 000", max: 30000 },
  { id: "50000", label: "До 50 000", max: 50000 },
];

function formatMoney(value, currency = "RUB") {
  return `${Number(value || 0).toLocaleString("ru-RU")} ${currency}`;
}

function availableQuantity(product) {
  return (product.variants || []).reduce((total, variant) => total + Math.max(0, variant.available_qty || 0), 0);
}

function readRecentProducts() {
  try {
    const value = JSON.parse(localStorage.getItem(RECENT_STORAGE_KEY) || "[]");
    return Array.isArray(value) ? value.slice(0, 8) : [];
  } catch {
    return [];
  }
}

function rememberProduct(productId, currentIds) {
  const next = [productId, ...currentIds.filter((id) => String(id) !== String(productId))].slice(0, 8);
  try {
    localStorage.setItem(RECENT_STORAGE_KEY, JSON.stringify(next));
  } catch {
    // Browsing still works when Telegram or the browser blocks storage.
  }
  return next;
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
        <span className="card-conversion-link">{available > 0 ? "Выбрать размер →" : "Посмотреть модель →"}</span>
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

function CompactProductRail({ products, wishlistIds, onOpenProduct, onToggleWishlist }) {
  return (
    <div className="compact-product-rail">
      {products.map((product) => (
        <article key={product.id} className="compact-product-card">
          <button type="button" className="compact-image" onClick={() => onOpenProduct(product)}>
            <img src={product.images?.[0]?.url || "/fallback-product.svg"} alt={product.title} loading="lazy" />
          </button>
          <button
            type="button"
            className={`compact-favorite ${wishlistIds.has(product.id) ? "active" : ""}`}
            aria-label={wishlistIds.has(product.id) ? "Убрать из избранного" : "Добавить в избранное"}
            onClick={() => onToggleWishlist(product)}
          >
            {wishlistIds.has(product.id) ? "♥" : "♡"}
          </button>
          <button type="button" className="compact-copy" onClick={() => onOpenProduct(product)}>
            <b>{product.title}</b>
            <span>{formatMoney(product.price, product.currency)}</span>
          </button>
        </article>
      ))}
    </div>
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
  const [occasion, setOccasion] = useState("everyday");
  const [budget, setBudget] = useState("all");
  const [recentIds, setRecentIds] = useState(() => readRecentProducts());

  useEffect(() => {
    setRecentIds((current) => current.filter((id) => products.some((product) => String(product.id) === String(id))));
  }, [products]);

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

  const recentProducts = useMemo(() => recentIds
    .map((id) => products.find((product) => String(product.id) === String(id)))
    .filter(Boolean)
    .slice(0, 6), [products, recentIds]);

  const styleFinderProducts = useMemo(() => {
    const selectedOccasion = OCCASIONS.find((item) => item.id === occasion) || OCCASIONS[0];
    const selectedBudget = BUDGETS.find((item) => item.id === budget) || BUDGETS[0];

    return products
      .filter((product) => availableQuantity(product) > 0 && Number(product.price || 0) <= selectedBudget.max)
      .map((product) => {
        const searchable = `${product.title || ""} ${product.category || ""} ${product.description || ""}`.toLowerCase();
        const keywordScore = selectedOccasion.keywords.reduce((score, keyword) => score + (searchable.includes(keyword) ? 4 : 0), 0);
        const merchandisingScore = (product.is_drop ? 3 : 0) + (product.is_rare ? 2 : 0) + Math.min(3, availableQuantity(product));
        return { product, score: keywordScore + merchandisingScore };
      })
      .sort((left, right) => right.score - left.score || Number(right.product.price || 0) - Number(left.product.price || 0))
      .slice(0, 4)
      .map((item) => item.product);
  }, [budget, occasion, products]);

  function scrollToCatalog() {
    catalogRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  function openTrackedProduct(product) {
    setRecentIds((current) => rememberProduct(product.id, current));
    onOpenProduct(product);
  }

  const selectedOccasion = OCCASIONS.find((item) => item.id === occasion) || OCCASIONS[0];

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

      <section className="service-promises" aria-label="Преимущества магазина">
        <div><b>Размер без риска</b><span>Поможем подобрать посадку</span></div>
        <div><b>Лёгкий возврат</b><span>Поддержка внутри Telegram</span></div>
        <div><b>Образы целиком</b><span>Все позиции в одной корзине</span></div>
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
              <LookPreview key={look.id} look={look} products={products} onOpenProduct={openTrackedProduct} onOpenLooks={onOpenLooks} />
            ))}
          </div>
        </section>
      )}

      <section className="home-section style-finder" aria-labelledby="style-finder-heading">
        <div className="style-finder-intro">
          <span className="eyebrow">STYLE FINDER</span>
          <h2 id="style-finder-heading">Что надеть сегодня?</h2>
          <p>Выберите сценарий и бюджет — покажем подходящие вещи из наличия.</p>
        </div>

        <div className="finder-control-group" aria-label="Сценарий образа">
          {OCCASIONS.map((item) => (
            <button key={item.id} type="button" className={occasion === item.id ? "active" : ""} onClick={() => setOccasion(item.id)}>
              {item.label}
            </button>
          ))}
        </div>
        <p className="finder-hint">{selectedOccasion.hint}</p>

        <div className="finder-control-group budget-group" aria-label="Бюджет на одну вещь">
          {BUDGETS.map((item) => (
            <button key={item.id} type="button" className={budget === item.id ? "active" : ""} onClick={() => setBudget(item.id)}>
              {item.label}
            </button>
          ))}
        </div>

        {styleFinderProducts.length > 0 ? (
          <CompactProductRail products={styleFinderProducts} wishlistIds={wishlistIds} onOpenProduct={openTrackedProduct} onToggleWishlist={onToggleWishlist} />
        ) : (
          <div className="finder-empty">В выбранном бюджете пока нет доступных моделей.</div>
        )}
      </section>

      {recentProducts.length > 0 && (
        <section className="home-section recent-section" aria-labelledby="recent-heading">
          <div className="section-heading">
            <div>
              <span className="eyebrow">ВАША ИСТОРИЯ</span>
              <h2 id="recent-heading">Недавно смотрели</h2>
            </div>
            <button className="text-action muted" type="button" onClick={() => {
              try { localStorage.removeItem(RECENT_STORAGE_KEY); } catch { /* noop */ }
              setRecentIds([]);
            }}>Очистить</button>
          </div>
          <CompactProductRail products={recentProducts} wishlistIds={wishlistIds} onOpenProduct={openTrackedProduct} onToggleWishlist={onToggleWishlist} />
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
              onOpen={openTrackedProduct}
              onToggleWishlist={onToggleWishlist}
            />
          ))}
        </div>
      </section>
    </main>
  );
}
