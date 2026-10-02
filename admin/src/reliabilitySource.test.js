import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const main = readFileSync(new URL("./main.jsx", import.meta.url), "utf8");
const observability = readFileSync(new URL("./observability.js", import.meta.url), "utf8");

test("Admin mounts one QueryClient and clears it with the authenticated session", () => {
  assert.match(main, /QueryClientProvider/);
  assert.match(main, /createAdminQueryClient/);
  assert.match(main, /clearAdminServerState\(queryClient\)/);
  assert.match(main, /queryClient\.fetchQuery/);
});

test("Admin Sentry is release-bound, correlation-only and replay disabled", () => {
  assert.match(observability, /__FLASHIN_RELEASE_SHA__/);
  assert.match(observability, /sendDefaultPii: false/);
  assert.match(observability, /tracesSampleRate: 0/);
  assert.match(observability, /replaysSessionSampleRate: 0/);
  assert.match(observability, /delete next\.user/);
  assert.match(observability, /delete next\.request/);
  assert.match(observability, /request_id/);
  assert.match(observability, /trace_id/);
});
