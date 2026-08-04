export const SUPPORT_STATUS_LABELS = Object.freeze({
  open: "Новый",
  in_progress: "В работе",
  waiting_customer: "Ожидает клиента",
  resolved: "Решён",
  closed: "Закрыт",
});

export const SUPPORT_PRIORITY_LABELS = Object.freeze({
  low: "Низкий",
  normal: "Обычный",
  high: "Высокий",
  urgent: "Срочный",
});

export const PRIVACY_TYPE_LABELS = Object.freeze({
  export: "Экспорт данных",
  consent_withdrawal: "Отзыв согласий",
  delete: "Удаление данных",
});

export const PRIVACY_STATUS_LABELS = Object.freeze({
  requested: "Зарегистрирован",
  processing: "Обрабатывается",
  processed: "Исполнен",
  rejected: "Отклонён",
});

export const RETURN_STATUS_LABELS = Object.freeze({
  requested: "Запрошен",
  processing: "Создание возврата",
  refund_pending: "Ожидает провайдера",
  refund_retry_required: "Нужен повтор",
  refund_review_required: "Нужна проверка",
  approved: "Возвращён полностью",
  approved_partial: "Возвращён частично",
  rejected: "Отклонён",
});

export const SUPPORT_TRANSITIONS = Object.freeze({
  open: ["in_progress", "waiting_customer", "resolved", "closed"],
  in_progress: ["waiting_customer", "resolved", "closed"],
  waiting_customer: ["in_progress", "resolved", "closed"],
  resolved: ["in_progress", "closed"],
  closed: [],
});

const OPEN_PRIVACY_STATUSES = new Set(["requested", "processing"]);
const REFUND_ACTION_STATUSES = new Set([
  "requested",
  "processing",
  "refund_pending",
  "refund_retry_required",
  "refund_review_required",
]);

export function supportTransitions(status) {
  return SUPPORT_TRANSITIONS[status] || [];
}

export function canProcessPrivacy(status) {
  return OPEN_PRIVACY_STATUSES.has(status);
}

export function canApproveReturn(item) {
  return REFUND_ACTION_STATUSES.has(item?.status) && Number(item?.refundable_balance) > 0;
}

export function normalizeAdminAssignment(rawValue) {
  const normalized = String(rawValue ?? "").trim();
  if (!normalized) return { value: null };
  const adminId = Number(normalized);
  if (!Number.isInteger(adminId) || adminId <= 0) {
    return { error: "ID ответственного администратора должен быть положительным целым числом." };
  }
  return { value: adminId };
}

export function normalizeRefundAmount(rawValue, refundableBalance) {
  const amount = Number(rawValue);
  const balance = Number(refundableBalance);
  if (!Number.isFinite(amount) || amount <= 0) {
    return { error: "Сумма возврата должна быть больше нуля." };
  }
  if (!Number.isFinite(balance) || balance <= 0) {
    return { error: "У заказа нет доступного остатка для возврата." };
  }
  if (amount > balance) {
    return { error: "Сумма возврата превышает доступный остаток." };
  }
  return { value: Math.round(amount * 100) / 100 };
}

export function serviceAttentionCount({ tickets = [], privacy = [], returns = [] } = {}) {
  const ticketAttention = tickets.filter((ticket) => !["resolved", "closed"].includes(ticket.status)).length;
  const privacyAttention = privacy.filter((request) => canProcessPrivacy(request.status)).length;
  const returnAttention = returns.filter((item) => canApproveReturn(item)).length;
  return ticketAttention + privacyAttention + returnAttention;
}
