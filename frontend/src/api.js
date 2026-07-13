const API_BASE = (import.meta.env.VITE_API_BASE || "http://localhost:8000").replace(/\/$/, "");
const DEFAULT_TIMEOUT_MS = 15000;

function getToken() {
  return localStorage.getItem("flashin_token");
}

function clearToken() {
  localStorage.removeItem("flashin_token");
}

function headers(auth = true) {
  const result = { "Content-Type": "application/json" };
  const token = getToken();
  if (auth && token) result.Authorization = `Bearer ${token}`;
  return result;
}

async function readError(res) {
  try {
    const data = await res.json();
    if (typeof data?.detail === "string") return data.detail;
    if (data?.detail) return JSON.stringify(data.detail);
    if (typeof data?.message === "string") return data.message;
  } catch {
    try {
      return await res.text();
    } catch {
      return "";
    }
  }
  return "";
}

async function request(path, options = {}) {
  const controller = new AbortController();
  const timeoutMs = options.timeoutMs || DEFAULT_TIMEOUT_MS;
  const timeoutId = window.setTimeout(() => controller.abort(), timeoutMs);

  try {
    const res = await fetch(`${API_BASE}${path}`, {
      ...options,
      signal: options.signal || controller.signal,
      headers: { ...headers(options.auth !== false), ...(options.headers || {}) },
    });

    if (res.status === 401 && options.auth !== false) {
      clearToken();
      window.dispatchEvent(new CustomEvent("flashin:auth-expired"));
    }

    if (!res.ok) {
      const detail = await readError(res);
      const error = new Error(detail || `Ошибка запроса: ${res.status}`);
      error.status = res.status;
      error.path = path;
      throw error;
    }

    if (res.status === 204) return null;

    const contentType = res.headers.get("content-type") || "";
    if (contentType.includes("application/json")) return res.json();
    return res.text();
  } catch (error) {
    if (error?.name === "AbortError") {
      throw new Error("Сервер не ответил вовремя. Проверьте соединение и повторите попытку.");
    }
    if (error instanceof TypeError) {
      throw new Error("Нет связи с сервером. Проверьте интернет-соединение.");
    }
    throw error;
  } finally {
    window.clearTimeout(timeoutId);
  }
}

export async function telegramAuth(initData) {
  const data = await request("/api/auth/telegram", {
    method: "POST",
    auth: false,
    body: JSON.stringify({ init_data: initData }),
  });
  localStorage.setItem("flashin_token", data.access_token);
  return data;
}

export function logout() {
  clearToken();
}

export async function listProducts() {
  return request("/api/products", { auth: false });
}

export async function getProduct(id) {
  return request(`/api/products/${id}`, { auth: false });
}

export async function getCart() {
  return request("/api/cart");
}

export async function addToCart(productId, variantId, quantity = 1) {
  return request("/api/cart/items", {
    method: "POST",
    body: JSON.stringify({ product_id: productId, variant_id: variantId, quantity }),
  });
}

export async function removeCartItem(itemId) {
  return request(`/api/cart/items/${itemId}`, { method: "DELETE" });
}

export async function checkout(payload) {
  return request("/api/orders/checkout", {
    method: "POST",
    body: JSON.stringify(payload),
    timeoutMs: 30000,
  });
}

export async function createPayment(orderId) {
  return request("/api/payments", {
    method: "POST",
    body: JSON.stringify({ order_id: orderId }),
    timeoutMs: 30000,
  });
}

export async function trackEvent(eventType, payload = {}) {
  try {
    return await request("/api/analytics/events", {
      method: "POST",
      body: JSON.stringify({ event_type: eventType, payload }),
      timeoutMs: 5000,
    });
  } catch {
    return null;
  }
}

export async function applyPromo(code) {
  return request("/api/cart/promo", {
    method: "POST",
    body: JSON.stringify({ code: code.trim() }),
  });
}

export async function listOrders() {
  return request("/api/orders");
}

export async function createReturn(orderId, reason) {
  return request("/api/returns", {
    method: "POST",
    body: JSON.stringify({ order_id: orderId, reason: reason.trim() }),
  });
}

export async function addWishlist(productId) {
  return request("/api/wishlist", {
    method: "POST",
    body: JSON.stringify({ product_id: productId }),
  });
}

export async function subscribeRestock(variantId) {
  return request("/api/restock/subscribe", {
    method: "POST",
    body: JSON.stringify({ variant_id: variantId }),
  });
}

export async function getRecommendations(productId) {
  return request(`/api/recommendations/${productId}`, { auth: false });
}

export async function sizeHelper(payload) {
  return request("/api/recommendations/size-helper", {
    method: "POST",
    auth: false,
    body: JSON.stringify(payload),
  });
}

export async function searchProducts(q) {
  return request(`/api/search/products?q=${encodeURIComponent(q.trim())}`, { auth: false });
}

export async function listLooks() {
  return request("/api/looks", { auth: false });
}

export async function myLoyalty() {
  return request("/api/loyalty/transactions");
}

export async function myReferralCode() {
  return request("/api/loyalty/referral-code");
}

export async function applyLoyalty(points) {
  return request("/api/cart/loyalty", {
    method: "POST",
    body: JSON.stringify({ points }),
  });
}

export async function applyReferral(code) {
  return request("/api/cart/referral", {
    method: "POST",
    body: JSON.stringify({ code: code.trim() }),
  });
}

export async function getProfile() {
  return request("/api/profile");
}

export async function getTimeline() {
  return request("/api/timeline");
}

export async function createSupportTicket(payload) {
  return request("/api/support/tickets", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function listSupportTickets() {
  return request("/api/support/tickets");
}

export async function exportPrivacyData() {
  const token = getToken();
  const controller = new AbortController();
  const timeoutId = window.setTimeout(() => controller.abort(), 30000);

  try {
    const res = await fetch(`${API_BASE}/api/privacy/export`, {
      signal: controller.signal,
      headers: token ? { Authorization: `Bearer ${token}` } : {},
    });
    if (res.status === 401) clearToken();
    if (!res.ok) throw new Error((await readError(res)) || "Не удалось выгрузить данные.");
    return res.text();
  } finally {
    window.clearTimeout(timeoutId);
  }
}

export async function createPrivacyRequest(requestType) {
  return request("/api/privacy/requests", {
    method: "POST",
    body: JSON.stringify({ request_type: requestType }),
  });
}

export async function listPrivacyRequests() {
  return request("/api/privacy/requests");
}
