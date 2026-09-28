import { expect, test } from "@playwright/test";

async function login(page, email = "operator@flashin.test") {
  await page.goto("/");
  await page.getByPlaceholder("Email администратора").fill(email);
  await page.getByPlaceholder("Пароль").fill("pilot-password");
  await page.getByRole("button", { name: "Войти" }).click();
  await expect(page.getByRole("button", { name: "Выйти" })).toBeVisible();
}

async function installOperatorMocks(page, mode) {
  let appointment = { id: 91, customer_id: 501, product_id: 41, starts_at: "2026-08-20T10:30:00Z", duration_minutes: 30, status: "requested", notes: "Нужна примерка полного лука" };
  let feedback = { id: 71, product_id: 41, product_title: "Pilot Jacket", rating: 2, comment: "Нужна проверка отзыва", status: "published", created_at: "2026-08-14T08:00:00Z", updated_at: "2026-08-14T08:00:00Z" };
  let pricing = { product_id: 41, sku: "FLASH-PRICE-041", title: "Pilot Jacket", active: true, regular_price: 32000, configured_promo_price: null, effective_price: 32000, compare_at_price: null, promo_active: false, sale_starts_at: null, sale_ends_at: null, configuration_error: null };
  let lastPricingPatch = null;

  await page.route("http://localhost:8000/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname;
    const method = request.method();
    const json = (body, status = 200) => route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });

    if (path === "/api/admin/login" && method === "POST") return json({ access_token: "operator-token" });
    if (path === "/api/admin/session" && method === "GET") {
      return json(mode === "showroom"
        ? { id: 2, email: "showroom@flashin.test", role: "showroom_operator", all_access: false, permissions: ["showroom.read", "showroom.write"] }
        : { id: 3, email: "catalog@flashin.test", role: "catalog_manager", all_access: false, permissions: ["products.read", "products.write"] });
    }
    if (path === "/api/admin/products" && method === "GET") return json([]);
    if (path === "/api/admin/orders" && method === "GET") return json([]);
    if (path === "/api/admin/audit-logs" && method === "GET") return json([]);
    if (path === "/api/catalog/admin/showroom/appointments" && method === "GET") return json([appointment]);
    if (path === "/api/catalog/admin/showroom/appointments/91" && method === "PATCH") {
      appointment = { ...appointment, status: request.postDataJSON().status };
      return json({ ok: true, id: appointment.id, status: appointment.status });
    }
    if (path === "/api/catalog/admin/feedback" && method === "GET") {
      const status = url.searchParams.get("status");
      return json(feedback.status === status ? [feedback] : []);
    }
    if (path === "/api/catalog/admin/feedback/71" && method === "PATCH") {
      feedback = { ...feedback, status: request.postDataJSON().status };
      return json({ ok: true, id: feedback.id, status: feedback.status });
    }
    if (path === "/api/catalog/admin/pricing" && method === "GET") return json([pricing]);
    if (path === "/api/catalog/admin/products/41/pricing" && method === "PATCH") {
      lastPricingPatch = request.postDataJSON();
      pricing = { ...pricing, configured_promo_price: lastPricingPatch.promo_price, sale_starts_at: lastPricingPatch.sale_starts_at, sale_ends_at: lastPricingPatch.sale_ends_at, effective_price: lastPricingPatch.promo_price ?? pricing.regular_price, compare_at_price: lastPricingPatch.promo_price == null ? null : pricing.regular_price, promo_active: lastPricingPatch.promo_price != null };
      return json(pricing);
    }
    if (path === "/api/catalog/admin/products" && method === "GET") return json([]);
    if (path === "/api/moysklad-deep-mapping/status" && method === "GET") return json({ matches: [], conflicts: [] });
    if (method === "GET") return json([]);
    return json({ detail: `Unmocked ${method} ${path}` }, 501);
  });

  return { appointment: () => appointment, feedback: () => feedback, pricing: () => pricing, lastPricingPatch: () => lastPricingPatch };
}

test("showroom operator manages visits without product catalog access", async ({ page }) => {
  const state = await installOperatorMocks(page, "showroom");
  await login(page, "showroom@flashin.test");
  const operations = page.locator("section.catalog-support-operations");
  await expect(operations.getByRole("heading", { name: "Showroom и обратная связь" })).toBeVisible();
  await expect(operations.getByRole("heading", { name: "Записи на примерку" })).toBeVisible();
  await expect(operations.getByText("Product #41", { exact: true })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Каталог и merchandising" })).toHaveCount(0);
  await expect(page.getByRole("heading", { name: "Scheduled pricing" })).toHaveCount(0);
  await expect(operations.getByRole("heading", { name: "Отзывы и рейтинги" })).toHaveCount(0);
  await operations.getByRole("button", { name: "Подтвердить визит" }).click();
  await expect(operations.getByRole("status")).toContainText("confirmed");
  expect(state.appointment().status).toBe("confirmed");
});

test("catalog manager moderates feedback without customer identity data", async ({ page }) => {
  const state = await installOperatorMocks(page, "feedback");
  await login(page, "catalog@flashin.test");
  const operations = page.locator("section.catalog-support-operations");
  await expect(operations.getByRole("heading", { name: "Отзывы и рейтинги" })).toBeVisible();
  await expect(operations.getByText("Нужна проверка отзыва", { exact: true })).toBeVisible();
  await expect(operations.getByText(/Customer #/)).toHaveCount(0);
  await operations.getByRole("button", { name: "Скрыть отзыв" }).click();
  await expect(operations.getByRole("status")).toContainText("hidden");
  expect(state.feedback().status).toBe("hidden");
});

test("catalog manager schedules and explicitly clears merchandising promo pricing", async ({ page }) => {
  const state = await installOperatorMocks(page, "pricing");
  await login(page, "catalog@flashin.test");
  const pricingPanel = page.locator("section.catalog-pricing-panel");
  await expect(pricingPanel.getByRole("heading", { name: "Scheduled pricing" })).toBeVisible();
  await expect(pricingPanel.getByText("FLASH-PRICE-041", { exact: true })).toBeVisible();
  await expect(pricingPanel.getByText("Regular: 32000", { exact: true })).toBeVisible();

  await pricingPanel.getByLabel("Promo price").fill("28000");
  await pricingPanel.getByLabel("Start UTC").fill("2026-08-20T10:00");
  await pricingPanel.getByLabel("End UTC").fill("2026-08-21T10:00");
  await pricingPanel.getByRole("button", { name: "Сохранить pricing" }).click();
  await expect(pricingPanel.getByRole("status")).toContainText("pricing сохранён");
  expect(state.lastPricingPatch()).toEqual({ promo_price: 28000, sale_starts_at: "2026-08-20T10:00:00.000Z", sale_ends_at: "2026-08-21T10:00:00.000Z" });

  await pricingPanel.getByLabel("Promo price").fill("");
  await pricingPanel.getByLabel("Start UTC").fill("");
  await pricingPanel.getByLabel("End UTC").fill("");
  await pricingPanel.getByRole("button", { name: "Сохранить pricing" }).click();
  await expect(pricingPanel.getByRole("status")).toContainText("pricing сохранён");
  expect(state.lastPricingPatch()).toEqual({ promo_price: null, sale_starts_at: null, sale_ends_at: null });
  expect(state.pricing().configured_promo_price).toBeNull();
});
