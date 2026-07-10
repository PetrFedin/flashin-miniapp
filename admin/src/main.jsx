import React, { useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import "./style.css";

const API = import.meta.env.VITE_API_BASE || "http://localhost:8000";

function authHeaders() {
  const token = localStorage.getItem("admin_token");
  return { "Content-Type": "application/json", Authorization: `Bearer ${token}` };
}

async function api(path, options = {}) {
  const res = await fetch(`${API}${path}`, {
    ...options,
    headers: { ...authHeaders(), ...(options.headers || {}) },
  });
  if (!res.ok) throw new Error(await res.text());
  const ct = res.headers.get("content-type") || "";
  if (ct.includes("application/json")) return res.json();
  return res.text();
}

function App() {
  const [token, setToken] = useState(localStorage.getItem("admin_token") || "");
  const [email, setEmail] = useState("admin@flashin.store");
  const [password, setPassword] = useState("");
  const [products, setProducts] = useState([]);
  const [orders, setOrders] = useState([]);
  const [promocode, setPromocode] = useState({ code: "", discount_type: "percent", discount_value: 10, min_amount: 0 });
  const [productForm, setProductForm] = useState({
    sku: "",
    title: "",
    slug: "",
    brand: "FLASHIN",
    description: "",
    price: 0,
    currency: "RUB",
    category: "Clothing",
    gender: "unisex",
    images: [],
    variants: [{ size: "M", sku: "", stock_qty: 1, color: "" }]
  });
  const [error, setError] = useState("");
  const [auditLogs, setAuditLogs] = useState([]);
  const [lowStock, setLowStock] = useState([]);
  const [abandonedCarts, setAbandonedCarts] = useState([]);
  const [tickets, setTickets] = useState([]);
  const [privacyRequests, setPrivacyRequests] = useState([]);
  const [outboxRows, setOutboxRows] = useState([]);
  const [analytics, setAnalytics] = useState(null);
  const [crmProfiles, setCrmProfiles] = useState([]);
  const [moyskladLogs, setMoyskladLogs] = useState([]);
  const [campaigns, setCampaigns] = useState([]);
  const [campaignForm, setCampaignForm] = useState({ name: "", segment: "all", message: "" });
  const [lookForm, setLookForm] = useState({ title: "", description: "", product_ids: "" });
  const [customers, setCustomers] = useState([]);
  const [mappingRules, setMappingRules] = useState([]);
  const [moyskladConflicts, setMoyskladConflicts] = useState([]);
  const [reconciliation, setReconciliation] = useState([]);
  const [customerTimeline, setCustomerTimeline] = useState([]);
  const [fulfillmentTasks, setFulfillmentTasks] = useState([]);
  const [slaEvents, setSlaEvents] = useState([]);
  const [webhookDestinations, setWebhookDestinations] = useState([]);
  const [webhookForm, setWebhookForm] = useState({ name: "", url: "", event_type: "order.paid", active: true, signing_secret: "" });
  const [mappingForm, setMappingForm] = useState({ source_field: "size", source_value: "", target_field: "size", target_value: "", active: true });

  async function login() {
    try {
      setError("");
      const res = await fetch(`${API}/api/admin/login`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email, password }),
      });
      if (!res.ok) throw new Error(await res.text());
      const data = await res.json();
      localStorage.setItem("admin_token", data.access_token);
      setToken(data.access_token);
    } catch (e) {
      setError(e.message);
    }
  }

  async function loadOps() {
    try {
      setAuditLogs(await api("/api/admin/audit-logs"));
      setLowStock(await api("/api/ops/inventory/low-stock"));
      setAbandonedCarts(await api("/api/ops/abandoned-carts"));
      setTickets(await api("/api/support/admin/tickets"));
      setPrivacyRequests(await api("/api/privacy/admin/requests"));
      setOutboxRows(await api("/api/outbox"));
      setAnalytics(await api("/api/business-analytics/summary"));
      setCrmProfiles(await api("/api/crm/profiles"));
      setMoyskladLogs(await api("/api/moysklad/sync-logs"));
      setCampaigns(await api("/api/campaigns"));
      setCustomers(await api("/api/admin/customers"));
      setMappingRules(await api("/api/admin/moysklad/mapping-rules"));
      setMoyskladConflicts(await api("/api/admin/moysklad/conflicts"));
      setReconciliation(await api("/api/reconciliation/stock"));
      setFulfillmentTasks(await api("/api/fulfillment/tasks"));
      setSlaEvents(await api("/api/fulfillment/sla"));
      setWebhookDestinations(await api("/api/webhook-destinations"));
    } catch (e) {
      setError(e.message);
    }
  }

  async function updateFulfillment(id, status) {
    await api(`/api/fulfillment/tasks/${id}`, { method: "PATCH", body: JSON.stringify({ status }) });
    await loadOps();
  }

  async function createWebhookDestination() {
    await api("/api/webhook-destinations", { method: "POST", body: JSON.stringify(webhookForm) });
    setWebhookForm({ name: "", url: "", event_type: "order.paid", active: true, signing_secret: "" });
    await loadOps();
  }

  async function createMappingRule() {
    await api("/api/admin/moysklad/mapping-rules", { method: "POST", body: JSON.stringify(mappingForm) });
    setMappingForm({ source_field: "size", source_value: "", target_field: "size", target_value: "", active: true });
    await loadOps();
  }

  async function createCampaign() {
    await api("/api/campaigns", { method: "POST", body: JSON.stringify(campaignForm) });
    setCampaignForm({ name: "", segment: "all", message: "" });
    await loadOps();
  }

  async function scheduleCampaign(id) {
    const date = prompt("ISO date, e.g. 2026-06-01T12:00:00");
    if (!date) return;
    await api(`/api/campaigns/${id}/schedule`, { method: "POST", body: JSON.stringify({ scheduled_at: date }) });
    await loadOps();
  }

  async function loadCustomerTimeline(customerId) {
    setCustomerTimeline(await api(`/api/timeline/admin/customers/${customerId}`));
  }

  async function queueCampaign(id) {
    await api(`/api/campaigns/${id}/queue`, { method: "POST" });
    await loadOps();
  }

  async function configureMeili() {
    await api("/api/search/admin/configure-meili", { method: "POST" });
  }

  async function rebuildSearch() {
    await api("/api/search/admin/rebuild", { method: "POST" });
  }

  async function createLook() {
    await api("/api/looks", {
      method: "POST",
      body: JSON.stringify({
        title: lookForm.title,
        description: lookForm.description,
        product_ids: lookForm.product_ids.split(",").map(x => Number(x.trim())).filter(Boolean)
      })
    });
    setLookForm({ title: "", description: "", product_ids: "" });
  }

  async function syncMoySklad() {
    await api("/api/moysklad/sync", { method: "POST" });
    await loadOps();
    await load();
  }

  async function rebuildRecommendations() {
    await api("/api/recommendations/admin/rebuild", { method: "POST" });
  }

  async function recomputeCrm() {
    await api("/api/crm/recompute", { method: "POST" });
    await loadOps();
  }

  async function queueAbandoned() {
    await api("/api/ops/abandoned-carts/queue-notifications", { method: "POST" });
    await loadOps();
  }

  async function snapshotInventory() {
    await api("/api/ops/inventory/snapshot", { method: "POST" });
    await loadOps();
  }

  async function load() {
    try {
      setProducts(await api("/api/admin/products"));
      setOrders(await api("/api/admin/orders"));
    } catch (e) {
      setError(e.message);
    }
  }

  useEffect(() => { if (token) { load(); loadOps(); } }, [token]);

  async function updateOrder(orderId, status) {
    await api(`/api/admin/orders/${orderId}`, { method: "PATCH", body: JSON.stringify({ status }) });
    await load();
  }

  async function createPromo() {
    await api("/api/admin/promocodes", { method: "POST", body: JSON.stringify(promocode) });
    setPromocode({ code: "", discount_type: "percent", discount_value: 10, min_amount: 0 });
  }


