import { delay, http, HttpResponse } from "msw";

function detail(code, message) {
  return { detail: { code, message } };
}

export const scenarioHandlers = Object.freeze({
  "stock-race": [
    http.post("*/api/cart/items", () => HttpResponse.json(
      detail("stock_race", "Variant availability changed during add-to-cart."),
      { status: 409 },
    )),
    http.post("*/api/orders/checkout", () => HttpResponse.json(
      detail("stock_race", "Inventory changed before checkout commit."),
      { status: 409 },
    )),
  ],
  "payment-fail": [
    http.post("*/api/payments", () => HttpResponse.json(
      detail("provider_unavailable", "Payment provider is unavailable."),
      { status: 503 },
    )),
  ],
  "payment-pending": [
    http.post("*/api/payments", () => HttpResponse.json({
      order_id: 9001,
      payment_status: "pending",
      status: "pending",
      confirmation_url: "",
    })),
  ],
  "refund-pending": [
    http.post("*/api/returns", () => HttpResponse.json({
      id: 7001,
      status: "pending",
      refund_status: "pending",
    }, { status: 201 })),
  ],
  "delivery-unavailable": [
    http.post("*/api/delivery-quotes", () => HttpResponse.json(
      detail("provider_unavailable", "Delivery provider is unavailable."),
      { status: 503 },
    )),
  ],
  "provider-timeout": [
    http.post("*/api/delivery-quotes", async () => {
      await delay(30_000);
      return HttpResponse.json({ unreachable: true });
    }),
  ],
  "reservation-expired": [
    http.post("*/api/reservations/:reservationId/commit", () => HttpResponse.json(
      detail("reservation_expired", "Reservation has expired."),
      { status: 409 },
    )),
  ],
});

export function handlersForScenario(name) {
  return scenarioHandlers[String(name || "").trim()] || [];
}
