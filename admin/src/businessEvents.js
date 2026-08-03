export const BUSINESS_EVENT_STATUS_LABELS = Object.freeze({
  pending: "Ожидает обработки",
  processed: "Обработано",
  failed: "Требует вмешательства",
});

export function eventStatusLabel(status) {
  return BUSINESS_EVENT_STATUS_LABELS[status] || status || "Неизвестно";
}

export function formatEventDate(value) {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "—";
  return new Intl.DateTimeFormat("ru-RU", {
    dateStyle: "short",
    timeStyle: "medium",
  }).format(date);
}

export function canReplayBusinessEvent(event) {
  return Boolean(event && event.status === "failed");
}

export function compactEventError(value, maxLength = 160) {
  const normalized = String(value || "").trim();
  if (!normalized) return "Ошибка не зафиксирована";
  if (normalized.length <= maxLength) return normalized;
  return `${normalized.slice(0, Math.max(1, maxLength - 1))}…`;
}

export function buildBusinessEventReplayBody(reason, payloadText = "") {
  const normalizedReason = String(reason || "").trim();
  if (normalizedReason.length < 5) {
    throw new Error(
      "Укажите конкретную причину повторной обработки (минимум 5 символов).",
    );
  }
  if (normalizedReason.length > 500) {
    throw new Error("Причина повторной обработки не должна превышать 500 символов.");
  }

  const normalizedPayload = String(payloadText || "").trim();
  if (!normalizedPayload) return { reason: normalizedReason };

  let payload;
  try {
    payload = JSON.parse(normalizedPayload);
  } catch {
    throw new Error("Исправленный payload должен быть валидным JSON.");
  }
  if (!payload || Array.isArray(payload) || typeof payload !== "object") {
    throw new Error("Исправленный payload должен быть JSON-объектом.");
  }
  return { reason: normalizedReason, payload };
}
