import { expect, test } from "@playwright/test";

const richProduct = {
  id: 41,
  sku: "FLASH-CASH-041",
  slug: "cashmere-jacket",
  title: "Cashmere Pilot Jacket",
  brand: "FLASHIN",
  category: "Outerwear",
  description: "Cashmere pilot merchandising card",
  price: 32000,
  old_price: 40000,
  currency: "RUB",
  active: true,
  images: [{ id: 1, url: "/fallback-product.svg", sort_order: 0 }],
  videos: [{ id: 2, url: "https://cdn.flashin.test/cashmere.mp4", title: "Runway", sort_order: 0 }],
  variants: [
    { id: 4101, size: "M", color: "Black", sku: "FLASH-CASH-041-M", stock_qty: 2, reserved_qty: 0, available_qty: 2 },
    { id: 4102, size: "L", color: "Black", sku: "FLASH-CASH-041-L", stock_qty: 0, reserved_qty: 0, available_qty: 0 },
  ],
  merchandising: {
    availability_status: "in_stock",
    configured_availability_status: "preorder",
    material: "Cashmere",
    season: "FW26",
    badges: ["bestseller", "exclusive", "new_season"],
    grid_rank: 10,
    showroom_fitting_enabled: true,
    local_available_qty: 2,
    external_available: true,
    can_add_to_cart: true,
  },
  external_availability: [{
    id: 12,
    source_name: "Partner Boutique",
    url: "https://partner.example/item-41",
    availability_status: "in_stock",
    price: 33000,
    currency: "RUB",
    sort_order: 0,
  }],
  rating: { average: 4.8, count: 12 },
  recommendation_ids: [42],
  recommendations: [{
    id: 42,
    title: "Cashmere Pilot Trousers",
    brand: "FLASHIN",
    category: "Trousers",
    price: 24000,
    currency: "RUB",
    images: [{ url: "/fallback-product.svg" }],
    merchandising: { availability_status: "preorder", badges: ["new_season"], grid_rank: 20 },
    rating: { average: 4.5, count: 3 },
  }],
  // Deliberately stale legacy payload. Catalog+ must replace this with /share.
  share: {
    mini_app_url: "https://mini.flashin.store?product=41",
    telegram_share_url: "https://t.me/share/url?url=https%3A%2F%2Fmini.flashin.store%3Fproduct%3D41",
  },
};

const canonicalShare = {
  web_url: "https://mini.flashin.store?product=41",
  mini_app_deep_link: "https://t.me/FlashinPilotBot?startapp=product_41",
  telegram_share_url: "https://t.me/share/url?url=https%3A%2F%2Ft.me%2FFlashinPilotBot%3Fstartapp%3Dproduct_41&text=Cashmere%20Pilot%20Jacket",
};

const pricingByProduct = {
  41: {
    product_id: 41,
    regular_price: 32000,
    effective_price: 28000,
    compare_at_price: 32000,
    promo_price: 28000,
    promo_active: true,
    sale_ends_at: "2026-08-20T00:00:00",
  },
  42: {
    product_id: 42,
    regular_price: 24000,
    effective_price: 22000,
    compare_at_price: 24000,
    promo_price: 22000,
    promo_active: true,
    sale_ends_at: "2026-08-20T00:00:00",
  },
};

async function installTelegram(page) {
  await page.addInitScript(() => {
    window.Telegram = {
      WebApp: {
        initData: "query_id=catalog&user=%7B%22id%22%3A101%2C%22first_name%22%3A%22Pilot%22%7D&hash=test",
        initDataUnsafe: { user: { id: 101, first_name: "Pilot" } },
        themeParams: {},
        MainButton: { setText() {}, show() {}, hide() {}, enable() {}, disable() {}, onClick() {}, offClick() {} },
        BackButton: { show() {}, hide() {}, onClick() {}, offClick() {} },
        HapticFeedback: { notificationOccurred() {} },
        ready() {}, expand() {}, onEvent() {}, offEvent() {},
      },
    };
  });
}

