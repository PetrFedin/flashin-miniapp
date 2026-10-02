import { expect, test } from "@playwright/test";

const product = {
  id: 101,
  sku: "RESP-101",
  slug: "responsive-jacket",
  title: "Responsive Jacket",
  brand: "FLASHIN",
  category: "Outerwear",
  description: "Responsive layout verification product",
  price: 15900,
  currency: "RUB",
  images: [{ url: "/fallback-product.svg" }],
  variants: [
    { id: 1001, size: "M", sku: "RESP-101-M", available_qty: 3, color: "Black" },
  ],
};

const capabilities = {
  catalog: { enabled: true },
  cart: { enabled: true },
  commercial_checkout: { enabled: false },
  payments: { enabled: false, mode: "disabled", provider: null },
  preorder: { enabled: true },
  made_to_order: { enabled: true },
  showroom: { enabled: true },
  moysklad: { enabled: false, mode: "disabled" },
  search: { enabled: true, mode: "database" },
  media: { enabled: true, mode: "local" },
  controlled_commerce_pilot: { enabled: false, max_orders: null },
};

async function installTelegram(page) {
  await page.addInitScript(() => {
    const listeners = new Map();
    const button = {
      setText() {}, show() {}, hide() {}, enable() {}, disable() {},
      onClick(handler) { listeners.set("main", handler); },
      offClick() { listeners.delete("main"); },
    };
    window.Telegram = {
      WebApp: {
        initData: "query_id=responsive&user=%7B%22id%22%3A202%2C%22first_name%22%3A%22Responsive%22%7D&hash=test",
        initDataUnsafe: { user: { id: 202, first_name: "Responsive" } },
        themeParams: {},
        MainButton: button,
        BackButton: { show() {}, hide() {}, onClick() {}, offClick() {} },
        HapticFeedback: { notificationOccurred() {} },
        ready() {}, expand() {}, onEvent() {}, offEvent() {},
      },
    };
  });
}

async function mockApi(page) {
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

    if (path === "/api/auth/telegram" && method === "POST") return json({ access_token: "responsive-token" });
    if (path === "/api/platform/capabilities" && method === "GET") return json(capabilities);
    if (path === "/api/products" && method === "GET") return json([product]);
    if (path === "/api/products/101" && method === "GET") return json(product);
    if (path === "/api/cart" && method === "GET") {
      return json({ id: 1, items: [], total_amount: 0, discount_amount: 0, loyalty_discount: 0, final_amount: 0 });
    }
    if (path === "/api/looks" && method === "GET") return json([]);
    if (path === "/api/wishlist" && method === "GET") return json([]);
    if (path === "/api/analytics/events" && method === "POST") return json({ accepted: true });

    return json({ detail: `Unmocked ${method} ${path}` }, 501);
  });
}

async function expectNoHorizontalOverflow(page) {
  const geometry = await page.evaluate(() => ({
    documentWidth: document.documentElement.scrollWidth,
    viewportWidth: window.innerWidth,
    bodyWidth: document.body.scrollWidth,
  }));
  expect(geometry.documentWidth).toBeLessThanOrEqual(geometry.viewportWidth + 1);
  expect(geometry.bodyWidth).toBeLessThanOrEqual(geometry.viewportWidth + 1);
}

async function expectPrimaryControlsReadable(page) {
  const controls = page.locator("nav button");
  await expect(controls.first()).toBeVisible();
  const count = await controls.count();
  expect(count).toBeGreaterThanOrEqual(4);
  for (let index = 0; index < Math.min(count, 5); index += 1) {
    const box = await controls.nth(index).boundingBox();
    expect(box).not.toBeNull();
    expect(box.width).toBeGreaterThanOrEqual(44);
    expect(box.height).toBeGreaterThanOrEqual(36);
  }
}

test("storefront remains readable without horizontal overflow across responsive viewports", async ({ page }) => {
  await installTelegram(page);
  await mockApi(page);
  await page.goto("/");

  await expect(page.getByRole("heading", { name: "Каталог" })).toBeVisible();
  await expect(page.getByText("Responsive Jacket")).toBeVisible();
  await expectNoHorizontalOverflow(page);
  await expectPrimaryControlsReadable(page);

  await page.getByText("Responsive Jacket").click();
  await expect(page.getByRole("heading", { name: "Responsive Jacket" })).toBeVisible();
  await expect(page.getByText("Outerwear")).toBeVisible();
  await expectNoHorizontalOverflow(page);

  const productCard = page.locator(".product-card").first();
  const box = await productCard.boundingBox();
  expect(box).not.toBeNull();
  expect(box.width).toBeGreaterThan(140);
  expect(box.width).toBeLessThanOrEqual(660);
});
