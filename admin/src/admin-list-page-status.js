const API = import.meta.env.VITE_API_BASE || "http://localhost:8000";
const PAGE_EVENT = "flashin-admin-list-page-status";
const LIST_LABELS = Object.freeze({
  "/api/admin/products": "Товары",
  "/api/admin/orders": "Заказы",
  "/api/admin/audit-logs": "Audit log",
  "/api/admin/customers": "Клиенты",
  "/api/admin/moysklad/mapping-rules": "Правила маппинга",
  "/api/admin/moysklad/conflicts": "Конфликты МойСклад",
});

function requestMethod(input, init) {
  return String(init?.method || (input instanceof Request ? input.method : "GET")).toUpperCase();
}

function requestUrl(input) {
  const raw = input instanceof Request ? input.url : String(input);
  return new URL(raw, window.location.href);
}

function dispatchStatus(notices) {
  const detail = { notices: Array.from(notices.values()) };
  window.__flashinAdminListPageStatus = detail;
  window.dispatchEvent(new CustomEvent(PAGE_EVENT, { detail }));
}

export function installAdminListPageStatus() {
  if (window.__flashinAdminListPageStatusCoordinator) {
    return window.__flashinAdminListPageStatusCoordinator;
  }

  const delegatedFetch = window.fetch.bind(window);
  const notices = new Map();

  async function pageAwareFetch(input, init = {}) {
    let url;
    try {
      url = requestUrl(input);
    } catch {
      return delegatedFetch(input, init);
    }
    const base = new URL(API, window.location.href);
    if (
      requestMethod(input, init) !== "GET"
      || url.origin !== base.origin
      || !(url.pathname in LIST_LABELS)
    ) {
      return delegatedFetch(input, init);
    }

    const response = await delegatedFetch(input, init);
    const hasMore = response.headers.get("x-has-more") === "true";
    if (response.ok && hasMore) {
      const limit = Number(response.headers.get("x-page-limit") || 0);
      const offset = Number(response.headers.get("x-page-offset") || 0);
      notices.set(url.pathname, {
        path: url.pathname,
        label: LIST_LABELS[url.pathname],
        limit: Number.isSafeInteger(limit) && limit > 0 ? limit : null,
        offset: Number.isSafeInteger(offset) && offset >= 0 ? offset : 0,
      });
    } else {
      notices.delete(url.pathname);
    }
    dispatchStatus(notices);
    return response;
  }

  window.fetch = pageAwareFetch;
  const coordinator = Object.freeze({
    pageEvent: PAGE_EVENT,
    getStatus() {
      return window.__flashinAdminListPageStatus || { notices: [] };
    },
    restore() {
      window.fetch = delegatedFetch;
      notices.clear();
      window.__flashinAdminListPageStatus = null;
    },
  });
  window.__flashinAdminListPageStatusCoordinator = coordinator;
  return coordinator;
}
