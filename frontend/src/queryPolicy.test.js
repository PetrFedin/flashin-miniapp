import assert from "node:assert/strict";
import test from "node:test";

import {
  createFlashinQueryClient,
  queryKeys,
  queryPolicy,
} from "./queryClient.js";

test("business truth is always stale and refetched on focus/reconnect", () => {
  const client = createFlashinQueryClient();
  const queries = client.getDefaultOptions().queries;

  assert.equal(queries.staleTime, 0);
  assert.equal(queries.refetchOnMount, "always");
  assert.equal(queries.refetchOnReconnect, "always");
  assert.equal(queries.refetchOnWindowFocus, true);
  assert.equal(queryPolicy.businessTruth.staleTime, 0);
});

test("mutations never auto-retry and catalog has only a bounded stale window", () => {
  const client = createFlashinQueryClient();

  assert.equal(client.getDefaultOptions().mutations.retry, false);
  assert.equal(queryPolicy.catalog.staleTime, 30_000);
  assert.ok(queryPolicy.catalog.gcTime >= queryPolicy.catalog.staleTime);
});

test("customer and catalog query namespaces do not collide", () => {
  assert.deepEqual(queryKeys.cart(), ["customer", "cart"]);
  assert.deepEqual(queryKeys.orders(), ["customer", "orders"]);
  assert.deepEqual(queryKeys.product(42), ["catalog", "product", 42]);
  assert.deepEqual(queryKeys.catalogDetail(42), ["catalog", "detail", 42]);
});
