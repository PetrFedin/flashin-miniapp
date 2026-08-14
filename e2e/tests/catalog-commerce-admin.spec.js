import { expect, test } from "@playwright/test";

function richAdminProduct() {
  return {
    id: 41,
    sku: "FLASH-CASH-041",
    slug: "cashmere-jacket",
    title: "Cashmere Pilot Jacket",
    brand: "FLASHIN",
    description: "Cashmere pilot merchandising card",
    price: 32000,
    old_price: 40000,
    currency: "RUB",
    category: "Outerwear",
    gender: "unisex",
    active: true,
    is_drop: false,
    is_rare: false,
    moysklad_id: "ms-product-41",
    images: [{ id: 1, url: "https://cdn.flashin.test/item-41.webp", sort_order: 0 }],
    videos: [{ id: 2, url: "https://cdn.flashin.test/item-41.mp4", title: "Runway", sort_order: 0 }],
    variants: [{ id: 4101, size: "M", color: "Black", sku: "FLASH-CASH-041-M", moysklad_id: "ms-var-4101", stock_qty: 2, reserved_qty: 0, available_qty: 2 }],
    merchandising: {
      availability_status: "in_stock",
      configured_availability_status: "preorder",
      material: "Cashmere",
      season: "FW26",
      badges: ["bestseller", "exclusive"],
      grid_rank: 10,
      showroom_fitting_enabled: true,
      local_available_qty: 2,
      external_available: true,
    },
    external_availability: [{ id: 3, source_name: "Partner Boutique", url: "https://partner.example/item-41", availability_status: "in_stock", price: 33000, currency: "RUB", sort_order: 0 }],
    rating: { average: 4.8, count: 12 },
    recommendation_ids: [42],
    share: { telegram_share_url: "https://t.me/share/url?url=https%3A%2F%2Fmini.flashin.store%3Fproduct%3D41" },
  };
}

async function mockAdminCatalog(page) {
  let product = richAdminProduct();
  let appointment = {
    id: 91,
    customer_id: 101,
    product_id: 41,
    starts_at: "2026-08-20T12:00:00",
    duration_minutes: 30,
    status: "requested",
    notes: "Cashmere fitting",
  };
  let savedPayload = null;

  await page.route("http://localhost:8000/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname;
    const method = request.method();
    const json = (body, status = 200) => route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });

    if (path === "/api/admin/login" && method === "POST") return json({ access_token: "admin-catalog-token" });
    if (path === "/api/admin/session" && method === "GET") {
      return json({
        id: 1,
        email: "catalog@flashin.test",
        role: "catalog_manager",
        all_access: false,
        permissions: ["products.read", "products.write", "inventory.write", "media.write", "showroom.read", "showroom.write"],
      });
    }
    if (path === "/api/admin/products" && method === "GET") return json([]);
    if (path === "/api/ops/inventory/low-stock" && method === "GET") return json([]);
    if (path === "/api/catalog/admin/products" && method === "GET") return json([product]);
    if (path === "/api/catalog/admin/showroom/appointments" && method === "GET") return json([appointment]);
    if (path === "/api/catalog/admin/products/41" && method === "PUT") {
      savedPayload = request.postDataJSON();
      product = {
        ...product,
        ...savedPayload,
        merchandising: {
          ...product.merchandising,
          availability_status: savedPayload.availability_status,
          configured_availability_status: savedPayload.availability_status,
          material: savedPayload.material,
          season: savedPayload.season,
          badges: savedPayload.badges,
          grid_rank: savedPayload.grid_rank,
          showroom_fitting_enabled: savedPayload.showroom_fitting_enabled,
        },
        images: savedPayload.images.map((urlValue, index) => ({ id: index + 1, url: urlValue, sort_order: index })),
        videos: savedPayload.videos.map((item, index) => ({ id: index + 10, ...item })),
        variants: savedPayload.variants.map((item) => ({ ...item, reserved_qty: 0, available_qty: item.stock_qty })),
        external_availability: savedPayload.external_links.map((item, index) => ({ id: index + 20, ...item })),
        recommendation_ids: product.recommendation_ids,
      };
      return json(product);
    }
    if (path === "/api/catalog/admin/products/41/recommendations" && method === "PUT") {
      const body = request.postDataJSON();
      product.recommendation_ids = body.product_ids;
      return json({ ok: true, product_id: 41, recommendation_ids: body.product_ids });
    }
    if (path === "/api/catalog/admin/showroom/appointments/91" && method === "PATCH") {
      appointment = { ...appointment, status: request.postDataJSON().status };
      return json({ ok: true, id: 91, status: appointment.status });
    }
    if (path === "/api/moysklad-deep-mapping/status" && method === "GET") return json({ matches: [], conflicts: [] });
    if (path.startsWith("/api/moysklad") && method === "GET") return json([]);
    if (path.startsWith("/api/catalog") && method === "GET") return json([]);
    if (path.startsWith("/api/ops") && method === "GET") return json([]);
    if (method === "GET") return json([]);

    return json({ detail: `Unmocked ${method} ${path}` }, 501);
  });

  return {
    getSavedPayload: () => savedPayload,
    getAppointment: () => appointment,
  };
}

