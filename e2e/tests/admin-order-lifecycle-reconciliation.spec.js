import { expect, test } from "@playwright/test";

const SESSION = {
  id: 4,
  email: "support@flashin.test",
  role: "support",
  all_access: false,
  permissions: [
    "orders.read", "support.write", "customers.read", "notifications.read",
    "notifications.retry", "webhooks.read",
  ],
};

const ORDER = {
  id: 9002,
  status: "paid",
  payment_status: "paid",
  delivery_status: "pending",
  total_amount: 12000,
  currency: "RUB",
  customer: { first_name: "Lifecycle" },
  items: [{ id: 1, title: "Lifecycle Jacket", size: "M", quantity: 1 }],
};

function lifecycleTrace(overallStatus, moyskladStatus, requiresOperatorAction, operationalSignals = []) {
  return {
    schema_version: 3,
    request_id: `browser-${overallStatus.toLowerCase()}`,
    order: {
      id: ORDER.id,
      customer_id: 101,
      status: "paid",
      payment_status: "paid",
      delivery_status: "pending",
      total_amount: 12000,
      currency: "RUB",
    },
    payments: [{ id: 1 }],
    payment_events: [{ id: 2 }],
    returns: [],
    provider_commands: [{ id: 3 }],
    inventory: [{ id: 4 }],
    fulfillment: [{ id: 5 }],
    business_events: [],
    notifications: [{ id: 6 }],
    sla: [],
    attention: {
      provider_commands_actionable: 1,
      provider_failures: overallStatus === "BLOCKED" ? 1 : 0,
      inventory_invalid_rows: 0,
      failed_notifications: 0,
      business_events_unresolved: operationalSignals.length ? 1 : 0,
      business_events_failed: operationalSignals.some((item) => item.status === "REVIEW") ? 1 : 0,
      overdue_sla: 0,
      required: requiresOperatorAction,
    },
    reconciliation: {
      schema_version: 1,
      overall_status: overallStatus,
      requires_operator_action: requiresOperatorAction,
      stages: [
        { key: "payment", status: "PASS", reason: "payment_settled", next_action: "none", evidence: ["payment.status=succeeded"] },
        { key: "inventory", status: "PENDING", reason: "inventory_reserved_not_committed_yet", next_action: "wait_for_fulfillment", evidence: ["inventory.kind=reserve"] },
        {
          key: "moysklad",
          status: moyskladStatus,
          reason: moyskladStatus === "BLOCKED" ? "moysklad_command_failed" : "moysklad_command_in_progress",
          next_action: moyskladStatus === "BLOCKED" ? "inspect_moysklad_command_queue" : "wait_for_provider_command",
          evidence: ["provider=moysklad", `command.status=${moyskladStatus === "BLOCKED" ? "failed_or_review" : "pending_or_processing"}`],
        },
        { key: "fulfillment", status: "PENDING", reason: "fulfillment_in_progress", next_action: "wait_for_fulfillment", evidence: ["fulfillment.status=new"] },
        { key: "refunds", status: "PASS", reason: "no_refund_requested", next_action: "none", evidence: ["returns.count=0"] },
        { key: "notifications", status: "PENDING", reason: "notification_delivery_in_progress", next_action: "wait_for_notification_delivery", evidence: ["notification.status=pending_or_processing"] },
      ],
      operational_signals: operationalSignals,
    },
  };
}

function failedBusinessEventSignal() {
  return {
    key: "business_events",
    status: "REVIEW",
    reason: "business_event_recovery_required",
    next_action: "inspect_business_event_recovery",
    evidence: ["business_events.failed=1"],
  };
}

