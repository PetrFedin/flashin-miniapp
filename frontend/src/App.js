import React, { useEffect, useMemo, useState } from "react";
import {
  addToCart,
  addWishlist,
  applyLoyalty,
  applyPromo,
  applyReferral,
  checkout,
  createPayment,
  createPrivacyRequest,
  createReturn,
  createSupportTicket,
  exportPrivacyData,
  getCart,
  getProfile,
  getRecommendations,
  getTimeline,
  listLooks,
  listOrders,
  listPrivacyRequests,
  listProducts,
  listSupportTickets,
  myLoyalty,
  myReferralCode,
  removeCartItem,
  searchProducts,
  sizeHelper,
  subscribeRestock,
  telegramAuth,
  trackEvent,
} from "./api";
import ErrorBoundary from "./ErrorBoundary";
import SkeletonCard from "./components/SkeletonCard";
import messages_en from "./i18n/en.json";
import messages_ru from "./i18n/ru.json";
import messages_no from "./i18n/no.json";
import { useTelegram } from "./hooks/useTelegram";

const translations = { en: messages_en, ru: messages_ru, no: messages_no };

export default function App() {
  const { tg, initData, user } = useTelegram();
  const [language, setLanguage] = useState("ru");
  const [view, setView] = useState("catalog");
  const [products, setProducts] = useState([]);
  const [looks, setLooks] = useState([]);
  const [selected, setSelected] = useState(null);
  const [selectedVariantId, setSelectedVariantId] = useState(null);
  const [activeImageIndex, setActiveImageIndex] = useState(0);
  const [recommendations, setRecommendations] = useState([]);
  const [cart, setCart] = useState(null);
  const [profile, setProfile] = useState(null);
  const [loyaltyRows, setLoyaltyRows] = useState([]);
  const [referral, setReferral] = useState(null);
  const [timeline, setTimeline] = useState([]);
  const [supportTickets, setSupportTickets] = useState([]);
  const [privacyRequests, setPrivacyRequests] = useState([]);
  const [orders, setOrders] = useState([]);
  const [searchQuery, setSearchQuery] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [promo, setPromo] = useState("");
  const [referralInput, setReferralInput] = useState("");
  const [loyaltyPoints, setLoyaltyPoints] = useState("");
  const [returnReason, setReturnReason] = useState("");
  const [supportForm, setSupportForm] = useState({ subject: "", message: "", order_id: "" });
  const [sizeForm, setSizeForm] = useState({ height_cm: "", weight_kg: "", usual_size: "", fit_preference: "regular" });
  const [sizeResult, setSizeResult] = useState(null);
  const [checkoutForm, setCheckoutForm] = useState({ name: "", phone: "", delivery_type: "pickup", address: "", comment: "" });

  const t = (section, key) => translations[language]?.[section]?.[key] || key;

  useEffect(() => {
    async function boot() {
      try {
        if (!initData) {
          setError("Откройте приложение внутри Telegram. В браузере авторизация недоступна.");
          return;
        }
        await telegramAuth(initData);
        const [p, c, l] = await Promise.all([listProducts(), getCart(), listLooks()]);
        setProducts(p);
        setCart(c);
        setLooks(l);

        const params = new URLSearchParams(window.location.search);
        const productId = params.get("product");
        if (productId) {
          const found = p.find((x) => String(x.id) === String(productId));
          if (found) openProduct(found);
        }
      } catch (e) {
        setError(e.message || "Ошибка загрузки");
      } finally {
        setLoading(false);
      }
    }
    boot();
  }, [initData]);

  useEffect(() => {
    if (!notice) return;
    const timer = window.setTimeout(() => setNotice(""), 2500);
    return () => window.clearTimeout(timer);
  }, [notice]);

  useEffect(() => {
    if (!tg?.MainButton) return;
    const add = () => handleAddSelected();
    const cartOpen = () => setView("cart");
    const submit = () => handleCheckout();

    if (view === "product" && selectedVariantId) {
      tg.MainButton.setText("Добавить в корзину");
      tg.MainButton.show();
      tg.MainButton.onClick(add);
      return () => tg.MainButton.offClick(add);
    }
    if (view !== "cart" && cart?.items?.length > 0) {
      tg.MainButton.setText(`Корзина · ${cart.items.length}`);
      tg.MainButton.show();
      tg.MainButton.onClick(cartOpen);
      return () => tg.MainButton.offClick(cartOpen);
    }
    if (view === "checkout") {
      tg.MainButton.setText("Создать заказ");
      tg.MainButton.show();
      tg.MainButton.onClick(submit);
      return () => tg.MainButton.offClick(submit);
    }
    tg.MainButton.hide();
  }, [tg, view, cart, selectedVariantId, checkoutForm]);

  async function loadProfileData() {
    const [p, loyalty, ref, tl, tickets, privacy] = await Promise.all([
      getProfile(),
      myLoyalty(),
      myReferralCode(),
      getTimeline(),
      listSupportTickets(),
      listPrivacyRequests(),
    ]);
    setProfile(p);
    setLoyaltyRows(loyalty);
    setReferral(ref);
    setTimeline(tl);
    setSupportTickets(tickets);
    setPrivacyRequests(privacy);
  }

  async function openProduct(p) {
    setSelected(p);
    setSelectedVariantId(p.variants?.find((v) => v.available_qty > 0)?.id || null);
    setActiveImageIndex(0);
    setRecommendations([]);
    setSizeResult(null);
    setView("product");
    window.scrollTo({ top: 0, behavior: "smooth" });
    trackEvent("product_view", { product_id: p.id });

    try {
      const rows = await getRecommendations(p.id);
      setRecommendations(Array.isArray(rows) ? rows.filter((item) => item.id !== p.id) : []);
    } catch {
      setRecommendations([]);
    }
  }

  async function handleSearch() {
    try {
      setError("");
      if (!searchQuery.trim()) {
        setProducts(await listProducts());
        return;
      }
      setProducts(await searchProducts(searchQuery));
    } catch (e) {
      setError(e.message || "Не удалось выполнить поиск");
    }
  }

  async function handleAddSelected() {
    if (!selected || !selectedVariantId) return;
    try {
      setError("");
      const c = await addToCart(selected.id, selectedVariantId, 1);
      setCart(c);
      setNotice("Товар добавлен в корзину");
      tg?.HapticFeedback?.notificationOccurred?.("success");
    } catch (e) {
      setError(e.message);
    }
  }

  async function handleWishlist() {
    if (!selected) return;
    try {
      setError("");
      await addWishlist(selected.id);
      setNotice("Товар добавлен в избранное");
      tg?.HapticFeedback?.notificationOccurred?.("success");
    } catch (e) {
      setError(e.message || "Не удалось добавить товар в избранное");
    }
  }

  async function handleRestock() {
    if (!selectedVariantId) return;
    try {
      setError("");
      await subscribeRestock(selectedVariantId);
      setNotice("Уведомление о поступлении включено");
    } catch (e) {
      setError(e.message || "Не удалось включить уведомление");
    }
  }

  async function handleRemoveCartItem(itemId) {
    try {
      setError("");
      await removeCartItem(itemId);
      setCart(await getCart());
      setNotice("Товар удалён из корзины");
    } catch (e) {
      setError(e.message || "Не удалось удалить товар");
    }
  }

  async function handleCheckout() {
    try {
      setError("");
      if (!checkoutForm.name || !checkoutForm.phone) return setError("Заполните имя и телефон.");
      const order = await checkout(checkoutForm);
      const payment = await createPayment(order.id);
      if (!payment.confirmation_url) throw new Error("Платёж создан без ссылки оплаты.");
      window.location.href = payment.confirmation_url;
    } catch (e) {
      setError(e.message);
    }
  }

  async function openProfile() {
    await loadProfileData();
    setView("profile");
  }

  async function openOrders() {
    setOrders(await listOrders());
    setView("orders");
  }

  const total = useMemo(() => cart?.total_amount || 0, [cart]);
  const selectedImages = selected?.images?.length ? selected.images : [{ url: "/fallback-product.svg" }];

  return (
    <ErrorBoundary>
      <div className="app">
        <header className="topbar">
          <div>
            <div className="brand">FLASHIN</div>
            {user?.first_name && <div className="hello">Привет, {user.first_name}</div>}
          </div>
          <select value={language} onChange={(e) => setLanguage(e.target.value)} aria-label="Язык интерфейса">
            <option value="ru">RU</option>
            <option value="en">EN</option>
            <option value="no">NO</option>
          </select>
        </header>

        <nav className="tabs">
          <button onClick={() => setView("catalog")}>Каталог</button>
          <button onClick={openOrders}>Заказы</button>
          <button onClick={openProfile}>Профиль</button>
          <button onClick={() => setView("looks")}>Looks</button>
        </nav>

        {error && <div className="error" role="alert">{error}</div>}
        {notice && <div className="notice" role="status">{notice}</div>}

        {loading && <main><SkeletonCard /><SkeletonCard /><SkeletonCard /></main>}

        {!loading && view === "catalog" && (
          <main>
            <h1>{t("catalog", "title")}</h1>
            <div className="search">
              <input
                placeholder="Поиск: бренд, категория, артикул"
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && handleSearch()}
              />
              <button className="secondary" onClick={handleSearch}>Найти</button>
            </div>
            {!products.length && <div className="empty-state">Ничего не найдено. Попробуйте изменить запрос.</div>}
            <div className="grid">
              {products.map((p) => (
                <button className="product-card" key={p.id} onClick={() => openProduct(p)}>
                  <img src={p.images?.[0]?.url || "/fallback-product.svg"} alt={p.title} loading="lazy" />
                  <div className="title">{p.title}</div>
                  <div className="price">{p.price.toLocaleString("ru-RU")} {p.currency}</div>
                </button>
              ))}
            </div>
          </main>
        )}

        {!loading && view === "product" && selected && (
          <main>
            <button className="link" onClick={() => setView("catalog")}>← Назад</button>
            <div className="gallery">
              <img className="hero" src={selectedImages[activeImageIndex]?.url || "/fallback-product.svg"} alt={selected.title} />
              {selectedImages.length > 1 && (
                <div className="gallery-thumbs" aria-label="Фотографии товара">
                  {selectedImages.map((image, index) => (
                    <button
                      key={`${image.url}-${index}`}
                      className={activeImageIndex === index ? "gallery-thumb active" : "gallery-thumb"}
                      onClick={() => setActiveImageIndex(index)}
                      aria-label={`Открыть фотографию ${index + 1}`}
                    >
                      <img src={image.url} alt="" loading="lazy" />
                    </button>
                  ))}
                </div>
              )}
            </div>
            <h1>{selected.title}</h1>
            <div className="price product-price">{selected.price.toLocaleString("ru-RU")} {selected.currency}</div>
            <p>{selected.description}</p>
            <div className="sizes">
              {selected.variants?.map((v) => (
                <button key={v.id} disabled={v.available_qty <= 0} className={selectedVariantId === v.id ? "size active" : "size"} onClick={() => setSelectedVariantId(v.id)}>
                  {v.size} · {v.available_qty}
                </button>
              ))}
            </div>
            <div className="panel">
              <h3>Помощник размера</h3>
              <div className="search">
                <input inputMode="numeric" placeholder="Рост" value={sizeForm.height_cm} onChange={e => setSizeForm({ ...sizeForm, height_cm: e.target.value })} />
                <input inputMode="numeric" placeholder="Вес" value={sizeForm.weight_kg} onChange={e => setSizeForm({ ...sizeForm, weight_kg: e.target.value })} />
              </div>
              <button className="secondary" onClick={async () => setSizeResult(await sizeHelper({
                height_cm: Number(sizeForm.height_cm) || null,
                weight_kg: Number(sizeForm.weight_kg) || null,
                usual_size: sizeForm.usual_size || null,
                fit_preference: sizeForm.fit_preference
              }))}>Подобрать размер</button>
              {sizeResult && <p>Рекомендуемый размер: <b>{sizeResult.suggested_size}</b></p>}
            </div>
            <div className="actions">
              <button className="primary" onClick={handleAddSelected} disabled={!selectedVariantId}>Добавить в корзину</button>
              <button className="secondary" onClick={handleWishlist}>В избранное</button>
              {selectedVariantId && <button className="secondary" onClick={handleRestock}>Уведомить о поступлении</button>}
            </div>

            {recommendations.length > 0 && (
              <section className="recommendations">
                <h2>С этим товаром смотрят</h2>
                <div className="recommendation-row">
                  {recommendations.slice(0, 8).map((item) => (
                    <button className="recommendation-card" key={item.id} onClick={() => openProduct(item)}>
                      <img src={item.images?.[0]?.url || "/fallback-product.svg"} alt={item.title} loading="lazy" />
                      <span>{item.title}</span>
                      <b>{item.price?.toLocaleString("ru-RU")} {item.currency}</b>
                    </button>
                  ))}
                </div>
              </section>
            )}
          </main>
        )}

        {!loading && view === "cart" && (
          <main>
            <button className="link" onClick={() => setView("catalog")}>← {t("cart", "back_to_catalog")}</button>
            <h1>{t("cart", "title")}</h1>
            {!cart?.items?.length && <div className="empty-state">{t("cart", "empty")}</div>}
            {cart?.items?.map((item) => (
              <div className="cart-line" key={item.id}>
                <div><b>{item.title}</b><div>Размер: {item.size} · {item.quantity}</div></div>
                <div className="cart-line-end">
                  <div>{(item.price * item.quantity).toLocaleString("ru-RU")} RUB</div>
                  <button className="remove-button" onClick={() => handleRemoveCartItem(item.id)}>Удалить</button>
                </div>
              </div>
            ))}
            {cart?.items?.length > 0 && (
              <>
                <div className="promo">
                  <input placeholder="Промокод" value={promo} onChange={(e) => setPromo(e.target.value)} />
                  <button className="secondary" onClick={async () => setCart(await applyPromo(promo))}>Применить</button>
                </div>
                <div className="promo">
                  <input inputMode="numeric" placeholder="Баллы к списанию" value={loyaltyPoints} onChange={(e) => setLoyaltyPoints(e.target.value)} />
                  <button className="secondary" onClick={async () => setCart(await applyLoyalty(Number(loyaltyPoints)))}>Списать</button>
                </div>
                <div className="promo">
                  <input placeholder="Referral-код" value={referralInput} onChange={(e) => setReferralInput(e.target.value)} />
                  <button className="secondary" onClick={async () => setCart(await applyReferral(referralInput))}>Добавить</button>
                </div>
                {cart?.discount_amount > 0 && <div className="discount">Скидка: {cart.discount_amount.toLocaleString("ru-RU")} RUB</div>}
                <div className="total">Итого: {(cart?.final_amount || total).toLocaleString("ru-RU")} RUB</div>
                <button className="primary" onClick={() => setView("checkout")}>Оформить заказ</button>
              </>
            )}
          </main>
        )}

        {!loading && view === "checkout" && (
          <main>
            <button className="link" onClick={() => setView("cart")}>← Корзина</button>
            <h1>Оформление</h1>
            <input autoComplete="name" placeholder="Имя" value={checkoutForm.name} onChange={(e) => setCheckoutForm({ ...checkoutForm, name: e.target.value })} />
            <input autoComplete="tel" inputMode="tel" placeholder="Телефон" value={checkoutForm.phone} onChange={(e) => setCheckoutForm({ ...checkoutForm, phone: e.target.value })} />
            <select value={checkoutForm.delivery_type} onChange={(e) => setCheckoutForm({ ...checkoutForm, delivery_type: e.target.value })}>
              <option value="pickup">Самовывоз</option>
              <option value="courier">Курьер</option>
            </select>
            <textarea placeholder="Адрес / комментарий" value={checkoutForm.address} onChange={(e) => setCheckoutForm({ ...checkoutForm, address: e.target.value })} />
            <button className="primary" onClick={handleCheckout}>Перейти к оплате</button>
          </main>
        )}

        {!loading && view === "orders" && (
          <main>
            <h1>Мои заказы</h1>
            {!orders.length && <div className="empty-state">Заказов пока нет.</div>}
            {orders.map((order) => (
              <div className="order-card" key={order.id}>
                <b>Заказ #{order.id}</b>
                <div>Статус: {order.status}</div>
                <div>Оплата: {order.payment_status}</div>
                <div>Сумма: {order.total_amount.toLocaleString("ru-RU")} {order.currency}</div>
                {order.payment_status === "paid" && (
                  <>
                    <input placeholder="Причина возврата" value={returnReason} onChange={(e) => setReturnReason(e.target.value)} />
                    <button className="secondary" onClick={() => createReturn(order.id, returnReason)}>Запросить возврат</button>
                  </>
                )}
              </div>
            ))}
          </main>
        )}

        {!loading && view === "looks" && (
          <main>
            <h1>Looks</h1>
            {!looks.length && <div className="empty-state">Пока нет готовых образов.</div>}
            {looks.map((look) => (
              <div className="order-card" key={look.id}>
                <b>{look.title}</b>
                <p>{look.description}</p>
                <div>ID товаров: {look.product_ids.join(", ")}</div>
              </div>
            ))}
          </main>
        )}

        {!loading && view === "profile" && (
          <main>
            <h1>Профиль</h1>
            {profile && (
              <div className="panel">
                <b>{profile.customer.first_name || profile.customer.username || "Клиент FLASHIN"}</b>
                <p>Сегмент: {profile.crm?.segment || "new"}</p>
                <p>Баллы: <b>{profile.loyalty_points}</b></p>
                <p>Referral: <code>{profile.referral_code}</code></p>
              </div>
            )}
            <h2>История баллов</h2>
            {loyaltyRows.map(row => <div className="cart-line" key={row.id}><span>{row.reason}</span><b>{row.points_delta}</b></div>)}
            <h2>Support</h2>
            <input placeholder="Тема" value={supportForm.subject} onChange={e => setSupportForm({ ...supportForm, subject: e.target.value })} />
            <textarea placeholder="Сообщение" value={supportForm.message} onChange={e => setSupportForm({ ...supportForm, message: e.target.value })} />
            <button className="secondary" onClick={async () => { await createSupportTicket({ subject: supportForm.subject, message: supportForm.message, order_id: supportForm.order_id ? Number(supportForm.order_id) : null }); await loadProfileData(); }}>Отправить</button>
            {supportTickets.map(t => <div className="order-card" key={t.id}><b>{t.subject}</b><div>{t.status}</div><p>{t.message}</p></div>)}
            <h2>Данные и privacy</h2>
            <button className="secondary" onClick={async () => alert(await exportPrivacyData())}>Экспортировать данные</button>
            <button className="secondary" onClick={async () => { await createPrivacyRequest("delete"); await loadProfileData(); }}>Запросить удаление данных</button>
            {privacyRequests.map(r => <div className="cart-line" key={r.id}><span>{r.request_type}</span><b>{r.status}</b></div>)}
            <h2>Timeline</h2>
            {timeline.map(t => <div className="cart-line" key={t.id}><span>{t.title}</span><small>{t.event_type}</small></div>)}
          </main>
        )}
      </div>
    </ErrorBoundary>
  );
}
