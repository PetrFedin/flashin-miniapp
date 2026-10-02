import assert from "node:assert/strict";
import test from "node:test";

import { adminQueryKeys, createAdminQueryClient } from "./queryClient.js";

test("Admin business state is always stale and mutations never auto-retry", () => {
  const client = createAdminQueryClient();
  const queries = client.getDefaultOptions().queries;

  assert.equal(queries.staleTime, 0);
  assert.equal(queries.refetchOnMount, "always");
  assert.equal(queries.refetchOnReconnect, "always");
  assert.equal(queries.refetchOnWindowFocus, true);
  assert.equal(client.getDefaultOptions().mutations.retry, false);
});

test("Admin query keys isolate session, catalogue, orders and operations", () => {
  assert.deepEqual(adminQueryKeys.session(), ["admin", "session"]);
  assert.deepEqual(adminQueryKeys.products(), ["admin", "products"]);
  assert.deepEqual(adminQueryKeys.orders(), ["admin", "orders"]);
  assert.deepEqual(adminQueryKeys.lowStock(), ["admin", "low-stock"]);
});
