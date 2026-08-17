import crypto from "node:crypto";
import { devices, expect, test } from "@playwright/test";

const BOT_TOKEN = process.env.TELEGRAM_BOT_TOKEN || "test-token";
const ADMIN_EMAIL = process.env.ADMIN_EMAIL || "admin@test.local";
const ADMIN_PASSWORD = process.env.ADMIN_PASSWORD || "test-password";
const API_BASE = "http://127.0.0.1:8000";
const ADMIN_URL = "http://127.0.0.1:5174";

function signedTelegramInitData(user) {
  const values = {
    auth_date: String(Math.floor(Date.now() / 1000)),
    query_id: `AAE2E_INTENT_${user.id}`,
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

async function installTelegram(page, user) {
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
  await expect(page.getByRole("heading", { name: "Каталог и merchandising" })).toBeVisible();
}

async function createZeroStockPreorder(catalogPanel, runId) {
  const sku = `E2E-INTENT-${runId}`;
  const title = `Integrated Preorder Coat ${runId}`;
  const slug = `integrated-preorder-coat-${runId.toLowerCase()}`;

  await catalogPanel.getByRole("button", { name: "Новая карточка" }).click();
  await catalogPanel.getByLabel("SKU карточки", { exact: true }).fill(sku);
  await catalogPanel.getByLabel("Название карточки", { exact: true }).fill(title);
  await catalogPanel.getByLabel("Slug карточки", { exact: true }).fill(slug);
  await catalogPanel.getByLabel("Категория карточки", { exact: true }).fill("Outerwear");
  await catalogPanel.getByLabel("Цена карточки", { exact: true }).fill("42000");
  await catalogPanel.getByPlaceholder("Размер", { exact: true }).fill("M");
  await catalogPanel.getByPlaceholder("Цвет", { exact: true }).fill("Black");
  await catalogPanel.getByPlaceholder("SKU варианта", { exact: true }).fill(`${sku}-M`);
  await catalogPanel.getByPlaceholder("Stock", { exact: true }).fill("0");
  await catalogPanel.getByRole("button", { name: "Создать карточку" }).click();
  await expect(catalogPanel.getByRole("status")).toContainText("Карточка #");

  return { sku, title };
}

async function customerGet(page, token, path) {
  const response = await page.request.get(`${API_BASE}${path}`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  expect(response.ok(), `GET ${path} failed with ${response.status()}`).toBeTruthy();
  return response.json();
}

test("preorder intent crosses real Admin, PostgreSQL and Mini App without creating checkout or payment", async ({ page, browser }) => {
  const runId = Date.now().toString(36);
  const telegramUser = {
    id: 202608170000 + (Date.now() % 100000),
    first_name: "Intent E2E",
    username: `intent_e2e_${runId}`,
  };
  await installTelegram(page, telegramUser);

  const adminContext = await browser.newContext({ ...devices["Desktop Chrome"] });
  const adminPage = await adminContext.newPage();
  await loginAdmin(adminPage);
  const catalogPanel = adminPage.locator("section.catalog-commerce");
  const productInput = await createZeroStockPreorder(catalogPanel, runId);

  const createdResponse = await page.request.get(`${API_BASE}/api/catalog/products?q=${encodeURIComponent(productInput.sku)}`);
  expect(createdResponse.ok()).toBeTruthy();
  const createdRows = await createdResponse.json();
  const created = createdRows.find((item) => item.sku === productInput.sku);
  expect(created, `${productInput.sku} must be created by the real Admin flow`).toBeTruthy();
  expect(created.variants).toHaveLength(1);
  expect(created.variants[0].stock_qty).toBe(0);
  expect(created.variants[0].reserved_qty).toBe(0);
  expect(created.variants[0].available_qty).toBe(0);

  await catalogPanel.getByLabel("Статус доступности карточки", { exact: true }).selectOption("preorder");
  await catalogPanel.getByLabel("Материал карточки", { exact: true }).fill("Pilot Cashmere");
  await catalogPanel.getByRole("button", { name: "Сохранить карточку" }).click();
  await expect(catalogPanel.getByRole("status")).toContainText(`Карточка #${created.id} сохранена`);

  const eligibleResponse = await page.request.get(`${API_BASE}/api/catalog/intents/eligible-products`);
  expect(eligibleResponse.ok()).toBeTruthy();
  const eligibleRows = await eligibleResponse.json();
  const eligible = eligibleRows.find((item) => item.id === created.id);
  expect(eligible).toBeTruthy();
  expect(eligible.intent_type).toBe("preorder");
  expect(eligible.variants.find((item) => item.id === created.variants[0].id)?.intent_eligible).toBe(true);

  const commercialUiMutations = [];
  page.on("request", (request) => {
    if (request.method() === "GET") return;
    const path = new URL(request.url()).pathname;
    if (["/api/cart/items", "/api/orders", "/api/payments"].some((prefix) => path.startsWith(prefix))) {
      commercialUiMutations.push(`${request.method()} ${path}`);
    }
  });

  await page.goto("/");
  await expect(page.getByRole("heading", { name: "Каталог" })).toBeVisible();
  await expect.poll(() => page.evaluate(() => localStorage.getItem("flashin_token"))).not.toBeNull();
  const token = await page.evaluate(() => localStorage.getItem("flashin_token"));
  expect(token).toBeTruthy();

  const cartBefore = await customerGet(page, token, "/api/cart");
  const ordersBefore = await customerGet(page, token, "/api/orders");
  const productBeforeResponse = await page.request.get(`${API_BASE}/api/catalog/products/${created.id}`);
  expect(productBeforeResponse.ok()).toBeTruthy();
  const productBefore = await productBeforeResponse.json();
  const variantBefore = productBefore.variants.find((item) => item.id === created.variants[0].id);
  expect(variantBefore.stock_qty).toBe(0);
  expect(variantBefore.reserved_qty).toBe(0);

  await page.getByRole("button", { name: "Предзаказ / под заказ" }).click();
  const dialog = page.getByRole("dialog", { name: "Предзаказ и индивидуальный заказ" });
  await expect(dialog).toBeVisible();
  await expect(dialog.getByLabel("Товар для предзаказа").locator(`option[value="${created.id}"]`)).toHaveCount(1);
  await dialog.getByLabel("Товар для предзаказа").selectOption(String(created.id));
  await expect(dialog.getByLabel("Вариант для предзаказа")).toBeVisible();
  await dialog.getByLabel("Вариант для предзаказа").selectOption(String(created.variants[0].id));
  await dialog.getByLabel("Количество").fill("2");
  await dialog.getByLabel("Комментарий").fill("Real-stack preorder request");
  await dialog.getByRole("button", { name: "Отправить заявку без оплаты" }).click();
  await expect(dialog.getByRole("status")).toContainText("Заявка #");

  const myIntents = await customerGet(page, token, "/api/catalog/intents/me");
  const intent = myIntents.find((item) => item.product_id === created.id);
  expect(intent).toBeTruthy();
  expect(intent.status).toBe("requested");
  expect(intent.payment_allowed).toBe(false);
  expect(intent.variant_id).toBe(created.variants[0].id);
  expect(intent.created_at).toMatch(/Z$/);

  const cartAfterIntent = await customerGet(page, token, "/api/cart");
  const ordersAfterIntent = await customerGet(page, token, "/api/orders");
  const productAfterIntentResponse = await page.request.get(`${API_BASE}/api/catalog/products/${created.id}`);
  expect(productAfterIntentResponse.ok()).toBeTruthy();
  const productAfterIntent = await productAfterIntentResponse.json();
  const variantAfterIntent = productAfterIntent.variants.find((item) => item.id === created.variants[0].id);

  expect(cartAfterIntent.items.length).toBe(cartBefore.items.length);
  expect(ordersAfterIntent.length).toBe(ordersBefore.length);
  expect(variantAfterIntent.stock_qty).toBe(variantBefore.stock_qty);
  expect(variantAfterIntent.reserved_qty).toBe(variantBefore.reserved_qty);
  expect(commercialUiMutations).toEqual([]);

  const blockedCartResponse = await page.request.post(`${API_BASE}/api/cart/items`, {
    headers: { Authorization: `Bearer ${token}` },
    data: { product_id: created.id, variant_id: created.variants[0].id, quantity: 1 },
  });
  expect(blockedCartResponse.status()).toBe(409);
  const blockedBody = await blockedCartResponse.json();
  expect(blockedBody.detail).toMatch(/Not enough stock/i);

  const cartAfterBlockedCheckout = await customerGet(page, token, "/api/cart");
  expect(cartAfterBlockedCheckout.items.length).toBe(cartBefore.items.length);

  const intentPanel = adminPage.locator("section.catalog-intent-operations");
  await intentPanel.getByRole("button", { name: "Обновить" }).click();
  await expect(intentPanel.getByText(productInput.title, { exact: false })).toBeVisible();
  await intentPanel.getByLabel(`Статус заявки ${intent.id}`).selectOption("working");
  await intentPanel.getByLabel(`Сумма предложения ${intent.id}`).fill("45500");
  await intentPanel.getByLabel(`Срок готовности ${intent.id}`).fill("2026-08-30T15:30");
  await intentPanel.getByLabel(`Комментарий оператора ${intent.id}`).fill("Production slot confirmed");
  await intentPanel.getByRole("button", { name: "Сохранить заявку" }).click();
  await expect(intentPanel.getByRole("status")).toContainText(`Заявка #${intent.id} обновлена`);

  await intentPanel.getByLabel(`Статус заявки ${intent.id}`).selectOption("ready");
  await intentPanel.getByRole("button", { name: "Сохранить заявку" }).click();
  await expect(intentPanel.getByRole("status")).toContainText(`Заявка #${intent.id} обновлена`);

  await dialog.getByRole("button", { name: "Обновить" }).click();
  const requestCard = dialog.locator(".intent-request").filter({ hasText: productInput.title });
  await expect(requestCard.getByText("Готово / доступно", { exact: true })).toBeVisible();
  await expect(requestCard.getByText(/Предложение:/)).toBeVisible();
  await expect(requestCard.getByText(/без автоматического списания/)).toBeVisible();
  await expect(requestCard.getByText(/Ориентир готовности:/)).toBeVisible();

  await intentPanel.getByLabel(`Сумма предложения ${intent.id}`).fill("");
  await intentPanel.getByLabel(`Срок готовности ${intent.id}`).fill("");
  await intentPanel.getByRole("button", { name: "Сохранить заявку" }).click();
  await expect(intentPanel.getByRole("status")).toContainText(`Заявка #${intent.id} обновлена`);

  await dialog.getByRole("button", { name: "Обновить" }).click();
  await expect(requestCard.getByText(/Предложение:/)).toHaveCount(0);
  await expect(requestCard.getByText(/Ориентир готовности:/)).toHaveCount(0);

  const finalIntents = await customerGet(page, token, "/api/catalog/intents/me");
  const finalIntent = finalIntents.find((item) => item.id === intent.id);
  expect(finalIntent.status).toBe("ready");
  expect(finalIntent.quote_amount).toBeNull();
  expect(finalIntent.estimated_ready_at).toBeNull();
  expect(finalIntent.updated_at).toMatch(/Z$/);

  const finalOrders = await customerGet(page, token, "/api/orders");
  const finalProductResponse = await page.request.get(`${API_BASE}/api/catalog/products/${created.id}`);
  expect(finalProductResponse.ok()).toBeTruthy();
  const finalProduct = await finalProductResponse.json();
  const finalVariant = finalProduct.variants.find((item) => item.id === created.variants[0].id);
  expect(finalOrders.length).toBe(ordersBefore.length);
  expect(finalVariant.stock_qty).toBe(0);
  expect(finalVariant.reserved_qty).toBe(0);
  expect(finalVariant.available_qty).toBe(0);

  await adminContext.close();
});