async function login(page) {
  await page.goto("/");
  await page.getByPlaceholder("Email администратора").fill("catalog@flashin.test");
  await page.getByPlaceholder("Пароль").fill("pilot-password");
  await page.getByRole("button", { name: "Войти" }).click();
  await expect(page.getByRole("button", { name: "Выйти" })).toBeVisible();
}

test("Admin fully edits merchandising card and showroom request", async ({ page }) => {
  const state = await mockAdminCatalog(page);
  await login(page);

  const catalogPanel = page.locator("section.catalog-commerce");
  await expect(catalogPanel.getByRole("heading", { name: "Каталог и merchandising" })).toBeVisible();
  await catalogPanel.getByRole("button", { name: /#41 · Cashmere Pilot Jacket/ }).click();

  await catalogPanel.getByLabel("Название карточки", { exact: true }).fill("Cashmere Pilot Jacket Updated");
  await catalogPanel.getByLabel("Материал карточки", { exact: true }).fill("Cashmere / Silk");
  await catalogPanel.getByLabel("Сезон карточки", { exact: true }).fill("FW26/27");
  await catalogPanel.getByLabel("Цена карточки", { exact: true }).fill("31500");
  await catalogPanel.getByLabel("Старая цена карточки", { exact: true }).fill("40000");
  await catalogPanel.getByLabel("Позиция карточки в сетке", { exact: true }).fill("3");
  await catalogPanel.getByLabel("Статус доступности карточки", { exact: true }).selectOption("preorder");
  await catalogPanel.getByText("Новый сезон", { exact: true }).click();

  await catalogPanel.getByRole("button", { name: "Добавить видео" }).click();
  await catalogPanel.getByLabel("Видео URL 2", { exact: true }).fill("https://cdn.flashin.test/detail-41.mp4");
  await catalogPanel.getByRole("button", { name: "Добавить внешний ресурс" }).click();
  const externalRows = catalogPanel.locator(".form-grid").filter({ has: catalogPanel.getByPlaceholder("Ресурс / магазин") });
  await externalRows.last().getByPlaceholder("Ресурс / магазин").fill("Marketplace Partner");
  await externalRows.last().getByPlaceholder("https://...").fill("https://market.example/41");

  await catalogPanel.getByLabel("ID связанных карточек", { exact: true }).fill("42, 43");
  await catalogPanel.getByRole("button", { name: "Сохранить карточку" }).click();
  await expect(catalogPanel.getByRole("status")).toContainText("Карточка #41 сохранена");

  const saved = state.getSavedPayload();
  expect(saved.title).toBe("Cashmere Pilot Jacket Updated");
  expect(saved.material).toBe("Cashmere / Silk");
  expect(saved.season).toBe("FW26/27");
  expect(saved.availability_status).toBe("preorder");
  expect(saved.grid_rank).toBe(3);
  expect(saved.videos).toHaveLength(2);
  expect(saved.external_links).toHaveLength(2);

  await expect(catalogPanel.getByRole("heading", { name: "Записи на примерку" })).toBeVisible();
  await catalogPanel.getByRole("button", { name: "Подтвердить" }).click();
  await expect(catalogPanel.getByRole("status")).toContainText("confirmed");
  expect(state.getAppointment().status).toBe("confirmed");
});
