import { expect, test } from "@playwright/test";

const product = {
  id: 81,
  sku: "PREORDER-081",
  slug: "preorder-coat",
  title: "Preorder Pilot Coat",
  brand: "FLASHIN",
  category: "Outerwear",
  description: "Demand-only pilot product",
  price: 48000,
  currency: "RUB",
  active: true,
  images: [{ url: "/fallback-product.svg" }],
  variants: [{ id: 8101, size: "M", color: "Black", sku: "PREORDER-081-M", stock_qty: 0, reserved_qty: 0, available_qty: 0 }],
  merchandising: {
    availability_status: "preorder",
    configured_availability_status: "preorder",
    local_available_qty: 0,
    badges: ["new_season"],
  },
};

async function installTelegram(page) {
  await page.addInitScript(() => {
    window.Telegram = {
      WebApp: {
        initData: "query_id=demand&user=%7B%22id%22%3A501%7D&hash=test",
        initDataUnsafe: { user: { id: 501, first_name: "Demand" } },
        themeParams: {},
        MainButton: { setText() {}, show() {}, hide() {}, enable() {}, disable() {}, onClick() {}, offClick() {} },
        BackButton: { show() {}, hide() {}, onClick() {}, offClick() {} },
        HapticFeedback: { notificationOccurred() {} },
        ready() {}, expand() {}, onEvent() {}, offEvent() {},
      },
    };
  });
}

test("zero-stock preorder creates demand request and never mutates cart", async ({ page }) => {
  await installTelegram(page);
  let cartMutations = 0;
  let demandBody = null;
  let demandRows = [];

  await page.route("http://localhost:8000/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname;
    const method = request.method();
    const json = (body, status = 200) => route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });

    if (path === "/api/auth/telegram" && method === "POST") return json({ access_token: "demand-token" });
    if (path === "/api/catalog/products" && method === "GET") {
      return json(url.searchParams.get("availability_status") === "preorder" ? [product] : []);
    }
    if (path === "/api/catalog/demand-requests/me" && method === "GET") return json(demandRows);
    if (path === "/api/catalog/demand-requests" && method === "POST") {
      demandBody = request.postDataJSON();
      const row = {
        id: 901,
        ...demandBody,
        product_title: product.title,
        product_sku: product.sku,
        status: "requested",
        created_at: "2026-08-14T23:30:00Z",
        updated_at: "2026-08-14T23:30:00Z",
      };
      demandRows = [row];
      return json(row);
    }
    if (path === "/api/cart/items" && method !== "GET") {
      cartMutations += 1;
      return json({ detail: "Demand journey must not use cart" }, 500);
    }

    if (path === "/api/products") return json([]);
    if (path === "/api/looks") return json([]);
    if (path === "/api/cart") return json({ id: 1, items: [], total_amount: 0, discount_amount: 0, loyalty_discount: 0, final_amount: 0 });
    if (path === "/api/wishlist") return json([]);
    if (path === "/api/analytics/events" && method === "POST") return json({ accepted: true });
    return json([]);
  });

  await page.goto("/");
  await page.getByRole("button", { name: "Предзаказ / под заказ" }).click();
  const dialog = page.getByRole("dialog", { name: "Предзаказ и товары под заказ" });
  await expect(dialog.getByText(product.title, { exact: true })).toBeVisible();
  await dialog.getByLabel("Количество для заявки").fill("2");
  await dialog.getByPlaceholder("Например: нужна примерка, уточнить срок и цвет").fill("Уточнить срок поставки");
  await dialog.getByRole("button", { name: "Оставить заявку на предзаказ" }).click();

  await expect(dialog.getByRole("status")).toContainText("Оплата и склад не затронуты");
  expect(demandBody).toMatchObject({
    product_id: 81,
    variant_id: 8101,
    request_type: "preorder",
    quantity: 2,
    requested_size: "M",
    requested_color: "Black",
  });
  expect(cartMutations).toBe(0);
  await expect(dialog.getByText("Заявка получена", { exact: true })).toBeVisible();
});
