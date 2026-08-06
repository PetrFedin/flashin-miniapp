import { invariant } from "./errors.js";

export type OrderStatus =
  | "created"
  | "awaiting_payment"
  | "paid"
  | "assembling"
  | "ready"
  | "shipped"
  | "completed"
  | "cancelled"
  | "refund_pending"
  | "partially_refunded"
  | "refunded"
  | "review_required";

export type PaymentStatus =
  | "none"
  | "pending"
  | "authorized"
  | "confirmed"
  | "partially_refunded"
  | "refunded"
  | "cancelled"
  | "review_required";

export type DeliveryStatus = "not_started" | "assembling" | "ready" | "shipped" | "delivered" | "cancelled";

const transitions: Readonly<Record<OrderStatus, readonly OrderStatus[]>> = {
  created: ["awaiting_payment", "cancelled"],
  awaiting_payment: ["paid", "cancelled", "review_required"],
  paid: ["assembling", "refund_pending", "review_required"],
  assembling: ["ready", "refund_pending", "review_required"],
  ready: ["shipped", "refund_pending", "review_required"],
  shipped: ["completed", "refund_pending", "review_required"],
  completed: ["refund_pending"],
  cancelled: [],
  refund_pending: ["partially_refunded", "refunded", "review_required"],
  partially_refunded: ["refund_pending", "refunded", "review_required"],
  refunded: [],
  review_required: ["awaiting_payment", "refund_pending", "cancelled"],
};

export interface OrderSnapshot {
  readonly orderStatus: OrderStatus;
  readonly paymentStatus: PaymentStatus;
  readonly deliveryStatus: DeliveryStatus;
}

export function assertOrderTransition(from: OrderStatus, to: OrderStatus): void {
  invariant(
    transitions[from].includes(to),
    "order.invalid_transition",
    `Order transition ${from} -> ${to} is not allowed`,
    { from, to },
  );
}

export function assertOrderSnapshot(snapshot: OrderSnapshot): void {
  const { orderStatus, paymentStatus, deliveryStatus } = snapshot;

  if (orderStatus === "created") {
    invariant(paymentStatus === "none", "order.invalid_payment_state", "Created order cannot have a payment");
    invariant(deliveryStatus === "not_started", "order.invalid_delivery_state", "Created order cannot be in delivery");
  }

  if (orderStatus === "awaiting_payment") {
    invariant(paymentStatus === "pending" || paymentStatus === "authorized", "order.invalid_payment_state", "Awaiting-payment order must have an active payment");
    invariant(deliveryStatus === "not_started", "order.invalid_delivery_state", "Unpaid order cannot be in delivery");
  }

  if (["paid", "assembling", "ready", "shipped", "completed"].includes(orderStatus)) {
    invariant(paymentStatus === "confirmed", "order.invalid_payment_state", "Fulfillment requires a confirmed payment");
  }

  const expectedDelivery: Partial<Record<OrderStatus, DeliveryStatus>> = {
    paid: "not_started",
    assembling: "assembling",
    ready: "ready",
    shipped: "shipped",
    completed: "delivered",
    cancelled: "cancelled",
  };
  const expected = expectedDelivery[orderStatus];
  if (expected) {
    invariant(deliveryStatus === expected, "order.invalid_delivery_state", "Order and delivery states are inconsistent", {
      orderStatus,
      deliveryStatus,
      expected,
    });
  }

  if (orderStatus === "partially_refunded") {
    invariant(paymentStatus === "partially_refunded", "order.invalid_payment_state", "Partially refunded order must match payment state");
  }

  if (orderStatus === "refunded") {
    invariant(paymentStatus === "refunded", "order.invalid_payment_state", "Refunded order must match payment state");
  }

  if (orderStatus === "review_required") {
    invariant(paymentStatus === "review_required", "order.invalid_payment_state", "Review order must match payment state");
  }
}

