import crypto from "node:crypto";
import { devices, expect, test } from "@playwright/test";

const BOT_TOKEN = process.env.TELEGRAM_BOT_TOKEN || "test-token";
const ADMIN_EMAIL = process.env.ADMIN_EMAIL || "admin@test.local";
const ADMIN_PASSWORD = process.env.ADMIN_PASSWORD || "test-password";
const API_BASE = "http://127.0.0.1:8000";
const ADMIN_URL = "http://127.0.0.1:5174";
const PRODUCT_SKU = "FLASHIN-COAT-001";
const MATERIAL = "Integrated Cashmere";
const SEASON = "FW26-E2E";

function signedTelegramInitData(user) {
  const values = {
    auth_date: String(Math.floor(Date.now() / 1000)),
    query_id: "AAE2E_RICH_CATALOG",
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
  const user = { id: 2026081401, first_name: "Rich Catalog E2E", username: "rich_catalog_e2e" };
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

function futureSlotInput() {
  const slot = new Date(Date.now() + 48 * 60 * 60 * 1000);
  slot.setMinutes(slot.getMinutes() < 30 ? 30 : 0, 0, 0);
  if (slot.getMinutes() === 0) slot.setHours(slot.getHours() + 1);
  const pad = (value) => String(value).padStart(2, "0");
  return `${slot.getFullYear()}-${pad(slot.getMonth() + 1)}-${pad(slot.getDate())}T${pad(slot.getHours())}:${pad(slot.getMinutes())}`;
}

test("Rich catalog changes cross real Admin, PostgreSQL and Mini App customer flows", async ({ page, browser }) => {
  await installTelegram(page);

  const baselineResponse = await page.request.get(`${API_BASE}/api/catalog/products`);
  expect(baselineResponse.ok()).toBeTruthy();
  const baselineProducts = await baselineResponse.json();
  const baseline = baselineProducts.find((item) => item.sku === PRODUCT_SKU);
  expect(baseline, `${PRODUCT_SKU} must exist in seeded catalog`).toBeTruthy();
  expect(baseline.variants.some((variant) => Number(variant.available_qty) > 0)).toBeTruthy();

  const adminContext = await browser.newContext({ ...devices["Desktop Chrome"] });
  const adminPage = await adminContext.newPage();
  await loginAdmin(adminPage);
  const catalogPanel = adminPage.locator("section.catalog-commerce");
  const productButton = catalogPanel.getByRole("button", { name: new RegExp(`#${baseline.id} ·`) });
  await expect(productButton).toBeVisible();
  await productButton.click();

  await catalogPanel.getByLabel("Материал карточки", { exact: true }).fill(MATERIAL);
  await catalogPanel.getByLabel("Сезон карточки", { exact: true }).fill(SEASON);
  await catalogPanel.getByLabel("Позиция карточки в сетке", { exact: true }).fill("5");
  const exclusive = catalogPanel.getByText("Эксклюзив", { exact: true });
  const exclusiveCheckbox = exclusive.locator("input[type=checkbox]");
  if (!(await exclusiveCheckbox.isChecked())) await exclusive.click();
  await catalogPanel.getByRole("button", { name: "Добавить внешний ресурс" }).click();
  const externalRows = catalogPanel.locator(".form-grid").filter({ has: catalogPanel.getByPlaceholder("Ресурс / магазин") });
  const externalRow = externalRows.last();
  await externalRow.getByPlaceholder("Ресурс / магазин").fill("Integrated Partner");
  await externalRow.getByPlaceholder("https://...").fill("https://partner.example/integrated-coat");
  await catalogPanel.getByRole("button", { name: "Сохранить карточку" }).click();
  await expect(catalogPanel.getByRole("status")).toContainText(`Карточка #${baseline.id} сохранена`);

  const enrichedResponse = await page.request.get(`${API_BASE}/api/catalog/products/${baseline.id}`);
  expect(enrichedResponse.ok()).toBeTruthy();
  const enriched = await enrichedResponse.json();
  expect(enriched.merchandising.material).toBe(MATERIAL);
  expect(enriched.merchandising.season).toBe(SEASON);
  expect(enriched.merchandising.grid_rank).toBe(5);
  expect(enriched.merchandising.badges).toContain("exclusive");
  expect(enriched.external_availability.some((item) => item.source_name === "Integrated Partner")).toBeTruthy();

  await page.goto("/");
  await expect(page.getByRole("heading", { name: "Каталог" })).toBeVisible();
  await page.getByRole("button", { name: "Открыть каталог с фильтрами" }).click();
  const catalog = page.getByRole("dialog", { name: "Расширенный каталог FLASHIN" });
  await expect(catalog).toBeVisible();
  await catalog.getByPlaceholder("Материал").fill(MATERIAL);
  await catalog.getByPlaceholder("Сезон").fill(SEASON);
  await catalog.getByRole("button", { name: "Применить фильтры" }).click();
  await expect(catalog.getByText(baseline.title, { exact: true })).toBeVisible();
  await catalog.getByRole("button").filter({ hasText: baseline.title }).click();
  await expect(catalog.getByRole("heading", { name: baseline.title })).toBeVisible();
  await expect(catalog.getByText(`${MATERIAL} · ${SEASON}`, { exact: true })).toBeVisible();
  await expect(catalog.getByText("Integrated Partner", { exact: true })).toBeVisible();

  await catalog.getByRole("button", { name: "В избранное" }).click();
  await expect(catalog.getByRole("status")).toContainText("Добавлено в избранное");
  await catalog.getByRole("button", { name: "Добавить в корзину" }).click();
  await expect(catalog.getByRole("status")).toContainText("добавлен в корзину");

  await catalog.getByLabel("Оценка товара").selectOption("5");
  await catalog.getByPlaceholder("Ваш комментарий").fill("Integrated real-stack feedback");
  await catalog.getByRole("button", { name: "Сохранить оценку" }).click();
  await expect(catalog.getByText("Integrated real-stack feedback", { exact: true })).toBeVisible();

  const slot = futureSlotInput();
  await catalog.getByLabel("Дата и время примерки").fill(slot);
  await catalog.getByPlaceholder("Комментарий к визиту").fill("Integrated fitting request");
  await catalog.getByRole("button", { name: "Записаться на примерку" }).click();
  await expect(catalog.getByRole("status")).toContainText("Запрос на примерку отправлен");

  await catalogPanel.getByRole("button", { name: "Обновить" }).click();
  const appointmentRow = catalogPanel.locator(".row").filter({ hasText: `Product #${baseline.id}` }).last();
  await expect(appointmentRow).toBeVisible();
  await appointmentRow.getByRole("button", { name: "Подтвердить" }).click();
  await expect(catalogPanel.getByRole("status")).toContainText("confirmed");

  await adminContext.close();
});
