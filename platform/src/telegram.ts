import { createHmac, timingSafeEqual } from "node:crypto";
import { DomainError, invariant } from "./errors.js";

export interface TelegramUser {
  readonly id: number;
  readonly first_name: string;
  readonly last_name?: string;
  readonly username?: string;
  readonly language_code?: string;
  readonly is_premium?: boolean;
  readonly allows_write_to_pm?: boolean;
  readonly photo_url?: string;
}

export interface TelegramInitData {
  readonly authDate: number;
  readonly queryId?: string;
  readonly user?: TelegramUser;
  readonly values: Readonly<Record<string, string>>;
}

export interface TelegramInitDataOptions {
  readonly nowSeconds?: number;
  readonly maxAgeSeconds?: number;
  readonly futureClockSkewSeconds?: number;
  readonly maxLength?: number;
}

export function validateTelegramInitData(
  source: string,
  botToken: string,
  options: TelegramInitDataOptions = {},
): TelegramInitData {
  const nowSeconds = options.nowSeconds ?? Math.floor(Date.now() / 1000);
  const maxAgeSeconds = options.maxAgeSeconds ?? 300;
  const futureClockSkewSeconds = options.futureClockSkewSeconds ?? 30;
  const maxLength = options.maxLength ?? 16_384;

  invariant(botToken.trim().length > 0, "telegram.empty_bot_token", "Telegram bot token cannot be empty");
  invariant(source.length > 0 && source.length <= maxLength, "telegram.invalid_init_data_size", "Telegram init data has an invalid size", {
    length: source.length,
    maxLength,
  });
  invariant(Number.isSafeInteger(nowSeconds) && nowSeconds >= 0, "telegram.invalid_now", "Current time must be a non-negative safe integer");
  invariant(Number.isSafeInteger(maxAgeSeconds) && maxAgeSeconds > 0, "telegram.invalid_max_age", "Maximum init-data age must be positive");
  invariant(
    Number.isSafeInteger(futureClockSkewSeconds) && futureClockSkewSeconds >= 0,
    "telegram.invalid_clock_skew",
    "Future clock skew must be non-negative",
  );

  const params = new URLSearchParams(source);
  const values: Record<string, string> = {};
  for (const [key, value] of params.entries()) {
    invariant(key.length > 0, "telegram.empty_init_data_key", "Telegram init data contains an empty key");
    invariant(values[key] === undefined, "telegram.duplicate_init_data_key", "Telegram init data contains a duplicate key", { key });
    values[key] = value;
  }

  const providedHash = values.hash;
  invariant(providedHash !== undefined && /^[a-f\d]{64}$/i.test(providedHash), "telegram.invalid_hash", "Telegram init data hash is missing or malformed");

  const dataCheckString = Object.keys(values)
    .filter((key) => key !== "hash")
    .sort((left, right) => left.localeCompare(right))
    .map((key) => `${key}=${values[key]}`)
    .join("\n");

  const secretKey = createHmac("sha256", "WebAppData").update(botToken, "utf8").digest();
  const expectedHash = createHmac("sha256", secretKey).update(dataCheckString, "utf8").digest("hex");
  const valid = timingSafeEqual(Buffer.from(providedHash.toLowerCase(), "hex"), Buffer.from(expectedHash, "hex"));
  invariant(valid, "telegram.invalid_signature", "Telegram init data signature is invalid");

  const authDateRaw = values.auth_date;
  invariant(authDateRaw !== undefined && /^\d+$/.test(authDateRaw), "telegram.invalid_auth_date", "Telegram auth_date is missing or malformed");
  const authDate = Number(authDateRaw);
  invariant(Number.isSafeInteger(authDate) && authDate >= 0, "telegram.invalid_auth_date", "Telegram auth_date is outside the safe integer range");
  invariant(authDate <= nowSeconds + futureClockSkewSeconds, "telegram.auth_date_in_future", "Telegram init data auth_date is too far in the future", {
    authDate,
    nowSeconds,
  });
  invariant(nowSeconds - authDate <= maxAgeSeconds, "telegram.init_data_expired", "Telegram init data has expired", {
    authDate,
    nowSeconds,
    maxAgeSeconds,
  });

  const user = values.user === undefined ? undefined : parseTelegramUser(values.user);
  const queryId = values.query_id?.trim();
  return {
    authDate,
    ...(queryId ? { queryId } : {}),
    ...(user ? { user } : {}),
    values: Object.freeze({ ...values }),
  };
}

