import crypto from "node:crypto";
import { devices, expect, test } from "@playwright/test";

const BOT_TOKEN = process.env.TELEGRAM_BOT_TOKEN || "test-token";
const ADMIN_EMAIL = process.env.ADMIN_EMAIL || "admin@test.local";
const ADMIN_PASSWORD = process.env.ADMIN_PASSWORD || "test-password";
const API_BASE = "http://127.0.0.1:8000";
const ADMIN_URL = "http://127.0.0.1:5174";
const SKU = "E2E-DEMAND-001";
const TITLE = "Integrated Preorder Demand Coat";

function signedTelegramInitData(user) {
  const values = {
    auth_date: String(Math.floor(Date.now() / 1000)),
    query_id: "AAE2E_DEMAND",
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
  const user = { id: 2026081501, first_name: "Demand E2E", username: "demand_e2e" };
  const initData = signedTelegramInitData(user);
  await page.addInitScript(({ initDataValue, telegramUser }) => {
    window.Telegram = {
      WebApp: {
        initData: initDataValue,
        initDataUnsafe: { user: telegramUser },
        themeParams: {},
        MainButton: { setText() {}, show() {}, hide() {}, enable() {}, disable() {}, onClick() {}, offClick() {} },
        BackButton: { show() {}, hide() {}, onClick() {}, offClick() {} },
        HapticFeedback: { notificationOccurred() {} },
        ready() {}, expand() {}, onEvent() {}, offEvent() {},
      },
    };
  }, { initDataValue: initData, telegramUser: user });
}

async function loginAdmin(page) {
  await page.goto(ADMIN_URL);
  await page.getByPlaceholder("Email администратора").fill(ADMIN_EMAIL);
  await page.getByPlaceholder("Пароль").fill(ADMIN_PASSWORD);
  await page.getByRole("button", { name: "Войти" }).click();
  await expect(page.getByRole("button", { name: "Выйти" })).toBeVisible();
}

test("preorder demand crosses Admin, PostgreSQL and Mini App without order, payment or reservation", async ({ page, browser }) => {
  await installTelegram(page);

  const adminContext = await browser.newContext({ ...devices["Desktop Chrome"] });
  const adminPage = await adminContext.newPage();
  await loginAdmin(adminPage);
  const catalog = adminPage.locator("section.catalog-commerce");
  await expect(catalog).toBeVisible();

  await catalog.getByRole("button", { name: "Новая карточка" }).click();
  await catalog.getByLabel("SKU карточки", { exact: true }).fill(SKU);
  await catalog.getByLabel("Название карточки", { exact: true }).fill(TITLE);
  await catalog.getByLabel("Slug карточки", { exact: true }).fill("integrated-preorder-demand-coat");
  await catalog.getByLabel("Категория карточки", { exact: true }).fill("Outerwear");
  await catalog.getByLabel("Цена карточки", { exact: true }).fill("52000");
  await catalog.getByLabel("Статус доступности карточки", { exact: true }).selectOption("preorder");
  await catalog.getByPlaceholder("Размер", { exact: true }).fill("M");
  await catalog.getByPlaceholder("Цвет", { exact: true }).fill("Black");
  await catalog.getByPlaceholder("SKU варианта", { exact: true }).fill(`${SKU}-M`);
  await catalog.getByPlaceholder("Stock", { exact: true }).fill("0");
  await catalog.getByRole("button", { name: "Создать карточку" }).click();
  await expect(catalog.getByRole("status")).toContainText("Карточка #");

  const productResponse = await page.request.get(`${API_BASE}/api/catalog/products?q=${encodeURIComponent(SKU)}`);
  expect(productResponse.ok()).toBeTruthy();
  const products = await productResponse.json();
  const product = products.find((item) => item.sku === SKU);
  expect(product).toBeTruthy();
  expect(product.merchandising.availability_status).toBe("preorder");
  expect(product.merchandising.local_available_qty).toBe(0);
  expect(product.variants[0].stock_qty).toBe(0);
  expect(product.variants[0].reserved_qty).toBe(0);

  await page.goto("/");
  await page.getByRole("button", { name: "Предзаказ / под заказ" }).click();
  const demand = page.getByRole("dialog", { name: "Предзаказ и товары под заказ" });
  await expect(demand.getByText(TITLE, { exact: true })).toBeVisible();
  await demand.getByRole("button", { name: "Оставить заявку на предзаказ" }).click();
  await expect(demand.getByRole("status")).toContainText("Оплата и склад не затронуты");
  await expect(demand.getByText("Заявка получена", { exact: true })).toBeVisible();

  const customerState = await page.evaluate(async () => {
    const token = localStorage.getItem("flashin_token");
    const headers = { Authorization: `Bearer ${token}` };
    const [cartResponse, ordersResponse] = await Promise.all([
      fetch("http://127.0.0.1:8000/api/cart", { headers }),
      fetch("http://127.0.0.1:8000/api/orders", { headers }),
    ]);
    return {
      cart: await cartResponse.json(),
      orders: await ordersResponse.json(),
    };
  });
  expect(customerState.cart.items || []).toHaveLength(0);
  expect(customerState.orders).toHaveLength(0);

  const afterResponse = await page.request.get(`${API_BASE}/api/catalog/products/${product.id}`);
  expect(afterResponse.ok()).toBeTruthy();
  const after = await afterResponse.json();
  expect(after.variants[0].stock_qty).toBe(0);
  expect(after.variants[0].reserved_qty).toBe(0);

  const operations = adminPage.locator("section.catalog-support-operations");
  await operations.getByRole("button", { name: "Обновить" }).click();
  await expect(operations.getByText(TITLE, { exact: false })).toBeVisible();
  await operations.getByRole("button", { name: "Связались" }).click();
  await expect(operations.getByRole("status")).toContainText("contacted");

  await adminContext.close();
});
