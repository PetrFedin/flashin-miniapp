import assert from "node:assert/strict";
import test from "node:test";

import {
  normalizePilotOperationsStatus,
  pilotIntegrityLabels,
  pilotStatusLabel,
  pilotStopLabel,
} from "./pilotOperations.js";

function healthyPayload() {
  return {
    schema_version: 1,
    generated_at: "2026-08-03T23:30:00Z",
    enforced: true,
    checkout_decision: "GO",
    runtime: {
      present: true,
      status: "active",
      run_ref: "abcdef123456",
      max_orders: 20,
      accepted_orders: 3,
      remaining_orders: 17,
      slot_count: 3,
      historical_slot_count: 0,
      allowlist_count: 2,
      stop_reason: null,
      opened_at: "2026-08-03T23:00:00Z",
      stopped_at: null,
      completed_at: null,
      updated_at: "2026-08-03T23:29:00Z",
    },
    database_integrity: { healthy: true, codes: [] },
    artifact_integrity: { applicable: true, healthy: true, codes: [] },
    money_attention: {
      payment_review_orders: 0,
      refund_attention_orders: 0,
      reconciliation_mismatches: 0,
      attention_required: false,
    },
  };
}

test("healthy backend contract remains GO and exposes only selected fields", () => {
  const payload = healthyPayload();
  payload.raw_manifest = "private admission manifest";
  payload.allowed_telegram_ids = ["123456789"];
  payload.runtime.run_id = "pilot-run-private";

  const status = normalizePilotOperationsStatus(payload);

  assert.equal(status.decision, "GO");
  assert.equal(status.contractValid, true);
  assert.equal(status.runtime.runRef, "abcdef123456");
  assert.equal(status.runtime.acceptedOrders, 3);
  const serialized = JSON.stringify(status);
  assert.doesNotMatch(serialized, /123456789/);
  assert.doesNotMatch(serialized, /pilot-run-private/);
  assert.doesNotMatch(serialized, /private admission manifest/);
});

test("malformed response is forced to NO-GO", () => {
  const status = normalizePilotOperationsStatus({
    schema_version: 99,
    checkout_decision: "GO",
    secret: "must-not-survive",
  });

  assert.equal(status.decision, "NO-GO");
  assert.equal(status.contractValid, false);
  assert.deepEqual(status.databaseIntegrity.codes, ["response_contract_invalid"]);
  assert.doesNotMatch(JSON.stringify(status), /must-not-survive/);
});

test("GO is downgraded when any safety condition is not confirmed", () => {
  const cases = [
    (payload) => { payload.enforced = false; },
    (payload) => { payload.runtime.status = "stopped"; },
    (payload) => {
      payload.database_integrity.healthy = false;
      payload.database_integrity.codes = ["slot_count_mismatch"];
    },
    (payload) => {
      payload.artifact_integrity.healthy = false;
      payload.artifact_integrity.codes = ["release_capability_invalid"];
    },
    (payload) => {
      payload.money_attention.attention_required = true;
      payload.money_attention.payment_review_orders = 1;
    },
  ];

  for (const mutate of cases) {
    const payload = healthyPayload();
    mutate(payload);
    assert.equal(normalizePilotOperationsStatus(payload).decision, "NO-GO");
  }
});

test("unknown integrity codes and stop reasons never render raw text", () => {
  const payload = healthyPayload();
  payload.checkout_decision = "NO-GO";
  payload.runtime.status = "stopped";
  payload.runtime.stop_reason = "secret customer 123456789";
  payload.database_integrity = {
    healthy: false,
    codes: ["secret_path_/srv/private/evidence.json"],
  };

  const status = normalizePilotOperationsStatus(payload);
  const labels = pilotIntegrityLabels(status.databaseIntegrity.codes);

  assert.equal(status.runtime.stopReason, null);
  assert.deepEqual(labels, ["Ответ pilot status имеет неверный формат"]);
  const serialized = JSON.stringify({ status, labels });
  assert.doesNotMatch(serialized, /123456789/);
  assert.doesNotMatch(serialized, /srv\/private/);
});

test("finite operator labels do not echo unknown values", () => {
  assert.equal(pilotStatusLabel("active"), "Активен");
  assert.equal(pilotStatusLabel("secret-status"), "Не запущен");
  assert.equal(pilotStopLabel("operator_stop"), "Остановлен оператором");
  assert.equal(
    pilotStopLabel("auto:unknown_secret_123"),
    "Автоматическая остановка из-за нарушения целостности",
  );
});
