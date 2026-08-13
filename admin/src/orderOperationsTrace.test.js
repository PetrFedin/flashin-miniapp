import assert from "node:assert/strict";
import test from "node:test";

import { normalizeOrderOperationsTrace } from "./orderOperationsTrace.js";


test("order operations trace fails closed on missing or malformed payload", () => {
  for (const payload of [null, {}, { order: {} }, { order: { id: 0 } }]) {
    const normalized = normalizeOrderOperationsTrace(payload);
    assert.equal(normalized.valid, false);
    assert.equal(normalized.attention.required, true);
  }
});


test("order operations trace normalizes only bounded operational summary fields", () => {
  const normalized = normalizeOrderOperationsTrace({
    request_id: "req-123",
    order: {
      id: 42,
      status: "paid",
      payment_status: "succeeded",
      delivery_status: "packing",
      total_amount: "14990.50",
      currency: "RUB",
    },
    payments: [{ id: 1 }],
    payment_events: [{ id: 2 }, { id: 3 }],
    returns: [],
    provider_commands: [{ id: 4 }],
    fulfillment: [{ id: 5 }],
    business_events: [{ id: 6 }],
    notifications: [{ id: 7 }],
    sla: [{ id: 8 }],
    attention: {
      required: false,
      provider_commands_actionable: 1,
      provider_failures: 0,
      failed_notifications: 0,
      business_events_unresolved: 1,
      business_events_failed: 0,
      overdue_sla: 0,
    },
  });

  assert.equal(normalized.valid, true);
  assert.deepEqual(normalized.order, {
    id: 42,
    status: "paid",
    paymentStatus: "succeeded",
    deliveryStatus: "packing",
    totalAmount: 14990.5,
    currency: "RUB",
  });
  assert.deepEqual(normalized.counts, {
    payments: 1,
    paymentEvents: 2,
    returns: 0,
    providerCommands: 1,
    fulfillment: 1,
    businessEvents: 1,
    notifications: 1,
    sla: 1,
  });
  assert.equal(normalized.attention.required, false);
  assert.equal(normalized.attention.providerCommandsActionable, 1);
});


test("failure counters force attention even if a stale backend flag says false", () => {
  const normalized = normalizeOrderOperationsTrace({
    order: { id: 9 },
    attention: {
      required: false,
      provider_failures: 1,
      failed_notifications: 2,
      business_events_failed: 3,
      overdue_sla: 4,
    },
  });

  assert.equal(normalized.valid, true);
  assert.equal(normalized.attention.required, true);
  assert.equal(normalized.attention.providerFailures, 1);
  assert.equal(normalized.attention.failedNotifications, 2);
  assert.equal(normalized.attention.businessEventsFailed, 3);
  assert.equal(normalized.attention.overdueSla, 4);
});
