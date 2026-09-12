import { expect, test } from "@playwright/test";

const product = {
  id: 41,
  sku: "DELIVERY-041",
  slug: "delivery-authority-jacket",
  title: "Delivery Authority Jacket",
  brand: "FLASHIN",
  category: "Outerwear",
  description: "Delivery authority browser fixture",
  price: 10000,
  currency: "RUB",
  images: [{ url: "/fallback-product.svg" }],
  variants: [
    { id: 411, size: "M", sku: "DELIVERY-041-M", available_qty: 3, color: "Black" },
  ],
};

function emptyCart() {
  return {
    id: 9041,
    items: [],
    total_amount: 0,
    discount_amount: 0,
    loyalty_discount: 0,
    final_amount: 0,
  };
}

function cartWithItem() {
  return {
    id: 9041,
    items: [{
      id: 9401,
      product_id: product.id,
      variant_id: 411,
      title: product.title,
      size: "M",
      quantity: 1,
      available_qty: 3,
      price: product.price,
    }],
    total_amount: product.price,
    discount_amount: 0,
    loyalty_discount: 0,
    final_amount: product.price,
  };
}

async function installTelegram(page) {
  await page.addInitScript(() => {
    const listeners = new Map();
    const mainButton = {
      setText() {}, show() {}, hide() {}, enable() {}, disable() {},
      onClick(handler) { listeners.set("main", handler); },
      offClick() { listeners.delete("main"); },
    };
    window.Telegram = {
      WebApp: {
        initData: "query_id=delivery-test&user=%7B%22id%22%3A4041%2C%22first_name%22%3A%22Delivery%22%7D&hash=test",
        initDataUnsafe: { user: { id: 4041, first_name: "Delivery" } },
        themeParams: {},
        MainButton: mainButton,
        BackButton: { show() {}, hide() {}, onClick() {}, offClick() {} },
        HapticFeedback: { notificationOccurred() {} },
        ready() {}, expand() {}, onEvent() {}, offEvent() {},
      },
    };
  });
}

async function installApi(page) {
  let cart = emptyCart();
  let quoteNumber = 0;
  let checkoutNumber = 0;
  const checkoutPayloads = [];

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

    if (path === "/api/auth/telegram" && method === "POST") return json({ access_token: "delivery-test-token" });
    if (path === "/api/products" && method === "GET") return json([product]);
    if (path === `/api/products/${product.id}` && method === "GET") return json(product);
    if (path === "/api/looks" && method === "GET") return json([]);
    if (path === "/api/wishlist" && method === "GET") return json([]);
    if (path === "/api/analytics/events" && method === "POST") return json({ accepted: true });

    if (path === "/api/cart" && method === "GET") return json(cart);
    if (path === "/api/cart/items" && method === "POST") {
      cart = cartWithItem();
      return json(cart);
    }

    if (path === "/api/delivery-quotes" && method === "POST") {
      quoteNumber += 1;
      const body = request.postDataJSON();
      const address = body.address;
      return json({
        public_id: `dq-browser-${quoteNumber}`,
        delivery_type: "courier",
        address_snapshot: `${address.city}, ${address.address_line}`,
        zone_id: 71,
        provider_code: "manual-courier",
        service_code: "manual-courier",
        price: quoteNumber === 1 ? "500.00" : "550.00",
        currency: "RUB",
        quote_version: 1,
        zone_version: quoteNumber,
        status: "created",
        expires_at: "2030-09-12T22:30:00Z",
      });
    }

    if (path === "/api/orders/checkout" && method === "POST") {
      checkoutNumber += 1;
      const body = request.postDataJSON();
      checkoutPayloads.push(body);
      if (checkoutNumber === 1) {
        return json({
          detail: {
            code: "quote_stale",
            message: "Delivery tariff changed; request a new quote",
          },
        }, 409);
      }
      const order = {
        id: 9901,
        status: "created",
        payment_status: "pending",
        delivery_status: "not_started",
        delivery_type: "courier",
        address: body.address,
        delivery_price: 550,
        total_amount: 10550,
        currency: "RUB",
        items: cart.items,
      };
      cart = emptyCart();
      return json(order);
    }

    if (path === "/api/payments" && method === "POST") {
      return json({ id: 901, order_id: 9901, status: "pending", confirmation_url: null });
    }
    if (path === "/api/orders" && method === "GET") {
      return json([{
        id: 9901,
        status: "created",
        payment_status: "pending",
        delivery_status: "not_started",
        delivery_type: "courier",
        address: checkoutPayloads.at(-1)?.address || "",
        delivery_price: 550,
        total_amount: 10550,
        currency: "RUB",
        items: cartWithItem().items,
      }]);
    }

    return json({ detail: `Unmocked ${method} ${path}` }, 501);
  });

  return {
    checkoutPayloads,
    quoteCount: () => quoteNumber,
    checkoutCount: () => checkoutNumber,
  };
}

test("courier checkout requires a fresh authoritative quote and explicit requote", async ({ page }) => {
  await installTelegram(page);
  const api = await installApi(page);
  await page.goto("/");

  await page.getByText(product.title).click();
  await page.getByRole("button", { name: "Добавить размер M в корзину" }).click();
  await page.getByRole("button", { name: /Корзина · 1/ }).click();
  await page.getByRole("button", { name: "Оформить заказ" }).click();

  await page.getByPlaceholder("Имя получателя").fill("Delivery User");
  await page.getByPlaceholder("+7 999 000-00-00").fill("+79990000000");
  await page.getByLabel("Способ получения").selectOption("courier");
  await page.getByPlaceholder("Москва").fill("Москва");
  await page.getByPlaceholder("Тверская улица, дом 1, квартира 10").fill("Тверская улица, 1");

  await page.getByRole("button", { name: "Рассчитать доставку" }).click();
  await expect(page.getByText("Подтверждено", { exact: true })).toBeVisible();
  await expect(page.getByText(/500/).last()).toBeVisible();
  await expect(page.getByRole("button", { name: "Создать заказ и перейти к оплате" })).toBeEnabled();

  await page.getByPlaceholder("Тверская улица, дом 1, квартира 10").fill("Тверская улица, 2");
  await expect(page.getByText("Нужно обновить", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Сначала рассчитайте доставку" })).toBeDisabled();

  await page.getByRole("button", { name: "Рассчитать доставку" }).click();
  await expect(page.getByText(/550/).last()).toBeVisible();
  await page.getByRole("button", { name: "Создать заказ и перейти к оплате" }).click();

  await expect(page.getByRole("alert")).toContainText("Условия доставки изменились");
  await expect(page.getByText("Нужно обновить", { exact: true })).toBeVisible();
  expect(api.checkoutCount()).toBe(1);
  expect(api.checkoutPayloads[0].delivery_quote_id).toBe("dq-browser-2");
  expect(api.checkoutPayloads[0].address).toBe("Москва, Тверская улица, 2");

  await page.getByRole("button", { name: "Рассчитать доставку" }).click();
  await page.getByRole("button", { name: "Создать заказ и перейти к оплате" }).click();

  await expect(page.getByRole("alert")).toContainText("Заказ #9901 создан");
  expect(api.checkoutCount()).toBe(2);
  expect(api.quoteCount()).toBe(3);
  expect(api.checkoutPayloads[1].delivery_quote_id).toBe("dq-browser-3");
  expect(api.checkoutPayloads[1].address).toBe("Москва, Тверская улица, 2");
});
