const SESSION_TOKEN_KEY = "flashin_customer_session_token";
const LEGACY_COMPATIBILITY_KEY = "flashin_token";
const LEGACY_SESSION_MARKER = "session";

function storage(name) {
  try {
    return globalThis?.[name] || null;
  } catch {
    return null;
  }
}

function safeGet(target, key) {
  try {
    return target?.getItem?.(key) || "";
  } catch {
    return "";
  }
}

function safeSet(target, key, value) {
  try {
    target?.setItem?.(key, value);
  } catch {
    // Web storage can be unavailable in restricted WebViews. The caller still
    // receives the authentication result and can continue for the current page.
  }
}

function safeRemove(target, key) {
  try {
    target?.removeItem?.(key);
  } catch {
    // Best-effort cleanup only; never surface storage diagnostics containing a bearer.
  }
}

let memoryToken = "";

function syncLegacyPresenceMarker(hasToken) {
  const persistentStorage = storage("localStorage");
  if (hasToken) {
    // Transitional compatibility for legacy experience layers that only check
    // whether FLASHIN auth exists. This value is deliberately not a bearer.
    safeSet(persistentStorage, LEGACY_COMPATIBILITY_KEY, LEGACY_SESSION_MARKER);
  } else {
    // This also deletes customer bearers left by pre-session-storage builds.
    safeRemove(persistentStorage, LEGACY_COMPATIBILITY_KEY);
  }
}

export function getCustomerToken() {
  const sessionToken = safeGet(storage("sessionStorage"), SESSION_TOKEN_KEY);
  const token = sessionToken || memoryToken;
  syncLegacyPresenceMarker(Boolean(token));
  return token;
}

export function setCustomerToken(token) {
  const normalized = String(token || "").trim();
  if (!normalized) {
    clearCustomerToken();
    return;
  }
  memoryToken = normalized;
  safeSet(storage("sessionStorage"), SESSION_TOKEN_KEY, normalized);
  syncLegacyPresenceMarker(true);
}

export function clearCustomerToken() {
  memoryToken = "";
  safeRemove(storage("sessionStorage"), SESSION_TOKEN_KEY);
  syncLegacyPresenceMarker(false);
}

export function hasCustomerToken() {
  return Boolean(getCustomerToken());
}

// On every fresh document load migrate away from the historic localStorage
// bearer. A surviving sessionStorage bearer gets a non-sensitive presence
// marker; otherwise the legacy key is removed immediately.
getCustomerToken();
