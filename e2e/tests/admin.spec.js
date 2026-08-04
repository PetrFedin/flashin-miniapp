import { expect, test } from "@playwright/test";

async function mockAdminApi(page) {
  let products = [{
    id: 1,
    sku: "FLASH-001",
    title: "Pilot Jacket",
    slug: "pilot-jacket",
    brand: "FLASHIN",
    price: 12000,
    currency: "RUB",
    category: "Outerwear",
    active: true,
    variants: [{ id: 11, size: "M", sku: "FLASH-001-M", stock_qty: 5 }],
  }];
  let orders = [{
    id: 9001,
    status: "created",
    payment_status: "pending",
    total: 12000,
    currency: "RUB",
    customer: { first_name: "Pilot" },
    items: [{ id: 1, title: "Pilot Jacket", size: "M", quantity: 1 }],
  }];

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

    if (path === "/api/admin/login" && method === "POST") return json({ access_token: "admin-pilot-token" });
    if (path === "/api/admin/products" && method === "GET") return json(products);
    if (path === "/api/admin/orders" && method === "GET") return json(orders);
    if (path === "/api/admin/audit-logs" && method === "GET") return json([]);
    if (path === "/api/ops/inventory/low-stock" && method === "GET") return json([]);
    if (path === "/api/ops/abandoned-carts" && method === "GET") return json([]);
    if (path === "/api/admin/promocodes" && method === "POST") return json({ id: 70, code: "PILOT10" });
    if (path === "/api/admin/products" && method === "POST") {
      const body = request.postDataJSON();
      const created = { id: 2, active: true, currency: "RUB", ...body };
      products = [...products, created];
      return json(created, 201);
    }
    if (path === "/api/admin/orders/9001/cancel" && method === "POST") {
      orders = orders.map((order) => order.id === 9001
        ? { ...order, status: "cancelled", payment_status: "cancelled" }
        : order);
      return json(orders[0]);
    }
    if (path.startsWith("/api/admin/pilot-operations") || path.startsWith("/api/ops/")) return json({});

    return json({ detail: `Unmocked ${method} ${path}` }, 501);
  });
}

test("Admin critical pilot operator journey", async ({ page }) => {
  await mockAdminApi(page);
  await page.goto("/");

  await expect(page.getByRole("heading", { name: "FLASHIN Admin" })).toBeVisible();
  await page.getByPlaceholder("Email администратора").fill("pilot@flashin.test");
  await page.getByPlaceholder("Пароль").fill("pilot-password");
  await page.getByRole("button", { name: "Войти" }).click();

  await expect(page.getByRole("button", { name: "Выйти" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Импорт и экспорт" })).toBeVisible();
  await expect(page.getByText("Pilot Jacket")).toBeVisible();

  await page.getByPlaceholder("CODE").fill("PILOT10");
  await page.getByRole("button", { name: "Создать" }).first().click();
  await expect(page.getByRole("status")).toContainText("Промокод создан");

  await page.getByPlaceholder("SKU").fill("FLASH-002");
  await page.getByPlaceholder("Название").fill("Pilot Trousers");
  await page.getByPlaceholder("slug").fill("pilot-trousers");
  await page.getByPlaceholder("Цена").fill("9000");
  await page.getByPlaceholder("Размер").fill("M");
  await page.getByPlaceholder("SKU размера").fill("FLASH-002-M");
  await page.getByRole("button", { name: /Создать товар/i }).click();
  await expect(page.getByRole("status")).toContainText("Товар создан");
  await expect(page.getByText("Pilot Trousers")).toBeVisible();

  page.once("dialog", (dialog) => dialog.accept());
  const cancelButton = page.getByRole("button", { name: /Отменить/ }).first();
  if (await cancelButton.isVisible()) {
    await cancelButton.click();
    await expect(page.getByRole("status")).toContainText("Заказ #9001 отменён");
  }

  await page.getByRole("button", { name: "Обновить" }).click();
  await expect(page.getByRole("status")).toContainText("Данные обновлены");

  await page.getByRole("button", { name: "Выйти" }).click();
  await expect(page.getByRole("button", { name: "Войти" })).toBeVisible();
});
