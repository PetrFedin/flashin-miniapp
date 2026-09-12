export const NEXT_FULFILLMENT_STATUS = Object.freeze({
  paid: "assembling",
});

export const ORDER_STATUS_LABELS = Object.freeze({
  created: "Создан",
  payment_created: "Ожидает оплаты",
  payment_review_required: "Проверка оплаты",
  paid: "Оплачен",
  assembling: "Собирается",
  ready: "Готов",
  shipped: "Отправлен",
  completed: "Завершён",
  refund_requested: "Запрошен возврат",
  partially_refunded: "Частично возвращён",
  refunded: "Возвращён",
  cancelled: "Отменён",
});

export function nextFulfillmentStatus(order) {
  return NEXT_FULFILLMENT_STATUS[order?.status] || null;
}

export function canCancelBeforePayment(order) {
  return order?.status === "created" && order?.payment_status === "pending";
}

export function orderAction(order) {
  const nextStatus = nextFulfillmentStatus(order);
  if (nextStatus) {
    return {
      type: "advance",
      status: nextStatus,
      label: `Перевести: ${ORDER_STATUS_LABELS[nextStatus]}`,
    };
  }
  if (canCancelBeforePayment(order)) {
    return {
      type: "cancel",
      status: "cancelled",
      label: "Отменить до оплаты",
    };
  }
  return null;
}
