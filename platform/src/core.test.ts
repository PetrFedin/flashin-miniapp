import assert from "node:assert/strict";
import test from "node:test";
import {
  CheckoutCoordinator,
  DomainError,
  InMemoryCheckoutStore,
  InventoryBook,
  Money,
  assertOrderSnapshot,
  assertOrderTransition,
  buildTBankInitRequest,
  calculatePricing,
  createTBankToken,
  parseTildaCsv,
  verifyTBankNotification,
} from "./index.js";

test("money uses exact kopek arithmetic", () => {
  assert.equal(Money.parse("0.10").add(Money.parse("0.20")).toString(), "0.30");
  assert.equal(Money.parse("199.99").multiply(3).toString(), "599.97");
});

test("percentage rounding is half-up and deterministic", () => {
  assert.equal(Money.parse("10.01").percentage(5000).toString(), "5.01");
  assert.equal(Money.parse("10.00").percentage(3333).toString(), "3.33");
});

test("pricing applies promo, loyalty cap and delivery in one order", () => {
  const result = calculatePricing({
    items: [
      { sku: "A", unitPrice: Money.parse("1000.00"), quantity: 2 },
      { sku: "B", unitPrice: Money.parse("500.00"), quantity: 1 },
    ],
    promo: { kind: "percent", basisPoints: 1000 },
    requestedLoyalty: Money.parse("225.00"),
    loyaltyCapBasisPoints: 1000,
    delivery: Money.parse("300.00"),
  });
  assert.deepEqual(
    Object.fromEntries(Object.entries(result).map(([key, value]) => [key, value.toString()])),
    {
      subtotal: "2500.00",
      promoDiscount: "250.00",
      loyaltyDiscount: "225.00",
      delivery: "300.00",
      total: "2325.00",
    },
  );
});

test("pricing fails closed when loyalty exceeds configured cap", () => {
  assert.throws(
    () =>
      calculatePricing({
        items: [{ sku: "A", unitPrice: Money.parse("1000.00"), quantity: 1 }],
        requestedLoyalty: Money.parse("100.01"),
        loyaltyCapBasisPoints: 1000,
      }),
    (error: unknown) => error instanceof DomainError && error.code === "pricing.loyalty_exceeds_limit",
  );
});

test("inventory reservation is idempotent and does not reserve twice", () => {
  const inventory = new InventoryBook();
  inventory.setStock("SKU-1", 5);
  inventory.reserve("r-1", [{ sku: "SKU-1", quantity: 2 }]);
  inventory.reserve("r-1", [{ sku: "SKU-1", quantity: 2 }]);
  assert.deepEqual(inventory.getStock("SKU-1"), { onHand: 5, reserved: 2, available: 3 });
});

test("inventory rejects reservation key reuse with different contents", () => {
  const inventory = new InventoryBook();
  inventory.setStock("SKU-1", 5);
  inventory.reserve("r-1", [{ sku: "SKU-1", quantity: 2 }]);
  assert.throws(
    () => inventory.reserve("r-1", [{ sku: "SKU-1", quantity: 3 }]),
    (error: unknown) => error instanceof DomainError && error.code === "inventory.idempotency_conflict",
  );
});

test("checkout replay returns one order and one reservation", () => {
  const inventory = new InventoryBook();
  inventory.setStock("SKU-1", 3);
  const store = new InMemoryCheckoutStore();
  const checkout = new CheckoutCoordinator(inventory, store, () => "order-1");
  const request = {
    customerId: "customer-1",
    idempotencyKey: "checkout-key-0001",
    items: [{ sku: "SKU-1", quantity: 2, unitPrice: Money.parse("1000.00") }],
  };

  const first = checkout.checkout(request);
  const second = checkout.checkout(request);
  assert.equal(first.replayed, false);
  assert.equal(second.replayed, true);
  assert.equal(first.order.id, second.order.id);
  assert.equal(store.countOrders(), 1);
  assert.deepEqual(inventory.getStock("SKU-1"), { onHand: 3, reserved: 2, available: 1 });
});

test("failed checkout rolls back claim and does not leave stock reserved", () => {
  const inventory = new InventoryBook();
  inventory.setStock("SKU-1", 1);
  const store = new InMemoryCheckoutStore();
  const checkout = new CheckoutCoordinator(inventory, store, () => "order-1");
  const request = {
    customerId: "customer-1",
    idempotencyKey: "checkout-key-0001",
    items: [{ sku: "SKU-1", quantity: 2, unitPrice: Money.parse("1000.00") }],
  };

  assert.throws(() => checkout.checkout(request));
  assert.deepEqual(inventory.getStock("SKU-1"), { onHand: 1, reserved: 0, available: 1 });
  inventory.setStock("SKU-1", 2);
  assert.equal(checkout.checkout(request).order.id, "order-1");
});

