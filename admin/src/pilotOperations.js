const PILOT_STATUSES = new Set(["not_armed", "active", "stopped", "completed"]);
const SAFE_RUN_REF = /^[0-9a-f]{12}$/;

export const PILOT_INTEGRITY_LABELS = Object.freeze({
  runtime_state_missing: "Состояние пилота не создано",
  runtime_state_id_invalid: "Нарушен singleton runtime state",
  runtime_state_not_singleton: "Найдено несколько runtime states",
  orphan_slots_without_runtime_state: "Обнаружены слоты без runtime state",
  configured_max_orders_not_twenty: "Лимит приложения не равен 20",
  runtime_config_max_orders_mismatch: "Лимиты приложения и БД не совпадают",
  max_orders_not_twenty: "Лимит runtime state не равен 20",
  accepted_orders_out_of_range: "Счётчик принятых заказов вне допустимого диапазона",
  slot_count_mismatch: "Число слотов не совпадает со счётчиком",
  slot_sequence_gap: "Нарушена последовательность слотов",
  slot_admission_binding_mismatch: "Слот связан с другим admission",
  duplicate_pilot_order: "Один заказ занял несколько слотов",
  run_id_missing: "Отсутствует идентификатор запуска",
  admission_binding_invalid: "Некорректная привязка admission",
  release_binding_invalid: "Некорректная привязка release",
  pilot_state_binding_missing: "Отсутствует привязка pilot control",
  active_allowlist_empty: "Активный пилот не имеет разрешённых пользователей",
  active_runtime_exhausted: "Активный runtime уже исчерпал лимит",
  completed_before_limit: "Runtime завершён до двадцатого заказа",
  stopped_without_reason: "Остановка не содержит причины",
  admission_evidence_invalid: "Admission evidence не подтверждён",
  current_release_invalid: "Текущий release не подтверждён",
  previous_release_invalid: "Rollback release не подтверждён",
  release_capability_invalid: "Release capability v2 не подтверждена",
  pilot_control_invalid: "Pilot control state не подтверждён",
  evidence_file_invalid: "Один из evidence-файлов не подтверждён",
  configuration_fingerprint_mismatch: "Конфигурация отличается от admission",
  signing_configuration_invalid: "Подпись pilot evidence не настроена",
  runtime_artifact_invalid: "Runtime artifacts не прошли проверку",
  response_contract_invalid: "Ответ pilot status имеет неверный формат",
});

export const PILOT_STOP_LABELS = Object.freeze({
  operator_stop: "Остановлен оператором",
  "auto:provider_payment_amount_invalid": "Провайдер вернул некорректную сумму",
  "auto:provider_payment_amount_or_currency_mismatch": "Сумма или валюта оплаты не совпала",
  "auto:provider_payment_order_reference_mismatch": "Оплата связана с другим заказом",
  "auto:provider_payment_confirmation_missing": "У активной оплаты нет confirmation URL",
  "auto:provider_payment_status_requires_review": "Статус оплаты требует проверки",
  "auto:provider_payment_id_invalid": "Провайдер вернул некорректный payment ID",
  "auto:provider_payment_status_invalid": "Провайдер вернул некорректный статус оплаты",
  "auto:stored_payment_order_mismatch": "Локальная оплата связана с другим заказом",
  "auto:payment_review:paid_after_cancel": "Оплата пришла после отмены заказа",
  "auto:payment_review:canceled_after_settlement": "Отмена пришла после settlement",
  "auto:payment_review:provider_cancel_conflict": "Отмена провайдера конфликтует с заказом",
  "auto:payment_reconciliation_mismatch": "Reconciliation выявил расхождение",
  "auto:refund_retry_required": "Возврат требует повторной попытки",
  "auto:refund_review_required": "Возврат требует ручной проверки",
  "auto:refund_finalization_integrity_failure": "Нарушена целостность финализации возврата",
  "auto:refund_finalization_integrity_conflict": "Конфликт повторной финализации возврата",
  "auto:pilot_slot_runtime_mismatch": "Слот связан с другим runtime",
  "auto:integrity_failure": "Автоматическая остановка из-за нарушения целостности",
});

