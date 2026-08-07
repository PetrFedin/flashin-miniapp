import crypto from "node:crypto";
import { devices, expect, test } from "@playwright/test";

const BOT_TOKEN = process.env.TELEGRAM_BOT_TOKEN || "test-token";
const ADMIN_EMAIL = process.env.ADMIN_EMAIL || "admin@test.local";
const ADMIN_PASSWORD = process.env.ADMIN_PASSWORD || "test-password";

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

test("real storefront, API, PostgreSQL and admin fulfillment share one order", async ({ page, browser }) => {
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
  const payment = await paymentResponse.json();
  expect(order.id).toBeGreaterThan(0);
  expect(payment.order_id).toBe(order.id);
  expect(payment.status).toBe("succeeded");

  await expect(page.getByRole("button", { name: "Заказы" })).toHaveClass(/active/);
  await expect(page.getByRole("status")).toContainText(`Заказ #${order.id} оплачен`);
  await expect(page.getByText("Оплачено", { exact: true })).toBeVisible();

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

  adminPage.once("dialog", (dialog) => dialog.accept());
  await task.getByRole("button", { name: "Подтвердить доставку" }).click();
  await expect(adminPage.getByRole("status")).toContainText("доставлен и завершён");
  await expect(task.getByText("Цикл завершён")).toBeVisible();

  await page.reload();
  await page.getByRole("button", { name: "Заказы" }).click();
  await expect(page.getByText("Доставлен", { exact: true })).toBeVisible();
  await expect(page.getByText("Доставлена", { exact: true })).toBeVisible();
  await expect(page.getByText(tracking)).toBeVisible();

  await adminContext.close();
});
