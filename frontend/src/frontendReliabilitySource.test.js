import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

function source(name) {
  return readFileSync(new URL(name, import.meta.url), "utf8");
}

test("Mini App mounts one QueryClient provider and dev-only MSW bootstrap", () => {
  const main = source("./main.jsx");
  const mocks = source("./mocks/enableMocking.js");

  assert.match(main, /QueryClientProvider/);
  assert.match(main, /createFlashinQueryClient/);
  assert.match(main, /await enableMocking\(\)/);
  assert.match(mocks, /import\.meta\.env\.DEV/);
  assert.match(mocks, /VITE_MSW_ENABLED !== "true"/);
});

test("frontend Sentry is release-bound and PII/replay safe by default", () => {
  const observability = source("./observability.js");

  assert.match(observability, /__FLASHIN_RELEASE_SHA__/);
  assert.match(observability, /sendDefaultPii: false/);
  assert.match(observability, /tracesSampleRate: 0/);
  assert.match(observability, /replaysSessionSampleRate: 0/);
  assert.match(observability, /replaysOnErrorSampleRate: 0/);
  assert.match(observability, /delete next\.user/);
  assert.match(observability, /delete next\.request/);
  assert.match(observability, /request_id/);
  assert.match(observability, /trace_id/);
});

test("catalog transport participates in request/trace correlation", () => {
  const catalogApi = source("./catalogApi.js");

  assert.match(catalogApi, /"X-Request-ID": createRequestId\(\)/);
  assert.match(catalogApi, /response\.headers\.get\("x-request-id"\)/);
  assert.match(catalogApi, /response\.headers\.get\("x-trace-id"\)/);
});
