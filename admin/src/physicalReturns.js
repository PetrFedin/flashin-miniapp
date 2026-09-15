export const PHYSICAL_RETURN_STATUS_LABELS = Object.freeze({
  not_started: "Не начат",
  requested: "Составляется",
  authorized: "Авторизован",
  in_transit: "В пути",
  received: "Принят",
  inspected: "Осмотр завершён",
});

export const PHYSICAL_DISPOSITION_LABELS = Object.freeze({
  resalable: "Вернуть в продажу",
  damaged: "Повреждён",
  quarantine: "Карантин",
});

const STORAGE_KEY = "flashin.physical-return.idempotency.v1";
const MAX_ENTRIES = 50;

function positiveInteger(value) {
  const number = Number(value);
  return Number.isInteger(number) && number > 0 ? number : null;
}

export function normalizePhysicalQuantity(rawValue, maximum, label = "Количество") {
  const quantity = positiveInteger(rawValue);
  const max = positiveInteger(maximum);
  if (quantity === null) return { error: `${label} должно быть положительным целым числом.` };
  if (max === null || quantity > max) return { error: `${label} превышает доступное количество.` };
  return { value: quantity };
}

export function physicalRemaining(item, phase) {
  if (!item || typeof item !== "object") return 0;
  if (phase === "authorize") return Math.max(Number(item.ordered_qty || 0) - Number(item.authorized_qty || 0), 0);
  if (phase === "receive") return Math.max(Number(item.authorized_qty || 0) - Number(item.received_qty || 0), 0);
  if (phase === "inspect") return Math.max(Number(item.received_qty || 0) - Number(item.inspected_qty || 0), 0);
  return 0;
}

export function physicalMutationSignature({
  operation,
  returnId,
  orderItemId,
  quantity,
  disposition = "",
  reason = "",
}) {
  return JSON.stringify([
    String(operation || ""),
    Number(returnId),
    Number(orderItemId),
    Number(quantity),
    String(disposition || "").trim().toLowerCase(),
    String(reason || "").trim().slice(0, 2000),
  ]);
}

function readRegistry(storage) {
  if (!storage) return [];
  try {
    const parsed = JSON.parse(storage.getItem(STORAGE_KEY) || "[]");
    if (!Array.isArray(parsed)) return [];
    return parsed.filter((entry) => (
      entry && typeof entry.signature === "string" && typeof entry.key === "string"
    )).slice(-MAX_ENTRIES);
  } catch {
    return [];
  }
}

function writeRegistry(storage, entries) {
  if (!storage) return;
  storage.setItem(STORAGE_KEY, JSON.stringify(entries.slice(-MAX_ENTRIES)));
}

export function getPhysicalIdempotencyKey(signature, {
  storage = globalThis.sessionStorage,
  createKey = () => `physical-${globalThis.crypto.randomUUID()}`,
} = {}) {
  const normalized = String(signature || "");
  if (!normalized) throw new Error("Physical return mutation signature is required");
  const entries = readRegistry(storage);
  const existing = entries.find((entry) => entry.signature === normalized);
  if (existing) return existing.key;
  const key = String(createKey());
  entries.push({ signature: normalized, key });
  writeRegistry(storage, entries);
  return key;
}

export function clearPhysicalIdempotencyKey(signature, {
  storage = globalThis.sessionStorage,
} = {}) {
  if (!storage) return;
  const normalized = String(signature || "");
  const entries = readRegistry(storage).filter((entry) => entry.signature !== normalized);
  writeRegistry(storage, entries);
}
