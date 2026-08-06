import assert from "node:assert/strict";
import { createHmac } from "node:crypto";
import test from "node:test";
import {
  DomainError,
  InMemoryTelegramUpdateClaimStore,
  KeyedSerialExecutor,
  TelegramUpdateGate,
  telegramCallbackIdempotencyKey,
  validateTelegramInitData,
} from "./index.js";

function signInitData(values: Readonly<Record<string, string>>, botToken: string): string {
  const dataCheckString = Object.keys(values)
    .sort((left, right) => left.localeCompare(right))
    .map((key) => `${key}=${values[key]}`)
    .join("\n");
  const secret = createHmac("sha256", "WebAppData").update(botToken, "utf8").digest();
  const hash = createHmac("sha256", secret).update(dataCheckString, "utf8").digest("hex");
  const params = new URLSearchParams({ ...values, hash });
  return params.toString();
}

test("Telegram Mini App init data validates signature, freshness and user shape", () => {
  const token = "123456:secret";
  const source = signInitData(
    {
      auth_date: "2000",
      query_id: "query-1",
      user: JSON.stringify({ id: 9_007_199_254_740, first_name: " Petr ", username: "petr" }),
    },
    token,
  );
  const result = validateTelegramInitData(source, token, { nowSeconds: 2100, maxAgeSeconds: 300 });
  assert.equal(result.authDate, 2000);
  assert.equal(result.queryId, "query-1");
  assert.equal(result.user?.id, 9_007_199_254_740);
  assert.equal(result.user?.first_name, "Petr");
});

test("Telegram Mini App init data rejects tampering", () => {
  const token = "123456:secret";
  const source = signInitData({ auth_date: "2000", query_id: "query-1" }, token).replace("query-1", "query-2");
  assert.throws(
    () => validateTelegramInitData(source, token, { nowSeconds: 2100 }),
    (error: unknown) => error instanceof DomainError && error.code === "telegram.invalid_signature",
  );
});

test("Telegram Mini App init data rejects expired and future sessions", () => {
  const token = "123456:secret";
  const expired = signInitData({ auth_date: "1000" }, token);
  assert.throws(
    () => validateTelegramInitData(expired, token, { nowSeconds: 2000, maxAgeSeconds: 300 }),
    (error: unknown) => error instanceof DomainError && error.code === "telegram.init_data_expired",
  );
  const future = signInitData({ auth_date: "2100" }, token);
  assert.throws(
    () => validateTelegramInitData(future, token, { nowSeconds: 2000, futureClockSkewSeconds: 30 }),
    (error: unknown) => error instanceof DomainError && error.code === "telegram.auth_date_in_future",
  );
});

test("Telegram Mini App init data rejects duplicate query keys", () => {
  const token = "123456:secret";
  const signed = signInitData({ auth_date: "2000", query_id: "one" }, token);
  assert.throws(
    () => validateTelegramInitData(`${signed}&query_id=two`, token, { nowSeconds: 2001 }),
    (error: unknown) => error instanceof DomainError && error.code === "telegram.duplicate_init_data_key",
  );
});

test("Telegram update gate accepts once, rejects retry, and permits reuse only after TTL", () => {
  const gate = new TelegramUpdateGate(new InMemoryTelegramUpdateClaimStore(), 10);
  assert.equal(gate.claim(100, 1_000), true);
  assert.equal(gate.claim(100, 1_001), false);
  assert.equal(gate.claim(100, 1_010), true);
});

test("Keyed serial executor prevents concurrent handling for the same chat", async () => {
  const executor = new KeyedSerialExecutor();
  const events: string[] = [];
  let releaseFirst!: () => void;
  const firstWait = new Promise<void>((resolve) => {
    releaseFirst = resolve;
  });

  const first = executor.run("chat:1", async () => {
    events.push("first:start");
    await firstWait;
    events.push("first:end");
  });
  const second = executor.run("chat:1", async () => {
    events.push("second:start");
    events.push("second:end");
  });

  await Promise.resolve();
  assert.deepEqual(events, ["first:start"]);
  releaseFirst();
  await Promise.all([first, second]);
  assert.deepEqual(events, ["first:start", "first:end", "second:start", "second:end"]);
});

test("Telegram callback idempotency keys are deterministic and reject blank IDs", () => {
  assert.equal(telegramCallbackIdempotencyKey(" callback-1 "), "telegram:callback:callback-1");
  assert.throws(
    () => telegramCallbackIdempotencyKey("  "),
    (error: unknown) => error instanceof DomainError && error.code === "telegram.invalid_callback_query_id",
  );
});
