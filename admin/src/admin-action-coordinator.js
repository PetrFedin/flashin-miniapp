const API = import.meta.env.VITE_API_BASE || "http://localhost:8000";

const MUTATION_METHODS = new Set(["POST", "PUT", "PATCH", "DELETE"]);
const AUTH_TRANSITION_PATHS = new Set([
  "/api/admin/login",
  "/api/admin/logout",
  "/api/admin/mfa/setup/start",
  "/api/admin/mfa/setup/confirm",
]);
const ACTION_EVENT = "flashin-admin-action-status";
const SESSION_EVENT = "flashin-admin-session-expired";
const MAX_ERROR_TEXT = 1000;
const MAX_FINGERPRINT_BODY = 100_000;

function requestMethod(input, init) {
  return String(init?.method || (input instanceof Request ? input.method : "GET")).toUpperCase();
}

function requestUrl(input) {
  const raw = input instanceof Request ? input.url : String(input);
  return new URL(raw, window.location.href);
}

function mergedHeaders(input, init) {
  const headers = new Headers(input instanceof Request ? input.headers : undefined);
  new Headers(init?.headers || undefined).forEach((value, key) => headers.set(key, value));
  return headers;
}

function bearerToken(input, init) {
  const authorization = mergedHeaders(input, init).get("authorization") || "";
  if (authorization.toLowerCase().startsWith("bearer ")) {
    return authorization.slice(7).trim();
  }
  return String(localStorage.getItem("admin_token") || "").trim();
}

function fileFingerprint(file) {
  return [
    "file",
    file.name,
    file.size,
    file.type,
    file.lastModified,
  ].join(":");
}

function bodyFingerprint(body) {
  if (body === undefined || body === null) return "empty";
  if (typeof body === "string") {
    return `text:${body.slice(0, MAX_FINGERPRINT_BODY)}`;
  }
  if (body instanceof URLSearchParams) {
    return `params:${body.toString().slice(0, MAX_FINGERPRINT_BODY)}`;
  }
  if (body instanceof FormData) {
    const entries = [];
    for (const [key, value] of body.entries()) {
      entries.push(
        `${key}=${value instanceof File ? fileFingerprint(value) : String(value)}`,
      );
    }
    entries.sort();
    return `form:${entries.join("&").slice(0, MAX_FINGERPRINT_BODY)}`;
  }
  if (body instanceof Blob) {
    return `blob:${body.type}:${body.size}`;
  }
  if (body instanceof ArrayBuffer) {
    return `array-buffer:${body.byteLength}`;
  }
  if (ArrayBuffer.isView(body)) {
    return `typed-array:${body.constructor.name}:${body.byteLength}`;
  }
  return "unfingerprintable";
}

function actionKey({ method, url, token, body }) {
  if (body === "unfingerprintable") return null;
  return [method, url.href, token, body].join("\n");
}

function dispatchAction(detail) {
  window.__flashinAdminActionStatus = detail;
  window.dispatchEvent(new CustomEvent(ACTION_EVENT, { detail }));
}

async function boundedResponseText(response) {
  try {
    return (await response.clone().text()).trim().slice(0, MAX_ERROR_TEXT);
  } catch {
    return "";
  }
}

function sessionFingerprint(token) {
  if (!token) return "anonymous";
  return `${token.length}:${token.slice(0, 6)}:${token.slice(-6)}`;
}

export function installAdminActionCoordinator() {
  if (window.__flashinAdminActionCoordinator) {
    return window.__flashinAdminActionCoordinator;
  }

  const delegatedFetch = window.fetch.bind(window);
  const inFlight = new Map();
  let sequence = 0;
  let sessionExpired = false;

  function expireSession(status, message) {
    if (sessionExpired) return;
    sessionExpired = true;
    localStorage.removeItem("admin_token");
    window.dispatchEvent(new CustomEvent(SESSION_EVENT, {
      detail: { status, message: message || "Административная сессия завершена" },
    }));
    window.setTimeout(() => window.location.reload(), 0);
  }

  function snapshot(last = null) {
    const active = Array.from(inFlight.values()).map((item) => ({
      id: item.id,
      method: item.method,
      path: item.path,
      startedAt: item.startedAt,
      duplicates: item.duplicates,
    }));
    dispatchAction({
      active,
      activeCount: active.length,
      last,
    });
  }

  async function coordinatedFetch(input, init = {}) {
    let url;
    try {
      url = requestUrl(input);
    } catch {
      return delegatedFetch(input, init);
    }
    const method = requestMethod(input, init);
    const base = new URL(API, window.location.href);
    if (!MUTATION_METHODS.has(method) || url.origin !== base.origin) {
      return delegatedFetch(input, init);
    }

    const token = bearerToken(input, init);
    const body = input instanceof Request
      ? "unfingerprintable"
      : bodyFingerprint(init.body);
    const key = actionKey({ method, url, token, body });
    if (!key) return delegatedFetch(input, init);

    const existing = inFlight.get(key);
    if (existing) {
      existing.duplicates += 1;
      snapshot({
        id: existing.id,
        type: "deduplicated",
        method,
        path: url.pathname,
        duplicates: existing.duplicates,
      });
      const shared = await existing.promise;
      return shared.clone();
    }

    const id = ++sequence;
    const startedAt = Date.now();
    const record = {
      id,
      method,
      path: url.pathname,
      token,
      session: sessionFingerprint(token),
      startedAt,
      duplicates: 0,
      promise: null,
    };

    const promise = delegatedFetch(input, init)
      .then(async (response) => {
        if (response.status === 401 && token && !AUTH_TRANSITION_PATHS.has(url.pathname)) {
          const message = await boundedResponseText(response);
          expireSession(response.status, message);
        }

        const currentToken = String(localStorage.getItem("admin_token") || "").trim();
        if (
          token
          && !AUTH_TRANSITION_PATHS.has(url.pathname)
          && currentToken !== token
        ) {
          throw new DOMException("Stale admin mutation response", "AbortError");
        }
        return response;
      })
      .then((response) => {
        snapshot({
          id,
          type: response.ok ? "succeeded" : "failed",
          method,
          path: url.pathname,
          status: response.status,
          durationMs: Date.now() - startedAt,
          duplicates: record.duplicates,
        });
        return response;
      })
      .catch((error) => {
        snapshot({
          id,
          type: "failed",
          method,
          path: url.pathname,
          status: 0,
          durationMs: Date.now() - startedAt,
          duplicates: record.duplicates,
          message: String(error?.message || "Сетевая ошибка").slice(0, MAX_ERROR_TEXT),
        });
        throw error;
      })
      .finally(() => {
        if (inFlight.get(key) === record) {
          inFlight.delete(key);
          snapshot();
        }
      });

    record.promise = promise;
    inFlight.set(key, record);
    snapshot({
      id,
      type: "started",
      method,
      path: url.pathname,
      duplicates: 0,
    });

    const response = await promise;
    return response.clone();
  }

  window.fetch = coordinatedFetch;
  const coordinator = Object.freeze({
    actionEvent: ACTION_EVENT,
    sessionEvent: SESSION_EVENT,
    getStatus() {
      return window.__flashinAdminActionStatus || {
        active: [],
        activeCount: 0,
        last: null,
      };
    },
    restore() {
      window.fetch = delegatedFetch;
      inFlight.clear();
      window.__flashinAdminActionStatus = null;
    },
  });
  window.__flashinAdminActionCoordinator = coordinator;
  return coordinator;
}
