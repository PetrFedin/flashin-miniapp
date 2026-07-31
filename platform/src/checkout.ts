import { createHash } from "node:crypto";
import { DomainError, invariant } from "./errors.js";
import { InventoryBook, type StockItem } from "./inventory.js";
import { Money } from "./money.js";
import { calculatePricing, type PricingBreakdown, type Promo } from "./pricing.js";

export interface CheckoutItem extends StockItem {
  readonly unitPrice: Money;
}

export interface CheckoutRequest {
  readonly customerId: string;
  readonly idempotencyKey: string;
  readonly items: readonly CheckoutItem[];
  readonly promo?: Promo;
  readonly requestedLoyalty?: Money;
  readonly loyaltyCapBasisPoints?: number;
  readonly delivery?: Money;
}

export interface Order {
  readonly id: string;
  readonly customerId: string;
  readonly reservationId: string;
  readonly pricing: PricingBreakdown;
  readonly items: readonly CheckoutItem[];
}

interface IdempotencyRecord {
  readonly fingerprint: string;
  state: "processing" | "completed";
  order?: Order;
}

export class InMemoryCheckoutStore {
  private readonly idempotency = new Map<string, IdempotencyRecord>();
  private readonly orders = new Map<string, Order>();

  public claim(customerId: string, key: string, fingerprint: string): Order | undefined {
    const identity = `${customerId}:${key}`;
    const existing = this.idempotency.get(identity);
    if (!existing) {
      this.idempotency.set(identity, { fingerprint, state: "processing" });
      return undefined;
    }
    if (existing.fingerprint !== fingerprint) {
      throw new DomainError("checkout.idempotency_conflict", "Idempotency key was reused with a different checkout request");
    }
    if (existing.state === "processing") {
      throw new DomainError("checkout.in_progress", "Checkout with this idempotency key is already in progress");
    }
    return existing.order;
  }

  public complete(customerId: string, key: string, order: Order): void {
    const identity = `${customerId}:${key}`;
    const record = this.idempotency.get(identity);
    invariant(record?.state === "processing", "checkout.missing_claim", "Checkout claim is missing");
    record.state = "completed";
    record.order = order;
    this.orders.set(order.id, order);
  }

  public abort(customerId: string, key: string): void {
    const identity = `${customerId}:${key}`;
    const record = this.idempotency.get(identity);
    if (record?.state === "processing") this.idempotency.delete(identity);
  }

  public countOrders(): number {
    return this.orders.size;
  }
}

export class CheckoutCoordinator {
  constructor(
    private readonly inventory: InventoryBook,
    private readonly store: InMemoryCheckoutStore,
    private readonly createId: () => string,
  ) {}

  public checkout(request: CheckoutRequest): { readonly order: Order; readonly replayed: boolean } {
    const customerId = request.customerId.trim();
    const key = request.idempotencyKey.trim();
    invariant(customerId.length > 0, "checkout.empty_customer", "Customer ID cannot be empty");
    invariant(key.length >= 8 && key.length <= 128, "checkout.invalid_idempotency_key", "Idempotency key must be 8-128 characters");

    const fingerprint = checkoutFingerprint(request);
    const replay = this.store.claim(customerId, key, fingerprint);
    if (replay) return { order: replay, replayed: true };

    const orderId = this.createId();
    const reservationId = `checkout:${orderId}`;
    try {
      const pricing = calculatePricing({
        items: request.items.map((item) => ({ sku: item.sku, unitPrice: item.unitPrice, quantity: item.quantity })),
        ...(request.promo ? { promo: request.promo } : {}),
        ...(request.requestedLoyalty ? { requestedLoyalty: request.requestedLoyalty } : {}),
        ...(request.loyaltyCapBasisPoints !== undefined ? { loyaltyCapBasisPoints: request.loyaltyCapBasisPoints } : {}),
        ...(request.delivery ? { delivery: request.delivery } : {}),
      });

      this.inventory.reserve(
        reservationId,
        request.items.map((item) => ({ sku: item.sku, quantity: item.quantity })),
      );

      const order: Order = { id: orderId, customerId, reservationId, pricing, items: request.items };
      this.store.complete(customerId, key, order);
      return { order, replayed: false };
    } catch (error) {
      try {
        this.inventory.release(reservationId);
      } catch {
        // No reservation was created or it was already terminal. The original failure remains authoritative.
      }
      this.store.abort(customerId, key);
      throw error;
    }
  }
}

function checkoutFingerprint(request: CheckoutRequest): string {
  const canonical = {
    customerId: request.customerId.trim(),
    items: [...request.items]
      .map((item) => ({ sku: item.sku.trim(), quantity: item.quantity, unitPrice: item.unitPrice.toString() }))
      .sort((left, right) => left.sku.localeCompare(right.sku)),
    promo:
      request.promo?.kind === "fixed"
        ? { kind: "fixed", amount: request.promo.amount.toString() }
        : request.promo
          ? { kind: "percent", basisPoints: request.promo.basisPoints }
          : null,
    requestedLoyalty: request.requestedLoyalty?.toString() ?? "0.00",
    loyaltyCapBasisPoints: request.loyaltyCapBasisPoints ?? 0,
    delivery: request.delivery?.toString() ?? "0.00",
  };
  return createHash("sha256").update(JSON.stringify(canonical)).digest("hex");
}

