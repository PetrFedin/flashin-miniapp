import { invariant } from "./errors.js";
import type { BotAction, BotCartLine, BotScene, BotSession, CheckoutContact, CheckoutDelivery } from "./bot-types.js";

type BotSessionData = Omit<BotSession, "version">;

export function initialBotSession(): BotSession {
  return { version: 0, scene: { kind: "home" }, cart: [] };
}

export function reduceBotSession(session: BotSession, action: BotAction): BotSession {
  invariant(Number.isSafeInteger(session.version) && session.version >= 0, "bot.invalid_session_version", "Bot session version must be a non-negative safe integer");
  const next = reduceScene(session, action);
  invariant(session.version < Number.MAX_SAFE_INTEGER, "bot.session_version_overflow", "Bot session version exhausted safe integer range");
  return { ...next, version: session.version + 1 };
}

function reduceScene(session: BotSession, action: BotAction): BotSessionData {
  switch (action.type) {
    case "reset":
      assertFreeNavigation(session.scene);
      return { scene: { kind: "home" }, cart: session.cart };
    case "open_catalog": {
      assertFreeNavigation(session.scene);
      const page = action.page ?? 1;
      invariant(Number.isSafeInteger(page) && page >= 1, "bot.invalid_catalog_page", "Catalog page must be a positive safe integer");
      const category = normalizeOptionalId(action.category, "bot.invalid_category", "Category");
      return { ...sessionData(session), scene: { kind: "catalog", page, ...(category ? { category } : {}) } };
    }
    case "open_product": {
      invariant(session.scene.kind === "catalog" || session.scene.kind === "product", "bot.invalid_transition", "Product can only be opened from catalog navigation");
      const productId = normalizeId(action.productId, "bot.invalid_product_id", "Product ID");
      const category = session.scene.category;
      return {
        ...sessionData(session),
        scene: { kind: "product", productId, page: session.scene.page, ...(category ? { category } : {}) },
      };
    }
    case "add_to_cart": {
      invariant(session.scene.kind === "catalog" || session.scene.kind === "product" || session.scene.kind === "cart", "bot.invalid_transition", "Items can only be added from catalog, product or cart screens");
      const quantity = action.quantity ?? 1;
      return { ...sessionData(session), scene: session.scene, cart: updateCart(session.cart, action.sku, quantity, "add") };
    }
    case "set_cart_quantity":
      invariant(session.scene.kind === "cart", "bot.invalid_transition", "Cart quantity can only be changed on the cart screen");
      return { ...sessionData(session), scene: session.scene, cart: updateCart(session.cart, action.sku, action.quantity, "set") };
    case "open_cart":
      assertFreeNavigation(session.scene);
      return { ...sessionData(session), scene: { kind: "cart" } };
    case "begin_checkout":
      invariant(session.scene.kind === "cart", "bot.invalid_transition", "Checkout can only start from cart");
      invariant(session.cart.length > 0, "bot.empty_cart", "Checkout cannot start with an empty cart");
      return { ...sessionData(session), scene: { kind: "checkout", step: "contact" } };
    case "submit_contact":
      invariant(session.scene.kind === "checkout" && session.scene.step === "contact", "bot.invalid_transition", "Contact data is not expected in the current checkout step");
      return { ...sessionData(session), contact: validateContact(action.contact), scene: { kind: "checkout", step: "delivery" } };
    case "submit_delivery":
      invariant(session.scene.kind === "checkout" && session.scene.step === "delivery", "bot.invalid_transition", "Delivery data is not expected in the current checkout step");
      invariant(session.contact, "bot.missing_contact", "Contact data must exist before delivery selection");
      return { ...sessionData(session), delivery: validateDelivery(action.delivery), scene: { kind: "checkout", step: "review" } };
    case "edit_contact":
      invariant(session.scene.kind === "checkout" && session.scene.step === "review", "bot.invalid_transition", "Contact editing is only available from checkout review");
      return { ...sessionData(session), scene: { kind: "checkout", step: "contact" } };
    case "edit_delivery":
      invariant(session.scene.kind === "checkout" && session.scene.step === "review", "bot.invalid_transition", "Delivery editing is only available from checkout review");
      return { ...sessionData(session), scene: { kind: "checkout", step: "delivery" } };
    case "payment_created": {
      invariant(session.scene.kind === "checkout" && session.scene.step === "review", "bot.invalid_transition", "Payment can only be created from checkout review");
      invariant(session.contact && session.delivery && session.cart.length > 0, "bot.incomplete_checkout", "Checkout must contain cart, contact and delivery data");
      const orderId = normalizeId(action.orderId, "bot.invalid_order_id", "Order ID");
      return { ...sessionData(session), scene: { kind: "payment", orderId, status: "pending" } };
    }
    case "payment_failed": {
      invariant(session.scene.kind === "payment" && session.scene.status === "pending", "bot.invalid_transition", "Only a pending payment can fail");
      assertSameOrder(session.scene.orderId, action.orderId);
      return { ...sessionData(session), scene: { kind: "payment", orderId: session.scene.orderId, status: "failed" } };
    }
    case "retry_payment": {
      invariant(session.scene.kind === "payment" && session.scene.status === "failed", "bot.invalid_transition", "Only a failed payment can be retried");
      assertSameOrder(session.scene.orderId, action.orderId);
      return { ...sessionData(session), scene: { kind: "payment", orderId: session.scene.orderId, status: "pending" } };
    }
    case "payment_confirmed": {
      invariant(session.scene.kind === "payment" && session.scene.status === "pending", "bot.invalid_transition", "Only a pending payment can be confirmed");
      assertSameOrder(session.scene.orderId, action.orderId);
      return { scene: { kind: "order", orderId: session.scene.orderId }, cart: [] };
    }
    case "cancel_checkout":
      invariant(session.scene.kind === "checkout", "bot.invalid_transition", "Checkout can only be cancelled before payment creation");
      return { scene: { kind: "cart" }, cart: session.cart };
    case "open_order":
      assertFreeNavigation(session.scene);
      return { ...sessionData(session), scene: { kind: "order", orderId: normalizeId(action.orderId, "bot.invalid_order_id", "Order ID") } };
    case "open_support":
      if (session.scene.kind === "support") return { ...sessionData(session), scene: session.scene };
      return { ...sessionData(session), scene: { kind: "support", returnTo: session.scene } };
    case "back":
      if (session.scene.kind === "checkout" && session.scene.step === "contact") {
        return { scene: { kind: "cart" }, cart: session.cart };
      }
      return { ...sessionData(session), scene: backTarget(session.scene) };
  }
}

