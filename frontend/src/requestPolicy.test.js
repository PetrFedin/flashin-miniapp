import assert from "node:assert/strict";
import test from "node:test";

import {
  createRequestCoordinator,
  createTimeoutController,
  mutationRequestKey,
} from "./requestPolicy.js";


test("safe requests are not deduplicated", () => {
  assert.equal(mutationRequestKey("/api/products"), null);
  assert.equal(mutationRequestKey("/api/products", { method: "HEAD" }), null);
});


test("mutation keys include method, path, and body", () => {
  assert.equal(
    mutationRequestKey("/api/support/tickets", {
      method: "post",
      body: JSON.stringify({ subject: "Delivery" }),
    }),
    'POST:/api/support/tickets:{"subject":"Delivery"}',
  );
});


test("identical in-flight mutations share one operation", async () => {
  const coordinator = createRequestCoordinator();
  let calls = 0;
  let resolveOperation;
  const operation = () => {
    calls += 1;
    return new Promise((resolve) => {
      resolveOperation = resolve;
    });
  };

  const first = coordinator.run("POST:/api/privacy/requests:delete", operation);
  const second = coordinator.run("POST:/api/privacy/requests:delete", operation);

  assert.equal(first, second);
  assert.equal(calls, 0);
  await Promise.resolve();
  assert.equal(calls, 1);
  assert.equal(coordinator.size(), 1);

  resolveOperation({ ok: true });
  assert.deepEqual(await first, { ok: true });
  assert.deepEqual(await second, { ok: true });
  assert.equal(coordinator.size(), 0);
});


test("failed mutations are removed and may be retried", async () => {
  const coordinator = createRequestCoordinator();
  let calls = 0;
  const operation = async () => {
    calls += 1;
    if (calls === 1) throw new Error("temporary");
    return "ok";
  };

  await assert.rejects(
    coordinator.run("POST:/api/cart/promo:X", operation),
    /temporary/,
  );
  assert.equal(coordinator.size(), 0);
  assert.equal(
    await coordinator.run("POST:/api/cart/promo:X", operation),
    "ok",
  );
  assert.equal(calls, 2);
});


test("timeout controller aborts and reports timeout", async () => {
  const timeout = createTimeoutController(5);
  await new Promise((resolve) => timeout.signal.addEventListener("abort", resolve, { once: true }));

  assert.equal(timeout.signal.aborted, true);
  assert.equal(timeout.didTimeout(), true);
  timeout.cleanup();
});


test("external abort is preserved without being reported as timeout", () => {
  const external = new AbortController();
  const timeout = createTimeoutController(1_000, external.signal);

  external.abort(new Error("navigation"));

  assert.equal(timeout.signal.aborted, true);
  assert.equal(timeout.didTimeout(), false);
  timeout.cleanup();
});


test("invalid timeout values are rejected", () => {
  assert.throws(() => createTimeoutController(0), /positive number/);
  assert.throws(() => createTimeoutController(Number.NaN), /positive number/);
});