function parseTelegramUser(source: string): TelegramUser {
  let parsed: unknown;
  try {
    parsed = JSON.parse(source);
  } catch {
    throw new DomainError("telegram.invalid_user_json", "Telegram user payload is not valid JSON");
  }
  invariant(parsed !== null && typeof parsed === "object" && !Array.isArray(parsed), "telegram.invalid_user", "Telegram user payload must be an object");
  const user = parsed as Record<string, unknown>;
  invariant(Number.isSafeInteger(user.id) && Number(user.id) > 0, "telegram.invalid_user_id", "Telegram user ID must be a positive safe integer");
  invariant(typeof user.first_name === "string" && user.first_name.trim().length > 0, "telegram.invalid_first_name", "Telegram first name cannot be empty");

  for (const key of ["last_name", "username", "language_code", "photo_url"] as const) {
    invariant(user[key] === undefined || typeof user[key] === "string", "telegram.invalid_user_field", `Telegram user field ${key} must be a string`, { key });
  }
  for (const key of ["is_premium", "allows_write_to_pm"] as const) {
    invariant(user[key] === undefined || typeof user[key] === "boolean", "telegram.invalid_user_field", `Telegram user field ${key} must be a boolean`, { key });
  }

  return {
    id: Number(user.id),
    first_name: user.first_name.trim(),
    ...(typeof user.last_name === "string" ? { last_name: user.last_name } : {}),
    ...(typeof user.username === "string" ? { username: user.username } : {}),
    ...(typeof user.language_code === "string" ? { language_code: user.language_code } : {}),
    ...(typeof user.is_premium === "boolean" ? { is_premium: user.is_premium } : {}),
    ...(typeof user.allows_write_to_pm === "boolean" ? { allows_write_to_pm: user.allows_write_to_pm } : {}),
    ...(typeof user.photo_url === "string" ? { photo_url: user.photo_url } : {}),
  };
}

export interface TelegramUpdateClaimStore {
  claim(updateId: number, expiresAtSeconds: number, nowSeconds: number): boolean;
}

export class InMemoryTelegramUpdateClaimStore implements TelegramUpdateClaimStore {
  private readonly claimed = new Map<number, number>();

  public constructor(private readonly maxEntries = 100_000) {
    invariant(Number.isSafeInteger(maxEntries) && maxEntries > 0, "telegram.invalid_claim_capacity", "Claim capacity must be positive");
  }

  public claim(updateId: number, expiresAtSeconds: number, nowSeconds: number): boolean {
    invariant(Number.isSafeInteger(updateId) && updateId >= 0, "telegram.invalid_update_id", "Telegram update_id must be a non-negative safe integer");
    invariant(Number.isSafeInteger(expiresAtSeconds) && expiresAtSeconds > nowSeconds, "telegram.invalid_claim_expiry", "Update claim expiry must be in the future");
    invariant(Number.isSafeInteger(nowSeconds) && nowSeconds >= 0, "telegram.invalid_now", "Current time must be a non-negative safe integer");

    for (const [id, expiry] of this.claimed) {
      if (expiry <= nowSeconds) this.claimed.delete(id);
    }
    if (this.claimed.has(updateId)) return false;
    invariant(this.claimed.size < this.maxEntries, "telegram.claim_store_capacity", "Telegram update claim store reached capacity");
    this.claimed.set(updateId, expiresAtSeconds);
    return true;
  }
}

export class TelegramUpdateGate {
  public constructor(
    private readonly store: TelegramUpdateClaimStore,
    private readonly ttlSeconds = 86_400,
  ) {
    invariant(Number.isSafeInteger(ttlSeconds) && ttlSeconds > 0, "telegram.invalid_update_ttl", "Telegram update TTL must be positive");
  }

  public claim(updateId: number, nowSeconds = Math.floor(Date.now() / 1000)): boolean {
    return this.store.claim(updateId, nowSeconds + this.ttlSeconds, nowSeconds);
  }
}

export class KeyedSerialExecutor {
  private readonly tails = new Map<string, Promise<void>>();

  public async run<T>(key: string, operation: () => Promise<T> | T): Promise<T> {
    const normalizedKey = key.trim();
    invariant(normalizedKey.length > 0 && normalizedKey.length <= 256, "telegram.invalid_serial_key", "Serial execution key must be 1-256 characters");

    const previous = this.tails.get(normalizedKey) ?? Promise.resolve();
    let release!: () => void;
    const current = new Promise<void>((resolve) => {
      release = resolve;
    });
    const tail = previous.then(() => current);
    this.tails.set(normalizedKey, tail);

    await previous;
    try {
      return await operation();
    } finally {
      release();
      if (this.tails.get(normalizedKey) === tail) this.tails.delete(normalizedKey);
    }
  }
}

export function telegramCallbackIdempotencyKey(callbackQueryId: string): string {
  const normalized = callbackQueryId.trim();
  invariant(normalized.length > 0 && normalized.length <= 128, "telegram.invalid_callback_query_id", "Telegram callback query ID must be 1-128 characters");
  return `telegram:callback:${normalized}`;
}
