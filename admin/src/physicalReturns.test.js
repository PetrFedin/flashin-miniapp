import assert from "node:assert/strict";
import test from "node:test";

import {
  clearPhysicalIdempotencyKey,
  getPhysicalIdempotencyKey,
  normalizePhysicalQuantity,
  physicalMutationSignature,
  physicalProviderStatusLabel,
  physicalRemaining,
} from "./physicalReturns.js";

function memoryStorage() {
  const values = new Map();
  return {
    getItem(key) { return values.has(key) ? values.get(key) : null; },
    setItem(key, value) { values.set(key, String(value)); },
  };
}

test("physical quantities are positive integers bounded by the remaining phase quantity", () => {
  assert.deepEqual(normalizePhysicalQuantity("2", 3, "Приёмка"), { value: 2 });
  assert.match(normalizePhysicalQuantity("0", 3, "Приёмка").error, /положительным целым/i);
  assert.match(normalizePhysicalQuantity("1.5", 3, "Приёмка").error, /положительным целым/i);
  assert.match(normalizePhysicalQuantity("4", 3, "Приёмка").error, /превышает/i);

  const item = {
    ordered_qty: 4,
    authorized_qty: 3,
    received_qty: 2,
    inspected_qty: 1,
    quarantine_qty: 1,
  };
  assert.equal(physicalRemaining(item, "authorize"), 1);
  assert.equal(physicalRemaining(item, "receive"), 1);
  assert.equal(physicalRemaining(item, "inspect"), 1);
  assert.equal(physicalRemaining(item, "quarantine"), 1);
});

test("provider disposition statuses are operator-readable and fail visibly", () => {
  assert.match(physicalProviderStatusLabel("sent"), /МойСклад/);
  assert.match(physicalProviderStatusLabel("review_required"), /сверка/i);
  assert.equal(physicalProviderStatusLabel("unexpected"), "unexpected");
});

test("one ambiguous physical mutation reuses exactly one idempotency key until success", () => {
  const storage = memoryStorage();
  const signature = physicalMutationSignature({
    operation: "receive",
    returnId: 18,
    orderItemId: 92,
    quantity: 1,
    reason: "Принято на складе",
  });
  let sequence = 0;
  const options = {
    storage,
    createKey: () => `physical-test-${++sequence}`,
  };

  const first = getPhysicalIdempotencyKey(signature, options);
  const retry = getPhysicalIdempotencyKey(signature, options);
  assert.equal(first, "physical-test-1");
  assert.equal(retry, first);

  clearPhysicalIdempotencyKey(signature, { storage });
  const nextIndependentAttempt = getPhysicalIdempotencyKey(signature, options);
  assert.equal(nextIndependentAttempt, "physical-test-2");
});

test("changing any authoritative physical mutation field creates a different signature", () => {
  const base = {
    operation: "inspect",
    returnId: 7,
    orderItemId: 11,
    quantity: 1,
    disposition: "resalable",
    reason: "Осмотрено",
  };
  const signature = physicalMutationSignature(base);
  for (const patch of [
    { orderItemId: 12 },
    { quantity: 2 },
    { disposition: "damaged" },
    { reason: "Другая причина" },
    { operation: "receive" },
  ]) {
    assert.notEqual(physicalMutationSignature({ ...base, ...patch }), signature);
  }
});
