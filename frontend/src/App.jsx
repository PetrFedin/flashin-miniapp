import React, { useEffect, useMemo, useState } from "react";
import {
  addToCart,
  addWishlist,
  applyLoyalty,
  applyPromo,
  applyReferral,
  cancelOrder,
  checkout,
  createPayment,
  createPrivacyRequest,
  createReturn,
  createSupportTicket,
  downloadPrivacyData,
  getCart,
  getOrder,
  getProduct,
  getProfile,
  getTimeline,
  listLooks,
  listOrders,
  listPrivacyRequests,
  listProducts,
  listSupportTickets,
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
  updateCartItem,
} from "./api";
import ErrorBoundary from "./ErrorBoundary";
import SkeletonCard from "./components/SkeletonCard";
import { useTelegram } from "./hooks/useTelegram";
import { parseLoyaltyPoints, validateCheckoutForm, validateSizeForm } from "./inputRules.js";
import {
  DELIVERY_LABELS,
  ORDER_LABELS,
  PAYMENT_LABELS,
  canCancelOrder,
  canPayOrder,
  canReturnOrder,
  paymentReturnMessage,
} from "./orderRules.js";
import { loadProfileSections, loadStorefrontBootstrap } from "./storefrontLoaders.js";
import { useActionLocks } from "./useActionLocks.js";

function money(value, currency = "RUB") {
  return new Intl.NumberFormat("ru-RU", {
    style: "currency",
    currency,
    maximumFractionDigits: 2,
  }).format(Number(value || 0));
}

function productImage(product) {
  return product?.images?.[0]?.url || product?.image_url || "/fallback-product.svg";
}

function ProductCard({ product, onOpen, action, disabled = false }) {
  return (
    <article className="product-card">
      <button className="product-open" onClick={() => onOpen(product)} disabled={disabled}>
        <img src={productImage(product)} alt={product.title} loading="lazy" />
        <span className="title">{product.title}</span>
        <span className="meta">{product.brand || product.category || "FLASHIN"}</span>
        <span className="price">{money(product.price, product.currency || "RUB")}</span>
      </button>
      {action}
    </article>
  );
}

function StatusRow({ label, value, tone = "neutral" }) {
  return (
    <div className="status-row">
      <span>{label}</span>
      <b className={`status ${tone}`}>{value}</b>
    </div>
  );
}

function EmptyState({ title, text, action, onAction }) {
  return (
    <div className="empty-state">
      <h2>{title}</h2>
      <p>{text}</p>
      {action && <button className="primary" onClick={onAction}>{action}</button>}
    </div>
  );
}

function sleep(milliseconds) {
  return new Promise((resolve) => window.setTimeout(resolve, milliseconds));
}

function warningText(prefix, warnings) {
  if (!warnings?.length) return "";
  return `${prefix}: ${warnings.join("; ")}`;
}

function cartItemActionKey(itemId) {
  return `cart-item-${itemId}`;
}

