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


function refundComponentAmount(rawValue, label) {
  const normalized = String(rawValue ?? "").trim();
  if (!normalized) return { value: 0 };
  const amount = Number(normalized);
  if (!Number.isFinite(amount) || amount < 0) {
    return { error: `${label}: сумма должна быть неотрицательным числом.` };
  }
  return { value: Math.round(amount * 100) / 100 };
}

export function buildRefundAllocationPayload(item, draft = {}, refundAmount) {
  const target = Math.round(Number(refundAmount) * 100);
  if (!Number.isInteger(target) || target <= 0) {
    return { error: "Сумма refund allocation должна быть больше нуля." };
  }

  const options = item?.financial_allocation_options || {};
  const fixed = Number(item?.financial_allocation?.allocated_cents || 0) > 0;
  if (fixed) return { allocations: [] };

  const allocations = [];
  let totalCents = 0;

  for (const line of Array.isArray(options.items) ? options.items : []) {
    const key = `item:${line.order_item_id}`;
    const parsed = refundComponentAmount(draft[key], `Товар #${line.order_item_id}`);
    if (parsed.error) return parsed;
    const cents = Math.round(parsed.value * 100);
    if (!cents) continue;
    if (cents > Number(line.remaining_cents || 0)) {
      return { error: `Allocation по товару #${line.order_item_id} превышает оставшуюся стоимость.` };
    }
    allocations.push({
      component_kind: "item",
      order_item_id: Number(line.order_item_id),
      amount: cents / 100,
    });
    totalCents += cents;
  }

  const delivery = refundComponentAmount(draft.delivery, "Доставка");
  if (delivery.error) return delivery;
  const deliveryCents = Math.round(delivery.value * 100);
  if (deliveryCents > Number(options.delivery_remaining_cents || 0)) {
    return { error: "Allocation по доставке превышает оставшуюся стоимость доставки." };
  }
  if (deliveryCents) {
    allocations.push({ component_kind: "delivery", amount: deliveryCents / 100 });
    totalCents += deliveryCents;
  }

  const goodwill = refundComponentAmount(draft.goodwill, "Goodwill");
  if (goodwill.error) return goodwill;
  const goodwillCents = Math.round(goodwill.value * 100);
  if (goodwillCents) {
    allocations.push({ component_kind: "goodwill", amount: goodwillCents / 100 });
    totalCents += goodwillCents;
  }

  if (!allocations.length) {
    const autoCents = (
      (Array.isArray(options.items) ? options.items : [])
        .reduce((sum, line) => sum + Number(line.remaining_cents || 0), 0)
      + Number(options.delivery_remaining_cents || 0)
    );
    if (autoCents === target) return { allocations: [] };
    return {
      error: "Для частичного или goodwill refund укажите финансовое распределение по товарам, доставке или goodwill.",
    };
  }

  if (totalCents !== target) {
    return {
      error: `Сумма allocation ${(totalCents / 100).toFixed(2)} не совпадает с refund ${(target / 100).toFixed(2)}.`,
    };
  }
  return { allocations };
}

export function refundReconciliationLabel(status) {
  return ({
    PASS: "PASS · деньги и физический возврат согласованы",
    PENDING: "PENDING · ожидается финансовая/физическая стадия",
    REVIEW: "REVIEW · требуется сверка оператора",
    BLOCKED: "BLOCKED · нарушен финансовый инвариант",
  })[status] || "Нет reconciliation evidence";
}
