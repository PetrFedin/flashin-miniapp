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

export function listCatalogProducts(filters = {}) {
  return catalogRequest(`/api/catalog/products${catalogQuery(filters)}`, { method: "GET" });
}

export function getCatalogProduct(productId) {
  return catalogRequest(`/api/catalog/products/${Number(productId)}`, { method: "GET" });
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

export function createDemandRequest(product, variantId, quantity = 1, notes = "") {
  const requestType = product?.merchandising?.configured_availability_status
    || product?.merchandising?.availability_status;
  const variant = (product?.variants || []).find((item) => Number(item.id) === Number(variantId));
  return catalogRequest("/api/catalog/demand-requests", {
    method: "POST",
    body: JSON.stringify({
      product_id: Number(product.id),
      variant_id: variantId ? Number(variantId) : null,
      request_type: requestType,
      quantity: Number(quantity || 1),
      requested_size: variant?.size || "",
      requested_color: variant?.color || "",
      notes: String(notes || "").trim(),
    }),
  });
}

export function listMyDemandRequests() {
  return catalogRequest("/api/catalog/demand-requests/me", { method: "GET" });
}

export function cancelDemandRequest(requestId) {
  return catalogRequest(`/api/catalog/demand-requests/${Number(requestId)}/cancel`, {
    method: "PATCH",
  });
}
