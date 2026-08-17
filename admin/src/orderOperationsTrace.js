const TEXT_LIMIT = 80;
const LIFECYCLE_STATUSES = new Set(["PASS", "PENDING", "REVIEW", "BLOCKED"]);
const LIFECYCLE_STATUS_RANK = { PASS: 0, PENDING: 1, REVIEW: 2, BLOCKED: 3 };
const LIFECYCLE_STAGE_KEYS = new Set([
  "payment",
  "inventory",
  "moysklad",
  "fulfillment",
  "refunds",
  "notifications",
]);
const OPERATIONAL_SIGNAL_KEYS = new Set(["business_events"]);

function boundedText(value, fallback = "unknown") {
  const text = String(value ?? "").trim();
  return (text || fallback).slice(0, TEXT_LIMIT);
}

function finiteNumber(value) {
  const number = Number(value);
  return Number.isFinite(number) ? number : 0;
}

function nonNegativeInteger(value) {
  const number = Number(value);
  return Number.isFinite(number) ? Math.max(0, Math.trunc(number)) : 0;
}

function listCount(value) {
  return Array.isArray(value) ? value.length : 0;
}

function normalizeLifecycleStatus(value) {
  const status = String(value ?? "").trim().toUpperCase();
  return LIFECYCLE_STATUSES.has(status) ? status : "REVIEW";
}

function stricterLifecycleStatus(left, right) {
  return LIFECYCLE_STATUS_RANK[left] >= LIFECYCLE_STATUS_RANK[right] ? left : right;
}

function normalizeEvidence(value) {
  return Array.isArray(value)
    ? value.slice(0, 6).map((entry) => boundedText(entry, "")).filter(Boolean)
    : [];
}

function normalizeReconciliation(value) {
  const invalid = {
    valid: false,
    overallStatus: "REVIEW",
    requiresOperatorAction: true,
    stages: [],
    operationalSignals: [],
  };
  if (!value || typeof value !== "object" || !Array.isArray(value.stages)) return invalid;

  const stages = value.stages
    .filter((item) => item && typeof item === "object" && LIFECYCLE_STAGE_KEYS.has(String(item.key || "")))
    .slice(0, LIFECYCLE_STAGE_KEYS.size)
    .map((item) => ({
      key: String(item.key),
      status: normalizeLifecycleStatus(item.status),
      reason: boundedText(item.reason),
      nextAction: boundedText(item.next_action, "none"),
      evidence: normalizeEvidence(item.evidence),
    }));

  if (stages.length !== LIFECYCLE_STAGE_KEYS.size) return invalid;
  const uniqueKeys = new Set(stages.map((item) => item.key));
  if (uniqueKeys.size !== LIFECYCLE_STAGE_KEYS.size) return invalid;

  const rawSignals = value.operational_signals ?? [];
  if (!Array.isArray(rawSignals) || rawSignals.length > OPERATIONAL_SIGNAL_KEYS.size) return invalid;
  if (rawSignals.some((item) => (
    !item
    || typeof item !== "object"
    || !OPERATIONAL_SIGNAL_KEYS.has(String(item.key || ""))
  ))) return invalid;

  const operationalSignals = rawSignals.map((item) => ({
    key: String(item.key),
    status: normalizeLifecycleStatus(item.status),
    reason: boundedText(item.reason),
    nextAction: boundedText(item.next_action, "none"),
    evidence: normalizeEvidence(item.evidence),
  }));

  const suppliedOverall = normalizeLifecycleStatus(value.overall_status);
  const stageOverall = stages.reduce(
    (current, item) => stricterLifecycleStatus(current, item.status),
    "PASS",
  );
  const signalOverall = operationalSignals.reduce(
    (current, item) => stricterLifecycleStatus(current, item.status),
    "PASS",
  );
  const overallStatus = stricterLifecycleStatus(
    suppliedOverall,
    stricterLifecycleStatus(stageOverall, signalOverall),
  );
  const requiresOperatorAction = value.requires_operator_action === true
    || overallStatus === "REVIEW"
    || overallStatus === "BLOCKED";
  return {
    valid: true,
    overallStatus,
    requiresOperatorAction,
    stages,
    operationalSignals,
  };
}

export function normalizeOrderOperationsTrace(payload) {
  const invalid = {
    valid: false,
    order: null,
    requestId: "",
    counts: {
      payments: 0,
      paymentEvents: 0,
      returns: 0,
      providerCommands: 0,
      inventoryMovements: 0,
      fulfillment: 0,
      businessEvents: 0,
      notifications: 0,
      sla: 0,
    },
    reconciliation: normalizeReconciliation(null),
    attention: {
      required: true,
      providerCommandsActionable: 0,
      providerFailures: 0,
      inventoryInvalidRows: 0,
      failedNotifications: 0,
      businessEventsUnresolved: 0,
      businessEventsFailed: 0,
      overdueSla: 0,
    },
  };

  if (!payload || typeof payload !== "object" || !payload.order || typeof payload.order !== "object") {
    return invalid;
  }

  const orderId = Number(payload.order.id);
  if (!Number.isInteger(orderId) || orderId <= 0) return invalid;

  const attentionSource = payload.attention && typeof payload.attention === "object"
    ? payload.attention
    : null;
  const attention = {
    providerCommandsActionable: nonNegativeInteger(attentionSource?.provider_commands_actionable),
    providerFailures: nonNegativeInteger(attentionSource?.provider_failures),
    inventoryInvalidRows: nonNegativeInteger(attentionSource?.inventory_invalid_rows),
    failedNotifications: nonNegativeInteger(attentionSource?.failed_notifications),
    businessEventsUnresolved: nonNegativeInteger(attentionSource?.business_events_unresolved),
    businessEventsFailed: nonNegativeInteger(attentionSource?.business_events_failed),
    overdueSla: nonNegativeInteger(attentionSource?.overdue_sla),
  };
  attention.required = attentionSource === null
    || attentionSource.required === true
    || attention.providerFailures > 0
    || attention.inventoryInvalidRows > 0
    || attention.failedNotifications > 0
    || attention.businessEventsFailed > 0
    || attention.overdueSla > 0;

  return {
    valid: true,
    order: {
      id: orderId,
      status: boundedText(payload.order.status),
      paymentStatus: boundedText(payload.order.payment_status),
      deliveryStatus: boundedText(payload.order.delivery_status),
      totalAmount: finiteNumber(payload.order.total_amount),
      currency: boundedText(payload.order.currency, "RUB"),
    },
    requestId: boundedText(payload.request_id, ""),
    counts: {
      payments: listCount(payload.payments),
      paymentEvents: listCount(payload.payment_events),
      returns: listCount(payload.returns),
      providerCommands: listCount(payload.provider_commands),
      inventoryMovements: listCount(payload.inventory),
      fulfillment: listCount(payload.fulfillment),
      businessEvents: listCount(payload.business_events),
      notifications: listCount(payload.notifications),
      sla: listCount(payload.sla),
    },
    reconciliation: normalizeReconciliation(payload.reconciliation),
    attention,
  };
}