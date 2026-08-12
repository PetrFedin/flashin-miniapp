import { expect, test } from "@playwright/test";

const PRODUCT = {
  id: 1,
  sku: "FLASH-RBAC-001",
  title: "RBAC Pilot Jacket",
  slug: "rbac-pilot-jacket",
  brand: "FLASHIN",
  description: "RBAC acceptance fixture",
  price: 12000,
  currency: "RUB",
  category: "Outerwear",
  active: true,
  variants: [{
    id: 11,
    size: "M",
    color: "Black",
    sku: "FLASH-RBAC-001-M",
    stock_qty: 5,
    reserved_qty: 1,
    available_qty: 4,
  }],
};

const ORDER = {
  id: 9002,
  status: "paid",
  payment_status: "paid",
  delivery_status: "pending",
  total_amount: 12000,
  currency: "RUB",
  customer: { first_name: "RBAC" },
  items: [{ id: 1, title: PRODUCT.title, size: "M", quantity: 1 }],
};

const RETURN = {
  id: 801,
  order_id: ORDER.id,
  customer_id: 101,
  customer_username: "rbac_customer",
  customer_name: "RBAC Customer",
  reason: "Размер",
  status: "requested",
  currency: "RUB",
  order_total: 12000,
  approved_refund_total: 0,
  refunded_total: 0,
  refundable_balance: 12000,
  provider_refund_id: "",
  provider_payment_id: "pay-rbac",
  provider_payment_status: "succeeded",
};

const SUPPORT = {
  id: 601,
  order_id: ORDER.id,
  subject: "RBAC support",
  message: "Проверка permission-aware интерфейса",
  status: "open",
  priority: "normal",
};

const PRIVACY = {
  id: 701,
  request_type: "export",
  status: "requested",
  result_url: "",
};

const FULFILLMENT_TASK = {
  id: 301,
  order_id: ORDER.id,
  status: "pending",
  assigned_admin_id: null,
  comment: "",
};

const SUPPLY_CHAIN = {
  schema_version: 1,
  attention_required: false,
  summary: {
    last_sync_status: "success",
    last_sync_at: "2026-08-12T18:00:00",
    pending_matches: 0,
    open_reconciliations: 0,
    open_conflicts: 0,
  },
  sync_logs: [],
  sku_matches: [],
  reconciliations: [],
  conflicts: [],
};

const ROLES = {
  manager: {
    id: 2,
    email: "manager@flashin.test",
    role: "manager",
    all_access: false,
    permissions: [
      "products.read", "products.write", "orders.read", "orders.write", "promo.write",
      "support.write", "notifications.read", "notifications.retry", "webhooks.read",
      "webhooks.write", "media.write", "security.read", "privacy.read",
    ],
  },
  warehouse: {
    id: 3,
    email: "warehouse@flashin.test",
    role: "warehouse",
    all_access: false,
    permissions: ["products.read", "inventory.write", "orders.read", "media.write"],
  },
  support: {
    id: 4,
    email: "support@flashin.test",
    role: "support",
    all_access: false,
    permissions: [
      "orders.read", "support.write", "customers.read", "notifications.read",
      "notifications.retry", "webhooks.read",
    ],
  },
};

async function installRoleApi(page, roleName) {
  const session = ROLES[roleName];
  const seen = [];
  await page.route("http://localhost:8000/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname;
    const method = request.method();
    seen.push(`${method} ${path}`);
    const json = (body, status = 200) => route.fulfill({
      status,
      contentType: "application/json",
      body: JSON.stringify(body),
    });

    if (path === "/api/admin/login" && method === "POST") return json({ access_token: `token-${roleName}` });
    if (path === "/api/admin/session" && method === "GET") return json(session);
    if (path === "/api/admin/products" && method === "GET") return json([PRODUCT]);
    if (path === "/api/admin/orders" && method === "GET") return json([ORDER]);
    if (path === "/api/ops/inventory/low-stock" && method === "GET") return json([]);
    if (path === "/api/ops/abandoned-carts" && method === "GET") {
      return json([{ cart_id: 77, customer_id: 101, telegram_id: "101", items_count: 1, total_amount: 12000 }]);
    }
    if (path === "/api/moysklad/operations-status" && method === "GET") return json(SUPPLY_CHAIN);
    if (path === "/api/ops/pilot-readiness" && method === "GET") return json({ schema_version: 0 });
    if (path === "/api/ops/pilot-runtime" && method === "GET") return json({ schema_version: 0 });
    if (path === "/api/fulfillment/tasks" && method === "GET") return json([FULFILLMENT_TASK]);
    if (path === "/api/delivery-providers/shipments" && method === "GET") return json([]);
    if (path === "/api/fulfillment/sla" && method === "GET") return json([]);
    if (path === "/api/support/admin/tickets" && method === "GET") return json([SUPPORT]);
    if (path === "/api/privacy/admin/requests" && method === "GET") return json([PRIVACY]);
    if (path === "/api/admin/returns" && method === "GET") return json([RETURN]);
    if (path === "/api/platform/admin/events/summary" && method === "GET") {
      return json({ counts: { failed: 0, pending: 0, processed: 0 }, oldest_failed_at: null });
    }
    if (path === "/api/platform/admin/events" && method === "GET") return json([]);

    // Mutation endpoints are not expected in this permission-rendering suite.
    return json({ detail: `Unexpected ${method} ${path}` }, 501);
  });
  return seen;
}

