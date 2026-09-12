export const DEFAULT_REQUEST_TIMEOUT_MS = 20_000;

const SAFE_METHODS = new Set(["GET", "HEAD", "OPTIONS"]);

export function mutationRequestKey(path, options = {}) {
  const method = String(options.method || "GET").toUpperCase();
  if (SAFE_METHODS.has(method)) return null;
  const body = typeof options.body === "string"
    ? options.body
    : options.body == null
      ? ""
      : JSON.stringify(options.body);
  return `${method}:${path}:${body}`;
}

export function createRequestCoordinator() {
  const scopes = new Map();

  return {
    run(key, operation, scope = "default") {
      if (!key) return Promise.resolve().then(operation);
      let inFlight = scopes.get(scope);
      if (!inFlight) {
        inFlight = new Map();
        scopes.set(scope, inFlight);
      }
      const current = inFlight.get(key);
      if (current) return current;
      const shared = Promise.resolve()
        .then(operation)
        .finally(() => {
          if (inFlight.get(key) === shared) inFlight.delete(key);
          if (inFlight.size === 0 && scopes.get(scope) === inFlight) scopes.delete(scope);
        });
      inFlight.set(key, shared);
      return shared;
    },
    size() {
      let total = 0;
      for (const inFlight of scopes.values()) total += inFlight.size;
      return total;
    },
  };
}

export function createTimeoutController(timeoutMs, externalSignal = null) {
  if (!Number.isFinite(timeoutMs) || timeoutMs <= 0) {
    throw new TypeError("Request timeout must be a positive number");
  }
  const controller = new AbortController();
  let timedOut = false;
  const abortFromExternalSignal = () => controller.abort(externalSignal?.reason);

  if (externalSignal?.aborted) abortFromExternalSignal();
  else externalSignal?.addEventListener?.("abort", abortFromExternalSignal, { once: true });

  const timer = setTimeout(() => {
    timedOut = true;
    controller.abort(new Error("Request timeout"));
  }, timeoutMs);

  return {
    signal: controller.signal,
    didTimeout: () => timedOut,
    cleanup() {
      clearTimeout(timer);
      externalSignal?.removeEventListener?.("abort", abortFromExternalSignal);
    },
  };
}
