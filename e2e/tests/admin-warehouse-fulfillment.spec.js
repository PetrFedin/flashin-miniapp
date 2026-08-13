import { expect, test } from "@playwright/test";

const SESSION = {
  id: 73,
  email: "warehouse-fulfillment@flashin.test",
  role: "warehouse",
  all_access: false,
  permissions: [
    "products.read",
    "inventory.write",
    "orders.read",
    "fulfillment.write",
    "media.write",
  ],
};

const ORDER = {
  id: 9201,
  status: "paid",
  payment_status: "paid",
  delivery_status: "assembling",
  total_amount: 15990,
  currency: "RUB",
  items: [],
};

const RETURN = {
  id: 9901,
  order_id: ORDER.id,
  customer_id: 501,
  customer_username: "warehouse-fixture",
  customer_name: "Warehouse Fixture",
  reason: "Размер",
  status: "requested",
  currency: "RUB",
  order_total: 15990,
  approved_refund_total: 0,
  refunded_total: 0,
  refundable_balance: 15990,
  provider_refund_id: "",
  provider_payment_id: "pay-warehouse-fixture",
  provider_payment_status: "succeeded",
};


test("warehouse can operate fulfillment without receiving financial order write", async ({ page }) => {
  await page.route("http://localhost:8000/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname;
    const method = request.method();
    const json = (body, status = 200) => route.fulfill({
      status,
      contentType: "application/json",
      body: JSON.stringify(body),
    });

    if (path === "/api/admin/login" && method === "POST") return json({ access_token: "warehouse-token" });
    if (path === "/api/admin/session" && method === "GET") return json(SESSION);
    if (path === "/api/admin/products" && method === "GET") return json([]);
    if (path === "/api/admin/orders" && method === "GET") return json([ORDER]);
    if (path === "/api/ops/inventory/low-stock" && method === "GET") return json([]);
    if (path === "/api/moysklad/operations-status" && method === "GET") {
      return json({
        schema_version: 1,
        attention_required: false,
        summary: {
          last_sync_status: "success",
          last_sync_at: null,
          pending_matches: 0,
          open_reconciliations: 0,
          open_conflicts: 0,
        },
        sync_logs: [],
        sku_matches: [],
        reconciliations: [],
        conflicts: [],
      });
    }
    if (path === "/api/fulfillment/tasks" && method === "GET") {
      return json([{ id: 301, order_id: ORDER.id, status: "new", assigned_admin_id: null, comment: "" }]);
    }
    if (path === "/api/delivery-providers/shipments" && method === "GET") return json([]);
    if (path === "/api/fulfillment/sla" && method === "GET") return json([]);
    if (path === "/api/admin/returns" && method === "GET") return json([RETURN]);
    if (path === "/api/platform/admin/events/summary" && method === "GET") {
      return json({ counts: { failed: 0, pending: 0, processed: 0 }, oldest_failed_at: null });
    }
    if (path === "/api/platform/admin/events" && method === "GET") return json([]);
    if (method === "GET") return json([]);
    return json({ detail: `Unexpected ${method} ${path}` }, 501);
  });

  await page.goto("/");
  await page.getByPlaceholder("Email администратора").fill(SESSION.email);
  await page.getByPlaceholder("Пароль").fill("pilot-password");
  await page.getByRole("button", { name: "Войти" }).click();

  await expect(page.getByText(`${SESSION.email} · ${SESSION.role}`, { exact: true })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Fulfillment & Delivery" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Начать сборку" })).toBeVisible();
  await expect(page.getByText(/Fulfillment доступен только для чтения/)).toHaveCount(0);
  await expect(page.getByRole("article", { name: "Возвраты и refunds" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Подтвердить refund" })).toHaveCount(0);
  await expect(page.getByText(/Только чтение: нет orders.write/)).toBeVisible();
});