async function uploadImage(file) {
  const form = new FormData();
  form.append("file", file);
  const res = await fetch(`${API}/api/media/upload`, {
    method: "POST",
    headers: { Authorization: `Bearer ${localStorage.getItem("admin_token")}` },
    body: form,
  });
  if (!res.ok) throw new Error(await res.text());
  const asset = await res.json();
  setProductForm({ ...productForm, images: [...productForm.images, asset.url] });
}

async function createProduct() {
  await api("/api/admin/products", {
    method: "POST",
    body: JSON.stringify({
      ...productForm,
      price: Number(productForm.price),
      variants: productForm.variants.map(v => ({ ...v, stock_qty: Number(v.stock_qty) }))
    }),
  });
  setProductForm({
    sku: "",
    title: "",
    slug: "",
    brand: "FLASHIN",
    description: "",
    price: 0,
    currency: "RUB",
    category: "Clothing",
    gender: "unisex",
    images: [],
    variants: [{ size: "M", sku: "", stock_qty: 1, color: "" }]
  });
  await load();
}

  async function importCsv(file) {
    const form = new FormData();
    form.append("file", file);
    const res = await fetch(`${API}/api/admin/products/import-csv`, {
      method: "POST",
      headers: { Authorization: `Bearer ${localStorage.getItem("admin_token")}` },
      body: form,
    });
    if (!res.ok) throw new Error(await res.text());
    await load();
  }

  if (!token) {
    return <main className="login">
      <h1>FLASHIN Admin</h1>
      {error && <div className="error">{error}</div>}
      <input value={email} onChange={e => setEmail(e.target.value)} placeholder="email" />
      <input value={password} onChange={e => setPassword(e.target.value)} placeholder="password" type="password" />
      <button onClick={login}>Войти</button>
    </main>;
  }

  return <main>
    <header>
      <h1>FLASHIN Admin</h1>
      <button onClick={() => { localStorage.removeItem("admin_token"); setToken(""); }}>Выйти</button>
    </header>
    {error && <div className="error">{error}</div>}

    <section>
      <h2>Импорт товаров CSV</h2>
      <input type="file" accept=".csv" onChange={(e) => e.target.files?.[0] && importCsv(e.target.files[0])} />
      <a href={`${API}/api/admin/orders/export-csv`} target="_blank">Скачать заказы CSV</a>
    </section>

    <section>
      <h2>Промокод</h2>
      <input placeholder="CODE" value={promocode.code} onChange={e => setPromocode({ ...promocode, code: e.target.value.toUpperCase() })} />
      <input type="number" value={promocode.discount_value} onChange={e => setPromocode({ ...promocode, discount_value: Number(e.target.value) })} />
      <button onClick={createPromo}>Создать</button>
    </section>


