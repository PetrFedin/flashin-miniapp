import assert from "node:assert/strict";
import test from "node:test";

import {
  normalizePilotReadiness,
  pilotReadinessCodeLabel,
} from "./pilotReadiness.js";

function diagnostics() {
  return {
    critical: {
      database: true,
      migrations: true,
      env: true,
      payments: true,
      moysklad: true,
      scheduler: true,
      notification_delivery: true,
      webhook_outbox: true,
      moysklad_sync: true,
    },
    advisory: {
      media: true,
      search: true,
    },
  };
}

function runtime() {
  return {
    checkout_decision: "GO",
    enforced: true,
    status: "active",
    accepted_orders: 3,
    remaining_orders: 17,
    allowlist_count: 2,
    database_integrity_healthy: true,
    artifact_integrity_applicable: true,
    artifact_integrity_healthy: true,
    money_attention_required: false,
    operational_safety_applicable: true,
    operational_safety_healthy: true,
  };
}

function healthyPayload() {
  return {
    schema_version: 1,
    decision: "GO",
    ready_for_next_order: true,
    blocking_codes: [],
    warning_codes: [],
    diagnostics: diagnostics(),
    runtime: runtime(),
    request_id: "safe-request-id",
  };
}

test("healthy cockpit contract remains GO without copying unselected fields", () => {
  const payload = healthyPayload();
  payload.provider_payload = { secret: "must-not-survive" };
  payload.runtime.private_run_id = "private-runtime-id";

  const result = normalizePilotReadiness(payload);

  assert.equal(result.decision, "GO");
  assert.equal(result.readyForNextOrder, true);
  assert.equal(result.contractValid, true);
  const serialized = JSON.stringify(result);
  assert.doesNotMatch(serialized, /must-not-survive|private-runtime-id|safe-request-id/);
});

test("migration drift is a valid fail-closed blocker", () => {
  const payload = healthyPayload();
  payload.decision = "NO-GO";
  payload.ready_for_next_order = false;
  payload.blocking_codes = ["diagnostic_failed:migrations"];
  payload.diagnostics.critical.migrations = false;

  const result = normalizePilotReadiness(payload);

  assert.equal(result.decision, "NO-GO");
  assert.equal(result.contractValid, true);
  assert.deepEqual(result.blockingCodes, ["diagnostic_failed:migrations"]);
  assert.equal(
    pilotReadinessCodeLabel(result.blockingCodes[0]),
    "Миграции БД: проверка не пройдена",
  );
});

test("advisory search degradation stays visible without overriding GO", () => {
  const payload = healthyPayload();
  payload.warning_codes = ["diagnostic_degraded:search"];
  payload.diagnostics.advisory.search = false;

  const result = normalizePilotReadiness(payload);

  assert.equal(result.decision, "GO");
  assert.equal(result.contractValid, true);
  assert.deepEqual(result.warningCodes, ["diagnostic_degraded:search"]);
});

test("explicit unavailable runtime remains a valid NO-GO response", () => {
  const payload = healthyPayload();
  payload.decision = "NO-GO";
  payload.ready_for_next_order = false;
  payload.blocking_codes = ["runtime_status_unavailable"];
  payload.runtime = {
    checkout_decision: "NO-GO",
    enforced: false,
    status: null,
    accepted_orders: null,
    remaining_orders: null,
    allowlist_count: null,
    database_integrity_healthy: null,
    artifact_integrity_applicable: null,
    artifact_integrity_healthy: null,
    money_attention_required: false,
    operational_safety_applicable: null,
    operational_safety_healthy: null,
  };

  const result = normalizePilotReadiness(payload);

  assert.equal(result.decision, "NO-GO");
  assert.equal(result.contractValid, true);
  assert.deepEqual(result.blockingCodes, ["runtime_status_unavailable"]);
});

test("unknown blocker text is never rendered and invalidates the contract", () => {
  const payload = healthyPayload();
  payload.decision = "NO-GO";
  payload.ready_for_next_order = false;
  payload.blocking_codes = ["secret_customer_123456789_/srv/private"];

  const result = normalizePilotReadiness(payload);
  const rendered = JSON.stringify({ result, labels: result.blockingCodes.map(pilotReadinessCodeLabel) });

  assert.equal(result.decision, "NO-GO");
  assert.equal(result.contractValid, false);
  assert.ok(result.blockingCodes.includes("response_contract_invalid"));
  assert.doesNotMatch(rendered, /123456789|srv\/private/);
});

test("incoherent GO with blockers is forced to NO-GO", () => {
  const payload = healthyPayload();
  payload.blocking_codes = ["runtime_money_attention"];
  payload.runtime.money_attention_required = true;

  const result = normalizePilotReadiness(payload);

  assert.equal(result.decision, "NO-GO");
  assert.equal(result.contractValid, false);
  assert.ok(result.blockingCodes.includes("response_contract_invalid"));
});

test("unknown label input never echoes the raw code", () => {
  assert.equal(
    pilotReadinessCodeLabel("secret customer 123456789"),
    "Ответ readiness имеет неверный или небезопасный формат",
  );
});
