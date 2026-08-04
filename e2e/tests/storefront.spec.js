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
    total_amount: 0,
    discount_amount: 0,
    loyalty_discount: 0,
    final_amount: 0,
  };
}

function cartWithItem(quantity = 1) {
  const total = product.price * quantity;
  return {
    id: 77,
    items: [{
      id: 501,
      product_id: 1,
      variant_id: 11,
      title: product.title,
      size: "M",
      quantity,
      available_qty: 5,
      price: product.price,
    }],
    total_amount: total,
    discount_amount: 0,
    loyalty_discount: 0,
    final_amount: total,
  };
}

function paidOrder(id = 9002) {
  return {
    id,
    status: "completed",
    payment_status: "paid",
    delivery_status: "delivered",
    delivery_type: "courier",
    address: "Berlin, Pilotstrasse 1",
    total_amount: 12000,
    currency: "RUB",
    items: [{
      id: 701,
      title: product.title,
      size: "M",
      quantity: 1,
      price: product.price,
    }],
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

async function mockApi(page, options = {}) {
  let cart = emptyCart();
  let wishlist = [...(options.initialWishlist || [])];
  let orders = [...(options.initialOrders || [])];
  let supportTickets = [];
  let privacyRequests = [];

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
    if (path === "/api/cart/items/501" && method === "PATCH") {
      const quantity = Number(url.searchParams.get("quantity"));
      cart = cartWithItem(quantity);
      return json(cart);
    }
    if (path === "/api/cart/items/501" && method === "DELETE") {
      cart = emptyCart();
      return json(cart);
    }
    if (path === "/api/cart/promo" && method === "POST") {
      cart = { ...cart, discount_amount: 1200, final_amount: cart.total_amount - 1200, promo_code: "PILOT10" };
      return json(cart);
    }
    if (path === "/api/cart/loyalty" && method === "POST") {
      cart = { ...cart, loyalty_discount: 500, final_amount: cart.final_amount - 500 };
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
    if (path === "/api/restock/subscribe" && method === "POST") return json({ subscribed: true });
    if (path === "/api/recommendations/size-helper/1" && method === "POST") {
      return json({ suggested_size: "M", note: "Pilot recommendation" });
    }

    if (path === "/api/orders/checkout" && method === "POST") {
      const order = {
        id: 9001,
        status: "created",
        payment_status: "pending",
        delivery_status: "not_started",
        delivery_type: "pickup",
        total_amount: cart.final_amount,
        currency: "RUB",
        items: cart.items,
      };
      orders = [order, ...orders];
      cart = emptyCart();
      return json(order);
    }
    if (path === "/api/payments" && method === "POST") {
      return json({ id: 1, order_id: 9001, status: "pending", confirmation_url: null });
    }
    if (path === "/api/orders" && method === "GET") return json(orders);
    if (/^\/api\/orders\/\d+$/.test(path) && method === "GET") {
      const orderId = Number(path.split("/").pop());
      const order = orders.find((item) => item.id === orderId);
      return order ? json(order) : json({ detail: "Order not found" }, 404);
    }
    if (/^\/api\/orders\/\d+\/cancel$/.test(path) && method === "POST") {
      const orderId = Number(path.split("/")[3]);
      orders = orders.map((order) => order.id === orderId
        ? { ...order, status: "cancelled", payment_status: "cancelled", delivery_status: "cancelled" }
        : order);
      return json(orders.find((order) => order.id === orderId));
    }
    if (path === "/api/returns" && method === "POST") {
      const body = request.postDataJSON();
      orders = orders.map((order) => order.id === body.order_id
        ? { ...order, status: "refund_requested" }
        : order);
      return json({ id: 801, order_id: body.order_id, reason: body.reason, status: "requested" }, 201);
    }

    if (path === "/api/profile" && method === "GET") {
      return json({
        customer: { id: 101, first_name: "Pilot", username: "pilot", phone: "+70000000000" },
        loyalty_points: 1250,
        referral_code: "PILOTREF",
      });
    }
    if (path === "/api/loyalty/transactions" && method === "GET") {
      return json([{ id: 1, reason: "Pilot purchase", points_delta: 250 }]);
    }
    if (path === "/api/loyalty/referral-code" && method === "GET") return json({ code: "PILOTREF" });
    if (path === "/api/timeline" && method === "GET") {
      return json([{ id: 1, title: "Заказ передан в доставку", event_type: "order.shipped" }]);
    }
    if (path === "/api/support/tickets" && method === "GET") return json(supportTickets);
    if (path === "/api/support/tickets" && method === "POST") {
      const body = request.postDataJSON();
      const ticket = { id: 601, status: "new", ...body };
      supportTickets = [ticket, ...supportTickets];
      return json(ticket, 201);
    }
    if (path === "/api/privacy/export" && method === "GET") {
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        headers: { "content-disposition": 'attachment; filename="flashin-pilot-export.json"' },
        body: JSON.stringify({ customer_id: 101, exported: true }),
      });
    }
    if (path === "/api/privacy/requests" && method === "GET") return json(privacyRequests);
    if (path === "/api/privacy/requests" && method === "POST") {
      const body = request.postDataJSON();
      const privacyRequest = { id: privacyRequests.length + 1, request_type: body.request_type, status: "registered" };
      privacyRequests = [privacyRequest, ...privacyRequests];
      return json(privacyRequest, 201);
    }
    if (path === "/api/analytics/events" && method === "POST") return json({ accepted: true });

    return json({ detail: `Unmocked ${method} ${path}` }, 501);
  });
}

