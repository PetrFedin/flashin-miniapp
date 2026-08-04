import { expect, test } from "@playwright/test";

async function mockOwnerJourney(page) {
  let ticket = {
    id: 901,
    order_id: 9002,
    subject: "Проверить возврат",
    message: "Требуется ответственный оператор",
    status: "open",
    priority: "normal",
    assigned_admin_id: null,
  };
  let capturedUpdate = null;

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

    if (path === "/api/admin/login" && method === "POST") return json({ access_token: "owner-pilot-token" });
    if (path === "/api/admin/products" && method === "GET") return json([]);
    if (path === "/api/admin/orders" && method === "GET") return json([]);
    if (path === "/api/admin/audit-logs" && method === "GET") return json([]);
    if (path === "/api/ops/inventory/low-stock" && method === "GET") return json([]);
    if (path === "/api/ops/abandoned-carts" && method === "GET") return json([]);
    if (path === "/api/ops/pilot-runtime" && method === "GET") {
      return json({
        schema_version: 1,
        checkout_decision: "NO-GO",
        generated_at: "2026-08-05T00:00:00Z",
        enforced: true,
        runtime: { present: false, status: "missing", max_orders: 20, accepted_orders: 0, remaining_orders: 0 },
        database_integrity: { healthy: true, codes: [] },
        artifact_integrity: { applicable: false, healthy: false, codes: [] },
        money_attention: {
          payment_review_orders: 0,
          refund_attention_orders: 0,
          reconciliation_mismatches: 0,
          attention_required: false,
        },
      });
    }
    if (path === "/api/support/admin/tickets" && method === "GET") return json([ticket]);
    if (path === "/api/support/admin/tickets/901" && method === "PATCH") {
      capturedUpdate = request.postDataJSON();
      ticket = { ...ticket, ...capturedUpdate };
      return json(ticket);
    }
    if (path === "/api/privacy/admin/requests" && method === "GET") return json([]);
    if (path === "/api/admin/returns" && method === "GET") return json([]);
    if (path === "/api/platform/admin/events/summary" && method === "GET") {
      return json({ counts: { failed: 0, pending: 0, processed: 0 }, oldest_failed_at: null });
    }
    if (path === "/api/platform/admin/events" && method === "GET") return json([]);

    return json({ detail: `Unmocked ${method} ${path}` }, 501);
  });

  return {
    getCapturedUpdate: () => capturedUpdate,
  };
}

test("Admin assigns an accountable owner to a support ticket", async ({ page }) => {
  const state = await mockOwnerJourney(page);
  await page.goto("/");

  await page.getByPlaceholder("Email администратора").fill("owner@flashin.test");
  await page.getByPlaceholder("Пароль").fill("owner-password");
  await page.getByRole("button", { name: "Войти" }).click();

  await expect(page.getByRole("heading", { name: "Service Operations" })).toBeVisible();
  await page.getByLabel("Статус обращения 901").selectOption("in_progress");
  await page.getByLabel("Приоритет обращения 901").selectOption("high");
  await page.getByLabel("Ответственный обращения 901").fill("42");
  await page.getByRole("button", { name: "Сохранить обращение" }).click();

  await expect(page.getByRole("status")).toContainText("Обращение #901 обновлено");
  await expect(page.getByLabel("Ответственный обращения 901")).toHaveValue("42");
  await expect(page.getByText("В работе", { exact: true })).toBeVisible();
  expect(state.getCapturedUpdate()).toEqual({
    status: "in_progress",
    priority: "high",
    assigned_admin_id: 42,
  });
});
