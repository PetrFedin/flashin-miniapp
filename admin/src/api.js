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
const PUBLIC_REQUEST_SCOPE = Symbol("public-admin-request-scope");

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

function clearAdminTokenLocal() {
  localStorage.removeItem("admin_token");
}

function revokeAdminSessionBestEffort(token) {
  if (!token) return;
  void fetch(`${API_BASE}/api/admin/logout`, {
    method: "POST",
    headers: { Authorization: `Bearer ${token}` },
    cache: "no-store",
    keepalive: true,
  }).catch(() => {
    // Local logout remains authoritative for this browser even when the
    // network is unavailable. The server-side session will still expire by
    // its normal JWT/session lifetime and cannot affect a replacement token.
  });
}

export function setAdminToken(token) {
  if (token) {
    localStorage.setItem("admin_token", token);
    return;
  }

  const tokenAtLogout = getAdminToken();
  clearAdminTokenLocal();
  revokeAdminSessionBestEffort(tokenAtLogout);
}

function staleAdminSessionError() {
  return new AdminApiError("Административная сессия изменилась. Повторите операцию.");
}

function assertAdminSessionUnchanged(auth, tokenAtStart) {
  if (!auth) return;
  if (getAdminToken() !== tokenAtStart) throw staleAdminSessionError();
}

function clearAdminTokenIfCurrent(tokenAtStart) {
  if (getAdminToken() !== tokenAtStart) throw staleAdminSessionError();
  clearAdminTokenLocal();
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

function authHeaders(auth, customHeaders = {}, token = getAdminToken()) {
  const result = { ...customHeaders };
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
  const tokenAtStart = auth ? getAdminToken() : "";
  const coordinationKey = dedupeKey ?? mutationRequestKey(path, requestOptions);
  const coordinationScope = auth ? tokenAtStart : PUBLIC_REQUEST_SCOPE;

  return requestCoordinator.run(coordinationKey, async () => {
    const response = await fetchWithTimeout(`${API_BASE}${path}`, {
      ...requestOptions,
      headers: authHeaders(auth, headers, tokenAtStart),
    });
    if (!response.ok) {
      const detail = await errorDetail(response);
      if (response.status === 401 && auth) clearAdminTokenIfCurrent(tokenAtStart);
      else assertAdminSessionUnchanged(auth, tokenAtStart);
      throw new AdminApiError(detail || `HTTP ${response.status}`, response.status);
    }

    assertAdminSessionUnchanged(auth, tokenAtStart);
    if (response.status === 204) return null;
    const contentType = response.headers.get("content-type") || "";
    const payload = contentType.includes("application/json")
      ? await response.json()
      : await response.text();
    assertAdminSessionUnchanged(auth, tokenAtStart);
    return payload;
  }, coordinationScope);
}

export function adminJson(path, options = {}) {
  return adminRequest(path, {
    ...options,
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
  });
}

export async function loginAdmin(email, password, totpCode = "") {
  const payload = { email, password };
  const normalizedTotp = String(totpCode || "").trim();
  if (normalizedTotp) payload.totp_code = normalizedTotp;

  const data = await adminJson("/api/admin/login", {
    method: "POST",
    auth: false,
    body: JSON.stringify(payload),
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
  const tokenAtStart = getAdminToken();
  const response = await fetchWithTimeout(`${API_BASE}${path}`, {
    headers: authHeaders(true, {}, tokenAtStart),
  });
  if (!response.ok) {
    const detail = await errorDetail(response);
    if (response.status === 401) clearAdminTokenIfCurrent(tokenAtStart);
    else assertAdminSessionUnchanged(true, tokenAtStart);
    throw new AdminApiError(detail || "Не удалось скачать файл", response.status);
  }
  assertAdminSessionUnchanged(true, tokenAtStart);
  const disposition = response.headers.get("content-disposition") || "";
  const match = disposition.match(/filename="?([^";]+)"?/i);
  const blob = await response.blob();
  assertAdminSessionUnchanged(true, tokenAtStart);
  return {
    blob,
    filename: match?.[1] || fallbackFilename,
  };
}
