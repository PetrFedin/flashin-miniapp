import { expect, test } from "@playwright/test";

async function installTelegram(page) {
  await page.addInitScript(() => {
    window.Telegram = {
      WebApp: {
        initData: "query_id=share&user=%7B%22id%22%3A101%2C%22first_name%22%3A%22Pilot%22%7D&hash=test",
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

async function mockSharedProduct(page) {
  const product = {
    id: 41,
    title: "Shared Cashmere Jacket",
    brand: "FLASHIN",
    category: "Outerwear",
    description: "Shared directly into the Mini App",
    price: 32000,
    currency: "RUB",
    images: [{ url: "/fallback-product.svg" }],
    variants: [{ id: 4101, size: "M", color: "Black", available_qty: 2 }],
    merchandising: { material: "Cashmere", season: "FW26", badges: ["exclusive"] },
    external_availability: [{ id: 1, source_name: "Partner Boutique", url: "https://partner.example/41" }],
  };
  const pricing = {
    product_id: 41,
    regular_price: 32000,
    effective_price: 32000,
    compare_at_price: null,
    promo_price: null,
    promo_active: false,
    sale_ends_at: null,
  };
  let cartAdded = false;
  let wishlistAdded = false;

  await page.route("http://localhost:8000/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname;
    const method = request.method();
    const json = (body, status = 200) => route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });

    if (path === "/api/auth/telegram" && method === "POST") return json({ access_token: "share-token" });
    if (path === "/api/products" && method === "GET") return json([product]);
    if (path === "/api/looks" && method === "GET") return json([]);
    if (path === "/api/cart" && method === "GET") return json({ id: 77, items: [], total_amount: 0, final_amount: 0 });
    if (path === "/api/wishlist" && method === "GET") return json([]);
    if (path === "/api/catalog/products/41" && method === "GET") return json(product);
    if (path === "/api/catalog/pricing" && method === "GET") return json([pricing]);
    if (path === "/api/cart/items" && method === "POST") {
      cartAdded = true;
      return json({ id: 77, items: [{ id: 1, product_id: 41, variant_id: 4101, quantity: 1 }], total_amount: 32000, final_amount: 32000 });
    }
    if (path === "/api/wishlist" && method === "POST") {
      wishlistAdded = true;
      return json(product);
    }
    if (path === "/api/analytics/events" && method === "POST") return json({ accepted: true });
    return json({ detail: `Unmocked ${method} ${path}` }, 501);
  });

  return {
    cartAdded: () => cartAdded,
    wishlistAdded: () => wishlistAdded,
  };
}

test("Telegram shared card opens the exact Mini App product and retains commerce actions", async ({ page }) => {
  await installTelegram(page);
  const state = await mockSharedProduct(page);
  await page.goto("/?product=41");

  await expect(page.getByRole("dialog", { name: "Отправленная карточка товара" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Shared Cashmere Jacket" })).toBeVisible();
  await expect(page.getByText("Cashmere · FW26")).toBeVisible();
  await expect(page.getByText("Partner Boutique")).toBeVisible();

  await page.getByRole("button", { name: "В избранное" }).click();
  await expect(page.getByRole("status")).toContainText("добавлена в избранное");
  await page.getByRole("button", { name: "Добавить в корзину" }).click();
  await expect(page.getByRole("status")).toContainText("Товар добавлен в корзину");

  expect(state.wishlistAdded()).toBe(true);
  expect(state.cartAdded()).toBe(true);
});