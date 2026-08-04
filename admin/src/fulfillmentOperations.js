export const FULFILLMENT_STATUS_LABELS = Object.freeze({
  new: "Новая задача",
  picking: "Сборка",
  blocked: "Заблокирована",
  packed: "Упакован",
  ready: "Готов к передаче",
});

export const SHIPMENT_STATUS_LABELS = Object.freeze({
  created: "Создана",
  shipped: "Отправлена",
  delivered: "Доставлена",
});

export function normalizeTracking(rawValue) {
  const value = String(rawValue ?? "").trim();
  if (value.length < 3) {
    return { error: "Укажите трек-номер минимум из трёх символов." };
  }
  if (value.length > 255) {
    return { error: "Трек-номер не должен превышать 255 символов." };
  }
  return { value };
}

export function isPicklistComplete(items = []) {
  return items.length > 0 && items.every((item) => (
    item.status === "picked"
    && Number(item.picked_qty) === Number(item.quantity)
    && Number(item.quantity) > 0
  ));
}

export function fulfillmentAction(task, shipment = null) {
  if (!task) return null;
  if (task.status === "new") {
    return { type: "task", status: "picking", label: "Начать сборку" };
  }
  if (task.status === "blocked") {
    return { type: "task", status: "picking", label: "Возобновить сборку" };
  }
  if (task.status === "picking") {
    return { type: "pick_pack", label: "Собрать все позиции и упаковать" };
  }
  if (task.status === "packed") {
    return { type: "task", status: "ready", label: "Подтвердить готовность" };
  }
  if (task.status !== "ready") return null;
  if (!shipment) {
    return { type: "create_shipment", label: "Создать отгрузку" };
  }
  if (shipment.status === "created") {
    return { type: "ship", label: "Передать в доставку" };
  }
  if (shipment.status === "shipped") {
    return { type: "deliver", label: "Подтвердить доставку" };
  }
  return null;
}

export function fulfillmentAttentionCount(tasks = [], shipments = []) {
  const shipmentsByOrder = new Map(
    shipments.map((shipment) => [Number(shipment.order_id), shipment]),
  );
  return tasks.filter((task) => {
    const shipment = shipmentsByOrder.get(Number(task.order_id));
    return task.status !== "ready" || shipment?.status !== "delivered";
  }).length;
}
