export const ORDER_LABELS = Object.freeze({
  created: "Заказ создан",
  payment_created: "Ожидает оплаты",
  paid: "Оплачен",
  assembling: "Собирается",
  ready: "Готов к отправке",
  shipped: "Передан в доставку",
  completed: "Доставлен",
  refund_requested: "Возврат рассматривается",
  partially_refunded: "Частично возвращён",
  refunded: "Возвращён",
  payment_review_required: "Требует проверки оплаты",
  cancelled: "Отменён",
});

export const PAYMENT_LABELS = Object.freeze({
  pending: "Оплата не начата",
  payment_created: "Ожидает оплаты",
  paid: "Оплачено",
  partially_refunded: "Частичный возврат",
  refund_processing: "Возврат обрабатывается",
  refund_pending: "Возврат ожидает подтверждения",
  refund_review_required: "Возврат требует проверки",
  paid_review_required: "Оплата требует проверки",
  refunded: "Возвращено",
  cancelled: "Отменено",
});

export const DELIVERY_LABELS = Object.freeze({
  not_started: "Не начата",
  assembling: "Комплектуется",
  ready: "Готова",
  shipped: "В пути",
  delivered: "Доставлена",
  cancelled: "Отменена",
});

export function canPayOrder(order) {
  return ["created", "payment_created"].includes(order?.status)
    && ["pending", "payment_created"].includes(order?.payment_status);
}

export function canCancelOrder(order) {
  return order?.status === "created" && order?.payment_status === "pending";
}

export function canReturnOrder(order) {
  return ["paid", "partially_refunded"].includes(order?.payment_status)
    && !["refund_requested", "refunded", "cancelled"].includes(order?.status);
}

export function paymentReturnMessage(orderId, paymentStatus) {
  if (paymentStatus === "paid") {
    return { type: "notice", text: `Заказ #${orderId} оплачен. Статус обновлён.` };
  }
  if (paymentStatus === "cancelled") {
    return { type: "error", text: `Оплата заказа #${orderId} отменена.` };
  }
  return {
    type: "notice",
    text: `Заказ #${orderId} создан. Подтверждение оплаты ещё обрабатывается.`,
  };
}
