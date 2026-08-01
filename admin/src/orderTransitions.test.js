import assert from "node:assert/strict";
import test from "node:test";

import {
  canCancelBeforePayment,
  nextFulfillmentStatus,
  orderAction,
} from "./orderTransitions.js";


test("fulfillment progression exposes only the next valid state", () => {
  assert.equal(nextFulfillmentStatus({ status: "paid" }), "assembling");
  assert.equal(nextFulfillmentStatus({ status: "assembling" }), "ready");
  assert.equal(nextFulfillmentStatus({ status: "ready" }), "shipped");
  assert.equal(nextFulfillmentStatus({ status: "shipped" }), "completed");
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


test("order actions never expose provider-owned status rewrites", () => {
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
  assert.equal(
    orderAction({ status: "payment_created", payment_status: "payment_created" }),
    null,
  );
  assert.equal(
    orderAction({ status: "refund_requested", payment_status: "paid" }),
    null,
  );
});
