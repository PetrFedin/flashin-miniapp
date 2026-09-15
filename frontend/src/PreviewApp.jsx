import React, { useMemo, useState } from "react";
import "./preview.css";

const PRODUCTS = [
  { id: 1, title: "FLASHIN Essential Tee", category: "Футболка", price: 8900, sizes: ["S", "M", "L", "XL"], tone: "ink" },
  { id: 2, title: "FLASHIN Oversized Hoodie", category: "Худи", price: 18900, sizes: ["M", "L", "XL"], tone: "sand" },
  { id: 3, title: "FLASHIN Cargo Shorts", category: "Шорты", price: 14900, sizes: ["S", "M", "L"], tone: "stone" },
  { id: 4, title: "FLASHIN Shell Jacket", category: "Куртка", price: 32900, sizes: ["M", "L"], tone: "graphite" },
];

function money(value) {
  return new Intl.NumberFormat("ru-RU", {
    style: "currency",
    currency: "RUB",
    maximumFractionDigits: 0,
  }).format(value);
}

function ProductVisual({ product, hero = false }) {
  return (
    <div className={`preview-product-visual ${product.tone} ${hero ? "hero-visual" : ""}`} aria-label={product.title}>
      <span>FLASHIN</span>
      <b>{product.category}</b>
    </div>
  );
}

function ProductCard({ product, onOpen, onAdd }) {
  return (
    <article className="product-card preview-card">
      <button className="product-open" onClick={() => onOpen(product)}>
        <ProductVisual product={product} />
        <span className="title">{product.title}</span>
        <span className="meta">{product.category}</span>
        <span className="price">{money(product.price)}</span>
      </button>
      <div className="card-action">
        <button className="secondary" onClick={() => onAdd(product)}>В корзину</button>
      </div>
    </article>
  );
}

