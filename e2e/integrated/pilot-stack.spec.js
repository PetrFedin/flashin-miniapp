import crypto from "node:crypto";
import { devices, expect, test } from "@playwright/test";

const BOT_TOKEN = process.env.TELEGRAM_BOT_TOKEN || "test-token";
const ADMIN_EMAIL = process.env.ADMIN_EMAIL || "admin@test.local";
const ADMIN_PASSWORD = process.env.ADMIN_PASSWORD || "test-password";
const API_BASE = "http://127.0.0.1:8000";

function signedTelegramInitData(user) {
  const values = {
    auth_date: String(Math.floor(Date.now() / 1000)),
    query_id: "AAE2E_FLASHIN",
    user: JSON.stringify(user),
  };
  const dataCheckString = Object.entries(values)
    .sort(([left], [right]) => left.localeCompare(right))
    .map(([key, value]) => `${key}=${value}`)
    .join("\n");
  const secretKey = crypto.createHmac("sha256", "WebAppData").update(BOT_TOKEN).digest();
  values.hash = crypto.createHmac("sha256", secretKey).update(dataCheckString).digest("hex");
  return new URLSearchParams(values).toString();
}

async function installTelegram(page) {
  const user = { id: 20260807, first_name: "E2E Pilot", username: "flashin_e2e" };
  const initData = signedTelegramInitData(user);
  await page.addInitScript(({ initDataValue, telegramUser }) => {
    const mainHandlers = new Set();
    const backHandlers = new Set();
    window.Telegram = {
      WebApp: {
        initData: initDataValue,
        initDataUnsafe: { user: telegramUser },
        themeParams: {},
        MainButton: {
          setText() {}, show() {}, hide() {}, enable() {}, disable() {},
          onClick(handler) { mainHandlers.add(handler); },
          offClick(handler) { mainHandlers.delete(handler); },
        },
        BackButton: {
          show() {}, hide() {},
          onClick(handler) { backHandlers.add(handler); },
          offClick(handler) { backHandlers.delete(handler); },
        },
        HapticFeedback: { notificationOccurred() {} },
        ready() {}, expand() {}, onEvent() {}, offEvent() {},
      },
    };
  }, { initDataValue: initData, telegramUser: user });
}

async function loginAdmin(page) {
  await page.goto("http://127.0.0.1:5174/");
  await page.getByPlaceholder("Email администратора").fill(ADMIN_EMAIL);
  await page.getByPlaceholder("Пароль").fill(ADMIN_PASSWORD);
  await page.getByRole("button", { name: "Войти" }).click();
  await expect(page.getByRole("button", { name: "Выйти" })).toBeVisible();
}

async function orderState(request, orderId) {
  const response = await request.get(`${API_BASE}/__e2e/state/orders/${orderId}`);
  expect(response.ok()).toBeTruthy();
  return response.json();
}

