import { expect, test } from "@playwright/test";

const pilotRuntimeStatus = {
  schema_version: 1,
  checkout_decision: "GO",
  generated_at: "2026-08-04T12:00:00Z",
  enforced: true,
  runtime: {
    present: true,
    status: "active",
    run_ref: "abcdef123456",
    max_orders: 20,
    accepted_orders: 3,
    remaining_orders: 17,
    slot_count: 3,
    historical_slot_count: 3,
    allowlist_count: 5,
    stop_reason: null,
    opened_at: "2026-08-04T10:00:00Z",
    stopped_at: null,
    completed_at: null,
    updated_at: "2026-08-04T12:00:00Z",
  },
  database_integrity: { healthy: true, codes: [] },
  artifact_integrity: { applicable: true, healthy: true, codes: [] },
  money_attention: {
    payment_review_orders: 0,
    refund_attention_orders: 0,
    reconciliation_mismatches: 0,
    attention_required: false,
  },
};

const pilotReadinessStatus = {
  schema_version: 1,
  decision: "GO",
  ready_for_next_order: true,
  blocking_codes: [],
  warning_codes: [],
  diagnostics: {
    critical: {
      database: true,
      migrations: true,
      env: true,
      payments: true,
      moysklad: true,
      scheduler: true,
      notification_delivery: true,
      webhook_outbox: true,
      moysklad_sync: true,
    },
    advisory: {
      media: true,
      search: true,
    },
  },
  runtime: {
    checkout_decision: "GO",
    enforced: true,
    status: "active",
    accepted_orders: 3,
    remaining_orders: 17,
    allowlist_count: 5,
    database_integrity_healthy: true,
    artifact_integrity_applicable: true,
    artifact_integrity_healthy: true,
    money_attention_required: false,
    operational_safety_applicable: true,
    operational_safety_healthy: true,
  },
  request_id: "browser-e2e-request-id",
};

function failedBusinessEvent() {
  return {
    id: 501,
    event_type: "order.paid",
    aggregate_type: "order",
    aggregate_id: "9002",
    status: "failed",
    attempts: 5,
    replay_count: 0,
    payload: { order_id: 9002 },
    payload_error: null,
    last_error: "Destination mapping missing",
    created_at: "2026-08-04T10:00:00Z",
    last_attempt_at: "2026-08-04T10:05:00Z",
    failed_at: "2026-08-04T10:05:00Z",
    processed_at: null,
    resolved_at: null,
  };
}

