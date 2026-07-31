const API = import.meta.env.VITE_API_BASE || "http://localhost:8000";

const DATASETS = Object.freeze({
  "/api/admin/products": { label: "Товары", fallback: [] },
  "/api/admin/orders": { label: "Заказы", fallback: [] },
  "/api/admin/audit-logs": { label: "Audit log", fallback: [] },
  "/api/ops/inventory/low-stock": { label: "Низкие остатки", fallback: [] },
  "/api/ops/abandoned-carts": { label: "Брошенные корзины", fallback: [] },
  "/api/support/admin/tickets": { label: "Обращения", fallback: [] },
  "/api/privacy/admin/requests": { label: "Privacy requests", fallback: [] },
  "/api/outbox": { label: "Webhook outbox", fallback: [] },
  "/api/business-analytics/summary": { label: "Аналитика", fallback: null },
  "/api/crm/profiles": { label: "CRM-профили", fallback: [] },
  "/api/moysklad/sync-logs": { label: "Логи МойСклад", fallback: [] },
  "/api/campaigns": { label: "Кампании", fallback: [] },
  "/api/admin/customers": { label: "Клиенты", fallback: [] },
  "/api/admin/moysklad/mapping-rules": { label: "Правила маппинга", fallback: [] },
  "/api/admin/moysklad/conflicts": { label: "Конфликты МойСклад", fallback: [] },
  "/api/reconciliation/stock": { label: "Сверка остатков", fallback: [] },
  "/api/fulfillment/tasks": { label: "Fulfillment", fallback: [] },
  "/api/fulfillment/sla": { label: "SLA", fallback: [] },
  "/api/webhook-destinations": { label: "Webhook destinations", fallback: [] },
});

const WAVE_TRIGGERS = new Set([
  "/api/admin/products",
  "/api/admin/audit-logs",
]);
const STATUS_EVENT = "flashin-admin-data-status";
const SESSION_EVENT = "flashin-admin-session-expired";
const MAX_ERROR_TEXT = 500;

function requestMethod(input, init) {
  return String(init?.method || (input instanceof Request ? input.method : "GET")).toUpperCase();
}

function requestUrl(input) {
  const raw = input instanceof Request ? input.url : String(input);
  return new URL(raw, window.location.href);
}

function headerValue(input, init, name) {
  const headers = new Headers(input instanceof Request ? input.headers : undefined);
  new Headers(init?.headers || undefined).forEach((value, key) => headers.set(key, value));
  return headers.get(name) || "";
}

function currentBearer(input, init) {
  const authorization = headerValue(input, init, "authorization").trim();
  if (authorization.toLowerCase().startsWith("bearer ")) return authorization.slice(7).trim();
  return String(localStorage.getItem("admin_token") || "").trim();
}

function fallbackResponse(value, sourceStatus) {
  return new Response(JSON.stringify(value), {
    status: 200,
    headers: {
      "Content-Type": "application/json; charset=utf-8",
      "Cache-Control": "no-store",
      "X-Flashin-Data-Fallback": "1",
      "X-Flashin-Source-Status": String(sourceStatus || 0),
    },
  });
}

function dispatchStatus(detail) {
  window.__flashinAdminDataStatus = detail;
  window.dispatchEvent(new CustomEvent(STATUS_EVENT, { detail }));
}

async function boundedResponseText(response) {
  try {
    return (await response.clone().text()).trim().slice(0, MAX_ERROR_TEXT);
  } catch {
    return "";
  }
}

export function installAdminDataCoordinator() {
  if (window.__flashinAdminDataCoordinator) return window.__flashinAdminDataCoordinator;

  const originalFetch = window.fetch.bind(window);
  let generation = 0;
  let batch = null;
  let sessionExpired = false;

  function expireSession(status, detail) {
    if (sessionExpired) return;
    sessionExpired = true;
    localStorage.removeItem("admin_token");
    window.dispatchEvent(new CustomEvent(SESSION_EVENT, {
      detail: { status, message: detail || "Административная сессия завершена" },
    }));
    window.setTimeout(() => window.location.reload(), 0);
  }

  function startBatch(token) {
    generation += 1;
    sessionExpired = false;
    const batchGeneration = generation;
    const failures = [];
    let completed = 0;
    const total = Object.keys(DATASETS).length;
    const requests = new Map();

    dispatchStatus({
      generation: batchGeneration,
      loading: true,
      completed,
      total,
      failures: [],
    });

    for (const [path, definition] of Object.entries(DATASETS)) {
      const request = originalFetch(`${API}${path}`, {
        method: "GET",
        headers: {
          Accept: "application/json",
          Authorization: `Bearer ${token}`,
        },
        cache: "no-store",
      })
        .then(async (response) => {
          if (response.status === 401) {
            const message = await boundedResponseText(response);
            expireSession(response.status, message);
            return response;
          }
          if (!response.ok) {
            const message = await boundedResponseText(response);
            failures.push({
              path,
              label: definition.label,
              status: response.status,
              message,
            });
            return fallbackResponse(definition.fallback, response.status);
          }
          const contentType = String(response.headers.get("content-type") || "").toLowerCase();
          if (!contentType.includes("application/json")) {
            failures.push({
              path,
              label: definition.label,
              status: response.status,
              message: "Сервер вернул неожиданный формат данных",
            });
            return fallbackResponse(definition.fallback, response.status);
          }
          return response;
        })
        .catch((error) => {
          failures.push({
            path,
            label: definition.label,
            status: 0,
            message: String(error?.message || "Сетевая ошибка").slice(0, MAX_ERROR_TEXT),
          });
          return fallbackResponse(definition.fallback, 0);
        })
        .finally(() => {
          completed += 1;
          dispatchStatus({
            generation: batchGeneration,
            loading: completed < total,
            completed,
            total,
            failures: failures.slice(),
          });
        });
      requests.set(path, request);
    }

    batch = {
      generation: batchGeneration,
      token,
      requests,
      served: new Set(),
    };
    return batch;
  }

  function ensureBatch(path, token) {
    if (!batch || batch.token !== token || (WAVE_TRIGGERS.has(path) && batch.served.has(path))) {
      return startBatch(token);
    }
    return batch;
  }

  async function coordinatedFetch(input, init = {}) {
    let url;
    try {
      url = requestUrl(input);
    } catch {
      return originalFetch(input, init);
    }
    const base = new URL(API, window.location.href);
    const path = url.pathname;
    if (
      requestMethod(input, init) !== "GET"
      || url.origin !== base.origin
      || !(path in DATASETS)
    ) {
      return originalFetch(input, init);
    }

    const token = currentBearer(input, init);
    if (!token) return originalFetch(input, init);
    const activeBatch = ensureBatch(path, token);
    activeBatch.served.add(path);
    const response = await activeBatch.requests.get(path);
    return response.clone();
  }

  window.fetch = coordinatedFetch;
  const coordinator = Object.freeze({
    datasets: DATASETS,
    statusEvent: STATUS_EVENT,
    sessionEvent: SESSION_EVENT,
    getStatus() {
      return window.__flashinAdminDataStatus || null;
    },
    restore() {
      window.fetch = originalFetch;
      batch = null;
      window.__flashinAdminDataStatus = null;
    },
  });
  window.__flashinAdminDataCoordinator = coordinator;
  return coordinator;
}
