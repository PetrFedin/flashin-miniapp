import React, { useEffect, useMemo, useState } from "react";
import {
  addToCart,
  addWishlist,
  applyLoyalty,
  applyPromo,
  applyReferral,
  checkout,
  createPayment,
  getCart,
  getProfile,
  getRecommendations,
  listLooks,
  listOrders,
  listProducts,
  listWishlist,
  myLoyalty,
  myReferralCode,
  removeCartItem,
  removeWishlist,
  searchProducts,
  sizeHelper,
  subscribeRestock,
  telegramAuth,
  trackEvent,
} from "./api";
import ErrorBoundary from "./ErrorBoundary";
import CommerceHome from "./components/CommerceHome";
import LooksShowcase from "./components/LooksShowcase";
import SkeletonCard from "./components/SkeletonCard";
import { useTelegram } from "./hooks/useTelegram";

const ROOT_VIEWS = new Set(["catalog", "looks", "wishlist", "orders", "profile"]);

function money(value, currency = "RUB") {
  return `${Number(value || 0).toLocaleString("ru-RU")} ${currency}`;
}

function parseLaunchTarget(startParam, search) {
  const params = new URLSearchParams(search);
  const productId = params.get("product");
  if (productId) return { type: "product", id: productId };
  if (!startParam) return null;
  if (startParam.startsWith("product_")) return { type: "product", id: startParam.slice(8) };
  if (["cart", "wishlist", "orders", "looks", "profile"].includes(startParam)) {
    return { type: "view", view: startParam };
  }
  return null;
}

function firstAvailableVariant(product) {
  return product?.variants?.find((variant) => variant.available_qty > 0) || null;
}

function BottomNavigation({ view, wishlistCount, cartCount, onNavigate, onOrders, onProfile }) {
  const items = [
    { id: "catalog", label: "Коллекция", icon: "⌂", action: () => onNavigate("catalog") },
    { id: "looks", label: "Образы", icon: "◇", action: () => onNavigate("looks") },
    { id: "wishlist", label: "Избранное", icon: "♡", badge: wishlistCount, action: () => onNavigate("wishlist") },
    { id: "orders", label: "Заказы", icon: "▱", action: onOrders },
    { id: "profile", label: "Профиль", icon: "○", action: onProfile },
  ];

  return (
    <nav className="bottom-navigation" aria-label="Основная навигация">
      {items.map((item) => (
        <button key={item.id} type="button" className={view === item.id ? "active" : ""} onClick={item.action}>
          <span className="nav-icon" aria-hidden="true">{item.icon}</span>
          <span>{item.label}</span>
          {item.badge > 0 && <b>{item.badge > 9 ? "9+" : item.badge}</b>}
        </button>
      ))}
      {cartCount > 0 && <span className="cart-presence" aria-hidden="true" />}
    </nav>
  );
}

