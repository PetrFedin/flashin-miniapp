import { expect, test } from "@playwright/test";

const product = {
  id: 1,
  sku: "FLASH-001",
  slug: "pilot-jacket",
  title: "Pilot Jacket",
  brand: "FLASHIN",
  category: "Outerwear",
  description: "Pilot browser journey product",
  price: 12000,
  currency: "RUB",
  images: [{ url: "/fallback-product.svg" }],
  variants: [
    { id: 11, size: "M", sku: "FLASH-001-M", available_qty: 5, color: "Black" },
    { id: 12, size: "L", sku: "FLASH-001-L", available_qty: 0, color: "Black" },
  ],
};

function emptyCart() {
  return {
    id: 77,
    items: [],
    subtotal: 0,
    discount_total: 0,
    loyalty_discount: 0,
    total: 0,
  };
}

function cartWithItem() {
  return {
    id: 77,
    items: [{
      id: 501,
      product_id: 1,
      variant_id: 11,
      title: product.title,
      size: "M",
      quantity: 1,
      available_qty: 5,
      price: product.price,
    }],
    subtotal: 12000,
    discount_total: 0,
    loyalty_discount: 0,
    total: 12000,
  };
}

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
        initData: "query_id=test&user=%7B%22id%22%3A101%2C%22first_name%22%3A%22Pilot%22%7D&hash=test",
        initDataUnsafe: { user: { id: 101, first_name: "Pilot" } },
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
  let cart = emptyCart();
  let wishlist = [];
  let orders = [];

  await page.route("http://localhost:8000/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname;
    const method = request.method();
    const json = (body, status = 200, headers = {}) => route.fulfill({
      status,
      contentType: "application/json",
      headers,
      body: JSON.stringify(body),
    });

    if (path === "/api/auth/telegram" && method === "POST") return json({ access_token: "pilot-token" });
    if (path === "/api/products" && method === "GET") return json([product]);
    if (path === "/api/products/1" && method === "GET") return json(product);
    if (path === "/api/search/products" && method === "GET") return json([product]);
    if (path === "/api/looks" && method === "GET") return json([]);
    if (path === "/api/cart" && method === "GET") return json(cart);
    if (path === "/api/cart/items" && method === "POST") {
      cart = cartWithItem();
      return json(cart);
    }
    if (path === "/api/cart/promo" && method === "POST") {
      cart = { ...cart, discount_total: 1200, total: 10800, promo_code: "PILOT10" };
      return json(cart);
    }
    if (path === "/api/cart/loyalty" && method === "POST") {
      cart = { ...cart, loyalty_discount: 500, total: 10300 };
      return json(cart);
    }
    if (path === "/api/cart/referral" && method === "POST") {
      cart = { ...cart, referral_code: "PILOTREF" };
      return json(cart);
    }
    if (path === "/api/wishlist" && method === "GET") return json(wishlist);
    if (path === "/api/wishlist" && method === "POST") {
      wishlist = [product];
      return json(product);
    }
    if (path === "/api/wishlist/1" && method === "DELETE") {
      wishlist = [];
      return json({ ok: true });
    }
    if (path === "/api/recommendations/size-helper/1" && method === "POST") {
      return json({ suggested_size: "M", note: "Pilot recommendation" });
    }
    if (path === "/api/orders/checkout" && method === "POST") {
      const order = {
        id: 9001,
        status: "created",
        payment_status: "pending",
        total: cart.total,
        currency: "RUB",
        items: cart.items,
      };
      orders = [order];
      cart = emptyCart();
      return json(order);
    }
    if (path === "/api/payments" && method === "POST") {
      return json({ id: 1, order_id: 9001, status: "pending", confirmation_url: null });
    }
    if (path === "/api/orders" && method === "GET") return json(orders);
    if (path === "/api/profile" && method === "GET") return json({ id: 101, first_name: "Pilot", phone: "+70000000000" });
    if (path === "/api/loyalty/transactions" && method === "GET") return json([]);
    if (path === "/api/loyalty/referral-code" && method === "GET") return json({ code: "PILOTREF" });
    if (path === "/api/timeline" && method === "GET") return json([]);
    if (path === "/api/support/tickets" && method === "GET") return json([]);
    if (path === "/api/privacy/requests" && method === "GET") return json([]);
    if (path === "/api/analytics/events" && method === "POST") return json({ accepted: true });

    return json({ detail: `Unmocked ${method} ${path}` }, 501);
  });
}

test("Mini App critical pilot journey", async ({ page }) => {
  await installTelegram(page);
  await mockApi(page);
  await page.goto("/");

  await expect(page.getByText("Pilot, ваш личный магазин")).toBeVisible();
  await expect(page.getByRole("heading", { name: "Каталог" })).toBeVisible();
  await expect(page.getByText("Pilot Jacket")).toBeVisible();

  await page.getByText("Pilot Jacket").click();
  await expect(page.getByRole("heading", { name: "Pilot Jacket" })).toBeVisible();

  await page.getByPlaceholder("Рост, см").fill("180");
  await page.getByPlaceholder("Вес, кг").fill("75");
  await page.getByPlaceholder("Обычный размер").fill("M");
  await page.getByRole("button", { name: "Получить рекомендацию" }).click();
  await expect(page.getByText("Pilot recommendation")).toBeVisible();

  await page.getByRole("button", { name: "Сохранить в избранное" }).click();
  await expect(page.getByText("сохранён в избранном")).toBeVisible();

  await page.getByRole("button", { name: "Добавить размер M в корзину" }).click();
  await expect(page.getByText("добавлен в корзину")).toBeVisible();
  await page.getByRole("button", { name: /Корзина · 1/ }).click();

  await page.getByPlaceholder("Промокод").fill("PILOT10");
  await page.getByRole("button", { name: "Применить" }).first().click();
  await expect(page.getByText("Промокод применён.")).toBeVisible();

  const numericInputs = page.locator('input[inputmode="numeric"]');
  await numericInputs.last().fill("500");
  await page.getByRole("button", { name: /Списать|Применить/ }).last().click();
  await expect(page.getByText("Баллы зарезервированы.")).toBeVisible();

  await page.getByPlaceholder(/Реферальный код/i).fill("PILOTREF");
  await page.getByRole("button", { name: "Добавить" }).click();
  await expect(page.getByText("Реферальный код связан с заказом.")).toBeVisible();

  await page.getByRole("button", { name: /Оформить заказ/ }).click();
  await page.getByPlaceholder(/Имя получателя/i).fill("Pilot User");
  await page.getByPlaceholder(/Телефон/i).fill("+70000000000");
  await page.getByRole("button", { name: /Перейти к оплате/ }).click();

  await expect(page.getByRole("alert")).toContainText("Заказ #9001 создан");
  await expect(page.getByRole("button", { name: "Заказы" })).toHaveClass(/active/);
});