test("Telegram -> YooKassa webhook -> stock/MoySklad -> fulfillment -> refund -> notification", async ({ page, browser }) => {
  await installTelegram(page);
  await page.goto("/");

  await expect(page.getByRole("heading", { name: "Каталог" })).toBeVisible();
  await expect(page.getByText("FLASHIN Wool Coat")).toBeVisible();
  await page.getByText("FLASHIN Wool Coat").first().click();
  await expect(page.getByRole("heading", { name: "FLASHIN Wool Coat" })).toBeVisible();

  await page.getByRole("button", { name: "Добавить размер S в корзину" }).click();
  await expect(page.getByText("добавлен в корзину")).toBeVisible();
  await page.getByRole("button", { name: /Корзина · 1/ }).click();

  await page.getByPlaceholder("Промокод").fill("FLASH10");
  await page.getByRole("button", { name: "Применить" }).first().click();
  await expect(page.getByText("Промокод применён.")).toBeVisible();

  await page.getByRole("button", { name: "Оформить заказ" }).click();
  await page.getByPlaceholder("Имя получателя").fill("E2E Pilot");
  await page.getByPlaceholder("+7 999 000-00-00").fill("+79990000001");

  const checkoutResponsePromise = page.waitForResponse((response) =>
    response.url().endsWith("/api/orders/checkout")
      && response.request().method() === "POST"
      && response.ok(),
  );
  const paymentResponsePromise = page.waitForResponse((response) =>
    response.url().endsWith("/api/payments")
      && response.request().method() === "POST"
      && response.ok(),
  );
  await page.getByRole("button", { name: "Создать заказ и перейти к оплате" }).click();

  const checkoutResponse = await checkoutResponsePromise;
  const paymentResponse = await paymentResponsePromise;
  const order = await checkoutResponse.json();
  expect(order.id).toBeGreaterThan(0);
  expect(paymentResponse.ok()).toBeTruthy();

  // The payment response triggers an immediate provider navigation, so its body is
  // deliberately not consumed after the redirect. Persisted state below proves
  // that the pending provider attempt passed through confirmation and webhook.
  await expect(page.getByRole("button", { name: "Заказы" })).toHaveClass(/active/);
  await expect(page.getByRole("status")).toContainText(`Заказ #${order.id} оплачен`);
  await expect(page.getByText("Оплачено", { exact: true })).toBeVisible();

  let state = await orderState(page.request, order.id);
  expect(state.order.status).toBe("paid");
  expect(state.order.payment_status).toBe("paid");
  expect(state.variants).toHaveLength(1);
  expect(state.variants[0].sku).toBe("FLASHIN-COAT-001-S");
  expect(state.variants[0].stock_qty).toBe(1);
  expect(state.variants[0].reserved_qty).toBe(0);
  expect(state.inventory_movements.map((item) => item.kind)).toEqual(["reserve", "commit"]);
  expect(state.fulfillment?.status).toBe("new");
  expect(state.provider_commands.map((item) => item.command_type)).toContain("moysklad.customer_order.create");
  expect(state.notifications.filter((item) => item.message.includes("оплачен"))).toHaveLength(1);

  const adminContext = await browser.newContext({ ...devices["Desktop Chrome"] });
  const adminPage = await adminContext.newPage();
  await loginAdmin(adminPage);

  await expect(adminPage.getByRole("heading", { name: "Fulfillment & Delivery" })).toBeVisible();
  const task = adminPage.getByRole("article", { name: `Заказ #${order.id}` });
  await expect(task.getByText("Новая задача")).toBeVisible();

  await task.getByRole("button", { name: "Начать сборку" }).click();
  await expect(adminPage.getByRole("status")).toContainText(`Сборка заказа #${order.id} начата`);

  await task.getByRole("button", { name: "Собрать все позиции и упаковать" }).click();
  await expect(adminPage.getByRole("status")).toContainText("полностью собран и упакован");

  await task.getByRole("button", { name: "Подтвердить готовность" }).click();
  await expect(adminPage.getByRole("status")).toContainText("готов к передаче в доставку");

  await task.getByRole("button", { name: "Создать отгрузку" }).click();
  await expect(adminPage.getByRole("status")).toContainText(`Отгрузка заказа #${order.id} создана`);

  const tracking = `E2E-${order.id}`;
  await task.getByLabel(`Трек-номер заказа ${order.id}`).fill(tracking);
  await task.getByRole("button", { name: "Передать в доставку" }).click();
  await expect(adminPage.getByRole("status")).toContainText("передан в доставку");
  await expect(task.getByText(tracking)).toBeVisible();

  state = await orderState(adminPage.request, order.id);
  expect(state.order.status).toBe("shipped");
  expect(state.provider_commands.map((item) => item.command_type)).toContain("moysklad.demand.create");

  adminPage.once("dialog", (dialog) => dialog.accept());
  await task.getByRole("button", { name: "Подтвердить доставку" }).click();
  await expect(adminPage.getByRole("status")).toContainText("доставлен и завершён");
  await expect(task.getByText("Цикл завершён")).toBeVisible();

  await page.reload();
  await page.getByRole("button", { name: "Заказы" }).click();
  await expect(page.getByText("Доставлен", { exact: true })).toBeVisible();
  await expect(page.getByText("Доставлена", { exact: true })).toBeVisible();
  await expect(page.getByText(tracking)).toBeVisible();

  // The customer registers the return from the real Mini App against the same row.
  await page.getByPlaceholder("Что необходимо вернуть и почему").fill("Не подошёл размер изделия, полный возврат E2E");
  await page.getByRole("button", { name: "Зарегистрировать возврат" }).click();
  await expect(page.getByRole("status")).toContainText(`Запрос на возврат заказа #${order.id} зарегистрирован`);
  await expect(page.getByText("Возврат рассматривается", { exact: true })).toBeVisible();

  state = await orderState(page.request, order.id);
  expect(state.returns).toHaveLength(1);
  expect(state.returns[0].status).toBe("requested");
  const returnId = state.returns[0].id;

  // Admin approves the full amount. The local provider intentionally returns
  // pending first, so no stock restoration can occur before refund.succeeded.
  await adminPage.getByRole("button", { name: "Обновить сервис" }).click();
  const returnsQueue = adminPage.getByRole("article", { name: "Возвраты и refunds" });
  await expect(returnsQueue.getByText(`#${returnId} · Заказ #${order.id}`)).toBeVisible();
  await adminPage.getByLabel(`Сумма возврата ${returnId}`).fill(String(state.order.total_amount));
  adminPage.once("dialog", (dialog) => dialog.accept());
  await returnsQueue.getByRole("button", { name: "Подтвердить refund" }).click();
  await expect(adminPage.getByRole("status")).toContainText(`Возврат #${returnId} передан платёжному провайдеру`);

  state = await orderState(adminPage.request, order.id);
  expect(state.order.status).toBe("refund_requested");
  expect(state.order.payment_status).toBe("refund_pending");
  expect(state.returns[0].status).toBe("refund_pending");
  expect(state.variants[0].stock_qty).toBe(1);
  const refundId = state.returns[0].provider_refund_id;
  expect(refundId).toContain("e2e-refund-");

  // Provider success is delivered twice to the canonical webhook. The second
  // callback must be idempotent: inventory, notification and provider commands
  // may not be duplicated.
  const refundConfirmation = await adminPage.request.post(
    `${API_BASE}/__e2e/yookassa/confirm-refund/${refundId}`,
  );
  expect(refundConfirmation.ok()).toBeTruthy();

  state = await orderState(adminPage.request, order.id);
  expect(state.order.status).toBe("refunded");
  expect(state.order.payment_status).toBe("refunded");
  expect(state.order.delivery_status).toBe("delivered");
  expect(state.returns[0].status).toBe("approved");
  expect(state.variants[0].stock_qty).toBe(2);
  expect(state.variants[0].reserved_qty).toBe(0);

  const movementKinds = state.inventory_movements.map((item) => item.kind);
  expect(movementKinds).toEqual(["reserve", "commit", "return"]);
  expect(movementKinds.filter((kind) => kind === "return")).toHaveLength(1);

  const commandTypes = state.provider_commands.map((item) => item.command_type);
  expect(commandTypes).toContain("moysklad.customer_order.create");
  expect(commandTypes).toContain("moysklad.demand.create");
  expect(commandTypes).toContain("moysklad.sales_return.create");
  expect(commandTypes.filter((type) => type === "moysklad.sales_return.create")).toHaveLength(1);

  const refundNotifications = state.notifications.filter((item) => item.message.includes("полностью возвращена"));
  expect(refundNotifications).toHaveLength(1);
  expect(refundNotifications[0].status).toBe("pending");

  await page.reload();
  await page.getByRole("button", { name: "Заказы" }).click();
  await expect(page.getByText("Возвращён", { exact: true })).toBeVisible();
  await expect(page.getByText("Возвращено", { exact: true })).toBeVisible();

  await adminPage.getByRole("button", { name: "Обновить сервис" }).click();
  await expect(returnsQueue.getByText("Возвращён полностью", { exact: true })).toBeVisible();

  await adminContext.close();
});
