import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import { normalizeOrderOperationsTrace } from "./orderOperationsTrace.js";

const panelSource = readFileSync(new URL("./OrderOperationsTracePanel.jsx", import.meta.url), "utf8");

function reconciledPayload() {
  return {
    schema_version: 3,
    request_id: "request-42",
    order: {
      id: 42,
      status: "paid",
      payment_status: "paid",
      delivery_status: "pending",
      total_amount: 1520,
      currency: "RUB",
    },
    payments: [{ id: 1 }],
    payment_events: [{ id: 2 }],
    returns: [],
    provider_commands: [{ id: 3 }],
    inventory: [{ id: 4 }],
    fulfillment: [{ id: 5 }],
    business_events: [{ id: 6 }],
    notifications: [{ id: 7 }],
    sla: [],
    attention: {
      provider_commands_actionable: 1,
      provider_failures: 0,
      inventory_invalid_rows: 0,
      failed_notifications: 0,
      business_events_unresolved: 1,
      business_events_failed: 0,
      overdue_sla: 0,
      required: false,
    },
    reconciliation: {
      schema_version: 1,
      overall_status: "PENDING",
      requires_operator_action: false,
      stages: [
        { key: "payment", status: "PASS", reason: "payment_settled", next_action: "none", evidence: ["payment.status=succeeded"] },
        { key: "inventory", status: "PENDING", reason: "inventory_reserved_not_committed_yet", next_action: "wait_for_fulfillment", evidence: ["inventory.kind=reserve"] },
        { key: "moysklad", status: "PENDING", reason: "moysklad_command_in_progress", next_action: "wait_for_provider_command", evidence: ["provider=moysklad"] },
        { key: "fulfillment", status: "PENDING", reason: "fulfillment_in_progress", next_action: "wait_for_fulfillment", evidence: ["fulfillment.tasks=1"] },
        { key: "refunds", status: "PASS", reason: "no_refund_requested", next_action: "none", evidence: ["returns.count=0"] },
        { key: "notifications", status: "PENDING", reason: "notification_delivery_in_progress", next_action: "wait_for_notification_delivery", evidence: ["notification.status=pending"] },
      ],
    },
  };
}


test("order operations trace fails closed on missing or malformed payload", () => {
  for (const payload of [null, {}, { order: {} }, { order: { id: 0 } }]) {
    const normalized = normalizeOrderOperationsTrace(payload);
    assert.equal(normalized.valid, false);
    assert.equal(normalized.attention.required, true);
    assert.equal(normalized.reconciliation.valid, false);
    assert.equal(normalized.reconciliation.overallStatus, "REVIEW");
    assert.equal(normalized.reconciliation.requiresOperatorAction, true);
  }
});


test("order operations trace normalizes bounded money, inventory and operations counts", () => {
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
    inventory: [
      { id: 10, kind: "reserve", source: "private-source" },
      { id: 11, kind: "commit", source: "private-source" },
    ],
    fulfillment: [{ id: 5 }],
    business_events: [{ id: 6 }],
    notifications: [{ id: 7 }],
    sla: [{ id: 8 }],
    attention: {
      required: false,
      provider_commands_actionable: 1,
      provider_failures: 0,
      inventory_invalid_rows: 0,
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
    inventoryMovements: 2,
    fulfillment: 1,
    businessEvents: 1,
    notifications: 1,
    sla: 1,
  });
  assert.equal(normalized.attention.required, false);
  assert.equal(normalized.attention.providerCommandsActionable, 1);
  assert.equal(normalized.reconciliation.valid, false);
  assert.equal(normalized.reconciliation.overallStatus, "REVIEW");
  assert.equal(Object.hasOwn(normalized, "inventory"), false);
});


test("inventory integrity and failure counters force attention despite stale backend flag", () => {
  const normalized = normalizeOrderOperationsTrace({
    order: { id: 9 },
    attention: {
      required: false,
      provider_failures: 1,
      inventory_invalid_rows: 2,
      failed_notifications: 3,
      business_events_failed: 4,
      overdue_sla: 5,
    },
  });

  assert.equal(normalized.valid, true);
  assert.equal(normalized.attention.required, true);
  assert.equal(normalized.attention.providerFailures, 1);
  assert.equal(normalized.attention.inventoryInvalidRows, 2);
  assert.equal(normalized.attention.failedNotifications, 3);
  assert.equal(normalized.attention.businessEventsFailed, 4);
  assert.equal(normalized.attention.overdueSla, 5);
});


test("normalizer keeps lifecycle PENDING separate from operator attention", () => {
  const normalized = normalizeOrderOperationsTrace(reconciledPayload());

  assert.equal(normalized.valid, true);
  assert.equal(normalized.reconciliation.valid, true);
  assert.equal(normalized.reconciliation.overallStatus, "PENDING");
  assert.equal(normalized.reconciliation.requiresOperatorAction, false);
  assert.equal(normalized.reconciliation.stages.length, 6);
  assert.equal(normalized.reconciliation.stages[2].key, "moysklad");
  assert.equal(normalized.reconciliation.stages[2].nextAction, "wait_for_provider_command");
  assert.equal(normalized.attention.providerCommandsActionable, 1);
  assert.equal(normalized.attention.required, false);
});


test("missing or partial reconciliation fails closed as REVIEW", () => {
  const missing = reconciledPayload();
  delete missing.reconciliation;
  const missingNormalized = normalizeOrderOperationsTrace(missing);
  assert.equal(missingNormalized.valid, true);
  assert.equal(missingNormalized.reconciliation.valid, false);
  assert.equal(missingNormalized.reconciliation.overallStatus, "REVIEW");
  assert.equal(missingNormalized.reconciliation.requiresOperatorAction, true);

  const partial = reconciledPayload();
  partial.reconciliation.stages = partial.reconciliation.stages.slice(0, 5);
  const partialNormalized = normalizeOrderOperationsTrace(partial);
  assert.equal(partialNormalized.reconciliation.valid, false);
  assert.equal(partialNormalized.reconciliation.requiresOperatorAction, true);
});


test("unknown backend lifecycle status fails closed to REVIEW", () => {
  const unknown = reconciledPayload();
  unknown.reconciliation.overall_status = "MAYBE";
  unknown.reconciliation.stages[0].status = "MAYBE";

  const normalized = normalizeOrderOperationsTrace(unknown);

  assert.equal(normalized.reconciliation.overallStatus, "REVIEW");
  assert.equal(normalized.reconciliation.requiresOperatorAction, true);
  assert.equal(normalized.reconciliation.stages[0].status, "REVIEW");
});


test("operator lifecycle panel remains read-only and distinguishes PENDING from REVIEW/BLOCKED", () => {
  assert.match(panelSource, /Lifecycle reconciliation/);
  assert.match(panelSource, /PENDING · нормальный прогресс/);
  assert.match(panelSource, /REVIEW · нужна проверка/);
  assert.match(panelSource, /BLOCKED · дальнейший шаг остановлен/);
  assert.match(panelSource, /data-testid="order-lifecycle-reconciliation"/);
  assert.match(panelSource, /\/api\/ops\/orders\/\$\{numericOrderId\}\/trace/);
  assert.doesNotMatch(panelSource, /method:\s*"(?:POST|PUT|PATCH|DELETE)"/);
  assert.doesNotMatch(panelSource, /payload_json/);
  assert.doesNotMatch(panelSource, /provider_payment_id/);
  assert.doesNotMatch(panelSource, /idempotency_key/);
  assert.doesNotMatch(panelSource, /telegram_id/);
  assert.doesNotMatch(panelSource, /last_error/);
});