async function mockAdminApi(page) {
  let products = [{
    id: 1,
    sku: "FLASH-001",
    title: "Pilot Jacket",
    slug: "pilot-jacket",
    brand: "FLASHIN",
    description: "",
    price: 12000,
    currency: "RUB",
    category: "Outerwear",
    active: true,
    variants: [{ id: 11, size: "M", color: "", sku: "FLASH-001-M", stock_qty: 5, reserved_qty: 0 }],
  }];
  let orders = [
    {
      id: 9001,
      status: "created",
      payment_status: "pending",
      total_amount: 12000,
      currency: "RUB",
      customer: { first_name: "Pilot" },
      items: [{ id: 1, title: "Pilot Jacket", size: "M", quantity: 1 }],
    },
    {
      id: 9002,
      status: "paid",
      payment_status: "paid",
      total_amount: 9000,
      currency: "RUB",
      customer: { first_name: "Pilot Paid" },
      items: [{ id: 2, title: "Pilot Trousers", size: "M", quantity: 1 }],
    },
  ];
  let businessEvent = failedBusinessEvent();
  let supportTickets = [{
    id: 601,
    order_id: 9002,
    subject: "Возврат заказа",
    message: "Клиент просит проверить возврат",
    status: "open",
    priority: "normal",
  }];
  let privacyRequests = [{
    id: 701,
    request_type: "consent_withdrawal",
    status: "requested",
    result_url: "",
  }];
  let returnRequests = [{
    id: 801,
    order_id: 9002,
    customer_id: 101,
    customer_username: "pilot",
    customer_name: "Pilot User",
    reason: "Не подошёл размер изделия",
    status: "requested",
    currency: "RUB",
    order_total: 9000,
    approved_refund_total: 0,
    refunded_total: 0,
    refundable_balance: 9000,
    provider_refund_id: "",
    provider_payment_id: "pay-9002",
    provider_payment_status: "succeeded",
  }];

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

    if (path === "/api/admin/login" && method === "POST") return json({ access_token: "admin-pilot-token" });
    if (path === "/api/admin/session" && method === "GET") {
      return json({
        id: 1,
        email: "pilot@flashin.test",
        role: "owner",
        all_access: true,
        permissions: [],
      });
    }
    if (path === "/api/admin/products" && method === "GET") return json(products);
    if (path === "/api/admin/orders" && method === "GET") return json(orders);
    if (path === "/api/admin/audit-logs" && method === "GET") {
      return json([{ id: 1, action: "pilot.login", entity_type: "admin", entity_id: "1", admin_id: 1, payload: "{}" }]);
    }
    if (path === "/api/ops/inventory/low-stock" && method === "GET") {
      return json([{ variant_id: 11, product_title: "Pilot Jacket", sku: "FLASH-001-M", stock_qty: 2, reserved_qty: 1, available_qty: 1 }]);
    }
    if (path === "/api/ops/abandoned-carts" && method === "GET") {
      return json([{ cart_id: 77, customer_id: 101, telegram_id: 101, items_count: 1, total_amount: 12000 }]);
    }
    if (path === "/api/admin/promocodes" && method === "POST") return json({ id: 70, code: "PILOT10" });
    if (path === "/api/admin/products" && method === "POST") {
      const body = request.postDataJSON();
      const created = { id: 2, active: true, currency: "RUB", ...body };
      products = [...products, created];
      return json(created, 201);
    }
    if (path === "/api/admin/products/import-csv" && method === "POST") {
      products = [...products, {
        id: 3,
        sku: "FLASH-CSV-001",
        title: "Imported Pilot Shirt",
        slug: "imported-pilot-shirt",
        brand: "FLASHIN",
        description: "",
        price: 7000,
        currency: "RUB",
        category: "Shirts",
        active: true,
        variants: [],
      }];
      return json({ imported: 1 });
    }
    if (path === "/api/admin/orders/export-csv" && method === "GET") {
      return route.fulfill({
        status: 200,
        contentType: "text/csv",
        headers: { "content-disposition": 'attachment; filename="flashin-pilot-orders.csv"' },
        body: "id,status,total\n9001,created,12000\n",
      });
    }
    if (path === "/api/admin/orders/9001/cancel" && method === "POST") {
      orders = orders.map((order) => order.id === 9001
        ? { ...order, status: "cancelled", payment_status: "cancelled" }
        : order);
      return json(orders.find((order) => order.id === 9001));
    }
    if (path === "/api/admin/orders/9002" && method === "PATCH") {
      const body = request.postDataJSON();
      orders = orders.map((order) => order.id === 9002 ? { ...order, status: body.status } : order);
      return json(orders.find((order) => order.id === 9002));
    }

    if (path === "/api/ops/abandoned-carts/queue-notifications" && method === "POST") return json({ queued: 1 });
    if (path === "/api/ops/inventory/snapshot" && method === "POST") return json({ created: true });
    if (path === "/api/ops/pilot-readiness" && method === "GET") return json(pilotReadinessStatus);
    if (path === "/api/ops/pilot-runtime" && method === "GET") return json(pilotRuntimeStatus);

    if (path === "/api/support/admin/tickets" && method === "GET") return json(supportTickets);
    if (path === "/api/support/admin/tickets/601" && method === "PATCH") {
      const body = request.postDataJSON();
      supportTickets = supportTickets.map((ticket) => ticket.id === 601 ? { ...ticket, ...body } : ticket);
      return json(supportTickets[0]);
    }
    if (path === "/api/privacy/admin/requests" && method === "GET") return json(privacyRequests);
    if (path === "/api/privacy/admin/requests/701/process" && method === "POST") {
      privacyRequests = privacyRequests.map((item) => item.id === 701
        ? { ...item, status: "processed", result_url: "processed://consent-withdrawal" }
        : item);
      return json(privacyRequests[0]);
    }
    if (path === "/api/admin/returns" && method === "GET") return json(returnRequests);
    if (path === "/api/returns/admin/approve" && method === "POST") {
      const body = request.postDataJSON();
      returnRequests = returnRequests.map((item) => item.id === body.return_id
        ? {
          ...item,
          status: body.amount < item.refundable_balance ? "approved_partial" : "approved",
          approved_refund_total: body.amount,
          refunded_total: body.amount,
          refundable_balance: Math.max(0, item.refundable_balance - body.amount),
          provider_refund_id: "refund-pilot-801",
        }
        : item);
      return json(returnRequests.find((item) => item.id === body.return_id));
    }

    if (path === "/api/platform/admin/events/summary" && method === "GET") {
      return json({
        counts: {
          failed: businessEvent.status === "failed" ? 1 : 0,
          pending: businessEvent.status === "pending" ? 1 : 0,
          processed: businessEvent.status === "processed" ? 1 : 0,
        },
        oldest_failed_at: businessEvent.status === "failed" ? businessEvent.failed_at : null,
      });
    }
    if (path === "/api/platform/admin/events" && method === "GET") {
      const status = url.searchParams.get("status");
      return json(!status || businessEvent.status === status ? [businessEvent] : []);
    }
    if (path === "/api/platform/admin/events/501" && method === "GET") return json(businessEvent);
    if (path === "/api/platform/admin/events/501/replay" && method === "POST") {
      businessEvent = {
        ...businessEvent,
        status: "pending",
        replay_count: businessEvent.replay_count + 1,
        last_error: null,
        failed_at: null,
        resolved_at: "2026-08-04T12:10:00Z",
      };
      return json(businessEvent);
    }

    return json({ detail: `Unmocked ${method} ${path}` }, 501);
  });
}

