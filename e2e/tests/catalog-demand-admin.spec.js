import { expect, test } from "@playwright/test";

async function login(page) {
  await page.goto("/");
  await page.getByPlaceholder("Email администратора").fill("demand@flashin.test");
  await page.getByPlaceholder("Пароль").fill("pilot-password");
  await page.getByRole("button", { name: "Войти" }).click();
  await expect(page.getByRole("button", { name: "Выйти" })).toBeVisible();
}

test("support manages preorder demand without catalog or financial write permissions", async ({ page }) => {
  let demand = {
    id: 901,
    customer_id: 501,
    product_id: 81,
    product_title: "Preorder Pilot Coat",
    product_sku: "PREORDER-081",
    variant_id: 8101,
    request_type: "preorder",
    quantity: 1,
    requested_size: "M",
    requested_color: "Black",
    notes: "Уточнить срок",
    status: "requested",
    admin_note: "",
  };
  let lastPatch = null;

  await page.route("http://localhost:8000/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname;
    const method = request.method();
    const json = (body, status = 200) => route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });

    if (path === "/api/admin/login" && method === "POST") return json({ access_token: "demand-admin-token" });
    if (path === "/api/admin/session" && method === "GET") {
      return json({
        id: 7,
        email: "demand@flashin.test",
        role: "support",
        all_access: false,
        permissions: ["demand.read", "demand.write", "showroom.read"],
      });
    }
    if (path === "/api/catalog/admin/demand-requests" && method === "GET") {
      return json(url.searchParams.get("status") === demand.status ? [demand] : []);
    }
    if (path === "/api/catalog/admin/demand-requests/901" && method === "PATCH") {
      lastPatch = request.postDataJSON();
      demand = { ...demand, status: lastPatch.status, admin_note: lastPatch.admin_note || "" };
      return json(demand);
    }
    if (path === "/api/catalog/admin/showroom/appointments" && method === "GET") return json([]);
    if (path === "/api/admin/products" && method === "GET") return json([]);
    if (path === "/api/admin/orders" && method === "GET") return json([]);
    if (path === "/api/admin/audit-logs" && method === "GET") return json([]);
    if (method === "GET") return json([]);
    return json({ detail: `Unmocked ${method} ${path}` }, 501);
  });

  await login(page);
  const operations = page.locator("section.catalog-support-operations");
  await expect(operations.getByRole("heading", { name: "Предзаказ / под заказ" })).toBeVisible();
  await expect(operations.getByText("Preorder Pilot Coat", { exact: false })).toBeVisible();
  await expect(operations.getByText("Customer #501", { exact: true })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Каталог и merchandising" })).toHaveCount(0);
  await expect(page.getByRole("heading", { name: "Заказы" })).toHaveCount(0);

  await operations.getByRole("button", { name: "Связались" }).click();
  await expect(operations.getByRole("status")).toContainText("contacted");
  expect(lastPatch).toEqual({ status: "contacted", admin_note: "" });
});
