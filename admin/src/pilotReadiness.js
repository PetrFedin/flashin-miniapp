const CRITICAL_CHECKS = Object.freeze([
  "database",
  "migrations",
  "env",
  "payments",
  "moysklad",
  "scheduler",
  "notification_delivery",
  "webhook_outbox",
  "moysklad_sync",
]);
const ADVISORY_CHECKS = Object.freeze(["media", "search"]);
const PILOT_STATUSES = new Set(["not_armed", "active", "stopped", "completed"]);

const CHECK_LABELS = Object.freeze({
  database: "База данных",
  migrations: "Миграции БД",
  env: "Production-конфигурация",
  payments: "YooKassa",
  moysklad: "MoySklad",
  scheduler: "Scheduler",
  notification_delivery: "Доставка Telegram-уведомлений",
  webhook_outbox: "Webhook outbox",
  moysklad_sync: "Синхронизация MoySklad",
  media: "Media storage",
  search: "Поиск",
});

const READINESS_LABELS = Object.freeze({
  diagnostics_unavailable: "Service diagnostics недоступны",
  runtime_status_unavailable: "Pilot runtime status недоступен",
  runtime_checkout_no_go: "Pilot runtime запретил следующий checkout",
  runtime_database_integrity_failed: "Нарушена целостность runtime БД",
  runtime_artifact_integrity_unavailable: "Runtime/release evidence недоступен",
  runtime_artifact_integrity_failed: "Runtime/release evidence не прошёл проверку",
  runtime_money_attention: "Денежная операция требует проверки",
  runtime_operational_safety_unavailable: "Проверка operational queues недоступна",
  runtime_operational_safety_failed: "Operational queues требуют вмешательства",
  response_contract_invalid: "Ответ readiness имеет неверный или небезопасный формат",
});

