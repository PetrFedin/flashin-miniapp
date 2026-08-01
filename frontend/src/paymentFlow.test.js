import assert from "node:assert/strict";
import test from "node:test";

import { normalizePaymentContinuation, paymentContinuationUrl } from "./paymentFlow.js";

test("active provider payment continues to confirmation URL", () => {
  assert.equal(
    paymentContinuationUrl(
      { status: "pending", confirmation_url: " https://pay.example/checkout " },
      7,
    ),
    "https://pay.example/checkout",
  );
});

test("succeeded payment continues to internal reconciliation route", () => {
  assert.equal(
    paymentContinuationUrl({ status: "succeeded", confirmation_url: "https://pay.example/stale" }, 7),
    "/payment-result?order_id=7",
  );
});

test("canceled or active payment without URL has no continuation", () => {
  assert.equal(paymentContinuationUrl({ status: "canceled" }, 7), "");
  assert.equal(paymentContinuationUrl({ status: "pending" }, 7), "");
  assert.equal(paymentContinuationUrl({ status: "succeeded" }, 0), "");
});

test("normalization preserves provider data and replaces only continuation URL", () => {
  assert.deepEqual(
    normalizePaymentContinuation(
      {
        order_id: 11,
        provider: "yookassa",
        status: "succeeded",
        confirmation_url: "",
        provider_payment_id: "pay-11",
      },
      11,
    ),
    {
      order_id: 11,
      provider: "yookassa",
      status: "succeeded",
      confirmation_url: "/payment-result?order_id=11",
      provider_payment_id: "pay-11",
    },
  );
});
