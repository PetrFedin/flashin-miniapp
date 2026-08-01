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
  const inFlight = new Map();

  return {
    run(key, operation) {
      if (!key) return Promise.resolve().then(operation);
      const current = inFlight.get(key);
      if (current) return current;
      const shared = Promise.resolve()
        .then(operation)
        .finally(() => {
          if (inFlight.get(key) === shared) inFlight.delete(key);
        });
      inFlight.set(key, shared);
      return shared;
    },
    size: () => inFlight.size,
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
