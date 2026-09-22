import assert from "node:assert/strict";
import test from "node:test";

import {
  canApproveReturn,
  canProcessPrivacy,
  normalizeAdminAssignment,
  normalizeRefundAmount,
  buildRefundAllocationPayload,
  refundReconciliationLabel,
  serviceAttentionCount,
  supportTransitions,
} from "./serviceOperations.js";

test("support transitions follow the backend state machine", () => {
  assert.deepEqual(supportTransitions("open"), ["in_progress", "waiting_customer", "resolved", "closed"]);
  assert.deepEqual(supportTransitions("resolved"), ["in_progress", "closed"]);
  assert.deepEqual(supportTransitions("closed"), []);
  assert.deepEqual(supportTransitions("unknown"), []);
});

test("support owner assignment accepts only positive integer Admin IDs", () => {
  assert.deepEqual(normalizeAdminAssignment("42"), { value: 42 });
  assert.deepEqual(normalizeAdminAssignment(""), { value: null });
  assert.match(normalizeAdminAssignment("0").error, /положительным целым/i);
  assert.match(normalizeAdminAssignment("4.2").error, /положительным целым/i);
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


test("refund allocation requires exact partial composition but permits exact full auto allocation", () => {
  const item = {
    financial_allocation: { allocated_cents: 0 },
    financial_allocation_options: {
      items: [
        { order_item_id: 11, remaining_cents: 70000 },
        { order_item_id: 12, remaining_cents: 30000 },
      ],
      delivery_remaining_cents: 10000,
    },
  };

  assert.deepEqual(
    buildRefundAllocationPayload(item, {}, 1100),
    { allocations: [] },
  );
  assert.match(
    buildRefundAllocationPayload(item, {}, 500).error,
    /частичного/i,
  );

  assert.deepEqual(
    buildRefundAllocationPayload(item, {
      "item:11": "350",
      "item:12": "100",
      goodwill: "50",
    }, 500),
    {
      allocations: [
        { component_kind: "item", order_item_id: 11, amount: 350 },
        { component_kind: "item", order_item_id: 12, amount: 100 },
        { component_kind: "goodwill", amount: 50 },
      ],
    },
  );
});

test("refund allocation rejects component over-cap and mismatched totals", () => {
  const item = {
    financial_allocation: { allocated_cents: 0 },
    financial_allocation_options: {
      items: [{ order_item_id: 11, remaining_cents: 40000 }],
      delivery_remaining_cents: 1000,
    },
  };
  assert.match(
    buildRefundAllocationPayload(item, { "item:11": "401" }, 401).error,
    /превышает/i,
  );
  assert.match(
    buildRefundAllocationPayload(item, { "item:11": "200" }, 300).error,
    /не совпадает/i,
  );
});

test("fixed refund allocation cannot be rewritten by retry UI", () => {
  const item = {
    financial_allocation: { allocated_cents: 50000 },
    financial_allocation_options: { items: [], delivery_remaining_cents: 0 },
  };
  assert.deepEqual(
    buildRefundAllocationPayload(item, { goodwill: "999" }, 500),
    { allocations: [] },
  );
});

test("refund reconciliation labels surface all authority states", () => {
  assert.match(refundReconciliationLabel("PASS"), /PASS/);
  assert.match(refundReconciliationLabel("PENDING"), /PENDING/);
  assert.match(refundReconciliationLabel("REVIEW"), /REVIEW/);
  assert.match(refundReconciliationLabel("BLOCKED"), /BLOCKED/);
});