test("order transition graph rejects fulfillment before payment", () => {
  assert.throws(
    () => assertOrderTransition("awaiting_payment", "assembling"),
    (error: unknown) => error instanceof DomainError && error.code === "order.invalid_transition",
  );
});

test("coherent paid and shipped snapshots pass", () => {
  assert.doesNotThrow(() =>
    assertOrderSnapshot({ orderStatus: "paid", paymentStatus: "confirmed", deliveryStatus: "not_started" }),
  );
  assert.doesNotThrow(() =>
    assertOrderSnapshot({ orderStatus: "shipped", paymentStatus: "confirmed", deliveryStatus: "shipped" }),
  );
});

test("contradictory completed snapshot is rejected", () => {
  assert.throws(
    () => assertOrderSnapshot({ orderStatus: "completed", paymentStatus: "confirmed", deliveryStatus: "ready" }),
    (error: unknown) => error instanceof DomainError && error.code === "order.invalid_delivery_state",
  );
});

test("T-Bank token ignores Token and nested objects and is order-independent", () => {
  const left = createTBankToken({ TerminalKey: "demo", Amount: 1000, Receipt: { Items: [] } }, "secret");
  const right = createTBankToken({ Receipt: { Items: [] }, Amount: 1000, Token: "ignored", TerminalKey: "demo" }, "secret");
  assert.equal(left, right);
  assert.match(left, /^[a-f\d]{64}$/);
});

test("T-Bank notification token is verified with constant-length digest comparison", () => {
  const unsigned = { TerminalKey: "demo", OrderId: "order-1", PaymentId: "123", Status: "CONFIRMED", Success: true };
  const Token = createTBankToken(unsigned, "secret");
  assert.equal(verifyTBankNotification({ ...unsigned, Token }, "secret"), true);
  assert.equal(verifyTBankNotification({ ...unsigned, Token: `${Token.slice(0, -1)}0` }, "secret"), false);
});

test("T-Bank init amount must equal receipt amount", () => {
  assert.throws(
    () =>
      buildTBankInitRequest({
        terminalKey: "demo",
        password: "secret",
        orderId: "order-1",
        amount: Money.parse("100.00"),
        items: [{ Name: "Dress", Price: 9000, Quantity: 1, Amount: 9000, Tax: "none" }],
      }),
    (error: unknown) => error instanceof DomainError && error.code === "tbank.receipt_mismatch",
  );
});

test("Tilda semicolon CSV supports quotes, variants and unlimited quantity", () => {
  const feed = [
    "TildaUID;External ID;Parent ID;Brand;SKU;Category;Title;Description;Photo;Price;Quantity;Price OLD;Editions",
    'uid-1;;;FLASHIN;SKU-1;Dresses;"Dress; black";"Text with ""quotes""";https://example.com/a.jpg;12000.00;;15000.00;Size: S',
  ].join("\n");
  const products = parseTildaCsv(feed);
  assert.equal(products.length, 1);
  assert.equal(products[0]!.identity, "uid-1");
  assert.equal(products[0]!.title, "Dress; black");
  assert.equal(products[0]!.quantity, null);
  assert.equal(products[0]!.price.toString(), "12000.00");
});

test("Tilda duplicate identity with conflicting data fails closed", () => {
  const feed = [
    "TildaUID;SKU;Title;Price;Quantity",
    "uid-1;SKU-1;Dress;12000.00;2",
    "uid-1;SKU-1;Dress;13000.00;2",
  ].join("\n");
  assert.throws(
    () => parseTildaCsv(feed),
    (error: unknown) => error instanceof DomainError && error.code === "tilda.conflicting_duplicate",
  );
});

test("Money.fromMinor rejects fractional and unsafe number input with a domain error", () => {
  assert.throws(
    () => Money.fromMinor(1.5),
    (error: unknown) => error instanceof DomainError && error.code === "money.unsafe_minor",
  );
  assert.throws(
    () => Money.fromMinor(Number.MAX_SAFE_INTEGER + 1),
    (error: unknown) => error instanceof DomainError && error.code === "money.unsafe_minor",
  );
});
