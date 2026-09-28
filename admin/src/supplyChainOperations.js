const SYNC_STATUSES = new Set(["never", "started", "success", "failed", "unknown"]);
const SYNC_TYPES = new Set(["manual", "scheduled", "startup", "worker", "unknown"]);

function finiteInt(value, fallback = 0) {
  const number = Number(value);
  return Number.isInteger(number) && number >= 0 ? number : fallback;
}

function safeText(value, maxLength = 120) {
  return String(value ?? "").replace(/\s+/g, " ").trim().slice(0, maxLength);
}

function safeStatus(value, allowed) {
  const status = safeText(value, 64).toLowerCase();
  return allowed.has(status) ? status : "unknown";
}

export function normalizeSupplyChainStatus(payload) {
  if (!payload || typeof payload !== "object" || Number(payload.schema_version) !== 1) {
    return {
      valid: false,
      attentionRequired: true,
      summary: {
        lastSyncStatus: "unknown",
        lastSyncAt: null,
        pendingMatches: 0,
        openReconciliations: 0,
        openConflicts: 0,
      },
      syncLogs: [],
      skuMatches: [],
      reconciliations: [],
      conflicts: [],
    };
  }

  const summary = payload.summary && typeof payload.summary === "object" ? payload.summary : {};
  const lastSyncStatus = safeStatus(summary.last_sync_status, SYNC_STATUSES);

  const syncLogs = (Array.isArray(payload.sync_logs) ? payload.sync_logs : []).map((row) => ({
    id: finiteInt(row?.id),
    syncType: safeStatus(row?.sync_type, SYNC_TYPES),
    status: safeStatus(row?.status, SYNC_STATUSES),
    productsSeen: finiteInt(row?.products_seen),
    productsUpserted: finiteInt(row?.products_upserted),
    variantsUpserted: finiteInt(row?.variants_upserted),
    hasError: Boolean(row?.has_error),
    createdAt: safeText(row?.created_at, 64) || null,
    finishedAt: safeText(row?.finished_at, 64) || null,
  }));

  const skuMatches = (Array.isArray(payload.sku_matches) ? payload.sku_matches : []).map((row) => ({
    id: finiteInt(row?.id),
    localVariantId: finiteInt(row?.local_variant_id),
    localSku: safeText(row?.local_sku),
    externalSku: safeText(row?.external_sku),
    confidence: Math.max(0, Math.min(1, Number(row?.confidence) || 0)),
    confirmed: Boolean(row?.confirmed),
    createdAt: safeText(row?.created_at, 64) || null,
  }));

  const reconciliations = (Array.isArray(payload.reconciliations) ? payload.reconciliations : []).map((row) => ({
    id: finiteInt(row?.id),
    variantId: finiteInt(row?.variant_id),
    sku: safeText(row?.sku),
    localStockQty: Number(row?.local_stock_qty) || 0,
    externalStockQty: Number(row?.external_stock_qty) || 0,
    localReservedQty: finiteInt(row?.local_reserved_qty),
    delta: Number(row?.delta) || 0,
    status: safeText(row?.status, 64) || "unknown",
    action: safeText(row?.action, 64) || "unknown",
    createdAt: safeText(row?.created_at, 64) || null,
  }));

  const conflicts = (Array.isArray(payload.conflicts) ? payload.conflicts : []).map((row) => ({
    id: finiteInt(row?.id),
    sku: safeText(row?.sku),
    conflictType: safeText(row?.conflict_type),
    status: safeText(row?.status, 64) || "unknown",
    createdAt: safeText(row?.created_at, 64) || null,
  }));

  const valid = lastSyncStatus !== "unknown"
    && Number.isInteger(Number(summary.pending_matches))
    && Number(summary.pending_matches) >= 0
    && Number.isInteger(Number(summary.open_reconciliations))
    && Number(summary.open_reconciliations) >= 0
    && Number.isInteger(Number(summary.open_conflicts))
    && Number(summary.open_conflicts) >= 0;

  return {
    valid,
    attentionRequired: !valid || Boolean(payload.attention_required),
    summary: {
      lastSyncStatus,
      lastSyncAt: safeText(summary.last_sync_at, 64) || null,
      pendingMatches: finiteInt(summary.pending_matches),
      openReconciliations: finiteInt(summary.open_reconciliations),
      openConflicts: finiteInt(summary.open_conflicts),
    },
    syncLogs,
    skuMatches,
    reconciliations,
    conflicts,
  };
}

export function syncStatusLabel(status) {
  return ({
    never: "Ещё не запускалась",
    started: "Выполняется",
    success: "Успешно",
    failed: "Ошибка",
    unknown: "Неизвестно",
  })[status] || "Неизвестно";
}

export function syncTypeLabel(syncType) {
  return ({
    manual: "Ручная",
    scheduled: "По расписанию",
    startup: "При запуске",
    worker: "Worker",
    unknown: "Неизвестно",
  })[syncType] || "Неизвестно";
}