async function openProduct(page) {
  await page.getByText("Pilot Jacket").click();
  await expect(page.getByRole("heading", { name: "Pilot Jacket" })).toBeVisible();
}

test("Mini App critical pilot journey", async ({ page }) => {
  await installTelegram(page);
  await mockApi(page);
  await page.goto("/");

  await expect(page.getByText("Pilot, ваш личный магазин")).toBeVisible();
  await expect(page.getByRole("heading", { name: "Каталог" })).toBeVisible();

  await page.getByRole("button", { name: "Образы" }).click();
  await expect(page.getByRole("heading", { name: "Активных образов нет" })).toBeVisible();
  await page.getByRole("button", { name: "Каталог", exact: true }).click();

  await page.getByLabel("Поиск товаров").fill("Pilot");
  await page.getByRole("button", { name: "Найти" }).click();
  await expect(page.getByText("Pilot Jacket")).toBeVisible();
  await openProduct(page);

  await page.locator(".sizes button").filter({ hasText: "L" }).click();
  await page.getByRole("button", { name: "Сообщить о поступлении размера L" }).click();
  await expect(page.getByText("Уведомление для размера L подключено.")).toBeVisible();
  await page.locator(".sizes button").filter({ hasText: "M" }).click();

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

  await page.getByPlaceholder("Баллы к списанию").fill("500");
  await page.getByRole("button", { name: "Списать" }).click();
  await expect(page.getByText("Баллы зарезервированы.")).toBeVisible();

  await page.getByPlaceholder("Реферальный код").fill("PILOTREF");
  await page.getByRole("button", { name: "Добавить" }).click();
  await expect(page.getByText("Реферальный код связан с заказом.")).toBeVisible();

  await page.getByRole("button", { name: "Оформить заказ" }).click();
  await page.getByPlaceholder("Имя получателя").fill("Pilot User");
  await page.getByPlaceholder("+7 999 000-00-00").fill("+70000000000");
  await page.getByRole("button", { name: "Создать заказ и перейти к оплате" }).click();

  await expect(page.getByRole("alert")).toContainText("Заказ #9001 создан");
  await expect(page.getByRole("button", { name: "Заказы" })).toHaveClass(/active/);

  page.once("dialog", (dialog) => dialog.accept());
  await page.getByRole("button", { name: "Отменить заказ" }).click();
  await expect(page.getByRole("status")).toContainText("Заказ #9001 отменён");
  await expect(page.getByText("Отменён")).toBeVisible();
});

