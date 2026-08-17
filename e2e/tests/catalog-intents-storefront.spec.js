import { expect, test } from "@playwright/test";

const intentProduct = {
  id: 51,
  title: "Zero Stock Preorder Coat",
  brand: "FLASHIN",
  price: 42000,
  currency: "RUB",
  intent_type: "preorder",
  image_url: "/fallback-product.svg",
  variants: [
    {
      id: 5101,
      size: "M",
      color: "Black",
      available_qty: 0,
      intent_eligible: true,
    },
  ],
};

async function installTelegram(page) {
  await page.addInitScript(() => {
    window.Telegram = {
      WebApp: {
        initData: "query_id=intent&user=%7B%22id%22%3A51001%2C%22first_name%22%3A%22Intent%22%7D&hash=test",
        initDataUnsafe: { user: { id: 51001, first_name: "Intent" } },
        themeParams: {},
        MainButton: { setText() {}, show() {}, hide() {}, enable() {}, disable() {}, onClick() {}, offClick() {} },
        BackButton: { show() {}, hide() {}, onClick() {}, offClick() {} },
        HapticFeedback: { notificationOccurred() {} },
        ready() {}, expand() {}, onEvent() {}, offEvent() {},
      },
    };
  });
}

async function installIntentMocks(page) {
  let requests = [];
  const commercialMutations = [];
  let createCalls = 0;

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

    if (method !== "GET" && ["/api/cart/items", "/api/orders", "/api/payments"].some((prefix) => path.startsWith(prefix))) {
      commercialMutations.push(`${method} ${path}`);
    }

    if (path === "/api/auth/telegram" && method === "POST") return json({ access_token: "intent-token" });
    if (path === "/api/products" && method === "GET") return json([]);
    if (path === "/api/looks" && method === "GET") return json([]);
    if (path === "/api/cart" && method === "GET") {
      return json({ id: 77, items: [], total_amount: 0, discount_amount: 0, loyalty_discount: 0, final_amount: 0 });
    }
    if (path === "/api/wishlist" && method === "GET") return json([]);
    if (path === "/api/catalog/intents/eligible-products" && method === "GET") return json([intentProduct]);
    if (path === "/api/catalog/intents/me" && method === "GET") return json(requests);
    if (path === "/api/catalog/intents" && method === "POST") {
      createCalls += 1;
      const body = request.postDataJSON();
      if (body.product_id !== intentProduct.id || body.variant_id !== intentProduct.variants[0].id) {
        return json({ detail: "Unexpected product or variant" }, 400);
      }
      requests = [{
        id: 801,
        product_id: intentProduct.id,
        product_title: intentProduct.title,
        variant_id: intentProduct.variants[0].id,
        variant_size: "M",
        variant_color: "Black",
        intent_type: "preorder",
        quantity: body.quantity,
        notes: body.notes,
        status: "requested",
        quote_amount: null,
        quote_currency: "RUB",
        estimated_ready_at: null,
        created_at: "2026-08-17T10:00:00Z",
        updated_at: "2026-08-17T10:00:00Z",
        payment_allowed: false,
        normal_checkout_available: false,
      }];
      return json(requests[0]);
    }
    if (path === "/api/analytics/events" && method === "POST") return json({ accepted: true });
    if (method === "GET") return json([]);
    return json({ detail: `Unmocked ${method} ${path}` }, 501);
  });

  return {
    requests: () => requests,
    commercialMutations: () => commercialMutations,
    createCalls: () => createCalls,
  };
}

test("customer creates zero-stock preorder intent without checkout or payment mutation", async ({ page }) => {
  await installTelegram(page);
  const state = await installIntentMocks(page);
  await page.goto("/");

  await expect.poll(() => page.evaluate(() => localStorage.getItem("flashin_token"))).toBe("intent-token");
  await page.getByRole("button", { name: "Предзаказ / под заказ" }).click();

  const dialog = page.getByRole("dialog", { name: "Предзаказ и индивидуальный заказ" });
  await expect(dialog).toBeVisible();
  await expect(dialog.getByText("Заявка фиксирует ваш интерес. Она не резервирует склад и не запускает оплату.")).toBeVisible();

  await dialog.getByLabel("Товар для предзаказа").selectOption(String(intentProduct.id));
  await dialog.getByLabel("Вариант для предзаказа").selectOption(String(intentProduct.variants[0].id));
  await dialog.getByLabel("Количество").fill("2");
  await dialog.getByLabel("Комментарий").fill("Нужен размер M к концу месяца");
  await dialog.getByRole("button", { name: "Отправить заявку без оплаты" }).click();

  await expect(dialog.getByRole("status")).toContainText("Заявка #801 создана");
  await expect(dialog.getByText("#801 · Zero Stock Preorder Coat", { exact: true })).toBeVisible();
  await expect(dialog.getByText("Заявка получена", { exact: true })).toBeVisible();
  await expect(dialog.getByText(/Оплата не списывается/)).toBeVisible();

  expect(state.createCalls()).toBe(1);
  expect(state.requests()).toHaveLength(1);
  expect(state.requests()[0].payment_allowed).toBe(false);
  expect(state.commercialMutations()).toEqual([]);
});
