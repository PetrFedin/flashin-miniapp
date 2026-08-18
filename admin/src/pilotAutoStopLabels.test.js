import assert from "node:assert/strict";
import test from "node:test";

import { normalizePilotOperationsStatus, pilotStopLabel } from "./pilotOperations.js";

function stoppedPayload(reason) {
  return {
    schema_version: 1,
    generated_at: "2026-08-17T22:30:00Z",
    enforced: true,
    checkout_decision: "NO-GO",
    runtime: {
      present: true,
      status: "stopped",
      run_ref: "abcdef123456",
      max_orders: 20,
      accepted_orders: 4,
      remaining_orders: 16,
      slot_count: 4,
      historical_slot_count: 0,
      allowlist_count: 2,
      stop_reason: reason,
      opened_at: "2026-08-17T21:00:00Z",
      stopped_at: "2026-08-17T22:29:00Z",
      completed_at: null,
      updated_at: "2026-08-17T22:29:00Z",
    },
    database_integrity: { healthy: true, codes: [] },
    artifact_integrity: { applicable: true, healthy: true, codes: [] },
    continuation: { applicable: false, ready: null, next_sequence: null },
    money_attention: {
      payment_review_orders: 1,
      refund_attention_orders: 0,
      reconciliation_mismatches: 0,
      attention_required: true,
      healthy: false,
      blocking_codes: ["pilot_payment_review_required"],
      stop_reason: "payment_review_required",
    },
  };
}

test("new runtime safety stop reasons remain finite and contract-valid", () => {
  const reasons = [
    "auto:payment_review_required",
    "auto:runtime_artifact_integrity_failure",
    "auto:pilot_control_decision_stop",
    "auto:pilot_database_integrity_failure",
    "auto:operational_safety_failure",
    "auto:pilot_money_safety_evaluation_failure",
    "auto:pilot_runtime_configuration_mismatch",
  ];

  for (const reason of reasons) {
    const status = normalizePilotOperationsStatus(stoppedPayload(reason));
    assert.equal(status.contractValid, true, reason);
    assert.equal(status.decision, "NO-GO", reason);
    assert.equal(status.runtime.stopReason, reason, reason);
    assert.notEqual(
      pilotStopLabel(reason),
      "Автоматическая остановка из-за нарушения целостности",
      reason,
    );
  }
});

test("unknown stop detail still fails closed without echoing raw text", () => {
  const secret = "auto:provider_secret_payload_123456789";
  const status = normalizePilotOperationsStatus(stoppedPayload(secret));

  assert.equal(status.contractValid, false);
  assert.equal(status.runtime.stopReason, null);
  assert.equal(
    pilotStopLabel(secret),
    "Автоматическая остановка из-за нарушения целостности",
  );
  assert.doesNotMatch(JSON.stringify(status), /123456789/);
});