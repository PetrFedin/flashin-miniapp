import assert from "node:assert/strict";
import test from "node:test";

import {
  canCancelBeforePayment,
  nextFulfillmentStatus,
  orderAction,
} from "./orderTransitions.js";


test("order table exposes only the fulfillment start transition", () => {
  assert.equal(nextFulfillmentStatus({ status: "paid" }), "assembling");
  assert.equal(nextFulfillmentStatus({ status: "assembling" }), null);
  assert.equal(nextFulfillmentStatus({ status: "ready" }), null);
  assert.equal(nextFulfillmentStatus({ status: "shipped" }), null);
  assert.equal(nextFulfillmentStatus({ status: "completed" }), null);
});


test("only an untouched unpaid order may be cancelled directly", () => {
  assert.equal(
    canCancelBeforePayment({ status: "created", payment_status: "pending" }),
    true,
  );
  assert.equal(
    canCancelBeforePayment({ status: "payment_created", payment_status: "payment_created" }),
    false,
  );
  assert.equal(
    canCancelBeforePayment({ status: "paid", payment_status: "paid" }),
    false,
  );
});


test("order actions never expose later fulfillment, shipment, or provider-owned rewrites", () => {
  assert.deepEqual(
    orderAction({ status: "paid", payment_status: "paid" }),
    {
      type: "advance",
      status: "assembling",
      label: "Перевести: Собирается",
    },
  );
  assert.deepEqual(
    orderAction({ status: "created", payment_status: "pending" }),
    {
      type: "cancel",
      status: "cancelled",
      label: "Отменить до оплаты",
    },
  );
  for (const status of [
    "assembling",
    "ready",
    "shipped",
    "completed",
    "payment_created",
    "refund_requested",
  ]) {
    assert.equal(orderAction({ status, payment_status: "paid" }), null);
  }
});