test("Mini App cart quantity and removal controls", async ({ page }) => {
  await installTelegram(page);
  await mockApi(page);
  await page.goto("/");

  await openProduct(page);
  await page.getByRole("button", { name: "Добавить размер M в корзину" }).click();
  await page.getByRole("button", { name: /Корзина · 1/ }).click();

  const quantityControl = page.locator(".quantity-control");
  await quantityControl.getByRole("button", { name: "+" }).click();
  await expect(quantityControl.locator("b")).toHaveText("2");
  await quantityControl.getByRole("button", { name: "−" }).click();
  await expect(quantityControl.locator("b")).toHaveText("1");

  await page.getByRole("button", { name: "Удалить" }).click();
  await expect(page.getByRole("heading", { name: "Корзина пуста" })).toBeVisible();
});

test("Mini App profile, support, privacy and return journey", async ({ page }) => {
  await installTelegram(page);
  await mockApi(page, {
    initialWishlist: [product],
    initialOrders: [paidOrder()],
  });
  await page.goto("/");

  await page.getByRole("button", { name: "Профиль" }).click();
  await expect(page.getByRole("heading", { name: "Профиль и сервис" })).toBeVisible();
  await expect(page.getByText("+70000000000")).toBeVisible();
  await expect(page.getByText("1250")).toBeVisible();
  await expect(page.getByText("PILOTREF")).toBeVisible();

  await page.getByRole("button", { name: "Удалить" }).click();
  await expect(page.getByRole("status")).toContainText("удалён из избранного");

  await page.getByRole("combobox").selectOption("9002");
  await page.getByPlaceholder("Тема обращения").fill("Возврат заказа");
  await page.getByPlaceholder("Опишите вопрос и ожидаемый результат").fill("Нужна проверка возврата пилотного заказа");
  await page.getByRole("button", { name: "Отправить обращение" }).click();
  await expect(page.getByRole("status")).toContainText("Обращение зарегистрировано");
  await expect(page.getByText("Нужна проверка возврата пилотного заказа")).toBeVisible();

  const downloadPromise = page.waitForEvent("download");
  await page.getByRole("button", { name: "Скачать мои данные" }).click();
  const download = await downloadPromise;
  expect(download.suggestedFilename()).toBe("flashin_customer_export.json");
  await expect(page.getByRole("status")).toContainText("Архив персональных данных сформирован");

  page.once("dialog", (dialog) => dialog.accept());
  await page.getByRole("button", { name: "Отозвать необязательные согласия" }).click();
  await expect(page.getByRole("status")).toContainText("Запрос на отзыв согласий зарегистрирован");
  await expect(page.getByText("consent_withdrawal")).toBeVisible();

  await page.getByRole("button", { name: "Заказы" }).click();
  await expect(page.getByRole("heading", { name: "Мои заказы" })).toBeVisible();
  await page.getByPlaceholder("Что необходимо вернуть и почему").fill("Не подошёл размер изделия");
  await page.getByRole("button", { name: "Зарегистрировать возврат" }).click();
  await expect(page.getByRole("status")).toContainText("Запрос на возврат заказа #9002 зарегистрирован");
  await expect(page.getByText("Возврат рассматривается")).toBeVisible();
});

test("Mini App payment return route refreshes paid order", async ({ page }) => {
  await installTelegram(page);
  await mockApi(page, { initialOrders: [paidOrder(9003)] });
  await page.goto("/payment-result?order_id=9003");

  await expect(page.getByRole("status")).toContainText("Заказ #9003 оплачен");
  await expect(page.getByRole("button", { name: "Заказы" })).toHaveClass(/active/);
  await expect(page.getByText("Оплачено")).toBeVisible();
});