async function loginAs(page, roleName) {
  const session = ROLES[roleName];
  await page.goto("/");
  await page.getByPlaceholder("Email администратора").fill(session.email);
  await page.getByPlaceholder("Пароль").fill("pilot-password");
  await page.getByRole("button", { name: "Войти" }).click();
  await expect(page.getByText(`${session.email} · ${session.role}`, { exact: true })).toBeVisible();
}

test("manager can mutate catalog and orders but cannot mutate stock or privacy", async ({ page }) => {
  const seen = await installRoleApi(page, "manager");
  await loginAs(page, "manager");

  await expect(page.getByRole("heading", { name: "Контролируемый пилот" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Каталог и остатки" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Supply Chain · МойСклад" })).toBeVisible();

  const product = page.getByRole("article", { name: PRODUCT.title }).first();
  await expect(product.getByLabel(`Цена ${PRODUCT.sku}`)).toBeEnabled();
  await expect(product.getByRole("button", { name: `Сохранить товар ${PRODUCT.sku}` })).toBeVisible();
  await expect(product.getByLabel(`Остаток ${PRODUCT.variants[0].sku}`)).toBeDisabled();
  await expect(product.getByRole("button", { name: `Обновить остаток ${PRODUCT.variants[0].sku}` })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Синхронизировать с МойСклад" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Сделать снимок остатков" })).toHaveCount(0);
  await expect(page.getByRole("heading", { name: "Брошенные корзины" })).toHaveCount(0);

  const privacy = page.getByRole("article", { name: "Privacy-запросы" });
  await expect(privacy).toBeVisible();
  await expect(privacy.getByText(/нет privacy\.write/)).toBeVisible();
  await expect(privacy.getByRole("button", { name: "Исполнить privacy-запрос" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Подтвердить refund" })).toBeVisible();

  expect(seen).not.toContain("GET /api/ops/abandoned-carts");
  expect(seen).not.toContain("GET /api/admin/audit-logs");
});

test("warehouse mutates stock but sees catalog, Supply Chain and orders as read-only", async ({ page }) => {
  const seen = await installRoleApi(page, "warehouse");
  await loginAs(page, "warehouse");

  await expect(page.getByRole("heading", { name: "Контролируемый пилот" })).toHaveCount(0);
  const product = page.getByRole("article", { name: PRODUCT.title }).first();
  await expect(product.getByLabel(`Цена ${PRODUCT.sku}`)).toBeDisabled();
  await expect(product.getByRole("button", { name: `Сохранить товар ${PRODUCT.sku}` })).toHaveCount(0);
  await expect(product.getByLabel(`Остаток ${PRODUCT.variants[0].sku}`)).toBeEnabled();
  await expect(product.getByRole("button", { name: `Обновить остаток ${PRODUCT.variants[0].sku}` })).toBeVisible();

  await expect(page.getByRole("heading", { name: "Supply Chain · МойСклад" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Синхронизировать с МойСклад" })).toHaveCount(0);
  await expect(page.getByText(/Supply Chain доступен только для чтения/)).toBeVisible();
  await expect(page.getByText(/Fulfillment доступен только для чтения/)).toBeVisible();
  await expect(page.getByRole("button", { name: "Подтвердить refund" })).toHaveCount(0);
  await expect(page.getByRole("article", { name: "Обращения клиентов" })).toHaveCount(0);
  await expect(page.getByRole("article", { name: "Privacy-запросы" })).toHaveCount(0);

  expect(seen).not.toContain("GET /api/ops/pilot-readiness");
  expect(seen).not.toContain("GET /api/support/admin/tickets");
  expect(seen).not.toContain("GET /api/privacy/admin/requests");
  expect(seen).not.toContain("GET /api/ops/abandoned-carts");
});

test("support sees customers and support queues but never catalog or money mutations", async ({ page }) => {
  const seen = await installRoleApi(page, "support");
  await loginAs(page, "support");

  await expect(page.getByRole("heading", { name: "Каталог и остатки" })).toHaveCount(0);
  await expect(page.getByRole("heading", { name: "Supply Chain · МойСклад" })).toHaveCount(0);
  await expect(page.getByRole("heading", { name: "Контролируемый пилот" })).toHaveCount(0);
  await expect(page.getByText("Cart #77")).toBeVisible();
  await expect(page.getByRole("button", { name: "Поставить уведомления по брошенным корзинам" })).toBeVisible();

  const support = page.getByRole("article", { name: "Обращения клиентов" });
  await expect(support).toBeVisible();
  await expect(support.getByRole("button", { name: "Сохранить обращение" })).toBeVisible();
  await expect(page.getByRole("article", { name: "Privacy-запросы" })).toHaveCount(0);
  await expect(page.getByRole("article", { name: "Возвраты и refunds" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Подтвердить refund" })).toHaveCount(0);
  await expect(page.getByText(/Fulfillment доступен только для чтения/)).toBeVisible();

  expect(seen).not.toContain("GET /api/admin/products");
  expect(seen).not.toContain("GET /api/moysklad/operations-status");
  expect(seen).not.toContain("GET /api/ops/inventory/low-stock");
  expect(seen).not.toContain("GET /api/ops/pilot-readiness");
});
