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
  pilot_database_evidence_invalid: "Подписанные результаты пилота не сходятся с PostgreSQL",
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
  "auto:payment_review_required": "Оплата требует ручной проверки",
  "auto:payment_reconciliation_mismatch": "Reconciliation выявил расхождение",
  "auto:refund_retry_required": "Возврат требует повторной попытки",
  "auto:refund_review_required": "Возврат требует ручной проверки",
  "auto:refund_finalization_integrity_failure": "Нарушена целостность финализации возврата",
  "auto:refund_finalization_integrity_conflict": "Конфликт повторной финализации возврата",
  "auto:runtime_artifact_integrity_failure": "Runtime evidence или release binding не прошли проверку",
  "auto:pilot_control_decision_stop": "Pilot control переведён в STOP",
  "auto:pilot_database_integrity_failure": "Database evidence пилота не прошёл проверку",
  "auto:operational_safety_failure": "Операционный safety-контур требует остановки",
  "auto:pilot_money_safety_evaluation_failure": "Money safety нельзя надёжно подтвердить",
  "auto:pilot_runtime_configuration_mismatch": "Конфигурация pilot runtime нарушена",
  "auto:pilot_slot_runtime_mismatch": "Слот связан с другим runtime",
  "auto:integrity_failure": "Автоматическая остановка из-за нарушения целостности",
});