function isObject(value) {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function isSafeInteger(value) {
  return Number.isSafeInteger(value) && value >= 0;
}

function isSafeIntegerOrNull(value) {
  return value === null || isSafeInteger(value);
}

function isBooleanOrNull(value) {
  return value === null || typeof value === "boolean";
}

function diagnosticCodeParts(code) {
  if (typeof code !== "string") return null;
  const match = /^diagnostic_(failed|missing|degraded):([a-z_]+)$/.exec(code);
  if (!match) return null;
  return { state: match[1], check: match[2] };
}

function isAllowedBlockingCode(code) {
  if (typeof code !== "string") return false;
  if (Object.hasOwn(READINESS_LABELS, code) && code !== "response_contract_invalid") return true;
  const parts = diagnosticCodeParts(code);
  return Boolean(
    parts
    && (parts.state === "failed" || parts.state === "missing")
    && CRITICAL_CHECKS.includes(parts.check),
  );
}

function isAllowedWarningCode(code) {
  const parts = diagnosticCodeParts(code);
  return Boolean(
    parts
    && (parts.state === "missing" || parts.state === "degraded")
    && ADVISORY_CHECKS.includes(parts.check),
  );
}

function normalizeCodes(value, validator) {
  if (!Array.isArray(value)) {
    return { codes: ["response_contract_invalid"], valid: false };
  }
  const codes = [];
  let valid = true;
  for (const code of value) {
    if (validator(code)) codes.push(code);
    else valid = false;
  }
  if (!valid) codes.push("response_contract_invalid");
  return { codes: [...new Set(codes)], valid };
}

function normalizeDiagnosticGroup(value, expected) {
  if (!isObject(value)) return { values: {}, valid: false };
  const values = {};
  let valid = true;
  for (const name of expected) {
    const item = value[name];
    if (typeof item === "boolean" || item === null) values[name] = item;
    else {
      values[name] = null;
      valid = false;
    }
  }
  return { values, valid };
}

function fallbackReadiness() {
  return {
    decision: "NO-GO",
    readyForNextOrder: false,
    contractValid: false,
    blockingCodes: ["response_contract_invalid"],
    warningCodes: [],
    critical: Object.fromEntries(CRITICAL_CHECKS.map((name) => [name, null])),
    advisory: Object.fromEntries(ADVISORY_CHECKS.map((name) => [name, null])),
  };
}

export function normalizePilotReadiness(payload) {
  if (!isObject(payload) || payload.schema_version !== 1) return fallbackReadiness();

  const blocking = normalizeCodes(payload.blocking_codes, isAllowedBlockingCode);
  const warnings = normalizeCodes(payload.warning_codes, isAllowedWarningCode);
  const diagnostics = isObject(payload.diagnostics) ? payload.diagnostics : {};
  const critical = normalizeDiagnosticGroup(diagnostics.critical, CRITICAL_CHECKS);
  const advisory = normalizeDiagnosticGroup(diagnostics.advisory, ADVISORY_CHECKS);
  const runtime = isObject(payload.runtime) ? payload.runtime : {};

  const decisionValid = payload.decision === "GO" || payload.decision === "NO-GO";
  const readyValid = typeof payload.ready_for_next_order === "boolean";
  const runtimeValid = (
    (runtime.checkout_decision === "GO" || runtime.checkout_decision === "NO-GO")
    && typeof runtime.enforced === "boolean"
    && (runtime.status === null || (typeof runtime.status === "string" && PILOT_STATUSES.has(runtime.status)))
    && isSafeIntegerOrNull(runtime.accepted_orders)
    && isSafeIntegerOrNull(runtime.remaining_orders)
    && isSafeIntegerOrNull(runtime.allowlist_count)
    && isBooleanOrNull(runtime.database_integrity_healthy)
    && isBooleanOrNull(runtime.artifact_integrity_applicable)
    && isBooleanOrNull(runtime.artifact_integrity_healthy)
    && typeof runtime.money_attention_required === "boolean"
    && isBooleanOrNull(runtime.operational_safety_applicable)
    && isBooleanOrNull(runtime.operational_safety_healthy)
  );
  const decisionCoherent = decisionValid
    && readyValid
    && ((payload.decision === "GO") === payload.ready_for_next_order);
  const goSignalsConfirmed = payload.decision !== "GO" || (
    blocking.codes.length === 0
    && CRITICAL_CHECKS.every((name) => critical.values[name] === true)
    && runtime.checkout_decision === "GO"
    && runtime.enforced === true
    && runtime.status === "active"
    && isSafeInteger(runtime.remaining_orders)
    && runtime.remaining_orders > 0
    && runtime.database_integrity_healthy === true
    && runtime.artifact_integrity_applicable === true
    && runtime.artifact_integrity_healthy === true
    && runtime.money_attention_required === false
    && runtime.operational_safety_applicable === true
    && runtime.operational_safety_healthy === true
  );
  const contractValid = Boolean(
    blocking.valid
    && warnings.valid
    && critical.valid
    && advisory.valid
    && runtimeValid
    && decisionCoherent
    && goSignalsConfirmed
  );

  const blockingCodes = contractValid
    ? blocking.codes
    : [...new Set([...blocking.codes, "response_contract_invalid"])];
  const decision = contractValid
    && payload.decision === "GO"
    && payload.ready_for_next_order === true
    && blockingCodes.length === 0
    ? "GO"
    : "NO-GO";

  return {
    decision,
    readyForNextOrder: decision === "GO",
    contractValid,
    blockingCodes,
    warningCodes: warnings.codes.filter((code) => code !== "response_contract_invalid"),
    critical: critical.values,
    advisory: advisory.values,
  };
}

export function pilotReadinessCodeLabel(code) {
  if (Object.hasOwn(READINESS_LABELS, code)) return READINESS_LABELS[code];
  const parts = diagnosticCodeParts(code);
  if (!parts || !Object.hasOwn(CHECK_LABELS, parts.check)) {
    return READINESS_LABELS.response_contract_invalid;
  }
  const check = CHECK_LABELS[parts.check];
  if (parts.state === "failed") return `${check}: проверка не пройдена`;
  if (parts.state === "missing") return `${check}: сигнал отсутствует`;
  if (parts.state === "degraded") return `${check}: деградация`;
  return READINESS_LABELS.response_contract_invalid;
}

export function pilotDiagnosticLabel(name) {
  return CHECK_LABELS[name] || "Неизвестная проверка";
}