async function mockCatalogCommerce(page) {
  let wishlist = [];
  let cart = { id: 77, items: [], total_amount: 0, discount_amount: 0, loyalty_discount: 0, final_amount: 0 };
  let appointments = [];
  let feedback = [{ id: 501, rating: 5, comment: "Great fit", created_at: "2026-08-10T10:00:00" }];
  let lastCatalogQuery = "";

  await page.route("http://localhost:8000/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname;
    const method = request.method();
    const json = (body, status = 200) => route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });

    if (path === "/api/auth/telegram" && method === "POST") return json({ access_token: "catalog-token" });
    if (path === "/api/products" && method === "GET") return json([richProduct]);
    if (path === "/api/looks" && method === "GET") return json([]);
    if (path === "/api/cart" && method === "GET") return json(cart);
    if (path === "/api/wishlist" && method === "GET") return json(wishlist);
    if (path === "/api/wishlist" && method === "POST") {
      wishlist = [richProduct];
      return json(richProduct);
    }
    if (path === "/api/wishlist/41" && method === "DELETE") {
      wishlist = [];
      return json({ ok: true });
    }
    if (path === "/api/cart/items" && method === "POST") {
      const effectivePrice = pricingByProduct[41].effective_price;
      cart = {
        ...cart,
        items: [{ id: 900, product_id: 41, variant_id: 4101, title: richProduct.title, size: "M", quantity: 1, available_qty: 2, price: effectivePrice }],
        total_amount: effectivePrice,
        final_amount: effectivePrice,
      };
      return json(cart);
    }
    if (path === "/api/catalog/products" && method === "GET") {
      lastCatalogQuery = url.search;
      return json([richProduct]);
    }
    if (path === "/api/catalog/products/41" && method === "GET") return json(richProduct);
    if (path === "/api/catalog/pricing" && method === "GET") {
      const ids = url.searchParams.getAll("product_id").map(Number);
      return json(ids.map((id) => pricingByProduct[id]).filter(Boolean));
    }
    if (path === "/api/catalog/products/41/share" && method === "GET") return json(canonicalShare);
    if (path === "/api/catalog/products/41/feedback" && method === "GET") return json(feedback);
    if (path === "/api/catalog/products/41/feedback" && method === "POST") {
      const body = request.postDataJSON();
      feedback = [{ id: 502, rating: body.rating, comment: body.comment, created_at: "2026-08-14T12:00:00" }];
      richProduct.rating = { average: body.rating, count: 1 };
      return json({ id: 502, rating: body.rating, comment: body.comment, status: "published" });
    }
    if (path === "/api/catalog/showroom/appointments/me" && method === "GET") return json(appointments);
    if (path === "/api/catalog/showroom/appointments" && method === "POST") {
      const body = request.postDataJSON();
      appointments = [{ id: 701, product_id: 41, starts_at: body.starts_at, duration_minutes: 30, status: "requested", notes: body.notes }];
      return json(appointments[0]);
    }
    if (path === "/api/analytics/events" && method === "POST") return json({ accepted: true });
    if (path === "/__catalog_query" && method === "GET") return json({ query: lastCatalogQuery });

    return json({ detail: `Unmocked ${method} ${path}` }, 501);
  });
}

test("Catalog+ customer merchandising, scheduled price, canonical Telegram share, cart, feedback and showroom journey", async ({ page }) => {
  await installTelegram(page);
  await mockCatalogCommerce(page);
  await page.goto("/");

  await page.getByRole("button", { name: "Открыть каталог с фильтрами" }).click();
  const catalog = page.getByRole("dialog", { name: "Расширенный каталог FLASHIN" });
  await expect(catalog.getByRole("heading", { name: "Расширенный каталог" })).toBeVisible();

  await catalog.getByPlaceholder("Материал").fill("Cashmere");
  await catalog.getByPlaceholder("Сезон").fill("FW26");
  await catalog.locator('select:has(option[value="price_desc"])').selectOption("price_desc");
  await catalog.getByRole("button", { name: "Применить фильтры" }).click();
  await expect(catalog.getByText("Cashmere Pilot Jacket", { exact: true })).toBeVisible();
  await expect(catalog.getByText(/28[\s\u00A0]?000/).first()).toBeVisible();
  await expect(catalog.locator(".catalog-plus-badge").filter({ hasText: "Бестселлер" })).toBeVisible();
  await expect(catalog.locator(".catalog-plus-badge").filter({ hasText: "Эксклюзив" })).toBeVisible();

  await catalog.getByRole("button").filter({ hasText: "Cashmere Pilot Jacket" }).click();
  await expect(catalog.getByRole("heading", { name: "Cashmere Pilot Jacket" })).toBeVisible();
  await expect(catalog.getByText(/28[\s\u00A0]?000/).first()).toBeVisible();
  await expect(catalog.getByText(/32[\s\u00A0]?000/).first()).toBeVisible();
  await expect(catalog.getByText("Cashmere · FW26", { exact: true })).toBeVisible();
  await expect(catalog.getByText(/Partner Boutique/)).toBeVisible();
  await expect(catalog.locator("video")).toHaveCount(1);
  await expect(catalog.getByRole("heading", { name: "Complete the look" })).toBeVisible();
  await expect(catalog.getByText("Cashmere Pilot Trousers", { exact: true })).toBeVisible();
  await expect(catalog.getByText(/22[\s\u00A0]?000/).first()).toBeVisible();
  await expect(catalog.getByRole("link", { name: "Открыть в Telegram" })).toHaveAttribute("href", canonicalShare.mini_app_deep_link);
  await expect(catalog.getByRole("link", { name: "Поделиться в Telegram" })).toHaveAttribute("href", canonicalShare.telegram_share_url);

  await catalog.getByRole("button", { name: "В избранное" }).click();
  await expect(catalog.getByRole("status")).toContainText("Добавлено в избранное");
  await catalog.getByRole("button", { name: "Добавить в корзину" }).click();
  await expect(catalog.getByRole("status")).toContainText("добавлен в корзину");
  await expect(catalog.getByText("Корзина · 1", { exact: true })).toBeVisible();

  await catalog.getByLabel("Оценка товара").selectOption("4");
  await catalog.getByPlaceholder("Ваш комментарий").fill("Очень хорошая посадка");
  await catalog.getByRole("button", { name: "Сохранить оценку" }).click();
  await expect(catalog.getByText("Очень хорошая посадка", { exact: true })).toBeVisible();
  await expect(catalog.getByText(/28[\s\u00A0]?000/).first()).toBeVisible();
  await expect(catalog.getByRole("link", { name: "Открыть в Telegram" })).toHaveAttribute("href", canonicalShare.mini_app_deep_link);

  await catalog.getByLabel("Дата и время примерки").fill("2026-08-20T12:00");
  await catalog.getByPlaceholder("Комментарий к визиту").fill("Примерить с брюками");
  await catalog.getByRole("button", { name: "Записаться на примерку" }).click();
  await expect(catalog.getByRole("status")).toContainText("Запрос на примерку отправлен");
});