function isObject(value) {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function isSafeInteger(value) {
  return Number.isSafeInteger(value) && value >= 0;
}

function safeInteger(value, fallback = 0) {
  return isSafeInteger(value) ? value : fallback;
}

function normalizedTimestamp(value, { optional = true } = {}) {
  if (value === null && optional) return { value: null, valid: true };
  if (typeof value !== "string" || !value.trim()) return { value: null, valid: false };
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return { value: null, valid: false };
  return { value: parsed.toISOString(), valid: true };
}

function normalizedCodes(value) {
  if (!Array.isArray(value)) {
    return { codes: ["response_contract_invalid"], valid: false };
  }
  const codes = [];
  let valid = true;
  for (const code of value) {
    if (typeof code === "string" && Object.hasOwn(PILOT_INTEGRITY_LABELS, code)) {
      codes.push(code);
    } else {
      valid = false;
    }
  }
  if (!valid) codes.push("response_contract_invalid");
  return { codes: [...new Set(codes)], valid };
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
    continuation: {
      applicable: false,
      ready: null,
      nextSequence: null,
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

  const runtimePresent = isObject(payload.runtime);
  const databasePresent = isObject(payload.database_integrity);
  const artifactsPresent = isObject(payload.artifact_integrity);
  const continuationPresent = isObject(payload.continuation);
  const moneyPresent = isObject(payload.money_attention);
  const runtime = runtimePresent ? payload.runtime : {};
  const database = databasePresent ? payload.database_integrity : {};
  const artifacts = artifactsPresent ? payload.artifact_integrity : {};
  const continuation = continuationPresent ? payload.continuation : {};
  const money = moneyPresent ? payload.money_attention : {};
  const databaseResult = normalizedCodes(database.codes);
  const artifactResult = normalizedCodes(artifacts.codes);
  const statusValid = typeof runtime.status === "string" && PILOT_STATUSES.has(runtime.status);
  const status = statusValid ? runtime.status : "not_armed";
  const runRefValid = runtime.run_ref === null
    || (typeof runtime.run_ref === "string" && SAFE_RUN_REF.test(runtime.run_ref));
  const runRef = runRefValid && typeof runtime.run_ref === "string" ? runtime.run_ref : null;
  const stopReasonValid = runtime.stop_reason === null
    || (typeof runtime.stop_reason === "string"
      && Object.hasOwn(PILOT_STOP_LABELS, runtime.stop_reason));
  const stopReason = stopReasonValid && typeof runtime.stop_reason === "string"
    ? runtime.stop_reason
    : null;
  const generatedAt = normalizedTimestamp(payload.generated_at, { optional: false });
  const openedAt = normalizedTimestamp(runtime.opened_at);
  const stoppedAt = normalizedTimestamp(runtime.stopped_at);
  const completedAt = normalizedTimestamp(runtime.completed_at);
  const updatedAt = normalizedTimestamp(runtime.updated_at);
  const numericValues = [
    runtime.max_orders,
    runtime.accepted_orders,
    runtime.remaining_orders,
    runtime.slot_count,
    runtime.historical_slot_count,
    runtime.allowlist_count,
    money.payment_review_orders,
    money.refund_attention_orders,
    money.reconciliation_mismatches,
  ];
  const numericContractValid = numericValues.every(isSafeInteger);
  const expectedDecision = payload.checkout_decision === "GO" || payload.checkout_decision === "NO-GO";
  const booleanContractValid = typeof payload.enforced === "boolean"
    && typeof runtime.present === "boolean"
    && typeof database.healthy === "boolean"
    && typeof artifacts.applicable === "boolean"
    && (typeof artifacts.healthy === "boolean" || artifacts.healthy === null)
    && typeof continuation.applicable === "boolean"
    && (typeof continuation.ready === "boolean" || continuation.ready === null)
    && typeof money.attention_required === "boolean";
  const integrityCoherent = database.healthy !== true || databaseResult.codes.length === 0;
  const artifactCoherent = artifacts.healthy !== true || artifactResult.codes.length === 0;
  const moneyCoherent = money.attention_required === true
    || (safeInteger(money.payment_review_orders)
      + safeInteger(money.refund_attention_orders)
      + safeInteger(money.reconciliation_mismatches) === 0);
  const runtimeCountsCoherent = !numericContractValid || (
    runtime.accepted_orders <= runtime.max_orders
    && runtime.remaining_orders <= runtime.max_orders
  );
  const continuationCoherent = continuation.applicable === true
    ? (typeof continuation.ready === "boolean"
      && isSafeInteger(continuation.next_sequence)
      && continuation.next_sequence === runtime.accepted_orders + 1
      && continuation.next_sequence <= 20)
    : continuation.ready === null && continuation.next_sequence === null;
  const contractValid = Boolean(
    expectedDecision
    && runtimePresent
    && databasePresent
    && artifactsPresent
    && continuationPresent
    && moneyPresent
    && statusValid
    && runRefValid
    && stopReasonValid
    && generatedAt.valid
    && openedAt.valid
    && stoppedAt.valid
    && completedAt.valid
    && updatedAt.valid
    && numericContractValid
    && booleanContractValid
    && databaseResult.valid
    && artifactResult.valid
    && integrityCoherent
    && artifactCoherent
    && moneyCoherent
    && runtimeCountsCoherent
    && continuationCoherent
  );

  const databaseHealthy = database.healthy === true && databaseResult.codes.length === 0;
  const artifactHealthy = artifacts.healthy === true && artifactResult.codes.length === 0;
  const attentionRequired = money.attention_required === true;
  const checkoutCountsSafe = runtime.max_orders === 20
    && runtime.accepted_orders < runtime.max_orders
    && runtime.remaining_orders === runtime.max_orders - runtime.accepted_orders
    && runtime.slot_count === runtime.accepted_orders;
  const continuationReady = continuation.applicable === true && continuation.ready === true;
  const decision = payload.checkout_decision === "GO"
    && contractValid
    && payload.enforced === true
    && status === "active"
    && checkoutCountsSafe
    && databaseHealthy
    && artifactHealthy
    && continuationReady
    && !attentionRequired
    ? "GO"
    : "NO-GO";
  const contractCodes = contractValid ? [] : ["response_contract_invalid"];

  return {
    decision,
    generatedAt: generatedAt.value,
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
      openedAt: openedAt.value,
      stoppedAt: stoppedAt.value,
      completedAt: completedAt.value,
      updatedAt: updatedAt.value,
    },
    databaseIntegrity: {
      healthy: contractValid && databaseHealthy,
      codes: [...new Set([...databaseResult.codes, ...contractCodes])],
    },
    artifactIntegrity: {
      applicable: artifacts.applicable === true,
      healthy: artifacts.healthy === null ? null : contractValid && artifactHealthy,
      codes: [...new Set([...artifactResult.codes, ...contractCodes])],
    },
    continuation: {
      applicable: continuation.applicable === true,
      ready: continuation.ready === null ? null : continuation.ready === true,
      nextSequence: isSafeInteger(continuation.next_sequence) ? continuation.next_sequence : null,
    },
    moneyAttention: {
      paymentReviewOrders: safeInteger(money.payment_review_orders),
      refundAttentionOrders: safeInteger(money.refund_attention_orders),
      reconciliationMismatches: safeInteger(money.reconciliation_mismatches),
      attentionRequired: contractValid ? attentionRequired : true,
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