async function login(page) {
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "FLASHIN Admin" })).toBeVisible();
  await page.getByPlaceholder("Email администратора").fill("pilot@flashin.test");
  await page.getByPlaceholder("Пароль").fill("pilot-password");
  await page.getByRole("button", { name: "Войти" }).click();
  await expect(page.getByRole("button", { name: "Выйти" })).toBeVisible();
}

test("Admin critical pilot operator journey", async ({ page }) => {
  await mockAdminApi(page);
  await login(page);

  await expect(page.getByRole("heading", { name: "Импорт и экспорт" })).toBeVisible();
  const productsSection = page.locator("section").filter({
    has: page.getByRole("heading", { name: "Товары" }),
  });
  await expect(productsSection.getByText("Pilot Jacket", { exact: true })).toBeVisible();

  await page.getByPlaceholder("CODE").fill("PILOT10");
  await page.getByRole("button", { name: "Создать" }).first().click();
  await expect(page.getByRole("status")).toContainText("Промокод создан");

  await page.getByPlaceholder("SKU", { exact: true }).fill("FLASH-002");
  await page.getByPlaceholder("Название").fill("Pilot Trousers");
  await page.getByPlaceholder("slug").fill("pilot-trousers");
  await page.getByPlaceholder("Цена").fill("9000");
  await page.getByPlaceholder("Размер", { exact: true }).fill("M");
  await page.getByPlaceholder("SKU размера").fill("FLASH-002-M");
  await page.getByRole("button", { name: /Создать товар/i }).click();
  await expect(productsSection.getByText("Pilot Trousers", { exact: true })).toBeVisible();

  page.once("dialog", (dialog) => dialog.accept());
  await page.getByRole("button", { name: "Отменить до оплаты" }).click();
  await expect(page.getByText("Отменён")).toBeVisible();

  await page.getByRole("button", { name: "Обновить", exact: true }).click();
  await expect(page.getByRole("status")).toContainText("Данные обновлены");

  await page.getByRole("button", { name: "Выйти" }).click();
  await expect(page.getByRole("button", { name: "Войти" })).toBeVisible();
});