export default function App() {
  const { tg, initData, user, initialized } = useTelegram();
  const { runAction, isBusy } = useActionLocks();
  const [view, setView] = useState("catalog");
  const [products, setProducts] = useState([]);
  const [looks, setLooks] = useState([]);
  const [wishlist, setWishlist] = useState([]);
  const [selected, setSelected] = useState(null);
  const [selectedVariantId, setSelectedVariantId] = useState(null);
  const [cart, setCart] = useState(null);
  const [orders, setOrders] = useState([]);
  const [profile, setProfile] = useState(null);
  const [loyaltyRows, setLoyaltyRows] = useState([]);
  const [referral, setReferral] = useState(null);
  const [timeline, setTimeline] = useState([]);
  const [supportTickets, setSupportTickets] = useState([]);
  const [privacyRequests, setPrivacyRequests] = useState([]);
  const [searchQuery, setSearchQuery] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [promo, setPromo] = useState("");
  const [referralInput, setReferralInput] = useState("");
  const [loyaltyPoints, setLoyaltyPoints] = useState("");
  const [returnReasons, setReturnReasons] = useState({});
  const [supportForm, setSupportForm] = useState({ subject: "", message: "", order_id: "" });
  const [sizeForm, setSizeForm] = useState({ height_cm: "", weight_kg: "", usual_size: "", fit_preference: "regular" });
  const [sizeResult, setSizeResult] = useState(null);
  const [checkoutForm, setCheckoutForm] = useState({ name: "", phone: "", delivery_type: "pickup", address: "", comment: "" });

  const selectedVariant = useMemo(
    () => selected?.variants?.find((variant) => variant.id === selectedVariantId) || null,
    [selected, selectedVariantId],
  );
  const isFavorite = useMemo(
    () => Boolean(selected && wishlist.some((product) => product.id === selected.id)),
    [selected, wishlist],
  );
  const cartCount = useMemo(
    () => cart?.items?.reduce((sum, item) => sum + item.quantity, 0) || 0,
    [cart],
  );
  const checkoutBusy = isBusy("checkout");
  const addToCartBusy = isBusy("add-to-cart");

  function clearMessages() {
    setError("");
    setNotice("");
  }

  async function act(key, operation, successMessage = "") {
    return runAction(key, async () => {
      clearMessages();
      try {
        const result = await operation();
        if (successMessage) setNotice(successMessage);
        tg?.HapticFeedback?.notificationOccurred?.("success");
        return result;
      } catch (operationError) {
        setError(operationError.message || "Операция не выполнена");
        tg?.HapticFeedback?.notificationOccurred?.("error");
        return null;
      }
    });
  }

  async function refreshOrders() {
    const nextOrders = await listOrders();
    setOrders(nextOrders);
    return nextOrders;
  }

  async function resolvePaymentReturn(orderId) {
    let current = null;
    let lastError = null;
    for (let attempt = 0; attempt < 5; attempt += 1) {
      try {
        current = await getOrder(orderId);
        lastError = null;
      } catch (pollError) {
        lastError = pollError;
      }
      if (current && !["pending", "payment_created"].includes(current.payment_status)) break;
      if (attempt < 4) await sleep(1500);
    }
    if (!current && lastError) throw lastError;

    try {
      await refreshOrders();
    } catch {
      if (current) setOrders((items) => items.some((item) => item.id === current.id)
        ? items.map((item) => item.id === current.id ? current : item)
        : [current, ...items]);
    }
    setView("orders");
    const message = paymentReturnMessage(orderId, current?.payment_status);
    if (message.type === "error") setError(message.text);
    else setNotice(message.text);
    window.history.replaceState({}, "", "/");
  }

  useEffect(() => {
    if (!initialized) return;
    async function boot() {
      if (!tg || !initData) {
        setError("Откройте приложение внутри Telegram: браузерная авторизация отключена.");
        setLoading(false);
        return;
      }
      setLoading(true);
      clearMessages();
      try {
        await telegramAuth(initData);
        const bootstrap = await loadStorefrontBootstrap({
          listProducts,
          getCart,
          listLooks,
          listWishlist,
        });
        setProducts(bootstrap.products);
        setCart(bootstrap.cart);
        setLooks(bootstrap.looks);
        setWishlist(bootstrap.wishlist);
        if (bootstrap.warnings.length) {
          setNotice(warningText("Основные данные загружены, но часть разделов временно недоступна", bootstrap.warnings));
        }

        const params = new URLSearchParams(window.location.search);
        const productId = Number(params.get("product"));
        const orderId = Number(params.get("order_id"));
        if (productId > 0) {
          const fullProduct = await getProduct(productId);
          setSelected(fullProduct);
          setSelectedVariantId(fullProduct.variants?.find((variant) => variant.available_qty > 0)?.id || fullProduct.variants?.[0]?.id || null);
          setView("product");
        } else if (orderId > 0 && window.location.pathname.includes("payment-result")) {
          await resolvePaymentReturn(orderId);
        }
      } catch (bootError) {
        setError(bootError.message || "Не удалось загрузить приложение");
      } finally {
        setLoading(false);
      }
    }
    boot();
  }, [initialized, tg, initData]);

  useEffect(() => {
    if (!tg?.BackButton) return undefined;
    const parent = { product: "catalog", cart: "catalog", checkout: "cart", orders: "catalog", looks: "catalog", profile: "catalog" }[view];
    if (!parent) {
      tg.BackButton.hide?.();
      return undefined;
    }
    const goBack = () => setView(parent);
    tg.BackButton.show?.();
    tg.BackButton.onClick?.(goBack);
    return () => tg.BackButton.offClick?.(goBack);
  }, [tg, view]);

  useEffect(() => {
    if (!tg?.MainButton) return undefined;
    const mainButton = tg.MainButton;
    const handlers = [];
    const bind = (text, handler, enabled = true) => {
      mainButton.setText?.(text);
      mainButton.show?.();
      if (enabled) mainButton.enable?.(); else mainButton.disable?.();
      mainButton.onClick?.(handler);
      handlers.push(handler);
    };
    if (view === "checkout") bind("Перейти к оплате", handleCheckout, !checkoutBusy);
    else if (view === "cart" && cartCount > 0) bind("Оформить заказ", () => setView("checkout"));
    else if (view === "product" && selectedVariant?.available_qty > 0) bind("Добавить в корзину", handleAddSelected, !addToCartBusy);
    else if (view !== "cart" && cartCount > 0) bind(`Корзина · ${cartCount}`, () => setView("cart"));
    else mainButton.hide?.();
    return () => handlers.forEach((handler) => mainButton.offClick?.(handler));
  }, [tg, view, cartCount, selectedVariant, checkoutForm, checkoutBusy, addToCartBusy]);

  async function loadProfileData() {
    const { data, warnings } = await loadProfileSections({
      getProfile,
      myLoyalty,
      myReferralCode,
      getTimeline,
      listSupportTickets,
      listPrivacyRequests,
      listWishlist,
      listOrders,
    });
    setProfile(data.profile);
    setLoyaltyRows(data.loyalty);
    setReferral(data.referral);
    setTimeline(data.timeline);
    setSupportTickets(data.tickets);
    setPrivacyRequests(data.privacy);
    setWishlist(data.wishlist);
    setOrders(data.orders);
    if (warnings.length) {
      setNotice(warningText("Профиль открыт, но не все данные обновились", warnings));
    }
    return data;
  }

  async function openProduct(product) {
    return act(`open-product-${product.id}`, async () => {
      const fullProduct = product?.variants ? product : await getProduct(product.id);
      setSelected(fullProduct);
      setSelectedVariantId(fullProduct.variants?.find((variant) => variant.available_qty > 0)?.id || fullProduct.variants?.[0]?.id || null);
      setSizeResult(null);
      setView("product");
      trackEvent("product_view", { product_id: fullProduct.id });
      return fullProduct;
    });
  }

  async function handleSearch(queryOverride = searchQuery) {
    await act("search", async () => {
      const query = String(queryOverride || "").trim();
      setSearchQuery(query);
      setProducts(query ? await searchProducts(query) : await listProducts());
    });
  }

  async function handleAddSelected() {
    if (!selected || !selectedVariant || selectedVariant.available_qty <= 0) return;
    const nextCart = await act(
      "add-to-cart",
      () => addToCart(selected.id, selectedVariant.id, 1),
      `${selected.title}, размер ${selectedVariant.size}, добавлен в корзину.`,
    );
    if (nextCart) setCart(nextCart);
  }

  async function handleFavorite() {
    if (!selected) return;
    const key = `wishlist-${selected.id}`;
    if (isFavorite) {
      const result = await act(key, () => removeWishlist(selected.id), `${selected.title} удалён из избранного.`);
      if (result) setWishlist((current) => current.filter((product) => product.id !== selected.id));
    } else {
      const saved = await act(key, () => addWishlist(selected.id), `${selected.title} сохранён в избранном.`);
      if (saved) setWishlist((current) => current.some((product) => product.id === saved.id) ? current : [...current, saved]);
    }
  }

  async function handleRestock() {
    if (!selectedVariant || selectedVariant.available_qty > 0) return;
    await act(
      `restock-${selectedVariant.id}`,
      () => subscribeRestock(selectedVariant.id),
      `Уведомление для размера ${selectedVariant.size} подключено.`,
    );
  }

  async function handleCartQuantity(item, quantity) {
    const maxQuantity = Math.min(item.available_qty, 10);
    if (quantity < 1 || quantity > maxQuantity) return;
    const nextCart = await act(
      cartItemActionKey(item.id),
      () => updateCartItem(item.id, quantity),
      `Количество ${item.title} обновлено.`,
    );
    if (nextCart) setCart(nextCart);
  }

  async function handleCartRemove(item) {
    const nextCart = await act(
      cartItemActionKey(item.id),
      () => removeCartItem(item.id),
      `${item.title} удалён из корзины.`,
    );
    if (nextCart) setCart(nextCart);
  }

  async function handleApplyPromo() {
    const code = promo.trim();
    if (!code) {
      setError("Введите промокод.");
      return;
    }
    const nextCart = await act("promo", () => applyPromo(code), "Промокод применён.");
    if (nextCart) {
      setCart(nextCart);
      setPromo("");
    }
  }

  async function handleApplyLoyalty() {
    const parsed = parseLoyaltyPoints(loyaltyPoints);
    if (parsed.error) {
      setError(parsed.error);
      return;
    }
    const nextCart = await act("loyalty", () => applyLoyalty(parsed.value), "Баллы зарезервированы.");
    if (nextCart) {
      setCart(nextCart);
      setLoyaltyPoints("");
    }
  }

  async function handleApplyReferral() {
    const code = referralInput.trim();
    if (!code) {
      setError("Введите реферальный код.");
      return;
    }
    const nextCart = await act("referral", () => applyReferral(code), "Реферальный код связан с заказом.");
    if (nextCart) {
      setCart(nextCart);
      setReferralInput("");
    }
  }

  async function handleCheckout() {
    const validation = validateCheckoutForm(checkoutForm);
    if (validation.error) {
      setError(validation.error);
      return;
    }
    await act("checkout", async () => {
      const order = await checkout(validation.value);
      try {
        setCart(await getCart());
      } catch {
        setCart(null);
      }
      try {
        const payment = await createPayment(order.id);
        if (!payment.confirmation_url) throw new Error("Платёж создан без ссылки на оплату");
        window.location.assign(payment.confirmation_url);
      } catch (paymentError) {
        try {
          await refreshOrders();
        } catch {
          setOrders((current) => current.some((item) => item.id === order.id) ? current : [order, ...current]);
        }
        setView("orders");
        throw new Error(`Заказ #${order.id} создан и товар зарезервирован. Продолжите оплату в разделе заказов. ${paymentError.message}`);
      }
    });
  }

  async function openProfile() {
    setView("profile");
    await act("profile", loadProfileData);
  }

  async function openOrders() {
    setView("orders");
    await act("orders", refreshOrders);
  }

  async function handleRefreshOrders() {
    await act("refresh-orders", refreshOrders, "Статусы заказов обновлены.");
  }

  async function handleOrderPayment(order) {
    await act(`pay-${order.id}`, async () => {
      const payment = await createPayment(order.id);
      if (!payment.confirmation_url) throw new Error("Для заказа нет активной ссылки оплаты");
      window.location.assign(payment.confirmation_url);
    });
  }

  async function handleOrderCancel(order) {
    if (!window.confirm(`Отменить заказ #${order.id}? Резерв товара будет освобождён.`)) return;
    await act(`cancel-${order.id}`, async () => {
      const updated = await cancelOrder(order.id);
      setOrders((current) => current.map((item) => item.id === updated.id ? updated : item));
      try {
        setCart(await getCart());
      } catch {
        // Cancellation succeeded; a cart refresh failure must not report it as failed.
      }
      return updated;
    }, `Заказ #${order.id} отменён, резерв освобождён.`);
  }

  async function handleReturn(order) {
    const reason = (returnReasons[order.id] || "").trim();
    if (reason.length < 5) {
      setError("Опишите причину возврата минимум в пяти символах.");
      return;
    }
    await act(`return-${order.id}`, async () => {
      const result = await createReturn(order.id, reason);
      setReturnReasons((current) => ({ ...current, [order.id]: "" }));
      try {
        await refreshOrders();
      } catch {
        setOrders((current) => current.map((item) => item.id === order.id
          ? { ...item, status: "refund_requested" }
          : item));
      }
      return result;
    }, `Запрос на возврат заказа #${order.id} зарегистрирован.`);
  }

  async function handleSupport() {
    if (supportForm.subject.trim().length < 3 || supportForm.message.trim().length < 5) {
      setError("Укажите тему и подробно опишите вопрос.");
      return;
    }
    await act("support", async () => {
      const result = await createSupportTicket({
        subject: supportForm.subject.trim(),
        message: supportForm.message.trim(),
        order_id: supportForm.order_id ? Number(supportForm.order_id) : null,
      });
      setSupportForm({ subject: "", message: "", order_id: "" });
      try {
        setSupportTickets(await listSupportTickets());
      } catch {
        if (result?.id) setSupportTickets((current) => [result, ...current.filter((item) => item.id !== result.id)]);
      }
      return result;
    }, "Обращение зарегистрировано. Его статус отображается ниже.");
  }

  async function handlePrivacyExport() {
    const exported = await act("privacy-export", downloadPrivacyData);
    if (!exported) return;
    const url = URL.createObjectURL(exported.blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = exported.filename;
    document.body.appendChild(anchor);
    anchor.click();
    anchor.remove();
    window.setTimeout(() => URL.revokeObjectURL(url), 0);
    setNotice("Архив персональных данных сформирован и скачан.");
  }

  async function handlePrivacyRequest(requestType, confirmation, message) {
    if (confirmation && !window.confirm(confirmation)) return;
    await act(`privacy-${requestType}`, async () => {
      const result = await createPrivacyRequest(requestType);
      try {
        setPrivacyRequests(await listPrivacyRequests());
      } catch {
        if (result?.id) setPrivacyRequests((current) => [result, ...current.filter((item) => item.id !== result.id)]);
      }
      return result;
    }, message);
  }

  async function handleSizeHelper() {
    const validation = validateSizeForm(sizeForm);
    if (validation.error) {
      setError(validation.error);
      return;
    }
    const result = await act("size", () => sizeHelper(selected.id, validation.value));
    if (result) setSizeResult(result);
  }

  return (
    <ErrorBoundary>
      <div className="app">
        <header className="topbar">
          <div><div className="brand">FLASHIN</div>{user?.first_name && <div className="hello">{user.first_name}, ваш личный магазин</div>}</div>
          <button className="cart-shortcut" onClick={() => setView("cart")} disabled={!cartCount}>Корзина {cartCount ? `· ${cartCount}` : ""}</button>
        </header>
        <nav className="tabs" aria-label="Основная навигация">
          <button className={view === "catalog" || view === "product" ? "active" : ""} onClick={() => setView("catalog")}>Каталог</button>
          <button className={view === "looks" ? "active" : ""} onClick={() => setView("looks")}>Образы</button>
          <button className={view === "orders" ? "active" : ""} onClick={openOrders} disabled={isBusy("orders")}>Заказы</button>
          <button className={view === "profile" ? "active" : ""} onClick={openProfile} disabled={isBusy("profile")}>Профиль</button>
        </nav>
        {error && <div className="message error" role="alert">{error}<button onClick={() => setError("")}>×</button></div>}
        {notice && <div className="message success" role="status">{notice}<button onClick={() => setNotice("")}>×</button></div>}
        {loading && <main><SkeletonCard /><SkeletonCard /><SkeletonCard /></main>}

        {!loading && view === "catalog" && (
          <main>
            <div className="section-heading"><div><h1>Каталог</h1><p>Актуальные товары и реальные остатки.</p></div></div>
            <div className="search">
              <input aria-label="Поиск товаров" placeholder="Бренд, категория или артикул" value={searchQuery} onChange={(event) => setSearchQuery(event.target.value)} onKeyDown={(event) => event.key === "Enter" && !isBusy("search") && handleSearch()} disabled={isBusy("search")} />
              <button className="secondary compact" onClick={() => handleSearch()} disabled={isBusy("search")}>Найти</button>
            </div>
            {!products.length ? <EmptyState title="Ничего не найдено" text="Измените запрос или вернитесь ко всему каталогу." action="Показать весь каталог" onAction={() => handleSearch("")} /> : <div className="grid">{products.map((product) => <ProductCard key={product.id} product={product} onOpen={openProduct} disabled={isBusy(`open-product-${product.id}`)} />)}</div>}
          </main>
        )}

        {!loading && view === "product" && selected && (
          <main>
            <button className="link" onClick={() => setView("catalog")}>← Каталог</button>
            <img className="hero" src={productImage(selected)} alt={selected.title} />
            <div className="product-heading"><div><div className="meta">{selected.brand} · {selected.category}</div><h1>{selected.title}</h1></div><div className="price">{money(selected.price, selected.currency)}</div></div>
            {selected.description && <p>{selected.description}</p>}
            <h3>Выберите размер</h3>
            <div className="sizes">{selected.variants?.map((variant) => <button key={variant.id} className={`${selectedVariantId === variant.id ? "size active" : "size"} ${variant.available_qty <= 0 ? "unavailable" : ""}`} onClick={() => setSelectedVariantId(variant.id)}>{variant.size}<small>{variant.available_qty > 0 ? `${variant.available_qty} шт.` : "нет в наличии"}</small></button>)}</div>
            {!selected.variants?.length && <div className="empty-inline">У товара нет размерных вариантов.</div>}
            <div className="panel">
              <h3>Ориентир по размеру</h3>
              <div className="form-grid">
                <input inputMode="numeric" placeholder="Рост, см" value={sizeForm.height_cm} onChange={(event) => setSizeForm({ ...sizeForm, height_cm: event.target.value })} disabled={isBusy("size")} />
                <input inputMode="numeric" placeholder="Вес, кг" value={sizeForm.weight_kg} onChange={(event) => setSizeForm({ ...sizeForm, weight_kg: event.target.value })} disabled={isBusy("size")} />
                <input placeholder="Обычный размер" value={sizeForm.usual_size} onChange={(event) => setSizeForm({ ...sizeForm, usual_size: event.target.value })} disabled={isBusy("size")} />
                <select value={sizeForm.fit_preference} onChange={(event) => setSizeForm({ ...sizeForm, fit_preference: event.target.value })} disabled={isBusy("size")}><option value="slim">По фигуре</option><option value="regular">Обычная посадка</option><option value="oversize">Свободная посадка</option></select>
              </div>
              <button className="secondary" onClick={handleSizeHelper} disabled={isBusy("size")}>Получить рекомендацию</button>
              {sizeResult && <div className="result-card"><span>Рекомендуемый размер</span><b>{sizeResult.suggested_size}</b><p>{sizeResult.note || "Сверьте результат с замерами конкретного изделия."}</p></div>}
            </div>
            <div className="actions">
              {selectedVariant?.available_qty > 0 ? <button className="primary" onClick={handleAddSelected} disabled={addToCartBusy}>Добавить размер {selectedVariant.size} в корзину</button> : selectedVariant ? <button className="primary" onClick={handleRestock} disabled={isBusy(`restock-${selectedVariant.id}`)}>Сообщить о поступлении размера {selectedVariant.size}</button> : null}
              <button className="secondary" onClick={handleFavorite} disabled={isBusy(`wishlist-${selected.id}`)}>{isFavorite ? "Удалить из избранного" : "Сохранить в избранное"}</button>
            </div>
          </main>
        )}

        {!loading && view === "cart" && (
          <main>
            <button className="link" onClick={() => setView("catalog")}>← Продолжить покупки</button><h1>Корзина</h1>
            {!cart?.items?.length ? <EmptyState title="Корзина пуста" text="Добавьте товар и выберите размер." action="Перейти в каталог" onAction={() => setView("catalog")} /> : <>
              <div className="cart-list">{cart.items.map((item) => { const itemBusy = isBusy(cartItemActionKey(item.id)); const maxQuantity = Math.min(item.available_qty, 10); return <div className="cart-item" key={item.id}><div><b>{item.title}</b><div className="meta">Размер {item.size} · доступно {item.available_qty}</div><div>{money(item.price * item.quantity)}</div></div><div className="quantity-control"><button onClick={() => handleCartQuantity(item, item.quantity - 1)} disabled={item.quantity <= 1 || itemBusy}>−</button><b>{item.quantity}</b><button onClick={() => handleCartQuantity(item, item.quantity + 1)} disabled={item.quantity >= maxQuantity || itemBusy}>+</button></div><button className="danger-link" onClick={() => handleCartRemove(item)} disabled={itemBusy}>Удалить</button></div>; })}</div>
              <div className="panel"><h3>Скидки и бонусы</h3>
                <div className="promo"><input placeholder="Промокод" value={promo} onChange={(event) => setPromo(event.target.value)} disabled={isBusy("promo")} /><button className="secondary compact" onClick={handleApplyPromo} disabled={!promo.trim() || isBusy("promo")}>Применить</button></div>
                <div className="promo"><input inputMode="numeric" placeholder="Баллы к списанию" value={loyaltyPoints} onChange={(event) => setLoyaltyPoints(event.target.value)} disabled={isBusy("loyalty")} /><button className="secondary compact" onClick={handleApplyLoyalty} disabled={!loyaltyPoints || isBusy("loyalty")}>Списать</button></div>
                <div className="promo"><input placeholder="Реферальный код" value={referralInput} onChange={(event) => setReferralInput(event.target.value)} disabled={isBusy("referral")} /><button className="secondary compact" onClick={handleApplyReferral} disabled={!referralInput.trim() || isBusy("referral")}>Добавить</button></div>
              </div>
              <div className="summary"><StatusRow label="Товары" value={money(cart.total_amount)} />{cart.discount_amount > 0 && <StatusRow label="Скидка" value={`−${money(cart.discount_amount)}`} tone="success" />}<div className="summary-total"><span>К оплате без доставки</span><b>{money(cart.final_amount)}</b></div></div>
              <button className="primary" onClick={() => setView("checkout")}>Оформить заказ</button>
            </>}
          </main>
        )}

        {!loading && view === "checkout" && (
          <main aria-busy={checkoutBusy}>
            <button className="link" onClick={() => setView("cart")} disabled={checkoutBusy}>← Корзина</button><h1>Получатель и доставка</h1><p className="lead">Перед созданием заказа повторно проверим цены, скидки, остатки и баллы.</p>
            <label>Имя<input autoComplete="name" placeholder="Имя получателя" value={checkoutForm.name} onChange={(event) => setCheckoutForm({ ...checkoutForm, name: event.target.value })} disabled={checkoutBusy} /></label>
            <label>Телефон<input autoComplete="tel" inputMode="tel" placeholder="+7 999 000-00-00" value={checkoutForm.phone} onChange={(event) => setCheckoutForm({ ...checkoutForm, phone: event.target.value })} disabled={checkoutBusy} /></label>
            <label>Способ получения<select value={checkoutForm.delivery_type} onChange={(event) => setCheckoutForm({ ...checkoutForm, delivery_type: event.target.value, address: event.target.value === "pickup" ? "" : checkoutForm.address })} disabled={checkoutBusy}><option value="pickup">Самовывоз</option><option value="courier">Курьер</option></select></label>
            {checkoutForm.delivery_type === "courier" && <label>Адрес<textarea placeholder="Город, улица, дом, квартира" value={checkoutForm.address} onChange={(event) => setCheckoutForm({ ...checkoutForm, address: event.target.value })} disabled={checkoutBusy} /></label>}
            <label>Комментарий<textarea placeholder="Необязательный комментарий" value={checkoutForm.comment} onChange={(event) => setCheckoutForm({ ...checkoutForm, comment: event.target.value })} disabled={checkoutBusy} /></label>
            <button className="primary" onClick={handleCheckout} disabled={checkoutBusy}>{checkoutBusy ? "Создаём заказ…" : "Создать заказ и перейти к оплате"}</button>
          </main>
        )}

        {!loading && view === "orders" && (
          <main>
            <div className="section-heading"><div><h1>Мои заказы</h1><p>Оплата, сборка, доставка и возврат в одном месте.</p></div><button className="secondary compact" onClick={handleRefreshOrders} disabled={isBusy("refresh-orders")}>Обновить</button></div>
            {!orders.length ? <EmptyState title="Заказов пока нет" text="После оформления здесь появятся состав, оплата и доставка." action="Выбрать товары" onAction={() => setView("catalog")} /> : orders.map((order) => <article className="order-card" key={order.id}>
              <div className="order-title"><div><div className="meta">Заказ #{order.id}</div><h2>{money(order.total_amount, order.currency)}</h2></div><span className="status neutral">{ORDER_LABELS[order.status] || order.status}</span></div>
              <StatusRow label="Оплата" value={PAYMENT_LABELS[order.payment_status] || order.payment_status} tone={order.payment_status === "paid" ? "success" : "neutral"} />
              <StatusRow label="Доставка" value={DELIVERY_LABELS[order.delivery_status] || order.delivery_status} />
              <StatusRow label="Получение" value={order.delivery_type === "courier" ? "Курьер" : "Самовывоз"} />
              {order.address && <StatusRow label="Адрес" value={order.address} />}{order.tracking_number && <StatusRow label="Трек-номер" value={order.tracking_number} />}
              <div className="order-items">{order.items?.map((item) => <div key={item.id}><span>{item.title} · {item.size} × {item.quantity}</span><b>{money(item.price * item.quantity, order.currency)}</b></div>)}</div>
              <div className="actions horizontal">{canPayOrder(order) && <button className="primary" onClick={() => handleOrderPayment(order)} disabled={isBusy(`pay-${order.id}`)}>Продолжить оплату</button>}{canCancelOrder(order) && <button className="secondary" onClick={() => handleOrderCancel(order)} disabled={isBusy(`cancel-${order.id}`)}>Отменить заказ</button>}</div>
              {canReturnOrder(order) && <div className="return-box"><label>Причина возврата<textarea placeholder="Что необходимо вернуть и почему" value={returnReasons[order.id] || ""} onChange={(event) => setReturnReasons({ ...returnReasons, [order.id]: event.target.value })} disabled={isBusy(`return-${order.id}`)} /></label><button className="secondary" onClick={() => handleReturn(order)} disabled={isBusy(`return-${order.id}`)}>Зарегистрировать возврат</button></div>}
            </article>)}
          </main>
        )}

        {!loading && view === "looks" && (
          <main>
            <div className="section-heading"><div><h1>Готовые образы</h1><p>Каждый элемент связан с доступной карточкой товара.</p></div></div>
            {!looks.length ? <EmptyState title="Активных образов нет" text="Все товары доступны в каталоге." action="Открыть каталог" onAction={() => setView("catalog")} /> : looks.map((look) => <section className="look-card" key={look.id}><div className="look-heading"><h2>{look.title}</h2>{look.description && <p>{look.description}</p>}</div><div className="look-products">{look.products.map((product) => <ProductCard key={product.id} product={product} onOpen={openProduct} disabled={isBusy(`open-product-${product.id}`)} />)}</div></section>)}
          </main>
        )}

        {!loading && view === "profile" && (
          <main aria-busy={isBusy("profile")}>
            <h1>Профиль и сервис</h1>
            {profile && <div className="panel profile-card"><div><b>{profile.customer.first_name || profile.customer.username || "Клиент FLASHIN"}</b><p>{profile.customer.phone || "Телефон добавится при оформлении заказа"}</p></div><div><span>Баллы</span><b>{profile.loyalty_points}</b></div><div><span>Реферальный код</span><code>{profile.referral_code || referral?.code || "Формируется"}</code></div></div>}
            <section><h2>Избранное</h2>{!wishlist.length ? <p className="muted">Сохранённых товаров нет.</p> : <div className="grid">{wishlist.map((product) => { const wishlistBusy = isBusy(`wishlist-${product.id}`); return <ProductCard key={product.id} product={product} onOpen={openProduct} disabled={isBusy(`open-product-${product.id}`)} action={<button className="danger-link card-action" disabled={wishlistBusy} onClick={async () => { const result = await act(`wishlist-${product.id}`, () => removeWishlist(product.id), `${product.title} удалён из избранного.`); if (result) setWishlist((current) => current.filter((item) => item.id !== product.id)); }}>Удалить</button>} />; })}</div>}</section>
            <section><h2>История баллов</h2>{!loyaltyRows.length ? <p className="muted">Операций пока нет.</p> : loyaltyRows.map((row) => <div className="cart-line" key={row.id}><span>{row.reason}</span><b className={row.points_delta >= 0 ? "positive" : "negative"}>{row.points_delta > 0 ? "+" : ""}{row.points_delta}</b></div>)}</section>
            <section><h2>Поддержка</h2><select value={supportForm.order_id} onChange={(event) => setSupportForm({ ...supportForm, order_id: event.target.value })} disabled={isBusy("support")}><option value="">Без привязки к заказу</option>{orders.map((order) => <option key={order.id} value={order.id}>Заказ #{order.id}</option>)}</select><input placeholder="Тема обращения" value={supportForm.subject} onChange={(event) => setSupportForm({ ...supportForm, subject: event.target.value })} disabled={isBusy("support")} /><textarea placeholder="Опишите вопрос и ожидаемый результат" value={supportForm.message} onChange={(event) => setSupportForm({ ...supportForm, message: event.target.value })} disabled={isBusy("support")} /><button className="secondary" onClick={handleSupport} disabled={isBusy("support")}>Отправить обращение</button>{supportTickets.map((ticket) => <div className="ticket" key={ticket.id}><div><b>{ticket.subject}</b><p>{ticket.message}</p></div><span className="status neutral">{ticket.status}</span></div>)}</section>
            <section><h2>Персональные данные</h2><p className="muted">Экспорт скачивается файлом. Запросы имеют отслеживаемый статус.</p><div className="actions"><button className="secondary" onClick={handlePrivacyExport} disabled={isBusy("privacy-export")}>Скачать мои данные</button><button className="secondary" onClick={() => handlePrivacyRequest("consent_withdrawal", "Отозвать необязательные согласия?", "Запрос на отзыв согласий зарегистрирован.")} disabled={isBusy("privacy-consent_withdrawal")}>Отозвать необязательные согласия</button><button className="danger" onClick={() => handlePrivacyRequest("delete", "Запросить обезличивание персональных данных? История финансовых операций сохранится по требованиям учёта.", "Запрос на обезличивание зарегистрирован.")} disabled={isBusy("privacy-delete")}>Запросить удаление данных</button></div>{privacyRequests.map((request) => <div className="cart-line" key={request.id}><span>{request.request_type}</span><b>{request.status}</b></div>)}</section>
            <section><h2>История действий</h2>{!timeline.length ? <p className="muted">Событий пока нет.</p> : timeline.map((event) => <div className="cart-line" key={event.id}><span>{event.title}</span><small>{event.event_type}</small></div>)}</section>
          </main>
        )}
      </div>
    </ErrorBoundary>
  );
}
