import {
  DEFAULT_REQUEST_TIMEOUT_MS,
  createRequestCoordinator,
  createTimeoutController,
  mutationRequestKey,
} from "./requestPolicy.js";

export const API_BASE = import.meta.env.VITE_API_BASE || "http://localhost:8000";
const configuredTimeout = Number(import.meta.env.VITE_API_TIMEOUT_MS);
const REQUEST_TIMEOUT_MS = Number.isFinite(configuredTimeout) && configuredTimeout > 0
  ? Math.min(Math.max(configuredTimeout, 3_000), 120_000)
  : DEFAULT_REQUEST_TIMEOUT_MS;
const requestCoordinator = createRequestCoordinator();

export class AdminApiError extends Error {
  constructor(message, status = 0, options = {}) {
    super(message, options);
    this.name = "AdminApiError";
    this.status = status;
  }
}

export function getAdminToken() {
  return localStorage.getItem("admin_token") || "";
}

export function setAdminToken(token) {
  if (token) localStorage.setItem("admin_token", token);
  else localStorage.removeItem("admin_token");
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
      throw new AdminApiError("Сервер не ответил вовремя. Повторите операцию.");
    }
    if (timeout.signal.aborted) {
      throw new AdminApiError("Запрос был отменён.");
    }
    throw new AdminApiError("Нет соединения с сервером.", 0, { cause: error });
  } finally {
    timeout.cleanup();
  }
}

function authHeaders(auth, customHeaders = {}) {
  const result = { ...customHeaders };
  const token = getAdminToken();
  if (auth && token) result.Authorization = `Bearer ${token}`;
  return result;
}

export async function adminRequest(path, options = {}) {
  const {
    auth = true,
    dedupeKey,
    headers = {},
    ...requestOptions
  } = options;
  const coordinationKey = dedupeKey ?? mutationRequestKey(path, requestOptions);

  return requestCoordinator.run(coordinationKey, async () => {
    const response = await fetchWithTimeout(`${API_BASE}${path}`, {
      ...requestOptions,
      headers: authHeaders(auth, headers),
    });
    if (!response.ok) {
      if (response.status === 401 && auth) setAdminToken("");
      throw new AdminApiError(
        (await errorDetail(response)) || `HTTP ${response.status}`,
        response.status,
      );
    }
    if (response.status === 204) return null;
    const contentType = response.headers.get("content-type") || "";
    return contentType.includes("application/json") ? response.json() : response.text();
  });
}

export function adminJson(path, options = {}) {
  return adminRequest(path, {
    ...options,
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
  });
}

export async function loginAdmin(email, password) {
  const data = await adminJson("/api/admin/login", {
    method: "POST",
    auth: false,
    body: JSON.stringify({ email, password }),
  });
  setAdminToken(data.access_token);
  return data;
}

export async function uploadAdminFile(path, file, field = "file") {
  const form = new FormData();
  form.append(field, file);
  const fileIdentity = [file.name, file.size, file.lastModified, file.type].join(":");
  return adminRequest(path, {
    method: "POST",
    body: form,
    dedupeKey: `UPLOAD:${path}:${field}:${fileIdentity}`,
  });
}

export async function downloadAdminFile(path, fallbackFilename) {
  const response = await fetchWithTimeout(`${API_BASE}${path}`, {
    headers: authHeaders(true),
  });
  if (!response.ok) {
    if (response.status === 401) setAdminToken("");
    throw new AdminApiError(
      (await errorDetail(response)) || "Не удалось скачать файл",
      response.status,
    );
  }
  const disposition = response.headers.get("content-disposition") || "";
  const match = disposition.match(/filename="?([^";]+)"?/i);
  return {
    blob: await response.blob(),
    filename: match?.[1] || fallbackFilename,
  };
}
