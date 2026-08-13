import React, { useEffect, useRef, useState } from "react";
import { createRoot } from "react-dom/client";

import {
  hasAdminPermission,
  normalizeAdminSession,
} from "./adminPermissions.js";
import {
  AdminApiError,
  adminJson,
  downloadAdminFile,
  getAdminToken,
  loginAdmin,
  setAdminToken,
  uploadAdminFile,
} from "./api.js";
import BusinessEventsPanel from "./BusinessEventsPanel.jsx";
import {
  ORDER_STATUS_LABELS,
  orderAction,
} from "./orderTransitions.js";
import "./style.css";

const EMPTY_PROMO = {
  code: "",
  discount_type: "percent",
  discount_value: 10,
  min_amount: 0,
};

const EMPTY_PRODUCT = {
  sku: "",
  title: "",
  slug: "",
  brand: "FLASHIN",
  description: "",
  price: "",
  currency: "RUB",
  category: "Clothing",
  gender: "unisex",
  images: [],
  variants: [{ size: "M", sku: "", stock_qty: 0, color: "" }],
};

function downloadBlob(blob, filename) {
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  setTimeout(() => URL.revokeObjectURL(url), 0);
}

function App() {
  const [token, setToken] = useState(getAdminToken());
  const [session, setSession] = useState(() => normalizeAdminSession(null));
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [totpCode, setTotpCode] = useState("");
  const [products, setProducts] = useState([]);
  const [orders, setOrders] = useState([]);
  const [auditLogs, setAuditLogs] = useState([]);
  const [lowStock, setLowStock] = useState([]);
  const [abandonedCarts, setAbandonedCarts] = useState([]);
  const [promocode, setPromocode] = useState(EMPTY_PROMO);
  const [productForm, setProductForm] = useState(EMPTY_PRODUCT);
  const [busyKeys, setBusyKeys] = useState(() => new Set());
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const operationLocks = useRef(new Set());

  const canProductsRead = hasAdminPermission(session, "products.read");
  const canProductsWrite = hasAdminPermission(session, "products.write");
  const canInventoryWrite = hasAdminPermission(session, "inventory.write");
  const canOrdersRead = hasAdminPermission(session, "orders.read");
  const canOrdersWrite = hasAdminPermission(session, "orders.write");
  const canPromoWrite = hasAdminPermission(session, "promo.write");
  const canCustomersRead = hasAdminPermission(session, "customers.read");
  const canNotificationsRetry = hasAdminPermission(session, "notifications.retry");
  const canAuditRead = hasAdminPermission(session, "audit.read");
  const canMediaWrite = hasAdminPermission(session, "media.write");

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

  function logout(message = "") {
    setAdminToken("");
    setToken("");
    setSession(normalizeAdminSession(null));
    setTotpCode("");
    setProductForm(EMPTY_PRODUCT);
    setPromocode(EMPTY_PROMO);
    setProducts([]);
    setOrders([]);
    setAuditLogs([]);
    setLowStock([]);
    setAbandonedCarts([]);
    if (message) setError(message);
  }

  async function runAction(key, operation, successMessage = "") {
    if (operationLocks.current.has(key)) return null;
    operationLocks.current.add(key);
    markBusy(key, true);
    setError("");
    setNotice("");
    try {
      const result = await operation();
      if (successMessage) setNotice(successMessage);
      return result;
    } catch (actionError) {
      if (actionError instanceof AdminApiError && actionError.status === 401) {
        logout("Сессия администратора истекла. Войдите снова.");
      } else {
        setError(actionError.message || "Операция не выполнена");
      }
      return null;
    } finally {
      operationLocks.current.delete(key);
      markBusy(key, false);
    }
  }

  async function loadSession() {
    const payload = await adminJson("/api/admin/session");
    const normalized = normalizeAdminSession(payload);
    if (!normalized.valid) {
      throw new Error("Сервер вернул некорректный контракт прав администратора.");
    }
    return normalized;
  }

  async function loadCore(activeSession = session) {
    const requests = [];
    if (hasAdminPermission(activeSession, "products.read")) {
      requests.push(adminJson("/api/admin/products").then((value) => setProducts(Array.isArray(value) ? value : [])));
    } else {
      setProducts([]);
    }
    if (hasAdminPermission(activeSession, "orders.read")) {
      requests.push(adminJson("/api/admin/orders").then((value) => setOrders(Array.isArray(value) ? value : [])));
    } else {
      setOrders([]);
    }
    await Promise.all(requests);
  }

  async function loadOperations(activeSession = session) {
    const sections = [];
    if (hasAdminPermission(activeSession, "audit.read")) {
      sections.push(["audit log", "/api/admin/audit-logs", setAuditLogs]);
    } else {
      setAuditLogs([]);
    }
    if (hasAdminPermission(activeSession, "products.read")) {
      sections.push(["низкие остатки", "/api/ops/inventory/low-stock", setLowStock]);
    } else {
      setLowStock([]);
    }
    if (hasAdminPermission(activeSession, "customers.read")) {
      sections.push(["брошенные корзины", "/api/ops/abandoned-carts", setAbandonedCarts]);
    } else {
      setAbandonedCarts([]);
    }
    if (!sections.length) return;

    const results = await Promise.allSettled(sections.map(([, path]) => adminJson(path)));
    const failures = [];
    results.forEach((result, index) => {
      if (result.status === "fulfilled") {
        sections[index][2](Array.isArray(result.value) ? result.value : []);
      } else {
        failures.push({ name: sections[index][0], error: result.reason });
      }
    });
    const authFailure = failures.find(({ error: failure }) => failure?.status === 401);
    if (authFailure) throw authFailure.error;
    if (failures.length) {
      throw new Error(`Не загружены разрешённые разделы: ${failures.map(({ name }) => name).join(", ")}`);
    }
  }

  async function refreshAll(activeSession = session) {
    if (!activeSession?.valid) throw new Error("Права администратора не подтверждены.");
    await Promise.all([loadCore(activeSession), loadOperations(activeSession)]);
  }

  useEffect(() => {
    if (!token) return;
    let cancelled = false;
    runAction("initial-load", async () => {
      const nextSession = await loadSession();
      if (cancelled) return null;
      setSession(nextSession);
      await refreshAll(nextSession);
      return true;
    }).then((result) => {
      if (!cancelled && !result && getAdminToken()) {
        logout("Не удалось безопасно подтвердить права администратора. Войдите снова.");
      }
    });
    return () => {
      cancelled = true;
    };
  }, [token]);

  async function handleLogin() {
    const normalizedEmail = email.trim().toLowerCase();
    if (!normalizedEmail || !password) {
      setError("Введите email и пароль администратора.");
      return;
    }
    const result = await runAction(
      "login",
      () => loginAdmin(normalizedEmail, password, totpCode),
    );
    if (result) {
      setPassword("");
      setTotpCode("");
      setSession(normalizeAdminSession(null));
      setToken(result.access_token);
    }
  }

  async function createPromo() {
    if (!canPromoWrite) {
      setError("Недостаточно прав: создание промокода требует promo.write.");
      return;
    }
    if (!promocode.code.trim()) {
      setError("Введите код промокода.");
      return;
    }
    const result = await runAction(
      "create-promo",
      () => adminJson("/api/admin/promocodes", {
        method: "POST",
        body: JSON.stringify({ ...promocode, code: promocode.code.trim().toUpperCase() }),
      }),
      "Промокод создан.",
    );
    if (result) setPromocode(EMPTY_PROMO);
  }

  async function uploadImage(file) {
    if (!canMediaWrite) {
      setError("Недостаточно прав: загрузка медиа требует media.write.");
      return;
    }
    const asset = await runAction(
      "upload-image",
      () => uploadAdminFile("/api/media/upload", file),
      "Изображение загружено.",
    );
    if (asset) {
      setProductForm((current) => ({
        ...current,
        images: current.images.includes(asset.url)
          ? current.images
          : [...current.images, asset.url],
      }));
    }
  }

  function updateVariant(index, field, value) {
    setProductForm((current) => ({
      ...current,
      variants: current.variants.map((variant, variantIndex) => (
        variantIndex === index ? { ...variant, [field]: value } : variant
      )),
    }));
  }

  function addVariant() {
    setProductForm((current) => ({
      ...current,
      variants: [...current.variants, { size: "", sku: "", stock_qty: 0, color: "" }],
    }));
  }

  function removeVariant(index) {
    setProductForm((current) => ({
      ...current,
      variants: current.variants.length === 1
        ? current.variants
        : current.variants.filter((_, variantIndex) => variantIndex !== index),
    }));
  }

  async function createProduct() {
    if (!canProductsWrite) {
      setError("Недостаточно прав: создание товара требует products.write.");
      return;
    }
    if (!productForm.sku.trim() || !productForm.title.trim() || !productForm.slug.trim()) {
      setError("Для товара обязательны SKU, название и slug.");
      return;
    }
    if (!(Number(productForm.price) > 0)) {
      setError("Цена товара должна быть больше нуля.");
      return;
    }
    if (productForm.variants.some((variant) => !variant.size.trim() || !variant.sku.trim())) {
      setError("У каждого варианта должны быть размер и SKU.");
      return;
    }
    if (!canInventoryWrite && productForm.variants.some((variant) => Number(variant.stock_qty) !== 0)) {
      setError("Начальный остаток выше нуля требует inventory.write. Создайте товар с нулевым остатком или передайте остатки warehouse/owner.");
      return;
    }

    const result = await runAction(
      "create-product",
      () => adminJson("/api/admin/products", {
        method: "POST",
        body: JSON.stringify({
          ...productForm,
          sku: productForm.sku.trim().toUpperCase(),
          slug: productForm.slug.trim().toLowerCase(),
          price: Number(productForm.price),
          variants: productForm.variants.map((variant) => ({
            ...variant,
            sku: variant.sku.trim().toUpperCase(),
            stock_qty: canInventoryWrite ? Number(variant.stock_qty) : 0,
          })),
        }),
      }),
      "Товар создан.",
    );
    if (result) {
      setProductForm(EMPTY_PRODUCT);
      await runAction("reload-products", () => loadCore(session));
    }
  }

  async function importCsv(file) {
    if (!canProductsWrite || !canInventoryWrite) {
      setError("Недостаточно прав: CSV импорт меняет каталог и остатки, поэтому требует products.write + inventory.write.");
      return;
    }
    const result = await runAction(
      "import-csv",
      () => uploadAdminFile("/api/admin/products/import-csv", file),
      "CSV импортирован.",
    );
    if (result) await runAction("reload-products", () => loadCore(session));
  }

  async function exportOrders() {
    if (!canOrdersRead) {
      setError("Недостаточно прав: экспорт заказов требует orders.read.");
      return;
    }
    const exported = await runAction(
      "export-orders",
      () => downloadAdminFile("/api/admin/orders/export-csv", "flashin_orders.csv"),
    );
    if (exported) {
      downloadBlob(exported.blob, exported.filename);
      setNotice("Выгрузка заказов скачана.");
    }
  }

  async function refreshOperationsAfter(key, path, successMessage, requiredPermissions = []) {
    const missing = requiredPermissions.find((permission) => !hasAdminPermission(session, permission));
    if (missing) {
      setError(`Недостаточно прав: операция требует ${missing}.`);
      return;
    }
    const result = await runAction(
      key,
      () => adminJson(path, { method: "POST" }),
      successMessage,
    );
    if (result) await runAction("reload-operations", () => loadOperations(session));
  }

  async function handleOrderAction(order) {
    if (!canOrdersWrite) {
      setError("Недостаточно прав: изменение заказа требует orders.write.");
      return;
    }
    const action = orderAction(order);
    if (!action) return;
    if (action.type === "cancel" && !window.confirm(`Отменить заказ #${order.id} до оплаты?`)) return;

    const result = await runAction(
      `order-${order.id}`,
      () => action.type === "cancel"
        ? adminJson(`/api/admin/orders/${order.id}/cancel`, { method: "POST" })
        : adminJson(`/api/admin/orders/${order.id}`, {
          method: "PATCH",
          body: JSON.stringify({ status: action.status }),
        }),
      action.type === "cancel"
        ? `Заказ #${order.id} отменён.`
        : `Заказ #${order.id}: ${ORDER_STATUS_LABELS[action.status]}.`,
    );
    if (result) await runAction("reload-orders", () => loadCore(session));
  }

  if (!token) {
    return (
      <main className="login">
        <h1>FLASHIN Admin</h1>
        {error && <div className="error" role="alert">{error}</div>}
        <input
          value={email}
          onChange={(event) => setEmail(event.target.value)}
          onKeyDown={(event) => event.key === "Enter" && handleLogin()}
          placeholder="Email администратора"
          autoComplete="username"
        />
        <input
          value={password}
          onChange={(event) => setPassword(event.target.value)}
          onKeyDown={(event) => event.key === "Enter" && handleLogin()}
          placeholder="Пароль"
          type="password"
          autoComplete="current-password"
        />
        <input
          aria-label="Код двухфакторной аутентификации"
          value={totpCode}
          onChange={(event) => setTotpCode(event.target.value.replace(/\D/g, "").slice(0, 16))}
          onKeyDown={(event) => event.key === "Enter" && handleLogin()}
          placeholder="Код 2FA (если включён)"
          type="text"
          inputMode="numeric"
          autoComplete="one-time-code"
          maxLength={16}
        />
        <button onClick={handleLogin} disabled={isBusy("login")}>Войти</button>
      </main>
    );
  }

  if (!session.valid) {
    return (
      <main className="login" aria-busy="true">
        <h1>FLASHIN Admin</h1>
        <p>Проверка прав администратора…</p>
        {error && <div className="error" role="alert">{error}</div>}
        <button onClick={() => logout()}>Выйти</button>
      </main>
    );
  }

  return (
    <main>
      <header>
        <div>
          <h1>FLASHIN Admin</h1>
          <small>{session.email} · {session.role}</small>
        </div>
        <div>
          <button
            onClick={() => runAction("refresh-all", () => refreshAll(session), "Данные обновлены.")}
            disabled={isBusy("refresh-all")}
          >
            Обновить
          </button>
          <button onClick={() => logout()}>Выйти</button>
        </div>
      </header>
      {error && <div className="error" role="alert">{error}<button onClick={() => setError("")}>×</button></div>}
      {notice && <div className="notice" role="status">{notice}<button onClick={() => setNotice("")}>×</button></div>}

      {((canProductsWrite && canInventoryWrite) || canOrdersRead) && (
        <section>
          <h2>Импорт и экспорт</h2>
          {canProductsWrite && canInventoryWrite && (
            <input
              aria-label="Импорт товаров CSV"
              type="file"
              accept=".csv,text/csv"
              disabled={isBusy("import-csv")}
              onChange={(event) => {
                const file = event.target.files?.[0];
                event.target.value = "";
                if (file) importCsv(file);
              }}
            />
          )}
          {canOrdersRead && (
            <button onClick={exportOrders} disabled={isBusy("export-orders")}>Скачать заказы CSV</button>
          )}
        </section>
      )}

      {canPromoWrite && (
        <section>
          <h2>Промокод</h2>
          <input placeholder="CODE" value={promocode.code} onChange={(event) => setPromocode({ ...promocode, code: event.target.value.toUpperCase() })} />
          <input type="number" min="0" value={promocode.discount_value} onChange={(event) => setPromocode({ ...promocode, discount_value: Number(event.target.value) })} />
          <button onClick={createPromo} disabled={isBusy("create-promo")}>Создать</button>
        </section>
      )}

      {canProductsWrite && (
        <section>
          <h2>Создать товар</h2>
          <div className="form-grid">
            <input placeholder="SKU" value={productForm.sku} onChange={(event) => setProductForm({ ...productForm, sku: event.target.value })} />
            <input placeholder="Название" value={productForm.title} onChange={(event) => setProductForm({ ...productForm, title: event.target.value })} />
            <input placeholder="slug" value={productForm.slug} onChange={(event) => setProductForm({ ...productForm, slug: event.target.value })} />
            <input placeholder="Бренд" value={productForm.brand} onChange={(event) => setProductForm({ ...productForm, brand: event.target.value })} />
            <input type="number" min="0" step="0.01" placeholder="Цена" value={productForm.price} onChange={(event) => setProductForm({ ...productForm, price: event.target.value })} />
            <input placeholder="Категория" value={productForm.category} onChange={(event) => setProductForm({ ...productForm, category: event.target.value })} />
          </div>
          <textarea placeholder="Описание" value={productForm.description} onChange={(event) => setProductForm({ ...productForm, description: event.target.value })} />
          {canMediaWrite && (
            <>
              <h3>Фото</h3>
              <input
                aria-label="Загрузить фото товара"
                type="file"
                accept="image/*"
                disabled={isBusy("upload-image")}
                onChange={(event) => {
                  const file = event.target.files?.[0];
                  event.target.value = "";
                  if (file) uploadImage(file);
                }}
              />
              <div className="image-list">{productForm.images.map((url) => <img key={url} src={url} alt="Загруженный товар" />)}</div>
            </>
          )}
          <h3>Размеры</h3>
          {!canInventoryWrite && (
            <p className="event-warning">Товар можно создать, но начальный остаток будет 0: изменение stock требует inventory.write.</p>
          )}
          {productForm.variants.map((variant, index) => (
            <div className="form-grid" key={index}>
              <input placeholder="Размер" value={variant.size} onChange={(event) => updateVariant(index, "size", event.target.value)} />
              <input placeholder="SKU размера" value={variant.sku} onChange={(event) => updateVariant(index, "sku", event.target.value)} />
              <input placeholder="Цвет" value={variant.color} onChange={(event) => updateVariant(index, "color", event.target.value)} />
              <input
                type="number"
                min="0"
                placeholder="Остаток"
                value={variant.stock_qty}
                onChange={(event) => updateVariant(index, "stock_qty", event.target.value)}
                disabled={!canInventoryWrite}
                title={canInventoryWrite ? "" : "Требуется inventory.write"}
              />
              <button type="button" onClick={() => removeVariant(index)} disabled={productForm.variants.length === 1}>Удалить размер</button>
            </div>
          ))}
          <button type="button" onClick={addVariant}>Добавить размер</button>
          <button onClick={createProduct} disabled={isBusy("create-product")}>Создать товар</button>
        </section>
      )}

      {(canProductsRead || canCustomersRead || canInventoryWrite) && (
        <section>
          <h2>Операционный контроль</h2>
          {canCustomersRead && canNotificationsRetry && (
            <button
              onClick={() => refreshOperationsAfter(
                "queue-abandoned",
                "/api/ops/abandoned-carts/queue-notifications",
                "Уведомления поставлены в очередь.",
                ["customers.read", "notifications.retry"],
              )}
              disabled={isBusy("queue-abandoned")}
            >
              Поставить уведомления по брошенным корзинам
            </button>
          )}
          {canInventoryWrite && (
            <button
              onClick={() => refreshOperationsAfter(
                "snapshot-inventory",
                "/api/ops/inventory/snapshot",
                "Снимок остатков создан.",
                ["inventory.write"],
              )}
              disabled={isBusy("snapshot-inventory")}
            >
              Сделать снимок остатков
            </button>
          )}
          {canProductsRead && (
            <>
              <h3>Низкие остатки</h3>
              {!lowStock.length && <p>Товаров с низким остатком нет.</p>}
              <div className="table">
                {lowStock.map((item) => (
                  <div className="row" key={item.variant_id}>
                    <b>{item.product_title}</b>
                    <span>{item.sku}</span>
                    <span>stock {item.stock_qty}</span>
                    <span>reserved {item.reserved_qty}</span>
                    <span>available {item.available_qty}</span>
                  </div>
                ))}
              </div>
            </>
          )}
          {canCustomersRead && (
            <>
              <h3>Брошенные корзины</h3>
              {!abandonedCarts.length && <p>Брошенных корзин нет.</p>}
              <div className="table">
                {abandonedCarts.map((cart) => (
                  <div className="row" key={cart.cart_id}>
                    <b>Cart #{cart.cart_id}</b>
                    <span>User {cart.customer_id}</span>
                    <span>{cart.telegram_id}</span>
                    <span>{cart.items_count} items</span>
                    <span>{cart.total_amount}</span>
                  </div>
                ))}
              </div>
            </>
          )}
        </section>
      )}

      <BusinessEventsPanel onUnauthorized={logout} session={session} />

      {canAuditRead && (
        <section>
          <h2>Audit log</h2>
          {!auditLogs.length && <p>Записей аудита пока нет.</p>}
          <div className="table">
            {auditLogs.slice(0, 30).map((item) => (
              <div className="row" key={item.id}>
                <b>{item.action}</b>
                <span>{item.entity_type}</span>
                <span>{item.entity_id}</span>
                <span>admin {item.admin_id}</span>
                <span>{item.payload}</span>
              </div>
            ))}
          </div>
        </section>
      )}

      {canProductsRead && (
        <section>
          <h2>Товары</h2>
          {!products.length && <p>Товары не найдены.</p>}
          <div className="table">
            {products.map((product) => (
              <div className="row" key={product.id}>
                <b>{product.title}</b>
                <span>{product.price} {product.currency}</span>
                <span>{product.active ? "active" : "hidden"}</span>
              </div>
            ))}
          </div>
        </section>
      )}

      {canOrdersRead && (
        <section>
          <h2>Заказы</h2>
          {!orders.length && <p>Заказов пока нет.</p>}
          <div className="table">
            {orders.map((order) => {
              const action = orderAction(order);
              return (
                <div className="row order" key={order.id}>
                  <b>#{order.id}</b>
                  <span>{ORDER_STATUS_LABELS[order.status] || order.status}</span>
                  <span>{order.payment_status}</span>
                  <span>{order.total_amount} {order.currency}</span>
                  {canOrdersWrite && action ? (
                    <button
                      onClick={() => handleOrderAction(order)}
                      disabled={isBusy(`order-${order.id}`)}
                    >
                      {action.label}
                    </button>
                  ) : action ? (
                    <span>Только чтение: нет orders.write</span>
                  ) : (
                    <span>Нет доступного ручного перехода</span>
                  )}
                </div>
              );
            })}
          </div>
        </section>
      )}
    </main>
  );
}

createRoot(document.getElementById("root")).render(<App />);
