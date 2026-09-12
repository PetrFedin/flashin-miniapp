import { expect, test } from "@playwright/test";

async function login(page) {
  await page.goto("/");
  await page.getByPlaceholder("Email администратора").fill("catalog@flashin.test");
  await page.getByPlaceholder("Пароль").fill("pilot-password");
  await page.getByRole("button", { name: "Войти" }).click();
  await expect(page.getByRole("button", { name: "Выйти" })).toBeVisible();
}

async function installIntentAdminMocks(page) {
  let intent = {
    id: 801,
    customer_id: 501,
    product_id: 51,
    product_title: "Zero Stock Preorder Coat",
    variant_id: 5101,
    variant_size: "M",
    variant_color: "Black",
    intent_type: "preorder",
    quantity: 2,
    notes: "Нужен размер M к концу месяца",
    status: "requested",
    admin_note: "",
    quote_amount: null,
    quote_currency: "RUB",
    estimated_ready_at: null,
    created_at: "2026-08-17T10:00:00Z",
    updated_at: "2026-08-17T10:00:00Z",
    payment_allowed: false,
    normal_checkout_available: false,
  };
  const patches = [];

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

    if (path === "/api/admin/login" && method === "POST") return json({ access_token: "catalog-intent-admin-token" });
    if (path === "/api/admin/session" && method === "GET") {
      return json({
        id: 3,
        email: "catalog@flashin.test",
        role: "catalog_manager",
        all_access: false,
        permissions: ["products.read", "products.write"],
      });
    }
    if (path === "/api/catalog/admin/intents" && method === "GET") return json([intent]);
    if (path === "/api/catalog/admin/intents/801" && method === "PATCH") {
      const body = request.postDataJSON();
      patches.push(body);
      intent = {
        ...intent,
        status: body.status ?? intent.status,
        admin_note: Object.hasOwn(body, "admin_note") ? (body.admin_note ?? "") : intent.admin_note,
        quote_amount: Object.hasOwn(body, "quote_amount") ? body.quote_amount : intent.quote_amount,
        quote_currency: Object.hasOwn(body, "quote_currency") ? body.quote_currency : intent.quote_currency,
        estimated_ready_at: Object.hasOwn(body, "estimated_ready_at") ? body.estimated_ready_at : intent.estimated_ready_at,
        updated_at: "2026-08-17T11:00:00Z",
      };
      return json(intent);
    }
    if (path === "/api/moysklad-deep-mapping/status" && method === "GET") return json({ matches: [], conflicts: [] });
    if (method === "GET") return json([]);
    return json({ detail: `Unmocked ${method} ${path}` }, 501);
  });

  return {
    intent: () => intent,
    patches: () => patches,
  };
}

test("catalog operator progresses intent and can explicitly clear quote and ETA", async ({ page }) => {
  const state = await installIntentAdminMocks(page);
  await login(page);

  const panel = page.locator("section.catalog-intent-operations");
  await expect(panel.getByRole("heading", { name: "Предзаказ и товары под заказ" })).toBeVisible();
  await expect(panel.getByText("Customer #501 · PII скрыты", { exact: true })).toBeVisible();
  await expect(panel.getByText("Zero Stock Preorder Coat", { exact: false })).toBeVisible();

  await panel.getByLabel("Статус заявки 801").selectOption("working");
  await panel.getByLabel("Сумма предложения 801").fill("45500");
  await panel.getByLabel("Валюта предложения 801").fill("RUB");
  await panel.getByLabel("Срок готовности 801").fill("2026-08-25T15:30");
  await panel.getByLabel("Комментарий оператора 801").fill("Подтверждаем производство");
  await panel.getByRole("button", { name: "Сохранить заявку" }).click();

  await expect(panel.getByRole("status")).toContainText("Заявка #801 обновлена");
  expect(state.intent().status).toBe("working");
  expect(state.intent().quote_amount).toBe(45500);
  expect(state.intent().estimated_ready_at).toMatch(/^2026-08-25T/);

  await panel.getByLabel("Статус заявки 801").selectOption("ready");
  await panel.getByRole("button", { name: "Сохранить заявку" }).click();
  await expect(panel.getByRole("status")).toContainText("Заявка #801 обновлена");
  expect(state.intent().status).toBe("ready");

  await panel.getByLabel("Сумма предложения 801").fill("");
  await panel.getByLabel("Срок готовности 801").fill("");
  await panel.getByRole("button", { name: "Сохранить заявку" }).click();
  await expect(panel.getByRole("status")).toContainText("Заявка #801 обновлена");

  expect(state.intent().quote_amount).toBeNull();
  expect(state.intent().estimated_ready_at).toBeNull();
  expect(state.patches().at(-1).quote_amount).toBeNull();
  expect(state.patches().at(-1).estimated_ready_at).toBeNull();
});
