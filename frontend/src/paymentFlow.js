const REDIRECTABLE_PAYMENT_STATUSES = new Set(["pending", "waiting_for_capture"]);

export function paymentContinuationUrl(payment, orderId) {
  const status = String(payment?.status || "").trim().toLowerCase();
  const confirmationUrl = String(payment?.confirmation_url || "").trim();
  const normalizedOrderId = Number(orderId || payment?.order_id);

  if (status === "succeeded") {
    if (!Number.isInteger(normalizedOrderId) || normalizedOrderId < 1) return "";
    return `/payment-result?order_id=${encodeURIComponent(normalizedOrderId)}`;
  }

  if (REDIRECTABLE_PAYMENT_STATUSES.has(status) && confirmationUrl) {
    return confirmationUrl;
  }

  return "";
}

export function normalizePaymentContinuation(payment, orderId) {
  return {
    ...payment,
    confirmation_url: paymentContinuationUrl(payment, orderId),
  };
}
