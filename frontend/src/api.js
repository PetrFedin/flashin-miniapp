const API_BASE = import.meta.env.VITE_API_BASE || "http://localhost:8000";

function getToken() {
  return localStorage.getItem("flashin_token");
}

function headers(auth = true) {
  const h = { "Content-Type": "application/json" };
  const token = getToken();
  if (auth && token) h.Authorization = `Bearer ${token}`;
  return h;
}

async function request(path, options = {}) {
  const res = await fetch(`${API_BASE}${path}`, {
    ...options,
    headers: { ...headers(options.auth !== false), ...(options.headers || {}) },
  });
  if (!res.ok) {
    let detail = "";
    try {
      const data = await res.json();
      detail = typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail);
    } catch {
      detail = await res.text();
    }
    throw new Error(detail || `Request failed: ${res.status}`);
  }
  return res.json();
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
  });
}

export async function createPayment(orderId) {
  return request("/api/payments", {
    method: "POST",
    body: JSON.stringify({ order_id: orderId }),
  });
}

export async function trackEvent(eventType, payload = {}) {
  try {
    return await request("/api/analytics/events", {
      method: "POST",
      body: JSON.stringify({ event_type: eventType, payload }),
    });
  } catch {
    return null;
  }
}

export async function applyPromo(code) {
  return request("/api/cart/promo", {
    method: "POST",
    body: JSON.stringify({ code }),
  });
}

export async function listOrders() {
  return request("/api/orders");
}

export async function createReturn(orderId, reason) {
  return request("/api/returns", {
    method: "POST",
    body: JSON.stringify({ order_id: orderId, reason }),
  });
}

export async function listWishlist() {
  return request("/api/wishlist");
}

export async function addWishlist(productId) {
  return request("/api/wishlist", {
    method: "POST",
    body: JSON.stringify({ product_id: productId }),
  });
}

export async function removeWishlist(productId) {
  return request(`/api/wishlist/${productId}`, { method: "DELETE" });
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
  return request(`/api/search/products?q=${encodeURIComponent(q)}`, { auth: false });
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
    body: JSON.stringify({ code }),
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
  const token = localStorage.getItem("flashin_token");
  const res = await fetch(`${API_BASE}/api/privacy/export`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  if (!res.ok) throw new Error(await res.text());
  return res.text();
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
