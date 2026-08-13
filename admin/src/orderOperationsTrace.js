const TEXT_LIMIT = 80;

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
      fulfillment: 0,
      businessEvents: 0,
      notifications: 0,
      sla: 0,
    },
    attention: {
      required: true,
      providerCommandsActionable: 0,
      providerFailures: 0,
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
    failedNotifications: nonNegativeInteger(attentionSource?.failed_notifications),
    businessEventsUnresolved: nonNegativeInteger(attentionSource?.business_events_unresolved),
    businessEventsFailed: nonNegativeInteger(attentionSource?.business_events_failed),
    overdueSla: nonNegativeInteger(attentionSource?.overdue_sla),
  };
  attention.required = attentionSource === null
    || attentionSource.required === true
    || attention.providerFailures > 0
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
      fulfillment: listCount(payload.fulfillment),
      businessEvents: listCount(payload.business_events),
      notifications: listCount(payload.notifications),
      sla: listCount(payload.sla),
    },
    attention,
  };
}
