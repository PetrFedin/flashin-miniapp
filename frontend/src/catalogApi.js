const API_BASE = import.meta.env.VITE_API_BASE || "http://localhost:8000";

function authHeaders() {
  const token = localStorage.getItem("flashin_token");
  return {
    "Content-Type": "application/json",
    ...(token ? { Authorization: `Bearer ${token}` } : {}),
  };
}

async function catalogRequest(path, options = {}) {
  const response = await fetch(`${API_BASE}${path}`, {
    ...options,
    headers: { ...authHeaders(), ...(options.headers || {}) },
  });
  if (!response.ok) {
    const text = await response.text();
    let detail = text;
    try {
      const parsed = JSON.parse(text);
      detail = typeof parsed.detail === "string" ? parsed.detail : JSON.stringify(parsed.detail ?? parsed);
    } catch {
      // Keep the bounded raw response as diagnostic text.
    }
    if (response.status === 401) localStorage.removeItem("flashin_token");
    throw new Error(detail || `Catalog request failed: ${response.status}`);
  }
  if (response.status === 204) return null;
  return response.json();
}

function catalogQuery(filters = {}) {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(filters)) {
    if (value === "" || value === null || value === undefined) continue;
    params.set(key, String(value));
  }
  const query = params.toString();
  return query ? `?${query}` : "";
}

function applyPricing(product, pricing) {
  if (!product || !pricing) return product;
  return {
    ...product,
    price: Number(pricing.effective_price),
    old_price: pricing.compare_at_price == null ? null : Number(pricing.compare_at_price),
    pricing,
  };
}

async function loadCatalogPricing(productIds = []) {
  const ids = [...new Set(productIds.map(Number).filter((value) => Number.isInteger(value) && value > 0))];
  if (!ids.length) return new Map();

  const batches = [];
  for (let index = 0; index < ids.length; index += 100) {
    const params = new URLSearchParams();
    for (const productId of ids.slice(index, index + 100)) params.append("product_id", String(productId));
    batches.push(catalogRequest(`/api/catalog/pricing?${params.toString()}`, { method: "GET" }));
  }
  const rows = (await Promise.all(batches)).flat();
  return new Map(rows.map((row) => [Number(row.product_id), row]));
}

export async function listCatalogProducts(filters = {}) {
  const products = await catalogRequest(`/api/catalog/products${catalogQuery(filters)}`, { method: "GET" });
  const pricing = await loadCatalogPricing((products || []).map((product) => product.id));
  return (products || []).map((product) => applyPricing(product, pricing.get(Number(product.id))));
}

export async function getCatalogProduct(productId) {
  const id = Number(productId);
  const [product, pricing] = await Promise.all([
    catalogRequest(`/api/catalog/products/${id}`, { method: "GET" }),
    catalogRequest(`/api/catalog/products/${id}/pricing`, { method: "GET" }),
  ]);
  return applyPricing(product, pricing);
}

export function getProductShare(productId) {
  return catalogRequest(`/api/catalog/products/${Number(productId)}/share`, { method: "GET" });
}

export function listProductFeedback(productId) {
  return catalogRequest(`/api/catalog/products/${Number(productId)}/feedback`, { method: "GET" });
}

export function submitProductFeedback(productId, rating, comment) {
  return catalogRequest(`/api/catalog/products/${Number(productId)}/feedback`, {
    method: "POST",
    body: JSON.stringify({ rating: Number(rating), comment: String(comment || "").trim() }),
  });
}

export function createShowroomAppointment(productId, startsAt, notes = "") {
  return catalogRequest("/api/catalog/showroom/appointments", {
    method: "POST",
    body: JSON.stringify({
      product_id: Number(productId),
      starts_at: new Date(startsAt).toISOString(),
      duration_minutes: 30,
      notes: String(notes || "").trim(),
    }),
  });
}

export function listMyShowroomAppointments() {
  return catalogRequest("/api/catalog/showroom/appointments/me", { method: "GET" });
}

export function listIntentEligibleProducts() {
  return catalogRequest("/api/catalog/intents/eligible-products", { method: "GET" });
}

export function createProductIntent(payload) {
  return catalogRequest("/api/catalog/intents", {
    method: "POST",
    body: JSON.stringify({
      product_id: Number(payload.product_id),
      variant_id: payload.variant_id ? Number(payload.variant_id) : null,
      quantity: Number(payload.quantity || 1),
      requested_size: String(payload.requested_size || "").trim(),
      requested_color: String(payload.requested_color || "").trim(),
      notes: String(payload.notes || "").trim(),
    }),
  });
}

export function listMyProductIntents() {
  return catalogRequest("/api/catalog/intents/me", { method: "GET" });
}
