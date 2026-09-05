import { expect, test } from "@playwright/test";

async function installTelegram(page) {
  await page.addInitScript(() => {
    window.Telegram = {
      WebApp: {
        initData: "query_id=startapp&user=%7B%22id%22%3A101%2C%22first_name%22%3A%22Pilot%22%7D&hash=test",
        initDataUnsafe: {
          user: { id: 101, first_name: "Pilot" },
          start_param: "product_41",
        },
        themeParams: {},
        MainButton: { setText() {}, show() {}, hide() {}, enable() {}, disable() {}, onClick() {}, offClick() {} },
        BackButton: { show() {}, hide() {}, onClick() {}, offClick() {} },
        HapticFeedback: { notificationOccurred() {} },
        ready() {}, expand() {}, onEvent() {}, offEvent() {},
      },
    };
  });
}

async function mockApi(page) {
  const product = {
    id: 41,
    title: "Startapp Cashmere Jacket",
    brand: "FLASHIN",
    category: "Outerwear",
    description: "Telegram Main Mini App start parameter product",
    price: 32000,
    currency: "RUB",
    images: [{ url: "/fallback-product.svg" }],
    variants: [{ id: 4101, size: "M", color: "Black", available_qty: 2 }],
    merchandising: { material: "Cashmere", season: "FW26", badges: ["exclusive"] },
    external_availability: [],
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

  await page.route("http://localhost:8000/**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    const method = request.method();
    const json = (body, status = 200) => route.fulfill({
      status,
      contentType: "application/json",
      body: JSON.stringify(body),
    });
    if (path === "/api/auth/telegram" && method === "POST") return json({ access_token: "startapp-token" });
    if (path === "/api/catalog/products/41" && method === "GET") return json(product);
    if (path === "/api/catalog/pricing" && method === "GET") return json([pricing]);
    if (path === "/api/products" && method === "GET") return json([product]);
    if (path === "/api/looks" && method === "GET") return json([]);
    if (path === "/api/cart" && method === "GET") return json({ id: 1, items: [], total_amount: 0, final_amount: 0 });
    if (path === "/api/wishlist" && method === "GET") return json([]);
    if (path === "/api/analytics/events" && method === "POST") return json({ accepted: true });
    return json({ detail: `Unmocked ${method} ${path}` }, 501);
  });
}

test("Telegram startapp product parameter opens the exact shared product", async ({ page }) => {
  await installTelegram(page);
  await mockApi(page);
  await page.goto("/");

  const shared = page.getByRole("dialog", { name: "Отправленная карточка товара" });
  await expect(shared).toBeVisible();
  await expect(shared.getByRole("heading", { name: "Startapp Cashmere Jacket" })).toBeVisible();
  await expect(shared.getByText("Cashmere · FW26", { exact: true })).toBeVisible();
});