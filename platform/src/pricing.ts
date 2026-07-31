import { invariant } from "./errors.js";
import { Money } from "./money.js";

export interface PriceLine {
  readonly sku: string;
  readonly unitPrice: Money;
  readonly quantity: number;
}

export type Promo =
  | { readonly kind: "fixed"; readonly amount: Money }
  | { readonly kind: "percent"; readonly basisPoints: number };

export interface PricingInput {
  readonly items: readonly PriceLine[];
  readonly promo?: Promo;
  readonly requestedLoyalty?: Money;
  readonly loyaltyCapBasisPoints?: number;
  readonly delivery?: Money;
}

export interface PricingBreakdown {
  readonly subtotal: Money;
  readonly promoDiscount: Money;
  readonly loyaltyDiscount: Money;
  readonly delivery: Money;
  readonly total: Money;
}

export function calculatePricing(input: PricingInput): PricingBreakdown {
  invariant(input.items.length > 0, "pricing.empty_cart", "Cart must contain at least one item");

  let subtotal = Money.zero;
  for (const item of input.items) {
    invariant(item.sku.trim().length > 0, "pricing.empty_sku", "Every price line must have a SKU");
    invariant(
      Number.isSafeInteger(item.quantity) && item.quantity > 0,
      "pricing.invalid_quantity",
      "Item quantity must be a positive safe integer",
      { sku: item.sku, quantity: item.quantity },
    );
    invariant(!item.unitPrice.isNegative(), "pricing.negative_price", "Item price cannot be negative", { sku: item.sku });
    subtotal = subtotal.add(item.unitPrice.multiply(item.quantity));
  }

  let promoDiscount = Money.zero;
  if (input.promo?.kind === "fixed") {
    invariant(!input.promo.amount.isNegative(), "pricing.negative_promo", "Fixed promo cannot be negative");
    promoDiscount = input.promo.amount;
  } else if (input.promo?.kind === "percent") {
    invariant(
      Number.isSafeInteger(input.promo.basisPoints) &&
        input.promo.basisPoints > 0 &&
        input.promo.basisPoints <= 10_000,
      "pricing.invalid_promo_percent",
      "Promo percent must be between 1 and 10000 basis points",
    );
    promoDiscount = subtotal.percentage(input.promo.basisPoints);
  }

  invariant(
    promoDiscount.minor <= subtotal.minor,
    "pricing.promo_exceeds_subtotal",
    "Promo discount cannot exceed subtotal",
  );

  const afterPromo = subtotal.subtract(promoDiscount);
  const requestedLoyalty = input.requestedLoyalty ?? Money.zero;
  invariant(!requestedLoyalty.isNegative(), "pricing.negative_loyalty", "Loyalty redemption cannot be negative");

  const loyaltyCap = input.loyaltyCapBasisPoints ?? 0;
  invariant(
    Number.isSafeInteger(loyaltyCap) && loyaltyCap >= 0 && loyaltyCap <= 10_000,
    "pricing.invalid_loyalty_cap",
    "Loyalty cap must be between 0 and 10000 basis points",
  );

  const maximumLoyalty = afterPromo.percentage(loyaltyCap).min(afterPromo);
  invariant(
    requestedLoyalty.minor <= maximumLoyalty.minor,
    "pricing.loyalty_exceeds_limit",
    "Requested loyalty redemption exceeds the configured limit",
    { requested: requestedLoyalty.toString(), maximum: maximumLoyalty.toString() },
  );

  const delivery = input.delivery ?? Money.zero;
  invariant(!delivery.isNegative(), "pricing.negative_delivery", "Delivery price cannot be negative");

  return {
    subtotal,
    promoDiscount,
    loyaltyDiscount: requestedLoyalty,
    delivery,
    total: afterPromo.subtract(requestedLoyalty).add(delivery),
  };
}