test("Admin operations, fulfillment and BusinessEvent recovery journey", async ({ page }) => {
  await mockAdminApi(page);
  await login(page);

  await expect(page.getByRole("heading", { name: "Контролируемый пилот" })).toBeVisible();
  await expect(page.locator(".pilot-decision")).toContainText("GO");
  await expect(page.getByText("3 / 20")).toBeVisible();
  await expect(page.getByText("Pilot Jacket").last()).toBeVisible();
  await expect(page.getByText("Cart #77")).toBeVisible();

  const queueNotifications = page.waitForResponse((response) => (
    response.url().endsWith("/api/ops/abandoned-carts/queue-notifications")
      && response.request().method() === "POST"
  ));
  await page.getByRole("button", { name: "Поставить уведомления по брошенным корзинам" }).click();
  expect((await queueNotifications).ok()).toBe(true);
  await expect(page.getByText("Cart #77")).toBeVisible();

  const inventorySnapshot = page.waitForResponse((response) => (
    response.url().endsWith("/api/ops/inventory/snapshot")
      && response.request().method() === "POST"
  ));
  await page.getByRole("button", { name: "Сделать снимок остатков" }).click();
  expect((await inventorySnapshot).ok()).toBe(true);
  await expect(page.getByText("FLASH-001-M")).toBeVisible();

  const csvInput = page.locator('input[accept=".csv,text/csv"]');
  await csvInput.setInputFiles({
    name: "pilot-products.csv",
    mimeType: "text/csv",
    buffer: Buffer.from("sku,title,price\nFLASH-CSV-001,Imported Pilot Shirt,7000\n"),
  });
  await expect(page.getByText("Imported Pilot Shirt")).toBeVisible();

  const downloadPromise = page.waitForEvent("download");
  await page.getByRole("button", { name: "Скачать заказы CSV" }).click();
  const download = await downloadPromise;
  expect(download.suggestedFilename()).toBe("flashin_orders.csv");
  await expect(page.getByRole("status")).toContainText("Выгрузка заказов скачана");

  await page.getByRole("button", { name: "Перевести: Собирается" }).click();
  await expect(page.getByText("Собирается").first()).toBeVisible();

  await expect(page.getByRole("heading", { name: "BusinessEvent recovery" })).toBeVisible();
  await page.getByRole("button", { name: /#501 · order\.paid/ }).click();
  await expect(page.getByRole("heading", { name: "Событие #501" })).toBeVisible();
  await page.getByPlaceholder(/исправлено сопоставление destination/i).fill("Исправлено сопоставление destination и проверена идемпотентность");
  page.once("dialog", (dialog) => dialog.accept());
  await page.getByRole("button", { name: "Подтвердить replay" }).click();
  await expect(page.getByRole("status")).toContainText("Событие #501 возвращено в очередь");
  await expect(page.getByText("Ожидает обработки").first()).toBeVisible();
});

test("Admin completes support, privacy and refund service operations", async ({ page }) => {
  await mockAdminApi(page);
  await login(page);

  await expect(page.getByRole("heading", { name: "Service Operations" })).toBeVisible();
  await expect(page.getByText("Требуют действия: 3")).toBeVisible();

  const supportQueue = page.getByRole("article", { name: "Обращения клиентов" });
  const privacyQueue = page.getByRole("article", { name: "Privacy-запросы" });
  const returnsQueue = page.getByRole("article", { name: "Возвраты и refunds" });

  await page.getByLabel("Статус обращения 601").selectOption("in_progress");
  await page.getByLabel("Приоритет обращения 601").selectOption("high");
  await page.getByRole("button", { name: "Сохранить обращение" }).click();
  await expect(page.getByRole("status")).toContainText("Обращение #601 обновлено");
  await expect(supportQueue.locator(".service-item-heading span")).toHaveText("В работе");

  page.once("dialog", (dialog) => dialog.accept());
  await page.getByRole("button", { name: "Исполнить privacy-запрос" }).click();
  await expect(page.getByRole("status")).toContainText("Privacy-запрос #701 исполнен");
  await expect(privacyQueue.locator(".service-item-heading span")).toHaveText("Исполнен");

  await page.getByLabel("Сумма возврата 801").fill("4500");
  page.once("dialog", (dialog) => dialog.accept());
  await page.getByRole("button", { name: "Подтвердить refund" }).click();
  await expect(page.getByRole("status")).toContainText("Возврат #801 передан платёжному провайдеру");
  await expect(returnsQueue.locator(".service-item-heading span")).toHaveText("Возвращён частично");
  await expect(returnsQueue.getByText("Provider refund: refund-pilot-801")).toBeVisible();
  await expect(page.getByText("Требуют действия: 1")).toBeVisible();
});
