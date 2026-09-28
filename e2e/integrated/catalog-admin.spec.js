import crypto from "node:crypto";
import { devices, expect, test } from "@playwright/test";

const BOT_TOKEN = process.env.TELEGRAM_BOT_TOKEN || "test-token";
const ADMIN_EMAIL = process.env.ADMIN_EMAIL || "admin@test.local";
const ADMIN_PASSWORD = process.env.ADMIN_PASSWORD || "test-password";
const API_BASE = "http://127.0.0.1:8000";
const ADMIN_URL = "http://127.0.0.1:5174";
const PRODUCT_TITLE = "FLASHIN Wool Coat";
const PRODUCT_SKU = "FLASHIN-COAT-001";
const VARIANT_SKU = "FLASHIN-COAT-001-S";

function signedTelegramInitData(user) {
  const values = {
    auth_date: String(Math.floor(Date.now() / 1000)),
    query_id: "AAE2E_CATALOG_ADMIN",
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
  const user = { id: 20260808, first_name: "Catalog E2E", username: "flashin_catalog_e2e" };
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
  await page.goto(ADMIN_URL);
  await page.getByPlaceholder("Email администратора").fill(ADMIN_EMAIL);
  await page.getByPlaceholder("Пароль").fill(ADMIN_PASSWORD);
  await page.getByRole("button", { name: "Войти" }).click();
  await expect(page.getByRole("button", { name: "Выйти" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Каталог и остатки" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Supply Chain · МойСклад" })).toBeVisible();
}

async function publicProduct(request) {
  const response = await request.get(`${API_BASE}/api/products`);
  expect(response.ok()).toBeTruthy();
  const products = await response.json();
  const product = products.find((item) => item.sku === PRODUCT_SKU);
  expect(product, `${PRODUCT_SKU} must remain public`).toBeTruthy();
  return product;
}

async function setProductPrice(adminPage, productCard, price) {
  await productCard.getByLabel(`Цена ${PRODUCT_SKU}`).fill(String(price));
  await productCard.getByRole("button", { name: `Сохранить товар ${PRODUCT_SKU}` }).click();
  await expect(adminPage.getByRole("status")).toContainText(`Товар ${PRODUCT_SKU} обновлён`);
}

async function setVariantStock(adminPage, productCard, stock) {
  await productCard.getByLabel(`Остаток ${VARIANT_SKU}`).fill(String(stock));
  adminPage.once("dialog", (dialog) => dialog.accept());
  await productCard.getByRole("button", { name: `Обновить остаток ${VARIANT_SKU}` }).click();
  await expect(adminPage.getByRole("status")).toContainText(`Остаток ${VARIANT_SKU} обновлён`);
}

test("Admin catalog mutations are visible to customer surfaces and reversible", async ({ page, browser }) => {
  await installTelegram(page);
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "Каталог" })).toBeVisible();
  await expect(page.getByText(PRODUCT_TITLE)).toBeVisible();

  const baseline = await publicProduct(page.request);
  const baselinePrice = Number(baseline.price);
  const baselineVariant = baseline.variants.find((item) => item.sku === VARIANT_SKU);
  expect(baselineVariant).toBeTruthy();
  const baselineStock = Number(baselineVariant.stock_qty);
  expect(baselineStock).toBeGreaterThanOrEqual(Number(baselineVariant.reserved_qty || 0));

  const adminContext = await browser.newContext({ ...devices["Desktop Chrome"] });
  const adminPage = await adminContext.newPage();
  await loginAdmin(adminPage);
  const productCard = adminPage.getByRole("article", { name: PRODUCT_TITLE }).first();
  await expect(productCard).toBeVisible();
  await expect(productCard.getByText(PRODUCT_SKU, { exact: true })).toBeVisible();

  // Master-data mutation crosses the real Admin API and is immediately visible
  // on the public product contract. Restore it before moving to money flows.
  const temporaryPrice = Math.round((baselinePrice + 1) * 100) / 100;
  await setProductPrice(adminPage, productCard, temporaryPrice);
  let current = await publicProduct(page.request);
  expect(Number(current.price)).toBe(temporaryPrice);
  await setProductPrice(adminPage, productCard, baselinePrice);
  current = await publicProduct(page.request);
  expect(Number(current.price)).toBe(baselinePrice);

  // Publication state must cross all the way to the real Mini App catalog.
  adminPage.once("dialog", (dialog) => dialog.accept());
  await productCard.getByRole("button", { name: `Скрыть товар ${PRODUCT_SKU}` }).click();
  await expect(adminPage.getByRole("status")).toContainText(`Товар ${PRODUCT_SKU} скрыт из каталога`);
  await page.reload();
  await expect(page.getByRole("heading", { name: "Каталог" })).toBeVisible();
  await expect(page.getByText(PRODUCT_TITLE)).toHaveCount(0);

  await productCard.getByRole("button", { name: `Вернуть товар ${PRODUCT_SKU}` }).click();
  await expect(adminPage.getByRole("status")).toContainText(`Товар ${PRODUCT_SKU} снова опубликован`);
  await page.reload();
  await expect(page.getByText(PRODUCT_TITLE)).toBeVisible();

  // Inventory mutation honors the same backend used by checkout. The public
  // product payload must expose the new stock, then the test restores baseline.
  const temporaryStock = baselineStock + 1;
  await setVariantStock(adminPage, productCard, temporaryStock);
  current = await publicProduct(page.request);
  expect(current.variants.find((item) => item.sku === VARIANT_SKU)?.stock_qty).toBe(temporaryStock);
  await setVariantStock(adminPage, productCard, baselineStock);
  current = await publicProduct(page.request);
  expect(current.variants.find((item) => item.sku === VARIANT_SKU)?.stock_qty).toBe(baselineStock);

  await adminContext.close();
});
