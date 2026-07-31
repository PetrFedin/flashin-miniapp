import { createHmac, timingSafeEqual } from "node:crypto";
import { DomainError, invariant } from "./errors.js";
import type { BotAction, BotSession } from "./bot-types.js";

const CALLBACK_ACTIONS = ["home", "catalog", "product", "add", "cart", "checkout", "order", "support", "back"] as const;
export type BotCallbackAction = (typeof CALLBACK_ACTIONS)[number];

export interface BotCallbackPayload {
  readonly sessionVersion: number;
  readonly action: BotCallbackAction;
  readonly reference?: string;
}

export function encodeBotCallback(payload: BotCallbackPayload, secret: string): string {
  validateCallbackSecret(secret);
  invariant(Number.isSafeInteger(payload.sessionVersion) && payload.sessionVersion >= 0, "bot.invalid_callback_version", "Callback session version must be a non-negative safe integer");
  invariant(CALLBACK_ACTIONS.includes(payload.action), "bot.invalid_callback_action", "Callback action is not supported");
  const reference = payload.reference === undefined ? "" : normalizeCallbackReference(payload.reference);
  const body = `1.${payload.sessionVersion.toString(36)}.${payload.action}.${reference}`;
  const signature = callbackSignature(body, secret);
  const encoded = `${body}.${signature}`;
  invariant(Buffer.byteLength(encoded, "utf8") <= 64, "bot.callback_too_large", "Telegram callback_data must not exceed 64 bytes", { bytes: Buffer.byteLength(encoded, "utf8") });
  return encoded;
}

export function decodeBotCallback(encoded: string, secret: string): BotCallbackPayload {
  validateCallbackSecret(secret);
  invariant(Buffer.byteLength(encoded, "utf8") >= 1 && Buffer.byteLength(encoded, "utf8") <= 64, "bot.invalid_callback_size", "Telegram callback_data must be 1-64 bytes");
  const parts = encoded.split(".");
  invariant(parts.length === 5 && parts[0] === "1", "bot.invalid_callback_format", "Callback payload has an invalid format");
  const [versionTag, versionRaw, actionRaw, referenceRaw, providedSignature] = parts;
  const body = `${versionTag}.${versionRaw}.${actionRaw}.${referenceRaw}`;
  const expectedSignature = callbackSignature(body, secret);
  invariant(
    providedSignature !== undefined && providedSignature.length === expectedSignature.length && timingSafeEqual(Buffer.from(providedSignature), Buffer.from(expectedSignature)),
    "bot.invalid_callback_signature",
    "Callback signature is invalid",
  );
  const sessionVersion = Number.parseInt(versionRaw ?? "", 36);
  invariant(Number.isSafeInteger(sessionVersion) && sessionVersion >= 0, "bot.invalid_callback_version", "Callback session version is invalid");
  invariant(CALLBACK_ACTIONS.includes(actionRaw as BotCallbackAction), "bot.invalid_callback_action", "Callback action is not supported");
  const reference = referenceRaw ? normalizeCallbackReference(referenceRaw) : undefined;
  return { sessionVersion, action: actionRaw as BotCallbackAction, ...(reference ? { reference } : {}) };
}

export function assertFreshBotCallback(session: BotSession, payload: BotCallbackPayload): void {
  invariant(payload.sessionVersion === session.version, "bot.stale_callback", "This button belongs to an outdated bot screen", {
    expectedVersion: session.version,
    receivedVersion: payload.sessionVersion,
  });
}

export function resolveBotCommand(command: string): BotAction {
  const normalized = command.trim().split(/\s+/, 1)[0]?.toLowerCase().replace(/@[^\s]+$/, "") ?? "";
  switch (normalized) {
    case "/start":
    case "/home":
      return { type: "reset" };
    case "/catalog":
      return { type: "open_catalog" };
    case "/cart":
      return { type: "open_cart" };
    case "/support":
      return { type: "open_support" };
    default:
      throw new DomainError("bot.unknown_command", "Bot command is not supported", { command: normalized });
  }
}

function normalizeCallbackReference(value: string): string {
  const normalized = value.trim();
  invariant(normalized.length >= 1 && normalized.length <= 24 && /^[A-Za-z0-9_-]+$/.test(normalized), "bot.invalid_callback_reference", "Callback reference must be 1-24 URL-safe characters");
  return normalized;
}

function validateCallbackSecret(secret: string): void {
  invariant(secret.length >= 32, "bot.weak_callback_secret", "Callback signing secret must contain at least 32 characters");
}

function callbackSignature(body: string, secret: string): string {
  return createHmac("sha256", secret).update(body, "utf8").digest("base64url").slice(0, 10);
}