export default function PreviewApp() {
  const [view, setView] = useState("catalog");
  const [selected, setSelected] = useState(null);
  const [selectedSize, setSelectedSize] = useState("M");
  const [cart, setCart] = useState([]);
  const [query, setQuery] = useState("");
  const [notice, setNotice] = useState("");
  const [favorite, setFavorite] = useState(false);

  const cartCount = useMemo(() => cart.reduce((sum, item) => sum + item.qty, 0), [cart]);
  const cartTotal = useMemo(() => cart.reduce((sum, item) => sum + item.price * item.qty, 0), [cart]);
  const filtered = useMemo(() => {
    const needle = query.trim().toLowerCase();
    if (!needle) return PRODUCTS;
    return PRODUCTS.filter((product) => `${product.title} ${product.category}`.toLowerCase().includes(needle));
  }, [query]);

  function openProduct(product) {
    setSelected(product);
    setSelectedSize(product.sizes.includes("M") ? "M" : product.sizes[0]);
    setView("product");
    setNotice("");
  }

  function addToCart(product) {
    setCart((items) => {
      const existing = items.find((item) => item.id === product.id && item.size === selectedSize);
      if (existing) {
        return items.map((item) => item === existing ? { ...item, qty: item.qty + 1 } : item);
      }
      return [...items, { ...product, size: selectedSize, qty: 1 }];
    });
    setNotice(`${product.title} добавлен в корзину`);
  }

  function changeQty(index, delta) {
    setCart((items) => items
      .map((item, itemIndex) => itemIndex === index ? { ...item, qty: item.qty + delta } : item)
      .filter((item) => item.qty > 0));
  }

  function navigate(nextView) {
    setSelected(null);
    setView(nextView);
    setNotice("");
  }

  return (
    <div className="app">
      <header className="topbar">
        <div>
          <div className="brand">FLASHIN</div>
          <div className="hello">Browser preview • демо-данные</div>
        </div>
        <button className="cart-shortcut" onClick={() => navigate("cart")}>Корзина · {cartCount}</button>
      </header>

      <div className="preview-banner">
        <b>PREVIEW</b>
        <span>Визуальный контур текущего Mini App. Production-авторизация Telegram и реальные платежи здесь не включены.</span>
      </div>

      <nav className="tabs" aria-label="Разделы FLASHIN">
        <button className={view === "catalog" || view === "product" ? "active" : ""} onClick={() => navigate("catalog")}>Каталог</button>
        <button className={view === "looks" ? "active" : ""} onClick={() => navigate("looks")}>Образы</button>
        <button className={view === "orders" ? "active" : ""} onClick={() => navigate("orders")}>Заказы</button>
        <button className={view === "profile" ? "active" : ""} onClick={() => navigate("profile")}>Профиль</button>
      </nav>

      {notice && <div className="message success"><span>{notice}</span><button onClick={() => setNotice("")}>×</button></div>}

      <main>
        {view === "catalog" && (
          <>
            <div className="section-heading">
              <div>
                <h1>Каталог</h1>
                <p className="lead">Витрина, поиск, карточка товара, размеры, избранное и корзина.</p>
              </div>
            </div>
            <div className="search">
              <input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Найти товар" />
              <button className="secondary compact" onClick={() => setQuery("")}>Сбросить</button>
            </div>
            <div className="grid">
              {filtered.map((product) => <ProductCard key={product.id} product={product} onOpen={openProduct} onAdd={(item) => { setSelectedSize(item.sizes[0]); addToCart(item); }} />)}
            </div>
          </>
        )}

        {view === "product" && selected && (
          <>
            <button className="link" onClick={() => navigate("catalog")}>← Назад в каталог</button>
            <ProductVisual product={selected} hero />
            <div className="product-heading">
              <div>
                <h1>{selected.title}</h1>
                <span className="meta">{selected.category} · FLASHIN</span>
              </div>
              <span className="price">{money(selected.price)}</span>
            </div>
            <section>
              <h2>Размер</h2>
              <div className="sizes">
                {selected.sizes.map((size) => (
                  <button key={size} className={`size ${selectedSize === size ? "active" : ""}`} onClick={() => setSelectedSize(size)}>{size}<small>в наличии</small></button>
                ))}
              </div>
            </section>
            <div className="actions horizontal">
              <button className="primary" onClick={() => addToCart(selected)}>Добавить в корзину</button>
              <button className="secondary" onClick={() => { setFavorite((value) => !value); setNotice(!favorite ? "Добавлено в избранное" : "Удалено из избранного"); }}>{favorite ? "В избранном" : "♡ Избранное"}</button>
            </div>
            <section className="panel">
              <h2>Помощник размера</h2>
              <p className="muted">В рабочем приложении блок использует рост, вес, привычный размер и предпочтение по посадке.</p>
              <div className="result-card"><span>Пример рекомендации</span><b>{selectedSize}</b><p>Regular fit · доступность размера проверяется перед добавлением.</p></div>
            </section>
          </>
        )}

        {view === "looks" && (
          <>
            <h1>Образы</h1>
            <p className="lead">Готовые сочетания товаров с быстрым переходом в карточку.</p>
            <article className="look-card">
              <div className="look-heading"><h2>City Layering</h2><p>Худи + shell jacket</p></div>
              <div className="look-products">
                <ProductCard product={PRODUCTS[1]} onOpen={openProduct} onAdd={addToCart} />
                <ProductCard product={PRODUCTS[3]} onOpen={openProduct} onAdd={addToCart} />
              </div>
            </article>
            <article className="look-card">
              <div className="look-heading"><h2>Weekend Uniform</h2><p>Футболка + cargo shorts</p></div>
              <div className="look-products">
                <ProductCard product={PRODUCTS[0]} onOpen={openProduct} onAdd={addToCart} />
                <ProductCard product={PRODUCTS[2]} onOpen={openProduct} onAdd={addToCart} />
              </div>
            </article>
          </>
        )}

        {view === "cart" && (
          <>
            <div className="section-heading"><div><h1>Корзина</h1><p className="lead">Количество, промокод, лояльность и переход к оформлению.</p></div></div>
            {cart.length === 0 ? (
              <div className="empty-state"><h2>Корзина пуста</h2><p>Добавьте товары из каталога.</p><button className="primary" onClick={() => navigate("catalog")}>Перейти в каталог</button></div>
            ) : (
              <>
                <div className="cart-list">
                  {cart.map((item, index) => (
                    <div className="cart-item" key={`${item.id}-${item.size}-${index}`}>
                      <div><b>{item.title}</b><span className="meta">Размер {item.size} · {money(item.price)}</span></div>
                      <div className="quantity-control"><button onClick={() => changeQty(index, -1)}>−</button><b>{item.qty}</b><button onClick={() => changeQty(index, 1)}>+</button></div>
                    </div>
                  ))}
                </div>
                <div className="summary">
                  <div className="cart-line"><span>Товары</span><b>{cartCount}</b></div>
                  <div className="cart-line"><span>Доставка</span><b>Рассчитывается</b></div>
                  <div className="summary-total"><span>Итого</span><b>{money(cartTotal)}</b></div>
                </div>
                <div className="promo"><input placeholder="Промокод" /><button className="secondary compact">Применить</button></div>
                <button className="primary" onClick={() => setNotice("Preview: реальный checkout выполняется только через backend и Telegram-авторизацию")}>Перейти к оформлению</button>
              </>
            )}
          </>
        )}

        {view === "orders" && (
          <>
            <h1>Заказы</h1>
            <p className="lead">Статусы оплаты, доставки, отмены, возвраты и история заказа.</p>
            <article className="order-card">
              <div className="order-title"><div><h2>Заказ #1421</h2><span className="meta">15 сентября 2026</span></div><b>{money(27800)}</b></div>
              <div className="order-items"><div><span>Essential Tee × 1</span><b>{money(8900)}</b></div><div><span>Oversized Hoodie × 1</span><b>{money(18900)}</b></div></div>
              <div className="status-row"><span>Оплата</span><b className="status success">Оплачен</b></div>
              <div className="status-row"><span>Заказ</span><b className="status">Комплектуется</b></div>
              <div className="status-row"><span>Доставка</span><b className="status">Ожидает передачи</b></div>
              <div className="actions horizontal"><button className="secondary">История</button><button className="danger">Запросить отмену</button></div>
            </article>
          </>
        )}

        {view === "profile" && (
          <>
            <h1>Профиль</h1>
            <p className="lead">Лояльность, реферальная механика, поддержка и privacy-операции.</p>
            <section className="panel profile-card">
              <div><div><b>Пётр</b><p>Telegram customer profile</p></div><span className="status success">Активен</span></div>
              <div><div><b>2 450 баллов</b><p>Доступно к списанию</p></div><button className="link">История</button></div>
              <div><div><b>FLASHIN-PREVIEW</b><p>Реферальный код</p></div><button className="link">Копировать</button></div>
            </section>
            <section>
              <h2>Сервис</h2>
              <div className="actions">
                <button className="secondary">Создать обращение в поддержку</button>
                <button className="secondary">Мои обращения</button>
                <button className="secondary">Запросить выгрузку персональных данных</button>
                <button className="secondary">История privacy-запросов</button>
              </div>
            </section>
          </>
        )}
      </main>
    </div>
  );
}