function ProductDetail({
  product,
  selectedVariantId,
  wishlistIds,
  recommendations,
  sizeForm,
  sizeResult,
  onBack,
  onSelectVariant,
  onSizeFormChange,
  onFindSize,
  onAdd,
  onToggleWishlist,
  onShare,
  onRestock,
  onOpenProduct,
}) {
  const selectedVariant = product.variants?.find((variant) => variant.id === selectedVariantId);
  const groupedColors = Array.from(new Set((product.variants || []).map((variant) => variant.color).filter(Boolean)));

  return (
    <main className="product-page">
      <button className="back-link" type="button" onClick={onBack}>← Коллекция</button>

      <div className="product-gallery" aria-label={`Фотографии ${product.title}`}>
        {(product.images?.length ? product.images : [{ id: "fallback", url: "/fallback-product.svg" }]).map((image) => (
          <img key={image.id || image.url} src={image.url} alt={product.title} />
        ))}
      </div>

      <section className="product-information">
        <div className="product-heading-row">
          <div>
            <span className="eyebrow">{product.brand || "FLASHIN"}</span>
            <h1>{product.title}</h1>
          </div>
          <button className={`favorite-button detail-favorite ${wishlistIds.has(product.id) ? "active" : ""}`} type="button" onClick={() => onToggleWishlist(product)}>
            {wishlistIds.has(product.id) ? "♥" : "♡"}
          </button>
        </div>

        <div className="detail-price">
          <strong>{money(product.price, product.currency)}</strong>
          {product.old_price > product.price && <del>{money(product.old_price, product.currency)}</del>}
        </div>

        {product.description && <p className="product-description">{product.description}</p>}
        {groupedColors.length > 0 && <p className="product-meta">Цвет: <b>{groupedColors.join(", ")}</b></p>}

        <div className="selection-heading">
          <div>
            <span className="eyebrow">РАЗМЕР</span>
            <b>{selectedVariant ? `${selectedVariant.size}${selectedVariant.color ? ` · ${selectedVariant.color}` : ""}` : "Выберите размер"}</b>
          </div>
          <span>{selectedVariant?.available_qty ? `Осталось ${selectedVariant.available_qty}` : ""}</span>
        </div>

        <div className="sizes">
          {(product.variants || []).map((variant) => (
            <button
              key={variant.id}
              type="button"
              disabled={variant.available_qty <= 0}
              className={selectedVariantId === variant.id ? "size active" : "size"}
              onClick={() => onSelectVariant(variant.id)}
            >
              {variant.size}
            </button>
          ))}
        </div>

        <details className="size-assistant">
          <summary>Не уверены в размере?</summary>
          <div className="size-assistant-body">
            <div className="field-grid">
              <label>Рост, см<input inputMode="numeric" value={sizeForm.height_cm} onChange={(event) => onSizeFormChange({ ...sizeForm, height_cm: event.target.value })} /></label>
              <label>Вес, кг<input inputMode="numeric" value={sizeForm.weight_kg} onChange={(event) => onSizeFormChange({ ...sizeForm, weight_kg: event.target.value })} /></label>
            </div>
            <label>Обычный размер<input value={sizeForm.usual_size} onChange={(event) => onSizeFormChange({ ...sizeForm, usual_size: event.target.value })} placeholder="Например, M" /></label>
            <button className="secondary" type="button" onClick={onFindSize}>Подобрать размер</button>
            {sizeResult && <p className="size-result">Рекомендуем: <b>{sizeResult.suggested_size}</b></p>}
          </div>
        </details>

        <div className="product-actions">
          <button className="primary" type="button" onClick={onAdd} disabled={!selectedVariantId}>Добавить в корзину</button>
          <button className="secondary" type="button" onClick={() => onShare(product)}>Поделиться в Telegram</button>
          {!selectedVariantId && product.variants?.[0] && <button className="text-action" type="button" onClick={() => onRestock(product.variants[0].id)}>Сообщить о поступлении</button>}
        </div>
      </section>

      {recommendations.length > 0 && (
        <section className="recommendation-section">
          <div className="section-heading">
            <div><span className="eyebrow">COMPLETE THE LOOK</span><h2>Дополните образ</h2></div>
          </div>
          <div className="recommendation-rail">
            {recommendations.slice(0, 6).map((item) => (
              <button key={item.id} type="button" onClick={() => onOpenProduct(item)}>
                <img src={item.images?.[0]?.url || "/fallback-product.svg"} alt={item.title} loading="lazy" />
                <span>{item.title}</span>
                <b>{money(item.price, item.currency)}</b>
              </button>
            ))}
          </div>
        </section>
      )}
    </main>
  );
}