<section>
  <h2>Создать товар</h2>
  <div className="form-grid">
    <input placeholder="SKU" value={productForm.sku} onChange={e => setProductForm({ ...productForm, sku: e.target.value })} />
    <input placeholder="Название" value={productForm.title} onChange={e => setProductForm({ ...productForm, title: e.target.value })} />
    <input placeholder="slug" value={productForm.slug} onChange={e => setProductForm({ ...productForm, slug: e.target.value })} />
    <input placeholder="brand" value={productForm.brand} onChange={e => setProductForm({ ...productForm, brand: e.target.value })} />
    <input type="number" placeholder="Цена" value={productForm.price} onChange={e => setProductForm({ ...productForm, price: e.target.value })} />
    <input placeholder="Категория" value={productForm.category} onChange={e => setProductForm({ ...productForm, category: e.target.value })} />
  </div>
  <textarea placeholder="Описание" value={productForm.description} onChange={e => setProductForm({ ...productForm, description: e.target.value })} />
  <h3>Фото</h3>
  <input type="file" accept="image/*" onChange={(e) => e.target.files?.[0] && uploadImage(e.target.files[0])} />
  <div className="image-list">{productForm.images.map(url => <img key={url} src={url} />)}</div>
  <h3>Размеры</h3>
  {productForm.variants.map((v, idx) => <div className="form-grid" key={idx}>
    <input placeholder="Размер" value={v.size} onChange={e => {
      const variants = [...productForm.variants]; variants[idx].size = e.target.value; setProductForm({ ...productForm, variants });
    }} />
    <input placeholder="SKU размера" value={v.sku} onChange={e => {
      const variants = [...productForm.variants]; variants[idx].sku = e.target.value; setProductForm({ ...productForm, variants });
    }} />
    <input placeholder="Цвет" value={v.color} onChange={e => {
      const variants = [...productForm.variants]; variants[idx].color = e.target.value; setProductForm({ ...productForm, variants });
    }} />
    <input type="number" placeholder="Остаток" value={v.stock_qty} onChange={e => {
      const variants = [...productForm.variants]; variants[idx].stock_qty = e.target.value; setProductForm({ ...productForm, variants });
    }} />
  </div>)}
  <button onClick={() => setProductForm({ ...productForm, variants: [...productForm.variants, { size: "", sku: "", stock_qty: 1, color: "" }] })}>Добавить размер</button>
  <button onClick={createProduct}>Создать товар</button>
