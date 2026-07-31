import assert from "node:assert/strict";
import test from "node:test";
import {
  DomainError,
  assertFreshBotCallback,
  decodeBotCallback,
  encodeBotCallback,
  initialBotSession,
  reduceBotSession,
  resolveBotCommand,
} from "./index.js";

const secret = "s".repeat(32);

test("bot checkout follows cart, contact, delivery, review, payment and order sequence", () => {
  let session = initialBotSession();
  session = reduceBotSession(session, { type: "open_catalog" });
  session = reduceBotSession(session, { type: "open_product", productId: "dress-1" });
  session = reduceBotSession(session, { type: "add_to_cart", sku: "SKU-1", quantity: 2 });
  session = reduceBotSession(session, { type: "open_cart" });
  session = reduceBotSession(session, { type: "begin_checkout" });
  session = reduceBotSession(session, { type: "submit_contact", contact: { name: " Petr  Fedin ", phone: "+31 (6) 1234-5678", email: "P@EXAMPLE.COM" } });
  session = reduceBotSession(session, { type: "submit_delivery", delivery: { method: "courier", address: "  Amsterdam   Centrum  " } });
  session = reduceBotSession(session, { type: "payment_created", orderId: "order-1" });
  session = reduceBotSession(session, { type: "payment_confirmed", orderId: "order-1" });
  assert.deepEqual(session.scene, { kind: "order", orderId: "order-1" });
  assert.deepEqual(session.cart, []);
  assert.equal(session.version, 9);
});

test("bot rejects checkout with an empty cart", () => {
  const session = reduceBotSession(initialBotSession(), { type: "open_cart" });
  assert.throws(
    () => reduceBotSession(session, { type: "begin_checkout" }),
    (error: unknown) => error instanceof DomainError && error.code === "bot.empty_cart",
  );
});

test("bot rejects product opening outside catalog navigation", () => {
  assert.throws(
    () => reduceBotSession(initialBotSession(), { type: "open_product", productId: "dress-1" }),
    (error: unknown) => error instanceof DomainError && error.code === "bot.invalid_transition",
  );
});

test("bot cart aggregates duplicate SKU and enforces a hard quantity limit", () => {
  let session = reduceBotSession(initialBotSession(), { type: "open_catalog" });
  session = reduceBotSession(session, { type: "add_to_cart", sku: "B", quantity: 1 });
  session = reduceBotSession(session, { type: "add_to_cart", sku: "A", quantity: 2 });
  session = reduceBotSession(session, { type: "add_to_cart", sku: "A", quantity: 3 });
  assert.deepEqual(session.cart, [
    { sku: "A", quantity: 5 },
    { sku: "B", quantity: 1 },
  ]);
  assert.throws(
    () => reduceBotSession(session, { type: "add_to_cart", sku: "A", quantity: 95 }),
    (error: unknown) => error instanceof DomainError && error.code === "bot.cart_quantity_limit",
  );
});

test("bot cannot confirm a different order than the active payment", () => {
  let session = initialBotSession();
  session = reduceBotSession(session, { type: "open_catalog" });
  session = reduceBotSession(session, { type: "add_to_cart", sku: "SKU-1" });
  session = reduceBotSession(session, { type: "open_cart" });
  session = reduceBotSession(session, { type: "begin_checkout" });
  session = reduceBotSession(session, { type: "submit_contact", contact: { name: "Petr", phone: "+31612345678" } });
  session = reduceBotSession(session, { type: "submit_delivery", delivery: { method: "pickup", pickupPointId: "ams-1" } });
  session = reduceBotSession(session, { type: "payment_created", orderId: "order-1" });
  assert.throws(
    () => reduceBotSession(session, { type: "payment_confirmed", orderId: "order-2" }),
    (error: unknown) => error instanceof DomainError && error.code === "bot.order_mismatch",
  );
});

test("bot blocks global navigation while checkout is active and clears checkout data only on explicit cancellation", () => {
  let session = reduceBotSession(initialBotSession(), { type: "open_catalog" });
  session = reduceBotSession(session, { type: "add_to_cart", sku: "SKU-1" });
  session = reduceBotSession(session, { type: "open_cart" });
  session = reduceBotSession(session, { type: "begin_checkout" });
  session = reduceBotSession(session, { type: "submit_contact", contact: { name: "Petr", phone: "+31612345678" } });

  assert.throws(
    () => reduceBotSession(session, { type: "reset" }),
    (error: unknown) => error instanceof DomainError && error.code === "bot.checkout_navigation_locked",
  );
  assert.throws(
    () => reduceBotSession(session, { type: "open_cart" }),
    (error: unknown) => error instanceof DomainError && error.code === "bot.checkout_navigation_locked",
  );

  session = reduceBotSession(session, { type: "cancel_checkout" });
  assert.deepEqual(session.scene, { kind: "cart" });
  assert.equal(session.contact, undefined);
  assert.equal(session.delivery, undefined);
});