async function installApi(page) {
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

    if (path === "/api/admin/login" && method === "POST") return json({ access_token: "token-lifecycle" });
    if (path === "/api/admin/session" && method === "GET") return json(SESSION);
    if (path === "/api/admin/orders" && method === "GET") return json([ORDER]);
    if (path === "/api/ops/abandoned-carts" && method === "GET") return json([]);
    if (path === "/api/fulfillment/tasks" && method === "GET") return json([]);
    if (path === "/api/delivery-providers/shipments" && method === "GET") return json([]);
    if (path === "/api/fulfillment/sla" && method === "GET") return json([]);
    if (path === "/api/support/admin/tickets" && method === "GET") return json([]);
    if (path === "/api/admin/returns" && method === "GET") return json([]);
    if (path === "/api/platform/admin/events/summary" && method === "GET") {
      return json({ counts: { failed: 0, pending: 0, processed: 0 }, oldest_failed_at: null });
    }
    if (path === "/api/platform/admin/events" && method === "GET") return json([]);
    if (path === "/api/catalog/admin/showroom/appointments" && method === "GET") return json([]);
    if (path === "/api/ops/orders/9002/trace" && method === "GET") {
      return json(lifecycleTrace("PENDING", "PENDING", false));
    }
    if (path === "/api/ops/orders/9003/trace" && method === "GET") {
      const blocked = lifecycleTrace("BLOCKED", "BLOCKED", true);
      blocked.order.id = 9003;
      return json(blocked);
    }
    if (path === "/api/ops/orders/9004/trace" && method === "GET") {
      const review = lifecycleTrace("REVIEW", "PENDING", true, [failedBusinessEventSignal()]);
      review.order.id = 9004;
      return json(review);
    }

    return json({ detail: `Unexpected ${method} ${path}` }, 501);
  });
  return seen;
}

async function login(page) {
  await page.goto("/");
  await page.getByPlaceholder("Email администратора").fill(SESSION.email);
  await page.getByPlaceholder("Пароль").fill("pilot-password");
  await page.getByRole("button", { name: "Войти" }).click();
  await expect(page.getByText(`${SESSION.email} · ${SESSION.role}`, { exact: true })).toBeVisible();
}

test("operator distinguishes PENDING, recovery REVIEW and BLOCKED without mutation controls", async ({ page }) => {
  const seen = await installApi(page);
  await login(page);

  await expect(page.getByRole("heading", { name: "Диагностика сделки" })).toBeVisible();
  await page.getByLabel("ID заказа для диагностики").fill("9002");
  await page.getByRole("button", { name: "Открыть trace" }).click();

  const lifecycle = page.getByTestId("order-lifecycle-reconciliation");
  await expect(lifecycle).toBeVisible();
  await expect(page.getByText("PENDING · нормальный прогресс").first()).toBeVisible();
  await expect(page.getByText("Ручное действие не требуется")).toBeVisible();
  const moysklad = lifecycle.locator('[data-stage="moysklad"]');
  await expect(moysklad.getByText("PENDING", { exact: true })).toBeVisible();
  await expect(moysklad.getByText(/Ждать terminal state очереди провайдера/)).toBeVisible();

  await page.getByLabel("ID заказа для диагностики").fill("9004");
  await page.getByRole("button", { name: "Открыть trace" }).click();
  await expect(page.getByText("REVIEW · нужна проверка").first()).toBeVisible();
  const recoverySignals = page.getByTestId("order-lifecycle-operational-signals");
  await expect(recoverySignals).toBeVisible();
  await expect(recoverySignals.getByRole("heading", { name: "BusinessEvent recovery" })).toBeVisible();
  await expect(recoverySignals.getByText("REVIEW", { exact: true })).toBeVisible();
  await expect(recoverySignals.getByText(/Открыть BusinessEvent recovery/)).toBeVisible();

  await page.getByLabel("ID заказа для диагностики").fill("9003");
  await page.getByRole("button", { name: "Открыть trace" }).click();
  await expect(page.getByText("BLOCKED · дальнейший шаг остановлен").first()).toBeVisible();
  await expect(page.getByText("Нужно действие оператора")).toBeVisible();
  await expect(page.getByTestId("order-lifecycle-reconciliation").locator('[data-stage="moysklad"]').getByText("BLOCKED", { exact: true })).toBeVisible();
  await expect(page.getByText(/Проверить очередь команд МойСклад/)).toBeVisible();

  expect(seen).toContain("GET /api/ops/orders/9002/trace");
  expect(seen).toContain("GET /api/ops/orders/9004/trace");
  expect(seen).toContain("GET /api/ops/orders/9003/trace");
  expect(seen.some((entry) => /^(POST|PUT|PATCH|DELETE) \/api\/ops\/orders\//.test(entry))).toBe(false);
});