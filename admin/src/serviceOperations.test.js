import assert from "node:assert/strict";
import test from "node:test";

import {
  canApproveReturn,
  canProcessPrivacy,
  normalizeRefundAmount,
  serviceAttentionCount,
  supportTransitions,
} from "./serviceOperations.js";

test("support transitions follow the backend state machine", () => {
  assert.deepEqual(supportTransitions("open"), ["in_progress", "waiting_customer", "resolved", "closed"]);
  assert.deepEqual(supportTransitions("resolved"), ["in_progress", "closed"]);
  assert.deepEqual(supportTransitions("closed"), []);
  assert.deepEqual(supportTransitions("unknown"), []);
});

test("privacy processing is limited to open requests", () => {
  assert.equal(canProcessPrivacy("requested"), true);
  assert.equal(canProcessPrivacy("processing"), true);
  assert.equal(canProcessPrivacy("processed"), false);
});

test("refund amount is positive, bounded and rounded", () => {
  assert.deepEqual(normalizeRefundAmount("1200.129", 5000), { value: 1200.13 });
  assert.match(normalizeRefundAmount("0", 5000).error, /больше нуля/i);
  assert.match(normalizeRefundAmount("6000", 5000).error, /превышает/i);
  assert.match(normalizeRefundAmount("100", 0).error, /нет доступного остатка/i);
});

test("return action and aggregate attention are fail-closed", () => {
  assert.equal(canApproveReturn({ status: "requested", refundable_balance: 1200 }), true);
  assert.equal(canApproveReturn({ status: "approved", refundable_balance: 1200 }), false);
  assert.equal(canApproveReturn({ status: "requested", refundable_balance: 0 }), false);

  assert.equal(serviceAttentionCount({
    tickets: [{ status: "open" }, { status: "closed" }],
    privacy: [{ status: "requested" }, { status: "processed" }],
    returns: [
      { status: "refund_review_required", refundable_balance: 500 },
      { status: "approved", refundable_balance: 0 },
    ],
  }), 3);
});