test("bot back from the first checkout step exits cleanly without retaining checkout data", () => {
  let session = reduceBotSession(initialBotSession(), { type: "open_catalog" });
  session = reduceBotSession(session, { type: "add_to_cart", sku: "SKU-1" });
  session = reduceBotSession(session, { type: "open_cart" });
  session = reduceBotSession(session, { type: "begin_checkout" });
  session = reduceBotSession(session, { type: "back" });
  assert.deepEqual(session.scene, { kind: "cart" });
  assert.equal(session.contact, undefined);
  assert.equal(session.delivery, undefined);
});

test("bot keeps pending and failed payments locked against navigation, including through support", () => {
  let session = initialBotSession();
  session = reduceBotSession(session, { type: "open_catalog" });
  session = reduceBotSession(session, { type: "add_to_cart", sku: "SKU-1" });
  session = reduceBotSession(session, { type: "open_cart" });
  session = reduceBotSession(session, { type: "begin_checkout" });
  session = reduceBotSession(session, { type: "submit_contact", contact: { name: "Petr", phone: "+31612345678" } });
  session = reduceBotSession(session, { type: "submit_delivery", delivery: { method: "pickup", pickupPointId: "ams-1" } });
  session = reduceBotSession(session, { type: "payment_created", orderId: "order-1" });

  assert.throws(
    () => reduceBotSession(session, { type: "open_catalog" }),
    (error: unknown) => error instanceof DomainError && error.code === "bot.payment_navigation_locked",
  );
  session = reduceBotSession(session, { type: "open_support" });
  assert.throws(
    () => reduceBotSession(session, { type: "reset" }),
    (error: unknown) => error instanceof DomainError && error.code === "bot.payment_navigation_locked",
  );
  session = reduceBotSession(session, { type: "back" });
  session = reduceBotSession(session, { type: "payment_failed", orderId: "order-1" });
  assert.throws(
    () => reduceBotSession(session, { type: "open_order", orderId: "order-1" }),
    (error: unknown) => error instanceof DomainError && error.code === "bot.payment_navigation_locked",
  );
});

test("bot back navigation is deterministic and never guesses a screen", () => {
  let session = reduceBotSession(initialBotSession(), { type: "open_catalog", category: "new", page: 3 });
  session = reduceBotSession(session, { type: "open_product", productId: "dress-1" });
  session = reduceBotSession(session, { type: "back" });
  assert.deepEqual(session.scene, { kind: "catalog", category: "new", page: 3 });
  session = reduceBotSession(session, { type: "open_support" });
  session = reduceBotSession(session, { type: "back" });
  assert.deepEqual(session.scene, { kind: "catalog", category: "new", page: 3 });
});

test("signed callback stays within Telegram 64-byte limit and round-trips", () => {
  const encoded = encodeBotCallback({ sessionVersion: 123, action: "product", reference: "abc_123" }, secret);
  assert.ok(Buffer.byteLength(encoded, "utf8") <= 64);
  assert.deepEqual(decodeBotCallback(encoded, secret), { sessionVersion: 123, action: "product", reference: "abc_123" });
});

test("signed callback rejects tampering and weak secrets", () => {
  const encoded = encodeBotCallback({ sessionVersion: 1, action: "cart" }, secret);
  assert.throws(
    () => decodeBotCallback(encoded.replace("cart", "home"), secret),
    (error: unknown) => error instanceof DomainError && error.code === "bot.invalid_callback_signature",
  );
  assert.throws(
    () => encodeBotCallback({ sessionVersion: 1, action: "cart" }, "short"),
    (error: unknown) => error instanceof DomainError && error.code === "bot.weak_callback_secret",
  );
});

test("stale callback is rejected after the session advances", () => {
  const oldSession = initialBotSession();
  const payload = decodeBotCallback(encodeBotCallback({ sessionVersion: oldSession.version, action: "catalog" }, secret), secret);
  const currentSession = reduceBotSession(oldSession, { type: "open_catalog" });
  assert.throws(
    () => assertFreshBotCallback(currentSession, payload),
    (error: unknown) => error instanceof DomainError && error.code === "bot.stale_callback",
  );
});

test("command resolver normalizes bot suffix and rejects unsupported commands", () => {
  assert.deepEqual(resolveBotCommand(" /CATALOG@flashin_bot extra "), { type: "open_catalog" });
  assert.throws(
    () => resolveBotCommand("/unknown"),
    (error: unknown) => error instanceof DomainError && error.code === "bot.unknown_command",
  );
});
