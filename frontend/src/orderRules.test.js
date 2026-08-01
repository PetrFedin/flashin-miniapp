import assert from "node:assert/strict";
import test from "node:test";

import {
  canCancelOrder,
  canPayOrder,
  canReturnOrder,
  paymentReturnMessage,
} from "./orderRules.js";

test("payment is available only before successful payment", () => {
  assert.equal(canPayOrder({ status: "created", payment_status: "pending" }), true);
  assert.equal(canPayOrder({ status: "payment_created", payment_status: "payment_created" }), true);
  assert.equal(canPayOrder({ status: "paid", payment_status: "paid" }), false);
  assert.equal(canPayOrder({ status: "created", payment_status: "paid_review_required" }), false);
});

test("customer cancellation is limited to untouched orders", () => {
  assert.equal(canCancelOrder({ status: "created", payment_status: "pending" }), true);
  assert.equal(canCancelOrder({ status: "payment_created", payment_status: "payment_created" }), false);
  assert.equal(canCancelOrder({ status: "created", payment_status: "paid" }), false);
});

test("return action follows refundable order lifecycle", () => {
  assert.equal(canReturnOrder({ status: "paid", payment_status: "paid" }), true);
  assert.equal(canReturnOrder({ status: "shipped", payment_status: "paid" }), true);
  assert.equal(canReturnOrder({ status: "refund_requested", payment_status: "paid" }), false);
  assert.equal(canReturnOrder({ status: "partially_refunded", payment_status: "partially_refunded" }), true);
  assert.equal(canReturnOrder({ status: "refunded", payment_status: "refunded" }), false);
  assert.equal(canReturnOrder({ status: "cancelled", payment_status: "cancelled" }), false);
});

test("payment return messages distinguish paid, cancelled, and pending", () => {
  assert.deepEqual(paymentReturnMessage(7, "paid"), {
    type: "notice",
    text: "Заказ #7 оплачен. Статус обновлён.",
  });
  assert.equal(paymentReturnMessage(7, "cancelled").type, "error");
  assert.match(paymentReturnMessage(7, "payment_created").text, /обрабатывается/);
});
