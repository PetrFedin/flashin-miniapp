import { normalizePaymentContinuation } from "./paymentFlow.js";
import {
  DEFAULT_REQUEST_TIMEOUT_MS,
  createRequestCoordinator,
  createTimeoutController,
  mutationRequestKey,
} from "./requestPolicy.js";

const API_BASE = import.meta.env.VITE_API_BASE || "http://localhost:8000";
const ACTIVE_CART_STORAGE_KEY = "flashin_active_cart_id";
const CHECKOUT_KEY_PREFIX = "flashin_checkout_key:";
const configuredTimeout = Number(import.meta.env.VITE_API_TIMEOUT_MS);
const REQUEST_TIMEOUT_MS = Number.isFinite(configuredTimeout) && configuredTimeout > 0
  ? Math.min(Math.max(configuredTimeout, 3_000), 120_000)
  : DEFAULT_REQUEST_TIMEOUT_MS;
const requestCoordinator = createRequestCoordinator();

function getToken() {
  return localStorage.getItem("flashin_token");
}

function headers(auth = true) {
  const result = { "Content-Type": "application/json" };
  const token = getToken();
  if (auth && token) result.Authorization = `Bearer ${token}`;
  return result;
}

function createRequestId() {
  if (globalThis.crypto?.randomUUID) return globalThis.crypto.randomUUID();
  return `checkout-${Date.now()}-${Math.random().toString(36).slice(2)}-${Math.random().toString(36).slice(2)}`;
}

function checkoutKeyForActiveCart() {
  const cartId = localStorage.getItem(ACTIVE_CART_STORAGE_KEY);
  if (!cartId) throw new Error("Активная корзина не определена. Обновите корзину и повторите оформление.");
  const storageKey = `${CHECKOUT_KEY_PREFIX}${cartId}`;
  let idempotencyKey = localStorage.getItem(storageKey);
  if (!idempotencyKey) {
    idempotencyKey = createRequestId();
    localStorage.setItem(storageKey, idempotencyKey);
  }
  return { cartId, idempotencyKey, storageKey };
}

async function errorDetail(response) {
  const text = await response.text();
  if (!text) return "";
  try {
    const data = JSON.parse(text);
    if (typeof data.detail === "string") return data.detail;
    if (data.detail !== undefined) return JSON.stringify(data.detail);
    return text;
  } catch {
    return text;
  }
}

async function fetchWithTimeout(url, options = {}) {
  const {
    timeoutMs = REQUEST_TIMEOUT_MS,
    signal: externalSignal,
    ...fetchOptions
  } = options;
  const timeout = createTimeoutController(timeoutMs, externalSignal);
  try {
    return await fetch(url, { ...fetchOptions, signal: timeout.signal });
  } catch (error) {
    if (timeout.didTimeout()) {
      throw new Error("Сервер не ответил вовремя. Проверьте соединение и повторите попытку.");
    }
    if (timeout.signal.aborted) {
      throw new Error("Запрос был отменён.");
    }
    throw new Error("Не удалось связаться с сервером. Проверьте интернет-соединение.", {
      cause: error,
    });
  } finally {
    timeout.cleanup();
  }
}

async function request(path, options = {}) {
  const {
    auth = true,
    dedupeKey,
    headers: customHeaders,
    ...requestOptions
  } = options;
  const coordinationKey = dedupeKey ?? mutationRequestKey(path, requestOptions);

  return requestCoordinator.run(coordinationKey, async () => {
    const response = await fetchWithTimeout(`${API_BASE}${path}`, {
      ...requestOptions,
      headers: { ...headers(auth), ...(customHeaders || {}) },
    });
    if (!response.ok) {
      if (response.status === 401 && auth) {
        localStorage.removeItem("flashin_token");
      }
      throw new Error((await errorDetail(response)) || `Request failed: ${response.status}`);
    }
    if (response.status === 204) return null;
    return response.json();
  });
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

export async function searchProducts(query) {
  return request(`/api/search/products?q=${encodeURIComponent(query)}`, { auth: false });
}

export async function getCart() {
  const cart = await request("/api/cart");
  if (cart?.id) localStorage.setItem(ACTIVE_CART_STORAGE_KEY, String(cart.id));
  return cart;
}

export async function addToCart(productId, variantId, quantity = 1) {
  return request("/api/cart/items", {
    method: "POST",
    body: JSON.stringify({ product_id: productId, variant_id: variantId, quantity }),
  });
}

export async function updateCartItem(itemId, quantity) {
  return request(`/api/cart/items/${itemId}?quantity=${encodeURIComponent(quantity)}`, {
    method: "PATCH",
  });
}

export async function removeCartItem(itemId) {
  return request(`/api/cart/items/${itemId}`, { method: "DELETE" });
}

export async function applyPromo(code) {
  return request("/api/cart/promo", {
    method: "POST",
    body: JSON.stringify({ code }),
  });
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

export async function checkout(payload) {
  const { idempotencyKey, storageKey } = checkoutKeyForActiveCart();
  const order = await request("/api/orders/checkout", {
    method: "POST",
    headers: { "Idempotency-Key": idempotencyKey },
    body: JSON.stringify(payload),
  });
  localStorage.removeItem(storageKey);
  return order;
}

export async function listOrders() {
  return request("/api/orders");
}

export async function getOrder(orderId) {
  return request(`/api/orders/${orderId}`);
}

export async function cancelOrder(orderId) {
  return request(`/api/orders/${orderId}/cancel`, { method: "POST" });
}

export async function createPayment(orderId) {
  const payment = await request("/api/payments", {
    method: "POST",
    body: JSON.stringify({ order_id: orderId }),
  });
  return normalizePaymentContinuation(payment, orderId);
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

export async function listLooks() {
  return request("/api/looks", { auth: false });
}

export async function getRecommendations(productId) {
  return request(`/api/recommendations/${productId}`, { auth: false });
}

export async function sizeHelper(productId, payload) {
  return request(`/api/recommendations/size-helper/${productId}`, {
    method: "POST",
    auth: false,
    body: JSON.stringify(payload),
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

export async function myLoyalty() {
  return request("/api/loyalty/transactions");
}

export async function myReferralCode() {
  return request("/api/loyalty/referral-code");
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

export async function downloadPrivacyData() {
  const response = await fetchWithTimeout(`${API_BASE}/api/privacy/export`, {
    headers: { Authorization: `Bearer ${getToken()}` },
  });
  if (!response.ok) {
    throw new Error((await errorDetail(response)) || "Не удалось экспортировать данные");
  }
  const blob = await response.blob();
  const disposition = response.headers.get("content-disposition") || "";
  const match = disposition.match(/filename="?([^";]+)"?/i);
  return { blob, filename: match?.[1] || "flashin_customer_export.json" };
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
