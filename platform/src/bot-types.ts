export type BotScene =
  | { readonly kind: "home" }
  | { readonly kind: "catalog"; readonly category?: string; readonly page: number }
  | { readonly kind: "product"; readonly productId: string; readonly category?: string; readonly page: number }
  | { readonly kind: "cart" }
  | { readonly kind: "checkout"; readonly step: "contact" | "delivery" | "review" }
  | { readonly kind: "payment"; readonly orderId: string; readonly status: "pending" | "failed" }
  | { readonly kind: "order"; readonly orderId: string }
  | { readonly kind: "support"; readonly returnTo: Exclude<BotScene, { readonly kind: "support" }> };

export interface BotCartLine {
  readonly sku: string;
  readonly quantity: number;
}

export interface CheckoutContact {
  readonly name: string;
  readonly phone: string;
  readonly email?: string;
}

export interface CheckoutDelivery {
  readonly method: "courier" | "pickup";
  readonly address?: string;
  readonly pickupPointId?: string;
}

export interface BotSession {
  readonly version: number;
  readonly scene: BotScene;
  readonly cart: readonly BotCartLine[];
  readonly contact?: CheckoutContact;
  readonly delivery?: CheckoutDelivery;
}


export type BotAction =
  | { readonly type: "reset" }
  | { readonly type: "open_catalog"; readonly category?: string; readonly page?: number }
  | { readonly type: "open_product"; readonly productId: string }
  | { readonly type: "add_to_cart"; readonly sku: string; readonly quantity?: number }
  | { readonly type: "set_cart_quantity"; readonly sku: string; readonly quantity: number }
  | { readonly type: "open_cart" }
  | { readonly type: "begin_checkout" }
  | { readonly type: "submit_contact"; readonly contact: CheckoutContact }
  | { readonly type: "submit_delivery"; readonly delivery: CheckoutDelivery }
  | { readonly type: "edit_contact" }
  | { readonly type: "edit_delivery" }
  | { readonly type: "payment_created"; readonly orderId: string }
  | { readonly type: "payment_failed"; readonly orderId: string }
  | { readonly type: "retry_payment"; readonly orderId: string }
  | { readonly type: "payment_confirmed"; readonly orderId: string }
  | { readonly type: "cancel_checkout" }
  | { readonly type: "open_order"; readonly orderId: string }
  | { readonly type: "open_support" }
  | { readonly type: "back" };