export default function AppPremium() {
  const { tg, initData, user, launchContext } = useTelegram();
  const [view, setView] = useState("catalog");
  const [products, setProducts] = useState([]);
  const [looks, setLooks] = useState([]);
  const [wishlist, setWishlist] = useState([]);
  const [cart, setCart] = useState(null);
  const [selected, setSelected] = useState(null);
  const [selectedVariantId, setSelectedVariantId] = useState(null);
  const [recommendations, setRecommendations] = useState([]);
  const [orders, setOrders] = useState([]);
  const [profile, setProfile] = useState(null);
  const [loyaltyRows, setLoyaltyRows] = useState([]);
  const [referral, setReferral] = useState(null);
  const [searchQuery, setSearchQuery] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [addingLookId, setAddingLookId] = useState(null);
  const [promo, setPromo] = useState("");
  const [loyaltyPoints, setLoyaltyPoints] = useState("");
  const [referralInput, setReferralInput] = useState("");
  const [sizeForm, setSizeForm] = useState({ height_cm: "", weight_kg: "", usual_size: "", fit_preference: "regular" });
  const [sizeResult, setSizeResult] = useState(null);
  const [checkoutForm, setCheckoutForm] = useState({ name: "", phone: "", delivery_type: "pickup", address: "", comment: "" });

  const wishlistIds = useMemo(() => new Set(wishlist.map((item) => item.id)), [wishlist]);
  const cartCount = useMemo(() => (cart?.items || []).reduce((sum, item) => sum + item.quantity, 0), [cart]);
  const cartTotal = cart?.final_amount || cart?.total_amount || 0;

  function navigate(nextView) {
    setView(nextView);
    setError("");
    if (nextView !== "product") {
      const url = new URL(window.location.href);
      url.searchParams.delete("product");
      window.history.replaceState({}, "", `${url.pathname}${url.search}${url.hash}`);
    }
    window.scrollTo({ top: 0, behavior: "smooth" });
  }

  async function openProduct(product) {
    setSelected(product);
    setSelectedVariantId(firstAvailableVariant(product)?.id || null);
    setSizeResult(null);
    setView("product");
    const url = new URL(window.location.href);
    url.searchParams.set("product", product.id);
    window.history.replaceState({}, "", `${url.pathname}${url.search}${url.hash}`);
    trackEvent("product_view", { product_id: product.id, source: launchContext.startParam ? "telegram_deep_link" : "mini_app" });
    try {
      const rows = await getRecommendations(product.id);
      setRecommendations(Array.isArray(rows) ? rows : rows?.products || []);
    } catch {
      setRecommendations([]);
    }
    window.scrollTo({ top: 0, behavior: "smooth" });
  }

  useEffect(() => {
    async function boot() {
      try {
        if (!initData) {
          setError("Откройте приложение внутри Telegram, чтобы войти и совершать покупки.");
          return;
        }
        await telegramAuth(initData);
        const [catalog, currentCart, activeLooks, favorites] = await Promise.all([
          listProducts(), getCart(), listLooks(), listWishlist(),
        ]);
        setProducts(catalog);
        setCart(currentCart);
        setLooks(activeLooks);
        setWishlist(favorites);

        const target = parseLaunchTarget(launchContext.startParam, window.location.search);
        if (target?.type === "product") {
          const product = catalog.find((item) => String(item.id) === String(target.id));
          if (product) await openProduct(product);
        } else if (target?.type === "view") {
          setView(target.view);
        }
      } catch (caught) {
        setError(caught.message || "Не удалось загрузить приложение");
      } finally {
        setLoading(false);
      }
    }
    boot();
  }, [initData, launchContext.startParam]);

  useEffect(() => {
    if (!tg?.BackButton) return undefined;
    const goBack = () => {
      tg.HapticFeedback?.impactOccurred?.("light");
      if (view === "checkout") return navigate("cart");
      if (view === "cart" || view === "product") return navigate("catalog");
      return navigate("catalog");
    };
    if (view === "catalog") {
      tg.BackButton.hide();
      return undefined;
    }
    tg.BackButton.show();
    tg.BackButton.onClick(goBack);
    return () => tg.BackButton.offClick(goBack);
  }, [tg, view]);

  useEffect(() => {
    if (!tg?.MainButton) return undefined;
    const add = () => handleAddSelected();
    const openCart = () => navigate("cart");
    const submit = () => handleCheckout();
    tg.MainButton.offClick?.(add);
    tg.MainButton.offClick?.(openCart);
    tg.MainButton.offClick?.(submit);

    if (view === "checkout") {
      tg.MainButton.setText("Перейти к оплате");
      tg.MainButton.show();
      tg.MainButton.onClick(submit);
      return () => tg.MainButton.offClick(submit);
    }
    if (view === "product" && selectedVariantId) {
      tg.MainButton.setText("Добавить в корзину");
      tg.MainButton.show();
      tg.MainButton.onClick(add);
      return () => tg.MainButton.offClick(add);
    }
    if (view !== "cart" && cartCount > 0) {
      tg.MainButton.setText(`Корзина · ${cartCount} · ${money(cartTotal)}`);
      tg.MainButton.show();
      tg.MainButton.onClick(openCart);
      return () => tg.MainButton.offClick(openCart);
    }
    tg.MainButton.hide();
    return undefined;
  }, [tg, view, selectedVariantId, cartCount, cartTotal, checkoutForm]);

  async function handleSearch() {
    try {
      setError("");
      setProducts(searchQuery.trim() ? await searchProducts(searchQuery.trim()) : await listProducts());
      trackEvent("catalog_search", { query: searchQuery.trim() });
    } catch (caught) {
      setError(caught.message || "Не удалось выполнить поиск");
    }
  }

  async function toggleWishlist(product) {
    try {
      setError("");
      if (wishlistIds.has(product.id)) {
        await removeWishlist(product.id);
        setWishlist((current) => current.filter((item) => item.id !== product.id));
        tg?.HapticFeedback?.impactOccurred?.("light");
        trackEvent("wishlist_remove", { product_id: product.id });
      } else {
        await addWishlist(product.id);
        setWishlist((current) => [product, ...current.filter((item) => item.id !== product.id)]);
        tg?.HapticFeedback?.notificationOccurred?.("success");
        trackEvent("wishlist_add", { product_id: product.id });
      }
    } catch (caught) {
      setError(caught.message || "Не удалось обновить избранное");
    }
  }

  async function handleAddSelected() {
    if (!selected || !selectedVariantId) return;
    try {
      setError("");
      setCart(await addToCart(selected.id, selectedVariantId, 1));
      tg?.HapticFeedback?.notificationOccurred?.("success");
      trackEvent("add_to_cart", { product_id: selected.id, variant_id: selectedVariantId, source: "product" });
    } catch (caught) {
      setError(caught.message || "Не удалось добавить товар");
    }
  }

  async function addWholeLook(look, availableProducts) {
    setAddingLookId(look.id);
    setError("");
    try {
      let nextCart = cart;
      let added = 0;
      for (const product of availableProducts) {
        const variant = firstAvailableVariant(product);
        if (!variant) continue;
        nextCart = await addToCart(product.id, variant.id, 1);
        added += 1;
      }
      setCart(nextCart);
      tg?.HapticFeedback?.notificationOccurred?.("success");
      trackEvent("look_add_to_cart", { look_id: look.id, product_count: added });
      navigate("cart");
    } catch (caught) {
      setError(caught.message || "Не удалось добавить весь образ");
      tg?.HapticFeedback?.notificationOccurred?.("error");
    } finally {
      setAddingLookId(null);
    }
  }

  function shareProduct(product) {
    const productUrl = new URL(window.location.origin + window.location.pathname);
    productUrl.searchParams.set("product", product.id);
    const text = `${product.title} — ${money(product.price, product.currency)}`;
    tg?.openTelegramLink?.(`https://t.me/share/url?url=${encodeURIComponent(productUrl.toString())}&text=${encodeURIComponent(text)}`);
    trackEvent("product_share", { product_id: product.id, channel: "telegram" });
  }

  async function openOrders() {
    try {
      setOrders(await listOrders());
      navigate("orders");
    } catch (caught) {
      setError(caught.message || "Не удалось загрузить заказы");
    }
  }

  async function openProfile() {
    try {
      const [profileData, loyalty, referralData] = await Promise.all([getProfile(), myLoyalty(), myReferralCode()]);
      setProfile(profileData);
      setLoyaltyRows(loyalty);
      setReferral(referralData);
      navigate("profile");
    } catch (caught) {
      setError(caught.message || "Не удалось загрузить профиль");
    }
  }

  async function handleCheckout() {
    if (!checkoutForm.name.trim() || !checkoutForm.phone.trim()) {
      setError("Заполните имя и телефон.");
      return;
    }
    try {
      setError("");
      tg?.MainButton?.showProgress?.(true);
      const order = await checkout(checkoutForm);
      const payment = await createPayment(order.id);
      if (!payment.confirmation_url) throw new Error("Платёж создан без ссылки оплаты.");
      tg?.openLink?.(payment.confirmation_url, { try_instant_view: false });
    } catch (caught) {
      setError(caught.message || "Не удалось оформить заказ");
      tg?.HapticFeedback?.notificationOccurred?.("error");
    } finally {
      tg?.MainButton?.hideProgress?.();
    }
  }

  return (
    <ErrorBoundary>
      <div className="app premium-app">
        <header className="topbar premium-topbar">
          <button className="brand-button" type="button" onClick={() => navigate("catalog")}>
            <span className="brand">FLASHIN</span>
            <small>{user?.first_name ? `Для вас, ${user.first_name}` : "OFFICIAL STORE"}</small>
          </button>
          <button className="cart-button" type="button" onClick={() => navigate("cart")} aria-label={`Корзина, ${cartCount} товаров`}>
            <span>Корзина</span><b>{cartCount}</b>
          </button>
        </header>

        {error && <div className="error" role="alert">{error}<button type="button" aria-label="Закрыть сообщение" onClick={() => setError("")}>×</button></div>}
        {loading && <main className="loading-grid"><SkeletonCard /><SkeletonCard /><SkeletonCard /><SkeletonCard /></main>}

        {!loading && view === "catalog" && (
          <CommerceHome
            products={products}
            looks={looks}
            wishlistIds={wishlistIds}
            searchQuery={searchQuery}
            onSearchQueryChange={setSearchQuery}
            onSearch={handleSearch}
            onOpenProduct={openProduct}
            onToggleWishlist={toggleWishlist}
            onOpenLooks={() => navigate("looks")}
          />
        )}

        {!loading && view === "looks" && (
          <LooksShowcase looks={looks} products={products} onOpenProduct={openProduct} onAddLook={addWholeLook} addingLookId={addingLookId} />
        )}

        {!loading && view === "product" && selected && (
          <ProductDetail
            product={selected}
            selectedVariantId={selectedVariantId}
            wishlistIds={wishlistIds}
            recommendations={recommendations}
            sizeForm={sizeForm}
            sizeResult={sizeResult}
            onBack={() => navigate("catalog")}
            onSelectVariant={setSelectedVariantId}
            onSizeFormChange={setSizeForm}
            onFindSize={async () => setSizeResult(await sizeHelper({
              height_cm: Number(sizeForm.height_cm) || null,
              weight_kg: Number(sizeForm.weight_kg) || null,
              usual_size: sizeForm.usual_size || null,
              fit_preference: sizeForm.fit_preference,
            }))}
            onAdd={handleAddSelected}
            onToggleWishlist={toggleWishlist}
            onShare={shareProduct}
            onRestock={subscribeRestock}
            onOpenProduct={openProduct}
          />
        )}

        {!loading && view === "wishlist" && (
          <main className="standard-page">
            <section className="page-intro"><span className="eyebrow">СОХРАНЕНО</span><h1>Избранное</h1><p>Ваш персональный shortlist FLASHIN.</p></section>
            {!wishlist.length && <div className="empty-state"><b>Здесь пока пусто</b><p>Нажмите на сердце в каталоге, чтобы сохранить модель.</p><button className="primary" type="button" onClick={() => navigate("catalog")}>Перейти в коллекцию</button></div>}
            <div className="wishlist-grid">
              {wishlist.map((product) => (
                <article key={product.id}>
                  <button type="button" onClick={() => openProduct(product)}><img src={product.images?.[0]?.url || "/fallback-product.svg"} alt={product.title} /><span><b>{product.title}</b><small>{money(product.price, product.currency)}</small></span></button>
                  <button className="remove-button" type="button" aria-label={`Удалить ${product.title}`} onClick={() => toggleWishlist(product)}>×</button>
                </article>
              ))}
            </div>
          </main>
        )}

        {!loading && view === "cart" && (
          <main className="standard-page cart-page">
            <button className="back-link" type="button" onClick={() => navigate("catalog")}>← Продолжить покупки</button>
            <section className="page-intro"><span className="eyebrow">ВАШ ВЫБОР</span><h1>Корзина</h1><p>{cartCount ? `${cartCount} ${cartCount === 1 ? "позиция" : "позиций"}` : "Пока пусто"}</p></section>
            {!cart?.items?.length && <div className="empty-state"><b>Добавьте первую вещь</b><p>Начните с коллекции или выберите готовый образ.</p><button className="primary" type="button" onClick={() => navigate("catalog")}>Смотреть коллекцию</button></div>}
            <div className="cart-list">
              {(cart?.items || []).map((item) => (
                <article key={item.id} className="cart-line">
                  <div><b>{item.title}</b><small>Размер {item.size} · {item.quantity} шт.</small></div>
                  <div><strong>{money(item.price * item.quantity)}</strong><button type="button" onClick={async () => setCart(await removeCartItem(item.id))}>Удалить</button></div>
                </article>
              ))}
            </div>
            {cart?.items?.length > 0 && (
              <section className="cart-summary">
                <details><summary>Промокод и бонусы</summary><div className="cart-benefits">
                  <div className="inline-field"><input placeholder="Промокод" value={promo} onChange={(event) => setPromo(event.target.value)} /><button type="button" onClick={async () => setCart(await applyPromo(promo))}>Применить</button></div>
                  <div className="inline-field"><input placeholder="Баллы" inputMode="numeric" value={loyaltyPoints} onChange={(event) => setLoyaltyPoints(event.target.value)} /><button type="button" onClick={async () => setCart(await applyLoyalty(Number(loyaltyPoints)))}>Списать</button></div>
                  <div className="inline-field"><input placeholder="Referral-код" value={referralInput} onChange={(event) => setReferralInput(event.target.value)} /><button type="button" onClick={async () => setCart(await applyReferral(referralInput))}>Добавить</button></div>
                </div></details>
                <div className="summary-row"><span>Товары</span><b>{money(cart.total_amount)}</b></div>
                {cart.discount_amount > 0 && <div className="summary-row discount"><span>Ваша выгода</span><b>−{money(cart.discount_amount)}</b></div>}
                <div className="summary-row total"><span>Итого</span><b>{money(cartTotal)}</b></div>
                <button className="primary" type="button" onClick={() => navigate("checkout")}>Оформить заказ</button>
              </section>
            )}
          </main>
        )}

        {!loading && view === "checkout" && (
          <main className="standard-page checkout-page">
            <button className="back-link" type="button" onClick={() => navigate("cart")}>← Корзина</button>
            <section className="page-intro"><span className="eyebrow">ПОСЛЕДНИЙ ШАГ</span><h1>Оформление</h1><p>Проверим контакты и способ получения.</p></section>
            <div className="checkout-form">
              <label>Имя<input autoComplete="name" value={checkoutForm.name} onChange={(event) => setCheckoutForm({ ...checkoutForm, name: event.target.value })} /></label>
              <label>Телефон<input autoComplete="tel" inputMode="tel" value={checkoutForm.phone} onChange={(event) => setCheckoutForm({ ...checkoutForm, phone: event.target.value })} /></label>
              <label>Получение<select value={checkoutForm.delivery_type} onChange={(event) => setCheckoutForm({ ...checkoutForm, delivery_type: event.target.value })}><option value="pickup">Самовывоз</option><option value="courier">Курьер</option></select></label>
              {checkoutForm.delivery_type === "courier" && <label>Адрес<textarea value={checkoutForm.address} onChange={(event) => setCheckoutForm({ ...checkoutForm, address: event.target.value })} /></label>}
              <label>Комментарий<textarea value={checkoutForm.comment} onChange={(event) => setCheckoutForm({ ...checkoutForm, comment: event.target.value })} placeholder="Пожелания к заказу" /></label>
              <div className="checkout-total"><span>К оплате</span><b>{money(cartTotal)}</b></div>
              <button className="primary" type="button" onClick={handleCheckout}>Перейти к оплате</button>
            </div>
          </main>
        )}

        {!loading && view === "orders" && (
          <main className="standard-page"><section className="page-intro"><span className="eyebrow">FLASHIN SERVICE</span><h1>Мои заказы</h1><p>Оплата, доставка и история покупок.</p></section>{!orders.length && <div className="empty-state"><b>Заказов пока нет</b><p>После оформления покупки она появится здесь.</p></div>}<div className="order-list">{orders.map((order) => <article className="order-card" key={order.id}><div><span>Заказ #{order.id}</span><b>{money(order.total_amount, order.currency)}</b></div><p>{order.status} · {order.payment_status}</p><small>{order.delivery_status || order.delivery_type}</small></article>)}</div></main>
        )}

        {!loading && view === "profile" && (
          <main className="standard-page"><section className="page-intro"><span className="eyebrow">CLIENT SPACE</span><h1>{profile?.customer?.first_name || user?.first_name || "Профиль"}</h1><p>Персональные привилегии и история взаимодействия.</p></section><div className="profile-metrics"><article><small>Баллы</small><b>{profile?.loyalty_points || 0}</b></article><article><small>Статус</small><b>{profile?.crm?.segment || "New"}</b></article></div><section className="profile-section"><span className="eyebrow">ВАШ КОД</span><code>{referral?.referral_code || profile?.referral_code || "—"}</code></section><section className="profile-section"><span className="eyebrow">ИСТОРИЯ БАЛЛОВ</span>{!loyaltyRows.length && <p>Операций пока нет.</p>}{loyaltyRows.slice(0, 10).map((row) => <div className="summary-row" key={row.id}><span>{row.reason}</span><b>{row.points_delta > 0 ? "+" : ""}{row.points_delta}</b></div>)}</section></main>
        )}

        {!loading && ROOT_VIEWS.has(view) && (
          <BottomNavigation view={view} wishlistCount={wishlist.length} cartCount={cartCount} onNavigate={navigate} onOrders={openOrders} onProfile={openProfile} />
        )}
      </div>
    </ErrorBoundary>
  );
}