function isObject(value) {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function safeInteger(value, fallback = 0) {
  return Number.isSafeInteger(value) && value >= 0 ? value : fallback;
}

function safeTimestamp(value) {
  if (typeof value !== "string" || !value.trim()) return null;
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? null : parsed.toISOString();
}

function safeCodes(value) {
  if (!Array.isArray(value)) return ["response_contract_invalid"];
  const known = value.filter((code) => (
    typeof code === "string" && Object.hasOwn(PILOT_INTEGRITY_LABELS, code)
  ));
  if (known.length !== value.length) known.push("response_contract_invalid");
  return [...new Set(known)];
}

function fallbackStatus() {
  return {
    decision: "NO-GO",
    generatedAt: null,
    enforced: false,
    contractValid: false,
    runtime: {
      present: false,
      status: "not_armed",
      runRef: null,
      maxOrders: 20,
      acceptedOrders: 0,
      remainingOrders: 20,
      slotCount: 0,
      historicalSlotCount: 0,
      allowlistCount: 0,
      stopReason: null,
      openedAt: null,
      stoppedAt: null,
      completedAt: null,
      updatedAt: null,
    },
    databaseIntegrity: {
      healthy: false,
      codes: ["response_contract_invalid"],
    },
    artifactIntegrity: {
      applicable: false,
      healthy: null,
      codes: ["response_contract_invalid"],
    },
    moneyAttention: {
      paymentReviewOrders: 0,
      refundAttentionOrders: 0,
      reconciliationMismatches: 0,
      attentionRequired: false,
    },
  };
}

export function normalizePilotOperationsStatus(payload) {
  if (!isObject(payload) || payload.schema_version !== 1) return fallbackStatus();

  const runtime = isObject(payload.runtime) ? payload.runtime : {};
  const database = isObject(payload.database_integrity) ? payload.database_integrity : {};
  const artifacts = isObject(payload.artifact_integrity) ? payload.artifact_integrity : {};
  const money = isObject(payload.money_attention) ? payload.money_attention : {};
  const databaseCodes = safeCodes(database.codes);
  const artifactCodes = safeCodes(artifacts.codes);
  const status = PILOT_STATUSES.has(runtime.status) ? runtime.status : "not_armed";
  const runRef = typeof runtime.run_ref === "string" && SAFE_RUN_REF.test(runtime.run_ref)
    ? runtime.run_ref
    : null;
  const stopReason = typeof runtime.stop_reason === "string"
    && Object.hasOwn(PILOT_STOP_LABELS, runtime.stop_reason)
    ? runtime.stop_reason
    : null;
  const expectedDecision = payload.checkout_decision === "GO" || payload.checkout_decision === "NO-GO";
  const contractValid = Boolean(
    expectedDecision
    && isObject(payload.runtime)
    && isObject(payload.database_integrity)
    && isObject(payload.artifact_integrity)
    && isObject(payload.money_attention)
    && databaseCodes.length === (Array.isArray(database.codes) ? new Set(database.codes).size : -1)
    && artifactCodes.length === (Array.isArray(artifacts.codes) ? new Set(artifacts.codes).size : -1)
  );

  const databaseHealthy = database.healthy === true && !databaseCodes.length;
  const artifactHealthy = artifacts.healthy === true && !artifactCodes.length;
  const attentionRequired = money.attention_required === true;
  const decision = payload.checkout_decision === "GO"
    && contractValid
    && payload.enforced === true
    && status === "active"
    && databaseHealthy
    && artifactHealthy
    && !attentionRequired
    ? "GO"
    : "NO-GO";

  return {
    decision,
    generatedAt: safeTimestamp(payload.generated_at),
    enforced: payload.enforced === true,
    contractValid,
    runtime: {
      present: runtime.present === true,
      status,
      runRef,
      maxOrders: safeInteger(runtime.max_orders, 20),
      acceptedOrders: safeInteger(runtime.accepted_orders),
      remainingOrders: safeInteger(runtime.remaining_orders),
      slotCount: safeInteger(runtime.slot_count),
      historicalSlotCount: safeInteger(runtime.historical_slot_count),
      allowlistCount: safeInteger(runtime.allowlist_count),
      stopReason,
      openedAt: safeTimestamp(runtime.opened_at),
      stoppedAt: safeTimestamp(runtime.stopped_at),
      completedAt: safeTimestamp(runtime.completed_at),
      updatedAt: safeTimestamp(runtime.updated_at),
    },
    databaseIntegrity: {
      healthy: databaseHealthy,
      codes: contractValid ? databaseCodes : [...new Set([...databaseCodes, "response_contract_invalid"])],
    },
    artifactIntegrity: {
      applicable: artifacts.applicable === true,
      healthy: artifacts.healthy === null ? null : artifactHealthy,
      codes: contractValid ? artifactCodes : [...new Set([...artifactCodes, "response_contract_invalid"])],
    },
    moneyAttention: {
      paymentReviewOrders: safeInteger(money.payment_review_orders),
      refundAttentionOrders: safeInteger(money.refund_attention_orders),
      reconciliationMismatches: safeInteger(money.reconciliation_mismatches),
      attentionRequired,
    },
  };
}

export function pilotIntegrityLabels(codes) {
  if (!Array.isArray(codes) || !codes.length) return [];
  return codes.map((code) => (
    PILOT_INTEGRITY_LABELS[code] || PILOT_INTEGRITY_LABELS.response_contract_invalid
  ));
}

export function pilotStopLabel(reason) {
  if (!reason) return "Причина остановки не зафиксирована";
  return PILOT_STOP_LABELS[reason] || PILOT_STOP_LABELS["auto:integrity_failure"];
}

export function pilotStatusLabel(status) {
  return {
    not_armed: "Не запущен",
    active: "Активен",
    stopped: "Остановлен",
    completed: "Завершён",
  }[status] || "Не запущен";
}

export function formatPilotTimestamp(value) {
  if (!value) return "—";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return "—";
  return new Intl.DateTimeFormat("ru-RU", {
    dateStyle: "short",
    timeStyle: "medium",
  }).format(parsed);
}