</section>


<section>
  <h2>Операционный контроль</h2>
  <button onClick={queueAbandoned}>Поставить уведомления по брошенным корзинам</button>
  <button onClick={snapshotInventory}>Сделать снимок остатков</button>
  <h3>Низкие остатки</h3>
  <div className="table">
    {lowStock.map(x => <div className="row" key={x.variant_id}>
      <b>{x.product_title}</b>
      <span>{x.sku}</span>
      <span>stock {x.stock_qty}</span>
      <span>reserved {x.reserved_qty}</span>
      <span>available {x.available_qty}</span>
    </div>)}
  </div>
  <h3>Брошенные корзины</h3>
  <div className="table">
    {abandonedCarts.map(x => <div className="row" key={x.cart_id}>
      <b>Cart #{x.cart_id}</b>
      <span>User {x.customer_id}</span>
      <span>{x.telegram_id}</span>
      <span>{x.items_count} items</span>
      <span>{x.total_amount}</span>
    </div>)}
  </div>
</section>

<section>
  <h2>Audit log</h2>
  <div className="table">
    {auditLogs.slice(0, 30).map(x => <div className="row" key={x.id}>
      <b>{x.action}</b>
      <span>{x.entity_type}</span>
      <span>{x.entity_id}</span>
      <span>admin {x.admin_id}</span>
      <span>{x.payload}</span>
    </div>)}
  </div>
</section>

    <section>
      <h2>Товары</h2>
      <div className="table">
        {products.map(p => <div className="row" key={p.id}>
          <b>{p.title}</b>
          <span>{p.price} {p.currency}</span>
          <span>{p.active ? "active" : "hidden"}</span>
        </div>)}
      </div>
    </section>

    <section>
      <h2>Заказы</h2>
      <div className="table">
        {orders.map(o => <div className="row order" key={o.id}>
          <b>#{o.id}</b>
          <span>{o.status}</span>
          <span>{o.payment_status}</span>
          <span>{o.total_amount} {o.currency}</span>
          <select value={o.status} onChange={e => updateOrder(o.id, e.target.value)}>
            {["created","payment_created","paid","assembling","ready","shipped","completed","cancelled","refund_requested","refunded"].map(s => <option key={s}>{s}</option>)}
          </select>
        </div>)}
      </div>
    </section>
  </main>;
}

createRoot(document.getElementById("root")).render(<App />);
