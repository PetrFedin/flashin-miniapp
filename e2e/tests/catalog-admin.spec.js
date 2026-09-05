import { expect, test } from "@playwright/test";

const PRODUCT_SKU = "FLASH-001";
const VARIANT_SKU = "FLASH-001-M";

async function mockCatalogAdminApi(page) {
  let product = {
    id: 1,
    sku: PRODUCT_SKU,
    title: "Pilot Jacket",
    slug: "pilot-jacket",
    brand: "FLASHIN",
    description: "Pilot catalog browser acceptance product",
    price: 12000,
    currency: "RUB",
    category: "Outerwear",
    active: true,
    variants: [{
      id: 11,
      size: "M",
      color: "Black",
      sku: VARIANT_SKU,
      stock_qty: 5,
      reserved_qty: 1,
      available_qty: 4,
    }],
  };

  const supplyChain = {
    schema_version: 1,
    attention_required: false,
    summary: {
      last_sync_status: "success",
      last_sync_at: "2026-08-12T18:00:00",
      pending_matches: 0,
      open_reconciliations: 0,
      open_conflicts: 0,
    },
    sync_logs: [{
      id: 91,
      sync_type: "manual",
      status: "success",
      products_seen: 1,
      products_upserted: 0,
      variants_upserted: 0,
      has_error: false,
      created_at: "2026-08-12T17:59:00",
      finished_at: "2026-08-12T18:00:00",
    }],
    sku_matches: [],
    reconciliations: [],
    conflicts: [],
  };

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

    if (path === "/api/admin/login" && method === "POST") {
      return json({ access_token: "catalog-admin-token" });
    }
    if (path === "/api/admin/session" && method === "GET") {
      return json({
        id: 1,
        email: "catalog@flashin.test",
        role: "owner",
        all_access: true,
        permissions: [],
      });
    }
    if (path === "/api/admin/products" && method === "GET") return json([product]);
    if (path === "/api/moysklad/operations-status" && method === "GET") return json(supplyChain);
    if (path === "/api/admin/products/1" && method === "PATCH") {
      product = { ...product, ...request.postDataJSON() };
      return json(product);
    }
    if (path === "/api/admin/products/1/active" && method === "PATCH") {
      product = { ...product, active: url.searchParams.get("active") === "true" };
      return json(product);
    }
    if (path === "/api/admin/variants/11/stock" && method === "PATCH") {
      const stock = Number(url.searchParams.get("stock_qty"));
      product = {
        ...product,
        variants: product.variants.map((variant) => variant.id === 11
          ? { ...variant, stock_qty: stock, available_qty: stock - variant.reserved_qty }
          : variant),
      };
      return json({ ok: true, variant_id: 11, stock_qty: stock, reserved_qty: 1 });
    }

    // The real Admin application mounts operational panels together. This
    // focused browser contract keeps unrelated read-only datasets empty while
    // catalog mutations and the Supply Chain projection run through React/fetch.
    if (method === "GET") return json([]);
    return json({ ok: true });
  });
}

async function login(page) {
  await page.goto("/");
  await page.getByPlaceholder("Email администратора").fill("catalog@flashin.test");
  await page.getByPlaceholder("Пароль").fill("pilot-password");
  await page.getByRole("button", { name: "Войти" }).click();
  await expect(page.getByRole("button", { name: "Выйти" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Каталог и остатки" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Supply Chain · МойСклад" })).toBeVisible();
  await expect(page.getByLabel("Supply Chain summary")).toContainText("Успешно");
}

test("Admin edits product publication and inventory from the real catalog panel", async ({ page }) => {
  await mockCatalogAdminApi(page);
  await login(page);

  const card = page.getByRole("article", { name: "Pilot Jacket" }).first();
  await expect(card).toBeVisible();
  await expect(card.getByText(PRODUCT_SKU, { exact: true })).toBeVisible();

  await card.getByLabel(`Цена ${PRODUCT_SKU}`).fill("12500");
  await card.getByRole("button", { name: `Сохранить товар ${PRODUCT_SKU}` }).click();
  await expect(page.getByRole("status")).toContainText(`Товар ${PRODUCT_SKU} обновлён`);
  await expect(card.getByLabel(`Цена ${PRODUCT_SKU}`)).toHaveValue("12500");

  await card.getByLabel(`Остаток ${VARIANT_SKU}`).fill("7");
  page.once("dialog", (dialog) => dialog.accept());
  await card.getByRole("button", { name: `Обновить остаток ${VARIANT_SKU}` }).click();
  await expect(page.getByRole("status")).toContainText(`Остаток ${VARIANT_SKU} обновлён`);
  await expect(card.getByText(/stock 7 · reserved 1 · available 6/)).toBeVisible();

  page.once("dialog", (dialog) => dialog.accept());
  await card.getByRole("button", { name: `Скрыть товар ${PRODUCT_SKU}` }).click();
  await expect(page.getByRole("status")).toContainText(`Товар ${PRODUCT_SKU} скрыт из каталога`);
  await expect(card.getByRole("button", { name: `Вернуть товар ${PRODUCT_SKU}` })).toBeVisible();

  await card.getByRole("button", { name: `Вернуть товар ${PRODUCT_SKU}` }).click();
  await expect(page.getByRole("status")).toContainText(`Товар ${PRODUCT_SKU} снова опубликован`);
  await expect(card.getByRole("button", { name: `Скрыть товар ${PRODUCT_SKU}` })).toBeVisible();
});
