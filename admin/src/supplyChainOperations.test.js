import assert from "node:assert/strict";
import test from "node:test";

import {
  normalizeSupplyChainStatus,
  syncStatusLabel,
  syncTypeLabel,
} from "./supplyChainOperations.js";


function healthyPayload(overrides = {}) {
  return {
    schema_version: 1,
    attention_required: false,
    summary: {
      last_sync_status: "success",
      last_sync_at: "2026-08-12T18:00:00",
      pending_matches: 0,
      open_reconciliations: 0,
      open_conflicts: 0,
    },
    sync_logs: [{
      id: 1,
      sync_type: "manual",
      status: "success",
      products_seen: 12,
      products_upserted: 2,
      variants_upserted: 3,
      has_error: false,
      created_at: "2026-08-12T17:59:00",
      finished_at: "2026-08-12T18:00:00",
    }],
    sku_matches: [],
    reconciliations: [],
    conflicts: [],
    ...overrides,
  };
}


test("healthy Supply Chain contract normalizes to no attention", () => {
  const result = normalizeSupplyChainStatus(healthyPayload());
  assert.equal(result.valid, true);
  assert.equal(result.attentionRequired, false);
  assert.equal(result.summary.lastSyncStatus, "success");
  assert.equal(result.syncLogs[0].productsSeen, 12);
  assert.equal(syncStatusLabel("success"), "Успешно");
  assert.equal(syncTypeLabel("manual"), "Ручная");
});


test("unknown or malformed status fails attention closed without reflecting raw status", () => {
  const payload = healthyPayload();
  payload.summary.last_sync_status = "provider-secret-status";
  payload.sync_logs[0].sync_type = "<secret-sync-type>";
  payload.sync_logs[0].status = "provider-secret-status";

  const result = normalizeSupplyChainStatus(payload);
  assert.equal(result.valid, false);
  assert.equal(result.attentionRequired, true);
  assert.equal(result.summary.lastSyncStatus, "unknown");
  assert.equal(result.syncLogs[0].syncType, "unknown");
  assert.equal(result.syncLogs[0].status, "unknown");
  assert.doesNotMatch(JSON.stringify(result), /provider-secret|secret-sync-type/);
});


test("untrusted provider-only fields are not copied into Admin state", () => {
  const payload = healthyPayload({
    raw_payload: { token: "never-reflect-me" },
    provider_secret: "never-reflect-me",
    conflicts: [{
      id: 4,
      sku: "FLASH-001-M",
      conflict_type: "stock_below_reserved",
      status: "open",
      created_at: "2026-08-12T18:00:00",
      message: "never-reflect-me",
      moysklad_id: "never-reflect-me",
    }],
  });
  payload.attention_required = true;
  payload.summary.open_conflicts = 1;

  const result = normalizeSupplyChainStatus(payload);
  assert.equal(result.valid, true);
  assert.equal(result.attentionRequired, true);
  assert.equal(result.conflicts[0].conflictType, "stock_below_reserved");
  assert.doesNotMatch(JSON.stringify(result), /never-reflect-me|raw_payload|provider_secret|moysklad_id/);
});


test("missing contract is fail-closed", () => {
  const result = normalizeSupplyChainStatus(null);
  assert.equal(result.valid, false);
  assert.equal(result.attentionRequired, true);
  assert.equal(result.summary.lastSyncStatus, "unknown");
  assert.deepEqual(result.syncLogs, []);
});