function sessionData(session: BotSession): Omit<BotSession, "version" | "scene"> {
  return {
    cart: session.cart,
    ...(session.contact ? { contact: session.contact } : {}),
    ...(session.delivery ? { delivery: session.delivery } : {}),
  };
}

function assertFreeNavigation(scene: BotScene): void {
  const active = scene.kind === "support" ? scene.returnTo : scene;
  invariant(active.kind !== "checkout", "bot.checkout_navigation_locked", "Checkout navigation is locked until it is completed or explicitly cancelled");
  invariant(active.kind !== "payment", "bot.payment_navigation_locked", "Payment navigation is locked until the active payment is resolved");
}

function updateCart(cart: readonly BotCartLine[], rawSku: string, quantity: number, mode: "add" | "set"): readonly BotCartLine[] {
  const sku = normalizeId(rawSku, "bot.invalid_sku", "SKU");
  invariant(Number.isSafeInteger(quantity), "bot.invalid_cart_quantity", "Cart quantity must be a safe integer");
  invariant(mode === "set" ? quantity >= 0 : quantity > 0, "bot.invalid_cart_quantity", "Cart quantity is outside the allowed range");
  const current = cart.find((line) => line.sku === sku)?.quantity ?? 0;
  const nextQuantity = mode === "add" ? current + quantity : quantity;
  invariant(Number.isSafeInteger(nextQuantity) && nextQuantity <= 99, "bot.cart_quantity_limit", "Cart quantity per SKU cannot exceed 99", { sku, nextQuantity });
  return [...cart.filter((line) => line.sku !== sku), ...(nextQuantity > 0 ? [{ sku, quantity: nextQuantity }] : [])].sort((left, right) => left.sku.localeCompare(right.sku));
}

function validateContact(contact: CheckoutContact): CheckoutContact {
  const name = contact.name.trim().replace(/\s+/g, " ");
  const phone = contact.phone.replace(/[\s()-]/g, "");
  const email = contact.email?.trim().toLowerCase();
  invariant(name.length >= 2 && name.length <= 100, "bot.invalid_contact_name", "Contact name must be 2-100 characters");
  invariant(/^\+?[1-9]\d{7,14}$/.test(phone), "bot.invalid_phone", "Phone must contain 8-15 international digits");
  invariant(email === undefined || /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email), "bot.invalid_email", "Email has an invalid format");
  return { name, phone, ...(email ? { email } : {}) };
}

function validateDelivery(delivery: CheckoutDelivery): CheckoutDelivery {
  if (delivery.method === "courier") {
    const address = delivery.address?.trim().replace(/\s+/g, " ");
    invariant(address && address.length >= 8 && address.length <= 300, "bot.invalid_delivery_address", "Courier address must be 8-300 characters");
    return { method: "courier", address };
  }
  const pickupPointId = normalizeOptionalId(delivery.pickupPointId, "bot.invalid_pickup_point", "Pickup point ID");
  invariant(pickupPointId, "bot.invalid_pickup_point", "Pickup point ID is required for pickup");
  return { method: "pickup", pickupPointId };
}

function backTarget(scene: BotScene): BotScene {
  switch (scene.kind) {
    case "home":
      return scene;
    case "catalog":
      return { kind: "home" };
    case "product":
      return { kind: "catalog", page: scene.page, ...(scene.category ? { category: scene.category } : {}) };
    case "cart":
      return { kind: "home" };
    case "checkout":
      if (scene.step === "contact") return { kind: "cart" };
      if (scene.step === "delivery") return { kind: "checkout", step: "contact" };
      return { kind: "checkout", step: "delivery" };
    case "payment":
      return scene;
    case "order":
      return { kind: "home" };
    case "support":
      return scene.returnTo;
  }
}

function assertSameOrder(current: string, supplied: string): void {
  invariant(current === supplied.trim(), "bot.order_mismatch", "Payment event order does not match the active session order", { current, supplied });
}

function normalizeId(value: string, code: string, label: string): string {
  const normalized = value.trim();
  invariant(normalized.length >= 1 && normalized.length <= 200 && /^[A-Za-z0-9_.:-]+$/.test(normalized), code, `${label} contains invalid characters or length`);
  return normalized;
}

function normalizeOptionalId(value: string | undefined, code: string, label: string): string | undefined {
  return value === undefined || value.trim() === "" ? undefined : normalizeId(value, code, label);
}

