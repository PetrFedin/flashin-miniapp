import assert from "node:assert/strict";
import test from "node:test";

import {
  createRequestCoordinator,
  createTimeoutController,
  mutationRequestKey,
} from "./requestPolicy.js";


test("admin mutation requests receive deterministic keys", () => {
  assert.equal(mutationRequestKey("/api/admin/orders"), null);
  assert.equal(
    mutationRequestKey("/api/admin/promocodes", {
      method: "POST",
      body: '{"code":"FLASH"}',
    }),
    'POST:/api/admin/promocodes:{"code":"FLASH"}',
  );
});


test("identical admin mutations execute once while in flight", async () => {
  const coordinator = createRequestCoordinator();
  let calls = 0;
  let resolveOperation;
  const operation = () => {
    calls += 1;
    return new Promise((resolve) => {
      resolveOperation = resolve;
    });
  };

  const first = coordinator.run("POST:/api/ops/inventory/snapshot:", operation);
  const second = coordinator.run("POST:/api/ops/inventory/snapshot:", operation);

  assert.equal(first, second);
  await Promise.resolve();
  assert.equal(calls, 1);
  assert.equal(coordinator.size(), 1);
  resolveOperation("done");
  assert.equal(await first, "done");
  assert.equal(coordinator.size(), 0);
});


test("failed admin mutations may be retried", async () => {
  const coordinator = createRequestCoordinator();
  let calls = 0;
  const operation = async () => {
    calls += 1;
    if (calls === 1) throw new Error("temporary");
    return "ok";
  };

  await assert.rejects(coordinator.run("key", operation), /temporary/);
  assert.equal(await coordinator.run("key", operation), "ok");
  assert.equal(calls, 2);
});


test("admin timeout controller aborts hanging requests", async () => {
  const timeout = createTimeoutController(5);
  await new Promise((resolve) => timeout.signal.addEventListener("abort", resolve, { once: true }));

  assert.equal(timeout.didTimeout(), true);
  timeout.cleanup();
});
